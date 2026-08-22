"""Tenant-scoped Knowledge/RAG API (M6.4).

Datasets, documents and agent assignments are strictly owner-scoped: every
repository call receives the authenticated user id, so cross-tenant access is
indistinguishable from a missing resource. Document upload accepts a bounded
raw body limited to UTF-8 ``text/plain``/``text/markdown``; chunking,
duplicate SHA-256 detection and status lifecycle live in
:mod:`veetee_server.knowledge.ingest`. Retrieved chunks are untrusted data and
are returned together with provenance/citations and prompt-injection-safe
delimiting.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from veetee_server.config import Settings
from veetee_server.knowledge.ingest import ingest_document
from veetee_server.persistence import KnowledgeRepository, SearchResult, StoredChunk
from veetee_server.persistence.knowledge import StoredDataset, StoredDocument
from veetee_server.untrusted import sanitize_untrusted_text

from .router import CurrentUser
from .schemas import (
    AgentKnowledgeSearchRequest,
    DatasetCreate,
    DatasetUpdate,
    KnowledgeSearchRequest,
)

router = APIRouter(prefix="/api/v1/control", tags=["control-plane-knowledge"])

_FILENAME_MAX_CHARS = 255
_CHUNK_SIZE_MAX = 65536


def _knowledge_repository(request: Request) -> KnowledgeRepository:
    repository = getattr(request.app.state, "knowledge_repository", None)
    if not isinstance(repository, KnowledgeRepository):
        raise HTTPException(status_code=503, detail="Persistence is not enabled")
    return repository


KnowledgeRepositoryDependency = Annotated[
    KnowledgeRepository, Depends(_knowledge_repository)
]


def _settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):  # pragma: no cover - app always sets it
        raise HTTPException(status_code=500, detail="Settings are unavailable")
    return settings


SettingsDependency = Annotated[Settings, Depends(_settings)]


# ------------------------------------------------------------------ serializers


def _dataset_to_dict(dataset: StoredDataset) -> dict[str, Any]:
    return {
        "id": str(dataset.id),
        "name": dataset.name,
        "description": dataset.description,
        "version": dataset.version,
        "status": dataset.status,
        "created_at": dataset.created_at.isoformat(),
        "updated_at": dataset.updated_at.isoformat(),
    }


def _document_to_dict(document: StoredDocument) -> dict[str, Any]:
    return {
        "id": str(document.id),
        "dataset_id": str(document.dataset_id),
        "filename": document.filename,
        "media_type": document.media_type,
        "byte_size": document.byte_size,
        "sha256": document.sha256,
        "status": document.status,
        "error_code": document.error_code,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
    }


def _chunk_to_dict(chunk: StoredChunk) -> dict[str, Any]:
    return {
        "id": str(chunk.id),
        "document_id": str(chunk.document_id),
        "dataset_id": str(chunk.dataset_id),
        "ordinal": chunk.ordinal,
        "content": sanitize_untrusted_text(chunk.content),
        "char_start": chunk.char_start,
        "char_end": chunk.char_end,
        "token_estimate": chunk.token_estimate,
        "provenance": chunk.provenance,
        "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
    }


def _search_result_to_dict(result: SearchResult) -> dict[str, Any]:
    """Citation/provenance payload for one retrieved chunk."""
    citation = {
        "type": "knowledge",
        "chunk_id": str(result.chunk_id),
        "document_id": str(result.document_id),
        "dataset_id": str(result.dataset_id),
        "filename": result.filename,
        "ordinal": result.ordinal,
        "char_start": result.char_start,
        "char_end": result.char_end,
        "score": result.score,
    }
    return {
        **citation,
        "content": sanitize_untrusted_text(result.content),
        "provenance": result.provenance,
        "citation": citation,
    }


# ---------------------------------------------------------------- error mapping


def _map_repository_error(exc: Exception, missing: str) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=missing)
    if isinstance(exc, ValueError) and (
        "already exists" in str(exc) or "SHA256" in str(exc)
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    if isinstance(exc, ValueError) and "Optimistic lock" in str(exc):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Version mismatch"
        )
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Unexpected repository failure")


# -------------------------------------------------------------------- datasets


@router.post("/knowledge/datasets", status_code=201)
def create_dataset(
    payload: DatasetCreate,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> dict[str, Any]:
    try:
        dataset = repository.create_dataset(user_id, payload.name, payload.description)
    except Exception as exc:
        raise _map_repository_error(exc, "Dataset not found") from exc
    return _dataset_to_dict(dataset)


@router.get("/knowledge/datasets")
def list_datasets(
    user_id: CurrentUser, repository: KnowledgeRepositoryDependency
) -> list[dict[str, Any]]:
    return [_dataset_to_dict(d) for d in repository.list_datasets(user_id)]


@router.get("/knowledge/datasets/{dataset_id}")
def get_dataset(
    dataset_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> dict[str, Any]:
    dataset = repository.get_dataset(user_id, dataset_id)
    if dataset is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return _dataset_to_dict(dataset)


@router.patch("/knowledge/datasets/{dataset_id}")
def update_dataset(
    dataset_id: UUID,
    payload: DatasetUpdate,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> dict[str, Any]:
    try:
        dataset = repository.update_dataset(
            user_id,
            dataset_id,
            name=payload.name,
            description=payload.description,
            status=payload.status,
            expected_version=payload.expected_version,
        )
    except Exception as exc:
        raise _map_repository_error(exc, "Dataset not found") from exc
    return _dataset_to_dict(dataset)


@router.delete("/knowledge/datasets/{dataset_id}", status_code=204)
def delete_dataset(
    dataset_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> None:
    # Cascades to documents, chunks and agent assignments at the database level.
    if not repository.delete_dataset(user_id, dataset_id):
        raise HTTPException(status_code=404, detail="Dataset not found")


# ------------------------------------------------------------------- documents


def _validate_filename(filename: str) -> str:
    clean = filename.strip()
    if not clean or len(clean) > _FILENAME_MAX_CHARS:
        raise HTTPException(status_code=422, detail="Invalid document filename")
    if "/" in clean or "\\" in clean or "\x00" in clean or ".." in clean:
        raise HTTPException(status_code=422, detail="Invalid document filename")
    if any(ord(char) < 32 for char in clean):
        raise HTTPException(status_code=422, detail="Invalid document filename")
    return clean


async def _read_bounded_body(request: Request, max_bytes: int) -> bytes:
    """Reads the raw upload enforcing the size limit before and during streaming."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
        if declared > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Document exceeds maximum allowed size ({max_bytes} bytes)",
            )
    buffer: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Document exceeds maximum allowed size ({max_bytes} bytes)",
            )
        buffer.append(chunk)
    return b"".join(buffer)


@router.put("/knowledge/datasets/{dataset_id}/documents/{filename}", status_code=201)
async def upload_document(
    dataset_id: UUID,
    filename: str,
    request: Request,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
    settings: SettingsDependency,
    chunk_size: Annotated[int | None, Query(ge=1, le=_CHUNK_SIZE_MAX)] = None,
    chunk_overlap: Annotated[int | None, Query(ge=0, le=_CHUNK_SIZE_MAX)] = None,
) -> dict[str, Any]:
    safe_name = _validate_filename(filename)

    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in ("text/plain", "text/markdown"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only text/plain and text/markdown uploads are supported",
        )

    content_bytes = await _read_bounded_body(request, settings.rag_max_document_bytes)

    quota_service = getattr(request.app.state, "quota_service", None)
    if quota_service is not None:
        try:
            check = await asyncio.to_thread(
                quota_service.check_and_consume,
                user_id,
                "rag_bytes_month",
                len(content_bytes),
            )
            if not check.allowed:
                raise HTTPException(
                    status_code=429,
                    detail="Quota exceeded for rag_bytes_month",
                )
        except HTTPException:
            raise
        except Exception as exc:
            if await asyncio.to_thread(quota_service.is_quota_enabled, user_id):
                raise HTTPException(
                    status_code=503, detail="Quota enforcement unavailable"
                ) from exc

    effective_chunk_size = chunk_size or settings.rag_default_chunk_size
    effective_chunk_overlap = (
        chunk_overlap if chunk_overlap is not None else settings.rag_default_chunk_overlap
    )
    if effective_chunk_overlap >= effective_chunk_size:
        raise HTTPException(
            status_code=422,
            detail="chunk_overlap must be smaller than chunk_size",
        )

    try:
        document, chunks = ingest_document(
            repository,
            owner_user_id=user_id,
            dataset_id=dataset_id,
            filename=safe_name,
            media_type=cast(Literal["text/plain", "text/markdown"], media_type),
            content_bytes=content_bytes,
            chunk_size=effective_chunk_size,
            chunk_overlap=effective_chunk_overlap,
            max_bytes=settings.rag_max_document_bytes,
        )
    except Exception as exc:
        raise _map_repository_error(exc, "Dataset not found") from exc

    response = _document_to_dict(document)
    response["chunk_count"] = len(chunks)
    return response


@router.get("/knowledge/datasets/{dataset_id}/documents")
def list_documents(
    dataset_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> list[dict[str, Any]]:
    if repository.get_dataset(user_id, dataset_id) is None:
        raise HTTPException(status_code=404, detail="Dataset not found")
    docs = repository.list_documents(user_id, dataset_id)
    return [_document_to_dict(d) for d in docs]


@router.get("/knowledge/documents/{document_id}")
def get_document(
    document_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> dict[str, Any]:
    document = repository.get_document(user_id, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return _document_to_dict(document)


@router.get("/knowledge/documents/{document_id}/chunks")
def get_document_chunks(
    document_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> list[dict[str, Any]]:
    if repository.get_document(user_id, document_id) is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return [_chunk_to_dict(c) for c in repository.get_chunks(user_id, document_id)]


@router.delete("/knowledge/documents/{document_id}", status_code=204)
def delete_document(
    document_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> None:
    # Cascades to chunks; agent-dataset assignments reference datasets only.
    if not repository.delete_document(user_id, document_id):
        raise HTTPException(status_code=404, detail="Document not found")


# --------------------------------------------------------- agent assignments


@router.get("/agents/{agent_id}/knowledge/datasets")
def list_agent_datasets(
    agent_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> list[dict[str, Any]]:
    return [_dataset_to_dict(d) for d in repository.list_agent_datasets(user_id, agent_id)]


@router.put("/agents/{agent_id}/knowledge/datasets/{dataset_id}", status_code=204)
def assign_agent_dataset(
    agent_id: UUID,
    dataset_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> None:
    try:
        linked = repository.link_agent_dataset(user_id, agent_id, dataset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not linked:
        # Already assigned; assignment is idempotent.
        return


@router.delete("/agents/{agent_id}/knowledge/datasets/{dataset_id}", status_code=204)
def unassign_agent_dataset(
    agent_id: UUID,
    dataset_id: UUID,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
) -> None:
    if not repository.unlink_agent_dataset(user_id, agent_id, dataset_id):
        raise HTTPException(status_code=404, detail="Assignment not found")


# -------------------------------------------------------------- retrieval test


@router.post("/knowledge/search")
def search_knowledge(
    payload: KnowledgeSearchRequest,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
    settings: SettingsDependency,
) -> dict[str, Any]:
    limit = min(payload.limit, settings.rag_max_query_limit)
    max_chars = min(payload.max_chars, settings.rag_max_context_chars)
    results = repository.search_chunks(
        owner_user_id=user_id,
        dataset_ids=list(payload.dataset_ids),
        query=payload.query,
        limit=limit,
        max_chars=max_chars,
    )
    return {
        "results": [_search_result_to_dict(r) for r in results],
        "count": len(results),
    }


@router.post("/agents/{agent_id}/knowledge/search")
async def search_agent_knowledge(
    agent_id: UUID,
    payload: AgentKnowledgeSearchRequest,
    request: Request,
    user_id: CurrentUser,
    repository: KnowledgeRepositoryDependency,
    settings: SettingsDependency,
) -> dict[str, Any]:
    """Retrieval test through the M6.4 context provider path.

    Returns exactly what the prompt pipeline would inject: delimited
    ``<untrusted_knowledge>`` blocks plus citations/provenance metadata.
    """
    provider = _resolve_knowledge_provider(request, repository)
    config = {
        "limit": min(payload.limit, settings.rag_max_query_limit),
        "max_chars": min(payload.max_chars, settings.rag_max_context_chars),
    }
    result = await provider.fetch(
        owner_user_id=user_id,
        agent_id=agent_id,
        query=payload.query,
        timeout_ms=settings.context_provider_default_timeout_ms,
        config=config,
    )
    return {
        "provider_type": result.provider_type,
        "status": result.status,
        "content": result.content,
        "citations": result.citations,
        "provenance": result.provenance,
        "execution_time_ms": result.execution_time_ms,
    }


def _resolve_knowledge_provider(
    request: Request, repository: KnowledgeRepository
) -> Any:
    """Prefers the app-wired provider so runtime configuration stays consistent."""
    from veetee_server.prompt.providers import (
        ContextProviderRegistry,
        KnowledgeFTSContextProvider,
    )

    registry = getattr(request.app.state, "context_provider_registry", None)
    if isinstance(registry, ContextProviderRegistry):
        wired = registry.providers.get("knowledge_fts")
        if isinstance(wired, KnowledgeFTSContextProvider):
            return wired
    return KnowledgeFTSContextProvider(knowledge_repository=repository)

"""Document chunking, validation, streaming ingest, and status lifecycle management for RAG."""

from __future__ import annotations

import hashlib
import math
import uuid
from typing import Any, Literal

from veetee_server.persistence.knowledge import KnowledgeRepository, StoredChunk, StoredDocument


def validate_and_hash_document(
    content_bytes: bytes,
    media_type: str,
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[str, str, int]:
    """Validates byte size, media type, UTF-8 text encoding, and computes SHA-256 digest.

    Returns (decoded_text, sha256_hex, byte_size).
    """
    byte_size = len(content_bytes)
    if byte_size == 0:
        raise ValueError("Document content cannot be empty")
    if byte_size > max_bytes:
        raise ValueError(f"Document size ({byte_size} bytes) exceeds limit ({max_bytes} bytes)")

    clean_media_type = media_type.strip().lower()
    if clean_media_type not in ("text/plain", "text/markdown"):
        raise ValueError(
            f"Unsupported media_type '{media_type}'. "
            "Only text/plain and text/markdown are supported."
        )

    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"Invalid UTF-8 text content: {exc}") from exc

    # A whitespace-only payload would otherwise produce a "ready" document
    # with zero chunks; reject it before any persistence happens.
    if not text.strip():
        raise ValueError("Document contains no extractable text")

    sha256 = hashlib.sha256(content_bytes).hexdigest()
    return text, sha256, byte_size


def chunk_text(
    text: str,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
) -> list[tuple[int, str, int, int, int]]:
    """Deterministically chunks UTF-8 text into character range slices with overlap.

    Returns a list of (ordinal, chunk_text, char_start, char_end, token_estimate).
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    clean_text = text.strip()
    if not clean_text:
        return []

    text_len = len(clean_text)
    step = chunk_size - chunk_overlap
    chunks: list[tuple[int, str, int, int, int]] = []
    start = 0
    ordinal = 0

    while start < text_len:
        end = min(start + chunk_size, text_len)
        slice_str = clean_text[start:end]
        token_est = max(1, math.ceil(len(slice_str) / 4))
        chunks.append((ordinal, slice_str, start, end, token_est))
        ordinal += 1
        if end >= text_len:
            break
        start += step

    return chunks


def ingest_document(
    repository: KnowledgeRepository,
    owner_user_id: uuid.UUID,
    dataset_id: uuid.UUID,
    filename: str,
    media_type: Literal["text/plain", "text/markdown"],
    content_bytes: bytes,
    chunk_size: int = 500,
    chunk_overlap: int = 50,
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[StoredDocument, list[StoredChunk]]:
    """Performs full document ingest with pending->processing->ready status cycle.

    Validates size/media/UTF-8, rejects duplicate SHA-256 within the dataset,
    then deterministically chunks and stores provenance-tagged chunks.
    """
    text, sha256, byte_size = validate_and_hash_document(content_bytes, media_type, max_bytes)
    if not text.strip():
        raise ValueError("Document content cannot contain only whitespace")

    # Create document row in pending status
    doc = repository.create_document(
        owner_user_id=owner_user_id,
        dataset_id=dataset_id,
        filename=filename,
        media_type=media_type,
        byte_size=byte_size,
        sha256=sha256,
    )

    try:
        repository.update_document_status(owner_user_id, doc.id, "processing")
        raw_chunks = chunk_text(text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)

        chunk_data: list[tuple[int, str, int, int, int, dict[str, Any]]] = [
            (
                ord_val,
                c_text,
                c_start,
                c_end,
                tok_est,
                {
                    "filename": filename,
                    "dataset_id": str(dataset_id),
                    "document_id": str(doc.id),
                    "char_start": c_start,
                    "char_end": c_end,
                },
            )
            for ord_val, c_text, c_start, c_end, tok_est in raw_chunks
        ]

        inserted_chunks = repository.insert_chunks(
            owner_user_id, dataset_id, doc.id, chunk_data
        )
        repository.update_document_status(owner_user_id, doc.id, "ready")

        ready_doc = repository.get_document(owner_user_id, doc.id)
        assert ready_doc is not None
        return ready_doc, inserted_chunks
    except Exception:
        repository.update_document_status(
            owner_user_id, doc.id, "failed", error_code="ingest_failed"
        )
        raise

"""Tenant-aware repository for RAG Datasets, Documents, and Chunks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, cast

from psycopg.types.json import Jsonb

from .database import PostgresDatabase
from .repository import record_audit


@dataclass(frozen=True, slots=True)
class StoredDataset:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    name: str
    description: str
    version: int
    status: Literal["active", "archived"]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredDocument:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    dataset_id: uuid.UUID
    filename: str
    media_type: Literal["text/plain", "text/markdown"]
    byte_size: int
    sha256: str
    status: Literal["pending", "processing", "ready", "failed"]
    error_code: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredChunk:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    dataset_id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    content: str
    char_start: int
    char_end: int
    token_estimate: int
    provenance: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    dataset_id: uuid.UUID
    filename: str
    ordinal: int
    content: str
    char_start: int
    char_end: int
    score: float
    provenance: dict[str, Any] = field(default_factory=dict)


def cast_uuid(val: Any) -> uuid.UUID:
    if isinstance(val, uuid.UUID):
        return val
    return uuid.UUID(str(val))


def _dataset_from_row(row: tuple[Any, ...]) -> StoredDataset:
    return StoredDataset(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        name=cast(str, row[2]),
        description=cast(str, row[3]),
        version=cast(int, row[4]),
        status=cast(Literal["active", "archived"], row[5]),
        created_at=cast(datetime, row[6]),
        updated_at=cast(datetime, row[7]),
    )


_DATASET_COLUMNS = (
    "id, owner_user_id, name, description, version, status, created_at, updated_at"
)


def _document_from_row(row: tuple[Any, ...]) -> StoredDocument:
    return StoredDocument(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        dataset_id=cast_uuid(row[2]),
        filename=cast(str, row[3]),
        media_type=cast(Literal["text/plain", "text/markdown"], row[4]),
        byte_size=cast(int, row[5]),
        sha256=cast(str, row[6]),
        status=cast(Literal["pending", "processing", "ready", "failed"], row[7]),
        error_code=cast(str, row[8]),
        created_at=cast(datetime, row[9]),
        updated_at=cast(datetime, row[10]),
    )


_DOCUMENT_COLUMNS = (
    "id, owner_user_id, dataset_id, filename, media_type, byte_size, sha256, "
    "status, error_code, created_at, updated_at"
)


def _chunk_from_row(row: tuple[Any, ...]) -> StoredChunk:
    return StoredChunk(
        id=cast_uuid(row[0]),
        owner_user_id=cast_uuid(row[1]),
        dataset_id=cast_uuid(row[2]),
        document_id=cast_uuid(row[3]),
        ordinal=cast(int, row[4]),
        content=cast(str, row[5]),
        char_start=cast(int, row[6]),
        char_end=cast(int, row[7]),
        token_estimate=cast(int, row[8]),
        provenance=cast(dict[str, Any], row[9]),
        created_at=cast(datetime | None, row[10]),
    )


_CHUNK_COLUMNS = (
    "id, owner_user_id, dataset_id, document_id, ordinal, content, "
    "char_start, char_end, token_estimate, provenance, created_at"
)


class KnowledgeRepository:
    """PostgreSQL-backed tenant-isolated dataset, document, chunk, and RAG repository."""

    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def create_dataset(
        self,
        owner_user_id: uuid.UUID,
        name: str,
        description: str = "",
    ) -> StoredDataset:
        clean_name = name.strip()
        if not clean_name:
            raise ValueError("Dataset name cannot be empty")
        dataset_id = uuid.uuid4()
        with self.database.connection() as conn:
            # Check duplicate name for tenant
            dup = conn.execute(
                "SELECT id FROM veetee_datasets WHERE owner_user_id = %s AND name = %s",
                (owner_user_id, clean_name),
            ).fetchone()
            if dup:
                raise ValueError(f"Dataset with name '{clean_name}' already exists")

            row = conn.execute(
                "INSERT INTO veetee_datasets (id, owner_user_id, name, description) "
                "VALUES (%s, %s, %s, %s) "
                f"RETURNING {_DATASET_COLUMNS}",
                (dataset_id, owner_user_id, clean_name, description.strip()),
            ).fetchone()
            assert row is not None
            dataset = _dataset_from_row(row)
            # Audit event contains metadata without text content
            record_audit(
                self.database,
                owner_user_id,
                "dataset.create",
                "dataset",
                str(dataset_id),
                {"name": clean_name},
                connection=conn,
            )
            return dataset

    def get_dataset(
        self, owner_user_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> StoredDataset | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_DATASET_COLUMNS} "
                "FROM veetee_datasets WHERE id = %s AND owner_user_id = %s",
                (dataset_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            return _dataset_from_row(row)

    def list_datasets(self, owner_user_id: uuid.UUID) -> list[StoredDataset]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_DATASET_COLUMNS} "
                "FROM veetee_datasets WHERE owner_user_id = %s ORDER BY name ASC",
                (owner_user_id,),
            ).fetchall()
            return [_dataset_from_row(r) for r in rows]

    def update_dataset(
        self,
        owner_user_id: uuid.UUID,
        dataset_id: uuid.UUID,
        name: str | None = None,
        description: str | None = None,
        status: Literal["active", "archived"] | None = None,
        expected_version: int | None = None,
    ) -> StoredDataset:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_DATASET_COLUMNS} FROM veetee_datasets "
                "WHERE id = %s AND owner_user_id = %s",
                (dataset_id, owner_user_id),
            ).fetchone()
            current = _dataset_from_row(row) if row else None
            if not current:
                raise KeyError(f"Dataset {dataset_id} not found")
            if expected_version is not None and current.version != expected_version:
                raise ValueError(
                    "Optimistic lock failure: expected version "
                    f"{expected_version}, got {current.version}"
                )

            new_name = name.strip() if name is not None else current.name
            new_desc = description.strip() if description is not None else current.description
            new_status = status if status is not None else current.status

            row = conn.execute(
                "UPDATE veetee_datasets "
                "SET name = %s, description = %s, status = %s, version = version + 1, "
                "updated_at = now() "
                "WHERE id = %s AND owner_user_id = %s AND version = %s "
                f"RETURNING {_DATASET_COLUMNS}",
                (
                    new_name,
                    new_desc,
                    new_status,
                    dataset_id,
                    owner_user_id,
                    current.version,
                ),
            ).fetchone()
            if row is None:
                raise ValueError("Optimistic lock failure")
            dataset = _dataset_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "dataset.update",
                "dataset",
                str(dataset_id),
                {"version": dataset.version},
                connection=conn,
            )
            return dataset

    def delete_dataset(self, owner_user_id: uuid.UUID, dataset_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "DELETE FROM veetee_datasets WHERE id = %s AND owner_user_id = %s",
                (dataset_id, owner_user_id),
            )
            deleted = res.rowcount > 0
            if deleted:
                record_audit(
                    self.database,
                    owner_user_id,
                    "dataset.delete",
                    "dataset",
                    str(dataset_id),
                    connection=conn,
                )
            return bool(deleted)

    def create_document(
        self,
        owner_user_id: uuid.UUID,
        dataset_id: uuid.UUID,
        filename: str,
        media_type: Literal["text/plain", "text/markdown"],
        byte_size: int,
        sha256: str,
    ) -> StoredDocument:
        clean_filename = filename.strip()
        if not clean_filename:
            raise ValueError("Filename cannot be empty")
        if media_type not in ("text/plain", "text/markdown"):
            raise ValueError(f"Unsupported media_type: {media_type}")
        if len(sha256) != 64:
            raise ValueError("Invalid sha256 checksum length")

        with self.database.connection() as conn:
            # Check dataset exists and belongs to owner
            ds = conn.execute(
                "SELECT id FROM veetee_datasets WHERE id = %s AND owner_user_id = %s",
                (dataset_id, owner_user_id),
            ).fetchone()
            if not ds:
                raise KeyError(f"Dataset {dataset_id} not found")

            # Check duplicate sha256 within same dataset
            dup = conn.execute(
                "SELECT id, filename FROM veetee_documents "
                "WHERE dataset_id = %s AND sha256 = %s",
                (dataset_id, sha256),
            ).fetchone()
            if dup:
                raise ValueError(
                    "Duplicate document with SHA256 checksum already exists "
                    f"in dataset (id={dup[0]})"
                )

            doc_id = uuid.uuid4()
            row = conn.execute(
                "INSERT INTO veetee_documents "
                "(id, owner_user_id, dataset_id, filename, media_type, byte_size, "
                "sha256, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending') "
                f"RETURNING {_DOCUMENT_COLUMNS}",
                (
                    doc_id,
                    owner_user_id,
                    dataset_id,
                    clean_filename,
                    media_type,
                    byte_size,
                    sha256,
                ),
            ).fetchone()
            assert row is not None
            doc = _document_from_row(row)
            record_audit(
                self.database,
                owner_user_id,
                "document.create",
                "document",
                str(doc_id),
                {
                    "dataset_id": str(dataset_id),
                    "filename": clean_filename,
                    "byte_size": byte_size,
                },
                connection=conn,
            )
            return doc

    def get_document(
        self, owner_user_id: uuid.UUID, document_id: uuid.UUID
    ) -> StoredDocument | None:
        with self.database.connection() as conn:
            row = conn.execute(
                f"SELECT {_DOCUMENT_COLUMNS} "
                "FROM veetee_documents WHERE id = %s AND owner_user_id = %s",
                (document_id, owner_user_id),
            ).fetchone()
            if not row:
                return None
            return _document_from_row(row)

    def list_documents(
        self, owner_user_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> list[StoredDocument]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_DOCUMENT_COLUMNS} "
                "FROM veetee_documents WHERE dataset_id = %s AND owner_user_id = %s "
                "ORDER BY created_at DESC",
                (dataset_id, owner_user_id),
            ).fetchall()
            return [_document_from_row(r) for r in rows]

    def update_document_status(
        self,
        owner_user_id: uuid.UUID,
        document_id: uuid.UUID,
        status: Literal["pending", "processing", "ready", "failed"],
        error_code: str = "",
    ) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "UPDATE veetee_documents "
                "SET status = %s, error_code = %s, updated_at = now() "
                "WHERE id = %s AND owner_user_id = %s",
                (status, error_code, document_id, owner_user_id),
            )
            return res.rowcount > 0

    def delete_document(self, owner_user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "DELETE FROM veetee_documents WHERE id = %s AND owner_user_id = %s",
                (document_id, owner_user_id),
            )
            deleted = res.rowcount > 0
            if deleted:
                record_audit(
                    self.database,
                    owner_user_id,
                    "document.delete",
                    "document",
                    str(document_id),
                    connection=conn,
                )
            return bool(deleted)

    def insert_chunks(
        self,
        owner_user_id: uuid.UUID,
        dataset_id: uuid.UUID,
        document_id: uuid.UUID,
        chunk_data: list[tuple[int, str, int, int, int, dict[str, Any]]],
    ) -> list[StoredChunk]:
        """Inserts multiple document chunks in a single transaction."""
        inserted: list[StoredChunk] = []
        with self.database.connection() as conn:
            for ordinal, content, char_start, char_end, token_estimate, provenance in chunk_data:
                chunk_id = uuid.uuid4()
                row = conn.execute(
                    "INSERT INTO veetee_chunks "
                    "(id, owner_user_id, dataset_id, document_id, ordinal, content, "
                    "char_start, char_end, token_estimate, provenance) "
                    f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    f"RETURNING {_CHUNK_COLUMNS}",
                    (
                        chunk_id,
                        owner_user_id,
                        dataset_id,
                        document_id,
                        ordinal,
                        content,
                        char_start,
                        char_end,
                        token_estimate,
                        Jsonb(provenance),
                    ),
                ).fetchone()
                assert row is not None
                inserted.append(_chunk_from_row(row))
            return inserted

    def get_chunks(self, owner_user_id: uuid.UUID, document_id: uuid.UUID) -> list[StoredChunk]:
        with self.database.connection() as conn:
            rows = conn.execute(
                f"SELECT {_CHUNK_COLUMNS} "
                "FROM veetee_chunks WHERE document_id = %s AND owner_user_id = %s "
                "ORDER BY ordinal ASC",
                (document_id, owner_user_id),
            ).fetchall()
            return [_chunk_from_row(r) for r in rows]

    def search_chunks(
        self,
        owner_user_id: uuid.UUID,
        dataset_ids: list[uuid.UUID],
        query: str,
        limit: int = 5,
        max_chars: int = 2000,
    ) -> list[SearchResult]:
        """FTS retrieval using PostgreSQL native tsvector/tsquery with safe fallback."""
        clean_query = query.strip()
        if not clean_query or not dataset_ids:
            return []
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1")

        dataset_id_strs = [str(did) for did in dataset_ids]
        fetch_limit = max(limit * 2, limit)

        query_sql = """
        SELECT c.id, c.document_id, c.dataset_id, d.filename, c.ordinal, c.content,
               c.char_start, c.char_end,
               ts_rank_cd(c.content_tsv, q.tsq) AS score, c.provenance
        FROM veetee_chunks c
        JOIN veetee_documents d ON d.id = c.document_id
        CROSS JOIN LATERAL (
            SELECT COALESCE(
                NULLIF(websearch_to_tsquery('simple', %s), ''::tsquery),
                plainto_tsquery('simple', %s)
            ) AS tsq
        ) q
        WHERE c.owner_user_id = %s
          AND c.dataset_id = ANY(%s::uuid[])
          AND d.status = 'ready'
          AND c.content_tsv @@ q.tsq
        ORDER BY score DESC, c.document_id ASC, c.ordinal ASC
        LIMIT %s
        """

        results: list[SearchResult] = []
        accumulated_chars = 0

        with self.database.connection() as conn:
            rows = conn.execute(
                query_sql,
                (clean_query, clean_query, owner_user_id, dataset_id_strs, fetch_limit),
            ).fetchall()

            for r in rows:
                content = cast(str, r[5])
                if (accumulated_chars + len(content)) > max_chars:
                    break
                results.append(
                    SearchResult(
                        chunk_id=cast_uuid(r[0]),
                        document_id=cast_uuid(r[1]),
                        dataset_id=cast_uuid(r[2]),
                        filename=cast(str, r[3]),
                        ordinal=cast(int, r[4]),
                        content=content,
                        char_start=cast(int, r[6]),
                        char_end=cast(int, r[7]),
                        score=cast(float, r[8]),
                        provenance=cast(dict[str, Any], r[9]),
                    )
                )
                accumulated_chars += len(content)
                if len(results) >= limit:
                    break

        return results

    def link_agent_dataset(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> bool:
        with self.database.connection() as conn:
            # Verify owner
            agent_ok = conn.execute(
                "SELECT id FROM veetee_agents WHERE id = %s AND owner_user_id = %s",
                (agent_id, owner_user_id),
            ).fetchone()
            ds_ok = conn.execute(
                "SELECT id FROM veetee_datasets WHERE id = %s AND owner_user_id = %s",
                (dataset_id, owner_user_id),
            ).fetchone()
            if not agent_ok or not ds_ok:
                raise KeyError("Agent or dataset not found for current tenant")

            res = conn.execute(
                "INSERT INTO veetee_agent_datasets (owner_user_id, agent_id, dataset_id) "
                "VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (owner_user_id, agent_id, dataset_id),
            )
            linked = res.rowcount > 0
            if linked:
                record_audit(
                    self.database,
                    owner_user_id,
                    "agent.dataset.link",
                    "agent_dataset",
                    str(agent_id),
                    {"dataset_id": str(dataset_id)},
                    connection=conn,
                )
            return linked

    def unlink_agent_dataset(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID, dataset_id: uuid.UUID
    ) -> bool:
        with self.database.connection() as conn:
            res = conn.execute(
                "DELETE FROM veetee_agent_datasets ad "
                "USING veetee_datasets d "
                "WHERE ad.dataset_id = d.id AND ad.agent_id = %s "
                "AND ad.dataset_id = %s AND ad.owner_user_id = %s "
                "AND d.owner_user_id = %s",
                (agent_id, dataset_id, owner_user_id, owner_user_id),
            )
            unlinked = res.rowcount > 0
            if unlinked:
                record_audit(
                    self.database,
                    owner_user_id,
                    "agent.dataset.unlink",
                    "agent_dataset",
                    str(agent_id),
                    {"dataset_id": str(dataset_id)},
                    connection=conn,
                )
            return unlinked

    def list_agent_datasets(
        self, owner_user_id: uuid.UUID, agent_id: uuid.UUID
    ) -> list[StoredDataset]:
        with self.database.connection() as conn:
            rows = conn.execute(
                "SELECT d.id, d.owner_user_id, d.name, d.description, d.version, "
                "d.status, d.created_at, d.updated_at "
                "FROM veetee_datasets d "
                "JOIN veetee_agent_datasets ad ON ad.dataset_id = d.id "
                "WHERE ad.agent_id = %s AND ad.owner_user_id = %s "
                "AND d.owner_user_id = %s ORDER BY d.name ASC",
                (agent_id, owner_user_id, owner_user_id),
            ).fetchall()
            return [_dataset_from_row(r) for r in rows]

from __future__ import annotations

import asyncio
import sqlite3
import re
import unicodedata
from pathlib import Path
from typing import Iterable, List

from core.memory.models import MemoryFact


SCHEMA_VERSION = 2


def _normalize_search_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    normalized = normalized.replace("đ", "d")
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[a-z0-9]+", normalized))


def _row_to_fact(row: sqlite3.Row) -> MemoryFact:
    values = dict(row)
    return MemoryFact(
        id=int(values["id"]),
        owner_id=str(values["owner_id"]),
        scope=str(values["scope"]),
        kind=str(values["kind"]),
        key=str(values["key"]),
        value=str(values["value"]),
        source_turn_id=str(values["source_turn_id"]),
        evidence=str(values["evidence"]),
        revision=int(values["revision"]),
        deleted=bool(values["deleted"]),
        created_at=str(values["created_at"] or ""),
        updated_at=str(values["updated_at"] or ""),
        expires_at=values["expires_at"],
    )


class MemoryStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self._write_lock = asyncio.Lock()
        self._initialized = False
        self._init_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=1.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    def _initialize_sync(self) -> None:
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source_turn_id TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    search_text TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    expires_at TEXT,
                    UNIQUE(owner_id, scope, key)
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(memory_facts)").fetchall()
            }
            if "search_text" not in columns:
                conn.execute("ALTER TABLE memory_facts ADD COLUMN search_text TEXT NOT NULL DEFAULT ''")

            rows = conn.execute(
                "SELECT id,key,value,evidence,search_text FROM memory_facts"
            ).fetchall()
            for row in rows:
                if row["search_text"]:
                    continue
                search_text = _normalize_search_text(
                    f"{row['key']} {row['value']} {row['evidence']}"
                )
                conn.execute(
                    "UPDATE memory_facts SET search_text=? WHERE id=?",
                    (search_text, row["id"]),
                )

            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_facts_fts "
                    "USING fts5(fact_id UNINDEXED, search_text, tokenize='unicode61')"
                )
                conn.execute("DELETE FROM memory_facts_fts")
                conn.execute(
                    "INSERT INTO memory_facts_fts(rowid,fact_id,search_text) "
                    "SELECT id,id,search_text FROM memory_facts WHERE deleted=0"
                )
                conn.executescript(
                    """
                    CREATE TRIGGER IF NOT EXISTS memory_facts_ai AFTER INSERT ON memory_facts
                    WHEN new.deleted=0 BEGIN
                        INSERT INTO memory_facts_fts(rowid,fact_id,search_text)
                        VALUES(new.id,new.id,new.search_text);
                    END;
                    CREATE TRIGGER IF NOT EXISTS memory_facts_au AFTER UPDATE ON memory_facts BEGIN
                        DELETE FROM memory_facts_fts WHERE rowid=old.id;
                        INSERT INTO memory_facts_fts(rowid,fact_id,search_text)
                        SELECT new.id,new.id,new.search_text WHERE new.deleted=0;
                    END;
                    CREATE TRIGGER IF NOT EXISTS memory_facts_ad AFTER DELETE ON memory_facts BEGIN
                        DELETE FROM memory_facts_fts WHERE rowid=old.id;
                    END;
                    """
                )
            except sqlite3.OperationalError:
                # Some minimal SQLite builds omit FTS5. Retrieval has a bounded
                # SQL fallback so chat keeps working in that environment.
                pass
            conn.execute(
                "INSERT INTO memory_meta(key,value) VALUES('schema_version',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    async def upsert(
        self,
        *,
        owner_id: str,
        scope: str,
        kind: str,
        key: str,
        value: str,
        source_turn_id: str,
        evidence: str,
    ) -> int:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(
                self._upsert_sync,
                owner_id,
                scope,
                kind,
                key,
                value,
                source_turn_id,
                evidence,
            )

    def _upsert_sync(self, owner_id, scope, kind, key, value, source_turn_id, evidence) -> int:
        search_text = _normalize_search_text(f"{key} {value} {evidence}")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_facts(owner_id,scope,kind,key,value,source_turn_id,evidence,search_text,revision,deleted)
                VALUES(?,?,?,?,?,?,?,?,1,0)
                ON CONFLICT(owner_id,scope,key) DO UPDATE SET
                    kind=excluded.kind,
                    value=excluded.value,
                    source_turn_id=excluded.source_turn_id,
                    evidence=excluded.evidence,
                    search_text=excluded.search_text,
                    revision=memory_facts.revision+1,
                    deleted=0,
                    updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
                """,
                (owner_id, scope, kind, key, value, source_turn_id, evidence, search_text),
            )
            row = conn.execute(
                "SELECT revision FROM memory_facts WHERE owner_id=? AND scope=? AND key=?",
                (owner_id, scope, key),
            ).fetchone()
            return int(row["revision"])

    async def tombstone(self, *, owner_id: str, scope: str, keys: Iterable[str] | None = None) -> int:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(self._tombstone_sync, owner_id, scope, list(keys or []))

    def _tombstone_sync(self, owner_id: str, scope: str, keys: List[str]) -> int:
        with self._connect() as conn:
            if keys:
                placeholders = ",".join("?" for _ in keys)
                params = [owner_id, scope, *keys]
                cur = conn.execute(
                    f"UPDATE memory_facts SET deleted=1, revision=revision+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    f"WHERE owner_id=? AND scope=? AND deleted=0 AND key IN ({placeholders})",
                    params,
                )
            else:
                cur = conn.execute(
                    "UPDATE memory_facts SET deleted=1, revision=revision+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                    "WHERE owner_id=? AND scope=? AND deleted=0",
                    (owner_id, scope),
                )
            return int(cur.rowcount)

    async def list_active(self, *, owner_id: str, scope: str, limit: int = 50) -> List[MemoryFact]:
        await self.initialize()
        return await asyncio.to_thread(self._list_active_sync, owner_id, scope, limit)

    def _list_active_sync(self, owner_id: str, scope: str, limit: int) -> List[MemoryFact]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_facts WHERE owner_id=? AND scope=? AND deleted=0 "
                "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now')) "
                "ORDER BY updated_at DESC LIMIT ?",
                (owner_id, scope, max(1, int(limit))),
            ).fetchall()
        return [_row_to_fact(row) for row in rows]

    async def get_active_by_id(
        self,
        *,
        owner_id: str,
        scope: str,
        fact_id: int,
    ) -> MemoryFact | None:
        await self.initialize()
        return await asyncio.to_thread(
            self._get_active_by_id_sync,
            owner_id,
            scope,
            int(fact_id),
        )

    def _get_active_by_id_sync(self, owner_id: str, scope: str, fact_id: int) -> MemoryFact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_facts WHERE id=? AND owner_id=? AND scope=? AND deleted=0 "
                "AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (fact_id, owner_id, scope),
            ).fetchone()
        return _row_to_fact(row) if row is not None else None

    async def get_active_by_key(
        self,
        *,
        owner_id: str,
        scope: str,
        key: str,
    ) -> MemoryFact | None:
        await self.initialize()
        return await asyncio.to_thread(self._get_active_by_key_sync, owner_id, scope, key)

    def _get_active_by_key_sync(self, owner_id: str, scope: str, key: str) -> MemoryFact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM memory_facts WHERE owner_id=? AND scope=? AND key=? AND deleted=0",
                (owner_id, scope, key),
            ).fetchone()
        return _row_to_fact(row) if row is not None else None

    async def update_by_id(
        self,
        *,
        owner_id: str,
        scope: str,
        fact_id: int,
        expected_revision: int,
        value: str,
        source_turn_id: str,
        evidence: str,
    ) -> MemoryFact | None:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(
                self._update_by_id_sync,
                owner_id,
                scope,
                int(fact_id),
                int(expected_revision),
                value,
                source_turn_id,
                evidence,
            )

    def _update_by_id_sync(
        self,
        owner_id: str,
        scope: str,
        fact_id: int,
        expected_revision: int,
        value: str,
        source_turn_id: str,
        evidence: str,
    ) -> MemoryFact | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT key FROM memory_facts WHERE id=? AND owner_id=? AND scope=? AND deleted=0 AND revision=?",
                (fact_id, owner_id, scope, expected_revision),
            ).fetchone()
            if row is None:
                return None
            search_text = _normalize_search_text(f"{row['key']} {value} {evidence}")
            cur = conn.execute(
                "UPDATE memory_facts SET value=?, source_turn_id=?, evidence=?, search_text=?, "
                "revision=revision+1, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND owner_id=? AND scope=? AND deleted=0 AND revision=?",
                (
                    value,
                    source_turn_id,
                    evidence,
                    search_text,
                    fact_id,
                    owner_id,
                    scope,
                    expected_revision,
                ),
            )
            if cur.rowcount != 1:
                return None
            updated = conn.execute("SELECT * FROM memory_facts WHERE id=?", (fact_id,)).fetchone()
        return _row_to_fact(updated) if updated is not None else None

    async def tombstone_by_id(
        self,
        *,
        owner_id: str,
        scope: str,
        fact_id: int,
        expected_revision: int,
    ) -> bool:
        await self.initialize()
        async with self._write_lock:
            return await asyncio.to_thread(
                self._tombstone_by_id_sync,
                owner_id,
                scope,
                int(fact_id),
                int(expected_revision),
            )

    def _tombstone_by_id_sync(
        self,
        owner_id: str,
        scope: str,
        fact_id: int,
        expected_revision: int,
    ) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE memory_facts SET deleted=1, revision=revision+1, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE id=? AND owner_id=? AND scope=? AND deleted=0 AND revision=?",
                (fact_id, owner_id, scope, expected_revision),
            )
            return cur.rowcount == 1

    async def search_active(
        self,
        *,
        owner_id: str,
        scope: str,
        query: str,
        limit: int = 6,
    ) -> List[MemoryFact]:
        await self.initialize()
        return await asyncio.to_thread(
            self._search_active_sync,
            owner_id,
            scope,
            query,
            limit,
        )

    def _search_active_sync(
        self,
        owner_id: str,
        scope: str,
        query: str,
        limit: int,
    ) -> List[MemoryFact]:
        normalized = _normalize_search_text(query)
        tokens = [token for token in normalized.split() if len(token) > 1]
        if not tokens:
            return self._list_active_sync(owner_id, scope, limit)

        match_query = " OR ".join(f'"{token}"' for token in tokens[:16])
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT mf.*
                    FROM memory_facts_fts
                    JOIN memory_facts AS mf ON mf.id=memory_facts_fts.fact_id
                    WHERE memory_facts_fts MATCH ?
                      AND mf.owner_id=? AND mf.scope=? AND mf.deleted=0
                      AND (mf.expires_at IS NULL OR mf.expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                    ORDER BY bm25(memory_facts_fts), mf.updated_at DESC
                    LIMIT ?
                    """,
                    (match_query, owner_id, scope, max(1, int(limit))),
                ).fetchall()
            except sqlite3.OperationalError:
                likes = [f"%{token}%" for token in tokens[:8]]
                clauses = " OR ".join("search_text LIKE ?" for _ in likes)
                rows = conn.execute(
                    f"""
                    SELECT * FROM memory_facts
                    WHERE owner_id=? AND scope=? AND deleted=0
                      AND (expires_at IS NULL OR expires_at > strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                      AND ({clauses})
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (owner_id, scope, *likes, max(1, int(limit))),
                ).fetchall()
        return [_row_to_fact(row) for row in rows]

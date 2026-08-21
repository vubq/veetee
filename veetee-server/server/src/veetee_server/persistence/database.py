"""Small synchronous psycopg boundary, kept outside domain/application code."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

import psycopg


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    dsn: str = "dbname=veetee"


class PostgresDatabase:
    """Opens short-lived transactions so the audio/session hot path is unaffected."""

    def __init__(self, config: DatabaseConfig) -> None:
        self.config = config

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
        with psycopg.connect(self.config.dsn) as connection:
            yield connection

    def check(self) -> bool:
        with self.connection() as connection:
            connection.execute("SELECT 1")
        return True

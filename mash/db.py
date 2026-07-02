from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings


@contextmanager
def db() -> Iterator[sqlite3.Connection]:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def migrate() -> None:
    settings.scripts_dir.mkdir(parents=True, exist_ok=True)
    settings.runs_dir.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 0,
                schedule TEXT NOT NULL DEFAULT '',
                account TEXT NOT NULL DEFAULT '',
                path TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_scheduled_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                script_id TEXT NOT NULL,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at TEXT,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                matched_count INTEGER NOT NULL DEFAULT 0,
                action_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                error_message TEXT NOT NULL DEFAULT '',
                log_path TEXT NOT NULL,
                FOREIGN KEY (script_id) REFERENCES scripts(id) ON DELETE CASCADE
            )
            """
        )


def get_config(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row else default


def set_config(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO config (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )

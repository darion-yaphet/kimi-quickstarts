"""SQLite persistence for local sessions and their append-only events."""

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_connection = None
        if self.path == ":memory:":
            self._memory_connection = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._memory_connection.row_factory = sqlite3.Row
        self._initialize()

    def _connect(self):
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _close(self, connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    profile TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    role TEXT,
                    content TEXT,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS events_session_id_id
                    ON events(session_id, id);
                """
            )
            connection.commit()
        finally:
            self._close(connection)

    @staticmethod
    def _session(row) -> dict | None:
        return dict(row) if row else None

    @staticmethod
    def _event(row) -> dict:
        event = dict(row)
        event["metadata"] = json.loads(event["metadata"])
        return event

    def create_session(self, profile: str, title: str = "New session") -> dict:
        session_id = f"ks_{secrets.token_urlsafe(18)}"
        timestamp = _now()
        connection = self._connect()
        try:
            connection.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, 'idle', ?, ?)",
                (session_id, profile, title[:120] or "New session", timestamp, timestamp),
            )
            connection.commit()
        finally:
            self._close(connection)
        return self.get_session(session_id)

    def get_session(self, session_id: str) -> dict | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            return self._session(row)
        finally:
            self._close(connection)

    def list_sessions(self, profile: str | None = None) -> list[dict]:
        connection = self._connect()
        try:
            if profile:
                rows = connection.execute(
                    "SELECT * FROM sessions WHERE profile = ? ORDER BY updated_at DESC",
                    (profile,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC"
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close(connection)

    def set_status(self, session_id: str, status: str) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
                (status, _now(), session_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(session_id)
            connection.commit()
        finally:
            self._close(connection)

    def add_event(
        self,
        session_id: str,
        event_type: str,
        role: str | None = None,
        content: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        timestamp = _now()
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                INSERT INTO events(session_id, type, role, content, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    event_type,
                    role,
                    content,
                    json.dumps(metadata or {}, separators=(",", ":")),
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (timestamp, session_id),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
            return self._event(row)
        finally:
            self._close(connection)

    def events(self, session_id: str) -> list[dict]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM events WHERE session_id = ? ORDER BY id", (session_id,)
            ).fetchall()
            return [self._event(row) for row in rows]
        finally:
            self._close(connection)

    def messages(self, session_id: str) -> list[dict]:
        return [
            {"role": event["role"], "content": event["content"]}
            for event in self.events(session_id)
            if event["type"] == "message" and event["role"] in {"user", "assistant"}
        ]

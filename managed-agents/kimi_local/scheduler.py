"""Persistent, process-safe scheduling for the local managed-agent runtime.

Jobs are claimed with a bounded database lease before their runner is invoked.  A
heartbeat renews the lease while a live runner is working, so a second scheduler
process cannot claim the same job.  If a worker exits without finishing, the job
becomes eligible again after the last lease and its abandoned run is recorded as
failed.  Recovery is at-least-once, so runners should still be idempotent.
"""

from __future__ import annotations

import json
import math
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable


Runner = Callable[[dict], dict]


class Scheduler:
    """Store and execute one-shot or fixed-interval jobs using SQLite."""

    def __init__(
        self,
        database_path: str | Path,
        runner: Runner,
        *,
        clock: Callable[[], datetime] | None = None,
        lease_seconds: float = 300,
        retry_base_seconds: float = 30,
        retry_max_seconds: float = 3600,
    ):
        if not callable(runner):
            raise TypeError("runner must be callable")
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not all(
            math.isfinite(value) and value > 0
            for value in (retry_base_seconds, retry_max_seconds)
        ):
            raise ValueError("retry delays must be positive")
        if retry_base_seconds > retry_max_seconds:
            raise ValueError("retry_base_seconds cannot exceed retry_max_seconds")

        self.path = str(database_path)
        self.runner = runner
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.lease_seconds = float(lease_seconds)
        self.retry_base_seconds = float(retry_base_seconds)
        self.retry_max_seconds = float(retry_max_seconds)
        self._connection_lock = threading.RLock()
        self._memory_connection: sqlite3.Connection | None = None
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        else:
            self._memory_connection = sqlite3.connect(
                ":memory:", check_same_thread=False, isolation_level=None
            )
            self._memory_connection.row_factory = sqlite3.Row
            self._configure(self._memory_connection)
        self._initialize()

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    def _connect(self) -> sqlite3.Connection:
        if self._memory_connection is not None:
            return self._memory_connection
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        self._configure(connection)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is not self._memory_connection:
            connection.close()

    def _initialize(self) -> None:
        with self._connection_lock:
            connection = self._connect()
            try:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS scheduled_jobs (
                        id TEXT PRIMARY KEY,
                        profile TEXT NOT NULL,
                        prompt TEXT NOT NULL,
                        title TEXT NOT NULL,
                        knowledge_collection TEXT,
                        interval_seconds REAL,
                        run_at TEXT,
                        next_run_at TEXT,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        last_run_at TEXT,
                        claim_token TEXT,
                        lease_until TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS scheduled_jobs_due
                        ON scheduled_jobs(enabled, next_run_at, lease_until);
                    CREATE TABLE IF NOT EXISTS scheduled_runs (
                        id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL REFERENCES scheduled_jobs(id) ON DELETE CASCADE,
                        claim_token TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL,
                        scheduled_for TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        result TEXT,
                        error TEXT
                    );
                    CREATE INDEX IF NOT EXISTS scheduled_runs_job_started
                        ON scheduled_runs(job_id, started_at DESC);
                    """
                )
            finally:
                self._close(connection)

    @staticmethod
    def _datetime(value: datetime | str, field: str) -> datetime:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.endswith("Z"):
                candidate = candidate[:-1] + "+00:00"
            try:
                value = datetime.fromisoformat(candidate)
            except ValueError as error:
                raise ValueError(
                    f"{field} must be a valid UTC ISO timestamp"
                ) from error
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field} must be a timezone-aware UTC ISO timestamp")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    def _now(self, value: datetime | str | None = None) -> datetime:
        return self._datetime(self.clock() if value is None else value, "now")

    @staticmethod
    def _job(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        job = dict(row)
        job["enabled"] = bool(job["enabled"])
        job.pop("claim_token", None)
        job.pop("lease_until", None)
        return job

    @staticmethod
    def _run(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        run = dict(row)
        run.pop("claim_token", None)
        run["result"] = json.loads(run["result"]) if run["result"] else None
        run["error"] = json.loads(run["error"]) if run["error"] else None
        return run

    def add_job(
        self,
        profile: str,
        prompt: str,
        interval_seconds: float | None = None,
        run_at: datetime | str | None = None,
        title: str = "",
        knowledge_collection: str | None = None,
    ) -> dict:
        if not isinstance(profile, str) or not profile.strip():
            raise ValueError("profile must be a non-empty string")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be a non-empty string")
        if len(prompt.strip()) > 100_000:
            raise ValueError("prompt exceeds the 100000-character session limit")
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        if knowledge_collection is not None and (
            not isinstance(knowledge_collection, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", knowledge_collection)
        ):
            raise ValueError(
                "knowledge_collection must use 1–64 letters, digits, underscores or hyphens"
            )

        if interval_seconds is None:
            if run_at is None:
                raise ValueError("one-shot jobs require run_at")
            interval = None
        else:
            if isinstance(interval_seconds, bool):
                raise ValueError("interval_seconds must be positive")
            try:
                interval = float(interval_seconds)
            except (TypeError, ValueError, OverflowError) as error:
                raise ValueError("interval_seconds must be positive") from error
            if not math.isfinite(interval) or not 1 <= interval <= 31_536_000:
                raise ValueError(
                    "interval_seconds must be positive, finite and between 1 and 31536000 seconds"
                )

        timestamp = self._now()
        first_run = timestamp if run_at is None else self._datetime(run_at, "run_at")
        timestamp_iso = self._iso(timestamp)
        first_run_iso = self._iso(first_run)
        job_id = f"kj_{secrets.token_urlsafe(18)}"
        with self._connection_lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO scheduled_jobs(
                        id, profile, prompt, title, knowledge_collection,
                        interval_seconds, run_at, next_run_at, enabled,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        job_id,
                        profile.strip(),
                        prompt.strip(),
                        title.strip()[:120],
                        knowledge_collection.strip() if knowledge_collection else None,
                        interval,
                        first_run_iso,
                        first_run_iso,
                        timestamp_iso,
                        timestamp_iso,
                    ),
                )
            finally:
                self._close(connection)
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict | None:
        with self._connection_lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                return self._job(row)
            finally:
                self._close(connection)

    def list_jobs(self) -> list[dict]:
        with self._connection_lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT * FROM scheduled_jobs ORDER BY created_at, id"
                ).fetchall()
                return [self._job(row) for row in rows]
            finally:
                self._close(connection)

    def set_enabled(self, job_id: str, enabled: bool) -> dict:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        timestamp = self._iso(self._now())
        with self._connection_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT next_run_at FROM scheduled_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if existing is None:
                    raise KeyError(job_id)
                if enabled and existing["next_run_at"] is None:
                    raise ValueError(
                        "Completed one-shot jobs cannot be re-enabled; create a new schedule"
                    )
                cursor = connection.execute(
                    """
                    UPDATE scheduled_jobs
                    SET enabled = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (int(enabled), timestamp, job_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(job_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                self._close(connection)
        return self.get_job(job_id)

    def list_runs(self, job_id: str | None = None) -> list[dict]:
        with self._connection_lock:
            connection = self._connect()
            try:
                if job_id is None:
                    rows = connection.execute(
                        "SELECT * FROM scheduled_runs ORDER BY started_at DESC, id DESC"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT * FROM scheduled_runs
                        WHERE job_id = ? ORDER BY started_at DESC, id DESC
                        """,
                        (job_id,),
                    ).fetchall()
                return [self._run(row) for row in rows]
            finally:
                self._close(connection)

    def link_run_session(self, run_id: str, session_id: str) -> None:
        """Persist the attempt's session before a model request can fail or crash."""
        with self._connection_lock:
            connection = self._connect()
            try:
                connection.execute(
                    "UPDATE scheduled_runs SET result = ? WHERE id = ? AND status = 'running'",
                    (json.dumps({"session_id": session_id}), run_id),
                )
            finally:
                self._close(connection)

    def _claim_due(self, now: datetime) -> tuple[dict, str, str] | None:
        now_iso = self._iso(now)
        lease_until = self._iso(now + timedelta(seconds=self.lease_seconds))
        token = secrets.token_urlsafe(24)
        run_id = f"kr_{secrets.token_urlsafe(18)}"

        with self._connection_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                # Disabled jobs must not execute, but abandoned attempts still need a terminal audit.
                connection.execute(
                    """UPDATE scheduled_runs SET status = 'failed', finished_at = ?, error = ?
                       WHERE status = 'running' AND claim_token IN
                         (SELECT claim_token FROM scheduled_jobs
                          WHERE enabled = 0 AND lease_until <= ?)""",
                    (
                        now_iso,
                        json.dumps(
                            {
                                "type": "LeaseExpired",
                                "message": "disabled job's execution lease expired",
                            }
                        ),
                        now_iso,
                    ),
                )
                connection.execute(
                    "UPDATE scheduled_jobs SET claim_token = NULL, lease_until = NULL WHERE enabled = 0 AND lease_until <= ?",
                    (now_iso,),
                )
                row = connection.execute(
                    """
                    SELECT * FROM scheduled_jobs
                    WHERE enabled = 1
                      AND next_run_at IS NOT NULL
                      AND next_run_at <= ?
                      AND (lease_until IS NULL OR lease_until <= ?)
                    ORDER BY next_run_at, id
                    LIMIT 1
                    """,
                    (now_iso, now_iso),
                ).fetchone()
                if row is None:
                    connection.execute("COMMIT")
                    return None

                old_token = row["claim_token"]
                if old_token:
                    crash_error = json.dumps(
                        {
                            "type": "LeaseExpired",
                            "message": "execution lease expired before completion",
                        },
                        separators=(",", ":"),
                    )
                    connection.execute(
                        """
                        UPDATE scheduled_runs
                        SET status = 'failed', finished_at = ?, error = ?
                        WHERE claim_token = ? AND status = 'running'
                        """,
                        (now_iso, crash_error, old_token),
                    )

                scheduled_for = row["next_run_at"]
                cursor = connection.execute(
                    """
                    UPDATE scheduled_jobs
                    SET claim_token = ?, lease_until = ?, updated_at = ?
                    WHERE id = ?
                      AND (lease_until IS NULL OR lease_until <= ?)
                    """,
                    (token, lease_until, now_iso, row["id"], now_iso),
                )
                if cursor.rowcount != 1:
                    connection.execute("ROLLBACK")
                    return None
                connection.execute(
                    """
                    INSERT INTO scheduled_runs(
                        id, job_id, claim_token, status, scheduled_for, started_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (run_id, row["id"], token, scheduled_for, now_iso),
                )
                connection.execute("COMMIT")
                claimed = connection.execute(
                    "SELECT * FROM scheduled_jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                return self._job(claimed), token, run_id
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._close(connection)

    def _finish(
        self,
        job: dict,
        token: str,
        run_id: str,
        result: dict | None,
        error: dict | None,
        not_before: datetime,
    ) -> dict:
        finished = max(self._now(), not_before)
        finished_iso = self._iso(finished)
        succeeded = error is None
        status = "succeeded" if succeeded else "failed"

        with self._connection_lock:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                owned_job = connection.execute(
                    """
                    SELECT enabled FROM scheduled_jobs
                    WHERE id = ? AND claim_token = ?
                    """,
                    (job["id"], token),
                ).fetchone()
                if owned_job:
                    if succeeded:
                        failure_count = 0
                        if job["interval_seconds"] is None:
                            enabled = 0
                            next_run_at = None
                        else:
                            enabled = owned_job["enabled"]
                            anchor = self._datetime(job["next_run_at"], "next_run_at")
                            interval = job["interval_seconds"]
                            steps = max(
                                1,
                                math.floor(
                                    (finished - anchor).total_seconds() / interval
                                )
                                + 1,
                            )
                            next_run = anchor + timedelta(seconds=interval * steps)
                            next_run_at = self._iso(next_run)
                    else:
                        failure_count = job["failure_count"] + 1
                        exponent = min(job["failure_count"], 30)
                        delay = min(
                            self.retry_base_seconds * (2**exponent),
                            self.retry_max_seconds,
                        )
                        enabled = owned_job["enabled"]
                        next_run_at = self._iso(finished + timedelta(seconds=delay))
                    connection.execute(
                        """
                        UPDATE scheduled_jobs
                        SET enabled = ?, next_run_at = ?, failure_count = ?,
                            last_run_at = ?, claim_token = NULL, lease_until = NULL,
                            updated_at = ?
                        WHERE id = ? AND claim_token = ?
                        """,
                        (
                            enabled,
                            next_run_at,
                            failure_count,
                            finished_iso,
                            finished_iso,
                            job["id"],
                            token,
                        ),
                    )
                    connection.execute(
                        """
                        UPDATE scheduled_runs
                        SET status = ?, finished_at = ?, result = ?, error = ?
                        WHERE id = ? AND claim_token = ? AND status = 'running'
                        """,
                        (
                            status,
                            finished_iso,
                            json.dumps(result, separators=(",", ":"))
                            if result is not None
                            else None,
                            json.dumps(error, separators=(",", ":"))
                            if error is not None
                            else None,
                            run_id,
                            token,
                        ),
                    )
                connection.execute("COMMIT")
                row = connection.execute(
                    "SELECT * FROM scheduled_runs WHERE id = ?", (run_id,)
                ).fetchone()
                return self._run(row)
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                self._close(connection)

    def _renew_lease(self, job_id: str, token: str, extension_seconds: float) -> bool:
        """Extend an owned claim; return false after ownership has changed."""

        now = self._now()
        with self._connection_lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT lease_until FROM scheduled_jobs
                    WHERE id = ? AND claim_token = ?
                    """,
                    (job_id, token),
                ).fetchone()
                if row is None:
                    return False
                current_lease = self._datetime(row["lease_until"], "lease_until")
                lease_until = self._iso(
                    max(
                        now + timedelta(seconds=self.lease_seconds),
                        current_lease + timedelta(seconds=extension_seconds),
                    )
                )
                cursor = connection.execute(
                    """
                    UPDATE scheduled_jobs
                    SET lease_until = ?, updated_at = ?
                    WHERE id = ? AND claim_token = ?
                    """,
                    (lease_until, self._iso(now), job_id, token),
                )
                return cursor.rowcount == 1
            finally:
                self._close(connection)

    def _heartbeat(
        self, job_id: str, token: str
    ) -> tuple[threading.Event, threading.Thread]:
        stopped = threading.Event()

        def renew() -> None:
            cadence = max(0.05, self.lease_seconds / 3)
            while not stopped.wait(cadence):
                try:
                    if not self._renew_lease(job_id, token, cadence):
                        return
                except sqlite3.Error:
                    # A transient SQLite lock is safe: the current lease remains
                    # authoritative and the next cadence retries renewal.
                    continue

        thread = threading.Thread(
            target=renew,
            name=f"scheduler-lease-{job_id}",
            daemon=True,
        )
        thread.start()
        return stopped, thread

    def run_due(self, now: datetime | str | None = None) -> list[dict]:
        """Claim and run every job due at the supplied instant.

        Runner exceptions are recorded and returned as failed runs so one broken
        job does not prevent other due jobs from executing.
        """

        claim_time = self._now(now)
        completed = []
        while True:
            claim = self._claim_due(claim_time)
            if claim is None:
                return completed
            job, token, run_id = claim
            stopped, heartbeat = self._heartbeat(job["id"], token)
            try:
                runner_job = {**job, "_run_id": run_id}
                result = self.runner(runner_job)
                if not isinstance(result, dict):
                    raise TypeError("runner must return a dictionary")
                json.dumps(result)
                outcome = (result, None)
            except Exception as exception:
                details = {"type": type(exception).__name__, "message": str(exception)}
                if getattr(exception, "session_id", None):
                    details["session_id"] = exception.session_id
                outcome = (
                    None,
                    details,
                )
            finally:
                stopped.set()
                heartbeat.join(timeout=max(1, self.lease_seconds))
            run = self._finish(job, token, run_id, *outcome, claim_time)
            completed.append(run)

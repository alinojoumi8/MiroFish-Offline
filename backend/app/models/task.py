"""Durable, lease-owned status tracking for background tasks."""

import json
import sqlite3
import threading
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import Config
from ..utils.client_errors import TASK_FAILURE_MESSAGE


INTERRUPTED_MESSAGE = "Task interrupted by server restart"
SCHEMA_VERSION = 3


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


@dataclass
class Task:
    task_id: str
    task_type: str
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: int = 0
    message: str = ""
    result: Optional[Dict] = None
    error: Optional[str] = None
    error_request_id: Optional[str] = None
    metadata: Dict = field(default_factory=dict)
    progress_detail: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return the original client-facing task response shape."""
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "progress": self.progress,
            "message": self.message,
            "progress_detail": self.progress_detail,
            "result": self.result,
            "error": TASK_FAILURE_MESSAGE if self.status == TaskStatus.FAILED else self.error,
            "metadata": self.metadata,
        }


class TaskManager:
    """Thread/process-safe SQLite task registry with worker leases."""

    schema_version = SCHEMA_VERSION

    def __init__(
        self,
        db_path=None,
        *,
        upload_folder=None,
        lease_seconds=None,
        heartbeat_interval=None,
    ):
        root = Path(upload_folder or Config.UPLOAD_FOLDER)
        self.db_path = str(Path(db_path) if db_path is not None else root / "tasks.sqlite3")
        self.worker_id = str(uuid.uuid4())
        self.lease_seconds = float(
            lease_seconds
            if lease_seconds is not None
            else getattr(Config, "TASK_WORKER_LEASE_SECONDS", 30.0)
        )
        self.heartbeat_interval = float(
            heartbeat_interval
            if heartbeat_interval is not None
            else max(0.1, self.lease_seconds / 3.0)
        )
        self._task_lock = threading.RLock()
        self._stop_heartbeat = threading.Event()
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize_database()
        self._register_worker()
        self._recover_stale_tasks()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            args=(
                self.db_path,
                self.worker_id,
                self.heartbeat_interval,
                self.lease_seconds,
                self._stop_heartbeat,
            ),
            name=f"task-heartbeat-{self.worker_id[:8]}",
            daemon=True,
        )
        self._heartbeat_thread.start()
        self._finalizer = weakref.finalize(
            self,
            self._cleanup_worker,
            self.db_path,
            self.worker_id,
            self._stop_heartbeat,
        )

    def _connect(self, *, autocommit=False):
        connection = sqlite3.connect(
            self.db_path,
            timeout=5.0,
            isolation_level=None if autocommit else "DEFERRED",
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    @staticmethod
    def _column_names(connection, table):
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}

    def _initialize_database(self):
        connection = self._connect(autocommit=True)
        try:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Task database uses newer schema version {version}; supported version is {SCHEMA_VERSION}"
                )
            connection.execute("BEGIN IMMEDIATE")
            while version < SCHEMA_VERSION:
                if version == 0:
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS tasks (
                            task_id TEXT PRIMARY KEY,
                            task_type TEXT NOT NULL,
                            status TEXT NOT NULL,
                            created_at TEXT NOT NULL,
                            updated_at TEXT NOT NULL,
                            progress INTEGER NOT NULL DEFAULT 0,
                            message TEXT NOT NULL DEFAULT '',
                            result_json TEXT,
                            error TEXT,
                            error_request_id TEXT,
                            metadata_json TEXT NOT NULL DEFAULT '{}',
                            progress_detail_json TEXT NOT NULL DEFAULT '{}'
                        )
                        """
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC)"
                    )
                elif version == 1:
                    columns = self._column_names(connection, "tasks")
                    if "owner_worker_id" not in columns:
                        connection.execute("ALTER TABLE tasks ADD COLUMN owner_worker_id TEXT")
                    if "finished_at" not in columns:
                        connection.execute("ALTER TABLE tasks ADD COLUMN finished_at TEXT")
                    connection.execute(
                        """
                        CREATE TABLE IF NOT EXISTS workers (
                            worker_id TEXT PRIMARY KEY,
                            started_at TEXT NOT NULL,
                            heartbeat_at TEXT NOT NULL
                        )
                        """
                    )
                    connection.execute(
                        "CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(heartbeat_at)"
                    )
                elif version == 2:
                    columns = self._column_names(connection, "tasks")
                    if "retry_source_id" not in columns:
                        connection.execute("ALTER TABLE tasks ADD COLUMN retry_source_id TEXT")
                    connection.execute(
                        """
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_retry_source
                        ON tasks(retry_source_id) WHERE retry_source_id IS NOT NULL
                        """
                    )
                version += 1
                connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _register_worker(self):
        now = datetime.now().isoformat()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO workers(worker_id, started_at, heartbeat_at) VALUES (?, ?, ?)",
                (self.worker_id, now, now),
            )

    @staticmethod
    def _heartbeat_loop(db_path, worker_id, interval, lease_seconds, stop_event):
        while not stop_event.wait(interval):
            try:
                with sqlite3.connect(db_path, timeout=5.0) as connection:
                    connection.execute("PRAGMA busy_timeout = 5000")
                    connection.execute(
                        "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
                        (datetime.now().isoformat(), worker_id),
                    )
                    TaskManager._recover_stale_with_connection(
                        connection, lease_seconds
                    )
            except sqlite3.Error:
                continue

    @staticmethod
    def _cleanup_worker(db_path, worker_id, stop_event):
        stop_event.set()
        try:
            with sqlite3.connect(db_path, timeout=1.0) as connection:
                connection.execute("DELETE FROM workers WHERE worker_id = ?", (worker_id,))
        except sqlite3.Error:
            pass

    def close(self):
        """Stop lease maintenance without waiting on a sleeping thread."""
        if self._finalizer.alive:
            self._finalizer()
        if self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)

    def _heartbeat(self, connection=None):
        now = datetime.now().isoformat()
        if connection is not None:
            connection.execute(
                "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
                (now, self.worker_id),
            )
            return
        with self._connect() as owned:
            owned.execute(
                "UPDATE workers SET heartbeat_at = ? WHERE worker_id = ?",
                (now, self.worker_id),
            )

    def _recover_stale_tasks(self):
        with self._connect() as connection:
            self._recover_stale_with_connection(connection, self.lease_seconds)

    @staticmethod
    def _recover_stale_with_connection(connection, lease_seconds):
        now = datetime.now().isoformat()
        cutoff = (datetime.now() - timedelta(seconds=lease_seconds)).isoformat()
        connection.execute(
            """
            UPDATE tasks
            SET status = ?, updated_at = ?, finished_at = ?, message = ?, error = ?
            WHERE status IN (?, ?)
              AND (
                  owner_worker_id IS NULL
                  OR NOT EXISTS (
                      SELECT 1 FROM workers
                      WHERE workers.worker_id = tasks.owner_worker_id
                        AND workers.heartbeat_at >= ?
                  )
              )
            """,
            (
                TaskStatus.INTERRUPTED.value,
                now,
                now,
                INTERRUPTED_MESSAGE,
                INTERRUPTED_MESSAGE,
                TaskStatus.PENDING.value,
                TaskStatus.PROCESSING.value,
                cutoff,
            ),
        )

    @staticmethod
    def _decode_json(value, default):
        if value is None:
            return default
        try:
            return json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return default

    @classmethod
    def _row_to_task(cls, row):
        return Task(
            task_id=row["task_id"],
            task_type=row["task_type"],
            status=TaskStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            progress=row["progress"],
            message=row["message"],
            result=cls._decode_json(row["result_json"], None),
            error=row["error"],
            error_request_id=row["error_request_id"],
            metadata=cls._decode_json(row["metadata_json"], {}),
            progress_detail=cls._decode_json(row["progress_detail_json"], {}),
        )

    def create_task(self, task_type: str, metadata: Optional[Dict] = None) -> str:
        self._heartbeat()
        task_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        with self._task_lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO tasks (
                    task_id, task_type, status, created_at, updated_at, progress,
                    message, result_json, error, error_request_id, metadata_json,
                    progress_detail_json, owner_worker_id, finished_at, retry_source_id
                ) VALUES (?, ?, ?, ?, ?, 0, '', NULL, NULL, NULL, ?, '{}', ?, NULL, NULL)
                """,
                (
                    task_id,
                    task_type,
                    TaskStatus.PENDING.value,
                    now,
                    now,
                    json.dumps(dict(metadata or {}), ensure_ascii=False),
                    self.worker_id,
                ),
            )
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        self._heartbeat()
        with self._task_lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        return self._row_to_task(row) if row is not None else None

    def update_task(
        self,
        task_id: str,
        status: Optional[TaskStatus] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        result: Optional[Dict] = None,
        error: Optional[str] = None,
        progress_detail: Optional[Dict] = None,
        error_request_id: Optional[str] = None,
    ):
        now = datetime.now().isoformat()
        updates = {"updated_at": now}
        normalized_status = TaskStatus(status) if status is not None else None
        if normalized_status is not None:
            updates["status"] = normalized_status.value
            if normalized_status in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.INTERRUPTED,
            }:
                updates["finished_at"] = now
            else:
                updates["finished_at"] = None
                updates["owner_worker_id"] = self.worker_id
        if progress is not None:
            updates["progress"] = int(progress)
        if message is not None:
            updates["message"] = message
        if result is not None:
            updates["result_json"] = json.dumps(result, ensure_ascii=False)
        if error is not None:
            updates["error"] = TASK_FAILURE_MESSAGE
        if progress_detail is not None:
            updates["progress_detail_json"] = json.dumps(progress_detail, ensure_ascii=False)
        if error_request_id is not None:
            updates["error_request_id"] = error_request_id
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with self._task_lock, self._connect() as connection:
            self._heartbeat(connection)
            connection.execute(
                f"UPDATE tasks SET {assignments} WHERE task_id = ?",
                (*updates.values(), task_id),
            )

    def complete_task(self, task_id: str, result: Dict):
        self.update_task(
            task_id,
            status=TaskStatus.COMPLETED,
            progress=100,
            message="Task completed",
            result=result,
        )

    def fail_task(self, task_id: str, error: str = None, request_id: str = None):
        self.update_task(
            task_id,
            status=TaskStatus.FAILED,
            message=TASK_FAILURE_MESSAGE,
            error=TASK_FAILURE_MESSAGE,
            error_request_id=request_id,
        )

    def retry_task(self, task_id: str) -> str:
        with self._task_lock:
            connection = self._connect(autocommit=True)
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._heartbeat(connection)
                existing = connection.execute(
                    "SELECT task_id FROM tasks WHERE retry_source_id = ?", (task_id,)
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return existing["task_id"]
                source = connection.execute(
                    "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
                ).fetchone()
                if source is None:
                    raise KeyError(task_id)
                if TaskStatus(source["status"]) not in {
                    TaskStatus.FAILED,
                    TaskStatus.INTERRUPTED,
                }:
                    raise ValueError("Only failed or interrupted tasks can be retried")
                metadata = self._decode_json(source["metadata_json"], {})
                metadata["retry_of_task_id"] = task_id
                metadata["retry_attempt"] = int(metadata.get("retry_attempt", 0)) + 1
                retry_id = str(uuid.uuid4())
                now = datetime.now().isoformat()
                connection.execute(
                    """
                    INSERT INTO tasks (
                        task_id, task_type, status, created_at, updated_at, progress,
                        message, result_json, error, error_request_id, metadata_json,
                        progress_detail_json, owner_worker_id, finished_at, retry_source_id
                    ) VALUES (?, ?, ?, ?, ?, 0, '', NULL, NULL, NULL, ?, '{}', ?, NULL, ?)
                    """,
                    (
                        retry_id,
                        source["task_type"],
                        TaskStatus.PENDING.value,
                        now,
                        now,
                        json.dumps(metadata, ensure_ascii=False),
                        self.worker_id,
                        task_id,
                    ),
                )
                connection.commit()
                return retry_id
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()

    def list_tasks(self, task_type: Optional[str] = None) -> list:
        self._heartbeat()
        query = "SELECT * FROM tasks"
        params = ()
        if task_type:
            query += " WHERE task_type = ?"
            params = (task_type,)
        query += " ORDER BY created_at DESC"
        with self._task_lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_task(row).to_dict() for row in rows]

    def cleanup_old_tasks(self, max_age_hours: int = 24, limit: int = 1000):
        self._heartbeat()
        cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()
        limit = max(0, int(limit))
        if limit == 0:
            return 0
        terminal = (
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.INTERRUPTED.value,
        )
        with self._task_lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT task_id FROM tasks
                WHERE COALESCE(finished_at, updated_at) < ? AND status IN (?, ?, ?)
                ORDER BY COALESCE(finished_at, updated_at) ASC LIMIT ?
                """,
                (cutoff, *terminal, limit),
            ).fetchall()
            task_ids = [row["task_id"] for row in rows]
            if task_ids:
                placeholders = ",".join("?" for _ in task_ids)
                connection.execute(
                    f"DELETE FROM tasks WHERE task_id IN ({placeholders})", task_ids
                )
            return len(task_ids)


def get_task_manager():
    """Return the active Flask application's manager, or a local fallback."""
    try:
        from flask import current_app, has_app_context

        if has_app_context():
            return current_app.extensions["task_manager"]
    except (ImportError, KeyError):
        pass
    return TaskManager()

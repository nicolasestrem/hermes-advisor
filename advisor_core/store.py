"""Profile-local SQLite storage and OS-released per-session ownership locks."""

from __future__ import annotations

import hashlib
import os
import random
import sqlite3
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Callable, TypeVar

try:
    import fcntl
except ImportError:  # non-POSIX platform: import must still succeed
    fcntl = None

from .config import default_settings

T = TypeVar("T")


class StoreError(RuntimeError):
    """Database I/O, integrity, or transaction failure; never silently repaired."""


def _require_posix_lock() -> None:
    """Fail closed when the platform has no exclusive local file-lock primitive.

    Importing the plugin on such a platform is supported; taking ownership of a
    session is not. No lock is emulated and contention is never inferred.
    """
    if fcntl is None:
        raise StoreError(
            "session ownership requires POSIX flock, which is unavailable on this platform"
        )


def _lock_exclusive(fd: int) -> None:
    if fcntl is None:
        _require_posix_lock()
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if fcntl is None:
        _require_posix_lock()
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


_LIVE_LOCKS: "weakref.WeakSet[OwnedLock]" = weakref.WeakSet()


class OwnedLock:
    """An exclusive local flock held until explicit release or process death."""

    def __init__(self, fd: int, session_key: str, path: str | os.PathLike[str]):
        self._fd = fd
        self.session_key = session_key
        self._path = os.fspath(path)
        self._guard = threading.Lock()
        self._released = False
        self._suspended = False
        _LIVE_LOCKS.add(self)

    def _forget_in_child(self) -> None:
        # flock belongs to the open file description, which fork() shares with
        # the parent. A child must drop its copy of the descriptor WITHOUT
        # LOCK_UN (that would unlock the parent), otherwise a lingering child
        # would keep the lock alive after the real owner died.
        self._guard = threading.Lock()
        if not self._released:
            self._released = True
            try:
                os.close(self._fd)
            except OSError:
                pass

    @property
    def suspended(self) -> bool:
        with self._guard:
            return self._suspended

    def suspend(self) -> None:
        """Drop the fd+flock while keeping this lock's ownership slot alive.

        A disabled session runs nothing, so the process holds no descriptor
        for it; resume() re-establishes the flock before the session runs
        again. Idempotent; never touches a released lock.
        """
        with self._guard:
            if self._released or self._suspended:
                return
            try:
                _unlock(self._fd)
            finally:
                os.close(self._fd)
            self._fd = -1
            self._suspended = True

    def resume(self) -> None:
        """Re-establish the exclusive flock.

        Raises BlockingIOError when another worker holds the lock now, which
        the Service reports as an unavailable owner; no state was changed.
        """
        with self._guard:
            if self._released:
                raise StoreError("session ownership lock was released")
            if not self._suspended:
                return
            _require_posix_lock()
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(self._path, flags, 0o600)
            try:
                os.fchmod(fd, 0o600)
                _lock_exclusive(fd)
            except BaseException:
                os.close(fd)
                raise
            self._fd = fd
            self._suspended = False

    def release(self) -> None:
        with self._guard:
            if self._released:
                return
            self._released = True
            if self._suspended:
                return
            try:
                _unlock(self._fd)
            finally:
                os.close(self._fd)


def _forget_inherited_locks_in_child() -> None:
    for lock in list(_LIVE_LOCKS):
        lock._forget_in_child()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_forget_inherited_locks_in_child)


class SQLiteStore:
    """A single profile's canonical store; scope comes from ``data_dir`` only.

    The caller supplies the active profile's authenticated plugin-data directory
    when constructing the Service. Session IDs never select a path or profile.
    """

    SCHEMA_VERSION = 1

    def __init__(self, data_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir).expanduser()
        try:
            self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.data_dir, 0o700)
            self._locks_dir = self.data_dir / "locks"
            self._locks_dir.mkdir(mode=0o700, exist_ok=True)
            os.chmod(self._locks_dir, 0o700)
        except OSError as exc:
            raise StoreError("profile-local storage directory is unavailable") from exc
        self.path = self.data_dir / "advisor.sqlite3"
        self._init_guard = threading.Lock()
        # Serialize writes issued through this store instance before opening a
        # SQLite connection; independent Store instances/processes still rely on
        # SQLite's bounded busy handler.
        self._write_lock = threading.Lock()
        self._initialize()

    def acquire(self, session_id: str) -> OwnedLock | None:
        """Try an OS lock once; never infer owner death from PID or heartbeat."""
        _require_posix_lock()
        key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        path = self._locks_dir / f"{key}.lock"
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd: int | None = None
        try:
            fd = os.open(path, flags, 0o600)
            os.fchmod(fd, 0o600)
            _lock_exclusive(fd)
        except BlockingIOError:
            if fd is not None:
                os.close(fd)
            return None
        except OSError as exc:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise StoreError("session ownership lock is unavailable") from exc
        if fd is None:
            raise StoreError("session ownership lock was not opened")
        return OwnedLock(fd, key, path)

    def read(self, callback: Callable[[sqlite3.Connection], T]) -> T:
        connection = self._connect()
        try:
            connection.execute("BEGIN")
            result = callback(connection)
            connection.commit()
            return result
        except sqlite3.Error as exc:
            connection.rollback()
            raise StoreError("SQLite read failed") from exc
        finally:
            connection.close()

    def transaction(self, callback: Callable[[sqlite3.Connection], T]) -> T:
        deadline = time.monotonic() + 1.0
        remaining = max(0.0, deadline - time.monotonic())
        if not self._write_lock.acquire(timeout=remaining):
            timeout = TimeoutError("SQLite write deadline expired waiting for this store's writer")
            raise StoreError("SQLite transaction failed") from timeout

        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect(timeout=max(0.0, deadline - time.monotonic()), deadline=deadline)
            self._set_busy_timeout(connection, deadline)
            connection.execute("BEGIN IMMEDIATE")
            result = callback(connection)
            self._set_busy_timeout(connection, deadline)
            connection.commit()
            return result
        except sqlite3.Error as exc:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            raise StoreError("SQLite transaction failed") from exc
        except BaseException:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            raise
        finally:
            try:
                if connection is not None:
                    connection.close()
            finally:
                self._write_lock.release()

    @staticmethod
    def _set_busy_timeout(connection: sqlite3.Connection, deadline: float) -> None:
        """Set SQLite's busy wait to only the time remaining in the operation."""
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        connection.execute(f"PRAGMA busy_timeout={remaining_ms}")

    def _connect(
        self, timeout: float = 1.0, *, deadline: float | None = None
    ) -> sqlite3.Connection:
        connection: sqlite3.Connection | None = None
        try:
            if deadline is not None:
                timeout = min(timeout, max(0.0, deadline - time.monotonic()))
            connection = sqlite3.connect(self.path, timeout=max(0.0, timeout), isolation_level=None)
            connection.row_factory = sqlite3.Row
            if deadline is None:
                connection.execute("PRAGMA busy_timeout=1000")
            else:
                self._set_busy_timeout(connection, deadline)
            for statement in ("PRAGMA foreign_keys=ON", "PRAGMA synchronous=FULL"):
                if deadline is not None:
                    self._set_busy_timeout(connection, deadline)
                connection.execute(statement)
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise StoreError("SQLite store could not be opened") from exc

    def _initialize(self) -> None:
        # Concurrent first starts in several processes race on the journal-mode
        # switch, which SQLite reports as an immediate "database is locked"
        # that the busy timeout does not retry. Retry that one transient error
        # for a bounded time; every other failure is raised unchanged.
        deadline = time.monotonic() + 3.0
        while True:
            try:
                self._initialize_once()
                return
            except StoreError as exc:
                cause = exc.__cause__
                transient = isinstance(cause, sqlite3.OperationalError) and (
                    "locked" in str(cause) or "busy" in str(cause))
                if not transient or time.monotonic() >= deadline:
                    raise
                time.sleep(0.02 + random.random() * 0.05)

    def _initialize_once(self) -> None:
        with self._init_guard:
            connection = self._connect()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > self.SCHEMA_VERSION:
                    raise StoreError("database schema is newer than this core")
                if version == 0:
                    connection.execute("BEGIN IMMEDIATE")
                    self._create_schema(connection)
                    connection.execute(f"PRAGMA user_version={self.SCHEMA_VERSION}")
                    connection.commit()
                else:
                    self._create_schema(connection)
                    connection.commit()
                config = connection.execute("SELECT revision FROM profile_config WHERE singleton=1").fetchone()
                if config is None:
                    connection.execute("BEGIN IMMEDIATE")
                    # Another process may have won the first-start race.
                    connection.execute(
                        "INSERT OR IGNORE INTO profile_config(singleton, revision, settings, updated_at) VALUES(1,0,?,0)",
                        (_json(default_settings()),),
                    )
                    connection.commit()
                os.chmod(self.path, 0o600)
            except StoreError:
                connection.rollback()
                raise
            except OSError as exc:
                connection.rollback()
                raise StoreError("SQLite file permissions could not be protected") from exc
            except sqlite3.Error as exc:
                connection.rollback()
                raise StoreError("SQLite schema initialization failed") from exc
            finally:
                connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS profile_config (
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                revision INTEGER NOT NULL,
                settings TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                surface TEXT NOT NULL,
                owner_token TEXT NOT NULL,
                state TEXT NOT NULL,
                pause_reason TEXT,
                invocation_count INTEGER NOT NULL DEFAULT 0,
                applied_revision INTEGER NOT NULL,
                applied_generation INTEGER NOT NULL,
                applied_settings TEXT NOT NULL,
                discovered_at REAL NOT NULL,
                last_seen REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                packet_digest TEXT NOT NULL,
                applied_revision INTEGER NOT NULL,
                applied_generation INTEGER NOT NULL,
                owner_token TEXT NOT NULL,
                created_at REAL NOT NULL,
                deadline_at REAL NOT NULL,
                terminal_at REAL,
                status TEXT NOT NULL,
                cancellation_pending INTEGER NOT NULL DEFAULT 0,
                outcome TEXT,
                requested_provider TEXT,
                requested_model TEXT,
                served_provider TEXT,
                served_model TEXT,
                usage_json TEXT,
                usage_known INTEGER NOT NULL DEFAULT 0,
                coverage_json TEXT NOT NULL,
                error_code TEXT,
                error_text TEXT
            );
            CREATE INDEX IF NOT EXISTS attempts_session_terminal
                ON attempts(session_id, terminal_at DESC, attempt_id DESC);
            CREATE INDEX IF NOT EXISTS attempts_owner_status
                ON attempts(session_id, owner_token, status);
            CREATE TABLE IF NOT EXISTS findings (
                note_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                attempt_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                severity TEXT NOT NULL,
                explanation TEXT NOT NULL,
                evidence_refs_json TEXT NOT NULL,
                next_step TEXT NOT NULL,
                UNIQUE(attempt_id, ordinal)
            );
            CREATE INDEX IF NOT EXISTS findings_session ON findings(session_id, note_id);
            CREATE TABLE IF NOT EXISTS ratings (
                attempt_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                rating TEXT NOT NULL,
                reason TEXT,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS feedback_actions (
                action_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                note_ids_json TEXT NOT NULL,
                attempt_ids_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dedup (
                session_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                packet_digest TEXT NOT NULL,
                applied_generation INTEGER NOT NULL,
                attempt_id TEXT NOT NULL,
                PRIMARY KEY(session_id, turn_id, packet_digest, applied_generation)
            );
            """
        )


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)

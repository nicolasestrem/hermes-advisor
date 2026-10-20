"""Threaded, profile-scoped Advisor service with durable admission controls.

The core deliberately imports no Hermes runtime, provider, credential, or host
private APIs. A trusted public-API adapter must call ``discover`` only after it
has proved the hook's profile/session identity, and must call ``capture`` only
for the matching successful completed main answer.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
import json
import re
import threading
import time
import uuid
import weakref
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any

from .worker import BoundedWorkerPool, drain_detached_loop

from .config import ConfigError, default_settings, validate_settings
from .packet import CapturedPacket, PacketError, build_packet, sanitize_text
from .response import ResponseValidationError, validate_response
from .store import OwnedLock, SQLiteStore, StoreError, _json


class ServiceError(RuntimeError):
    """Public operation rejected without silently changing persistent state."""


class SessionNotDiscovered(ServiceError):
    """The session ID was not registered through the verified discovery path."""


class OwnerUnavailable(ServiceError):
    """Another live process holds the OS-released session owner lock."""


class ConfigurationConflict(ServiceError):
    """The saved profile configuration revision changed before this update."""


class ReviewerResultError(ServiceError):
    """The injected reviewer returned an unsupported result envelope."""


@dataclass(frozen=True, slots=True)
class ReviewerResult:
    """Adapter-neutral public-facade result envelope.

    ``response`` is the structured ``{outcome, findings}`` object. Served route
    and usage are optional because the host facade may not report them.
    """

    response: Any
    provider: str | None = None
    model: str | None = None
    usage: Mapping[str, Any] | None = None
    usage_known: bool | None = None


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    packet: CapturedPacket
    turn_id: str
    applied_revision: int
    applied_generation: int
    owner_token: str
    settings_json: str
    captured_mono: float
    expires_mono: float
    original_attempt_id: str


@dataclass(slots=True)
class _Work:
    session_id: str
    attempt_id: str
    turn_id: str
    packet: CapturedPacket
    applied_revision: int
    applied_generation: int
    owner_token: str
    settings_json: str
    deadline_mono: float
    deadline_wall: float
    context: contextvars.Context
    cancel_event: threading.Event = field(default_factory=threading.Event)
    invocation_started: threading.Event = field(default_factory=threading.Event)
    # True once an abandoned, still-running transport owns this attempt's
    # process call slot; its drain thread (not the worker) then frees it.
    slot_handed_off: bool = False


@dataclass(slots=True)
class _Active:
    work: _Work
    future: Future[Any] | None = None


def _release_held_locks(locks: Any) -> None:
    """Release every ownership lock a Service still held when it was dropped.

    Module-level finalizer target: it must not close over the Service, or the
    finalizer would keep the instance (and its locks) alive forever. It takes
    the lock dict directly, which the finalizer keeps reachable. Each
    ``OwnedLock.release`` is guarded and idempotent, so a lock already released
    through another path stays a no-op here.
    """
    for lock in list(locks.values()):
        try:
            lock.release()
        except Exception:
            pass
    locks.clear()


class Service:
    """Hermes Advisor core; one instance represents one profile-local worker.

    ``data_dir`` must be the authenticated active profile's plugin-data
    directory, not a path selected from a request/session ID. The host adapter
    owns this trust boundary. The reviewer is an injected async callable and is
    the only inference interface used here.
    """

    PROCESS_CONCURRENCY = 2
    SESSION_CONCURRENCY = 1
    PENDING_PER_SESSION = 1
    SNAPSHOT_CACHE_SIZE = 16
    SNAPSHOT_TTL_SECONDS = 1800
    RETENTION_PER_SESSION = 100
    # Seconds a transport gets to react to cancellation before the attempt is
    # abandoned locally. Local cancellation is not proof of remote termination,
    # so the attempt keeps cancellation_pending=1 and stays retention-protected
    # instead of being reported as a clean terminal cancellation.
    TRANSPORT_CANCEL_GRACE_SECONDS = 2.0
    SUPPORTED_SURFACES = frozenset({"cli", "desktop", "interactive_cli", "native_desktop"})

    def __init__(self, data_dir: str, reviewer: Any, route_validator: Any = None):
        if not callable(reviewer):
            raise TypeError("reviewer must be an injected async callable")
        if not (
            inspect.iscoroutinefunction(reviewer)
            or inspect.iscoroutinefunction(getattr(reviewer, "__call__", None))
        ):
            raise TypeError("reviewer must be asynchronous")
        self.reviewer = reviewer
        self.route_validator = route_validator
        self.store = SQLiteStore(data_dir)
        self._locks: dict[str, OwnedLock] = {}
        self._invalidation_callbacks: set[Any] = set()
        self._owner_tokens: dict[str, str] = {}
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._cache_unavailable: dict[str, str] = {}
        self._active: dict[str, _Active] = {}
        # session_id -> attempt_id whose transport ignored cancellation and is
        # still running. It keeps the session and one process call slot busy.
        self._unconfirmed: dict[str, str] = {}
        self._pending: OrderedDict[str, _Work] = OrderedDict()
        self._local_disabled: set[str] = set()
        self._volatile_errors: dict[str, str] = {}
        self._storage_latches: dict[str, str] = {}
        self._runtime_lock = threading.RLock()
        self._runtime_changed = threading.Condition(self._runtime_lock)
        # Plugin-owned bounded worker pool: exactly PROCESS_CONCURRENCY eager
        # daemon threads created here, replacing the ThreadPoolExecutor whose
        # threads were daemonized via the CPython-private `._threads` attribute
        # (a no-op — the executor had spawned no threads yet). The pool exposes
        # the same public Future contract (submit/add_done_callback/done/
        # result/cancel) with a bounded queue; cancelling a queued task never
        # releases a physical call slot, and shutdown makes no claim that a
        # thread is dead unless it observed the pool drain.
        self._executor = BoundedWorkerPool(
            max_workers=self.PROCESS_CONCURRENCY,
            max_queued=self.PROCESS_CONCURRENCY,
            thread_name_prefix="advisor-review",
        )
        self._closing = False
        self._closed = False
        self._release_when_idle = False
        # A Service dropped without close() must not keep its ownership locks
        # (fd + flock) for the life of the process: a new Service on the same
        # profile would see OwnerUnavailable for those sessions. The finalizer
        # releases whatever ``self._locks`` still holds once the instance is
        # unreachable; close() disarms it via _release_all_locks_locked().
        self._lock_finalizer = weakref.finalize(self, _release_held_locks, self._locks)

    # ---- Verified discovery and ownership ---------------------------------

    def discover(self, session_id: str, surface: str) -> dict[str, Any]:
        """Register metadata only, for an ID already verified by the adapter.

        This is the sole method that establishes local session identity. The
        public adapter must never forward caller-supplied IDs to it.
        """
        _validate_identifier(session_id, "session_id", max_chars=256)
        _validate_identifier(surface, "surface", max_chars=64)
        with self._runtime_lock:
            self._ensure_open()
            if session_id in self._locks:
                token = self._owner_tokens[session_id]
                try:
                    def refresh(conn: Any) -> dict[str, Any]:
                        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                        if row is None or row["owner_token"] != token:
                            raise OwnerUnavailable("durable owner record no longer matches this worker")
                        if surface not in self.SUPPORTED_SURFACES:
                            if row["state"] != "disabled" or row["pause_reason"] != "unsupported_surface":
                                now = time.time()
                                conn.execute(
                                    "UPDATE sessions SET surface=?,state='disabled',pause_reason='unsupported_surface',"
                                    "applied_generation=applied_generation+1,last_seen=? WHERE session_id=? AND owner_token=?",
                                    (surface, now, session_id, token),
                                )
                                _invalidate_attempts(conn, session_id, token, now, "unsupported_surface")
                            else:
                                conn.execute(
                                    "UPDATE sessions SET surface=?,last_seen=? WHERE session_id=? AND owner_token=?",
                                    (surface, time.time(), session_id, token),
                                )
                        else:
                            conn.execute(
                                "UPDATE sessions SET surface=?, last_seen=? WHERE session_id=? AND owner_token=?",
                                (surface, time.time(), session_id, token),
                            )
                        row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                        return dict(row)

                    row = self.store.transaction(refresh)
                except StoreError as exc:
                    self._storage_latches[session_id] = "storage_error"
                    raise ServiceError("session discovery could not be saved") from exc
                if surface not in self.SUPPORTED_SURFACES:
                    self._deactivate_local(session_id, cancel_active=True, unavailable_reason="unsupported_surface")
                if row["state"] != "enabled":
                    self._locks[session_id].suspend()
                return _discovery_result(row, recovered=False)

            lock = self.store.acquire(session_id)
            if lock is None:
                raise OwnerUnavailable("session owner is live or ambiguous; no state was changed")
            token = uuid.uuid4().hex
            now = time.time()
            recovered = False
            try:
                def claim(conn: Any) -> dict[str, Any]:
                    nonlocal recovered
                    config = conn.execute(
                        "SELECT revision, settings FROM profile_config WHERE singleton=1"
                    ).fetchone()
                    if config is None:
                        raise StoreError("profile configuration row is missing")
                    row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                    if row is None:
                        conn.execute(
                            "INSERT INTO sessions(session_id,surface,owner_token,state,pause_reason,invocation_count,"
                            "applied_revision,applied_generation,applied_settings,discovered_at,last_seen) "
                            "VALUES(?,?,?,'disabled',?,0,?,0,?,?,?)",
                            (session_id, surface, token,
                             None if surface in self.SUPPORTED_SURFACES else "unsupported_surface",
                             config["revision"], config["settings"], now, now),
                        )
                    elif row["owner_token"] != token:
                        recovered = True
                        old_token = row["owner_token"]
                        conn.execute(
                            "UPDATE attempts SET status='abandoned', cancellation_pending=0, terminal_at=?, "
                            "error_code='owner_recovered', error_text='prior worker terminated before completion' "
                            "WHERE session_id=? AND owner_token=? AND status IN ('queued','running')",
                            (now, session_id, old_token),
                        )
                        # The dead owner's local transport died with it, so it
                        # must not stay retention-protected forever. Remote
                        # termination remains explicitly unconfirmed.
                        conn.execute(
                            "UPDATE attempts SET cancellation_pending=0, "
                            "error_text='owner process terminated; remote termination unconfirmed' "
                            "WHERE session_id=? AND owner_token=? AND cancellation_pending=1",
                            (session_id, old_token),
                        )
                        conn.execute(
                            "UPDATE sessions SET surface=?, owner_token=?, state='disabled', "
                            "pause_reason=?, applied_generation=applied_generation+1, last_seen=? "
                            "WHERE session_id=?",
                            (surface, token,
                             "owner_recovered" if surface in self.SUPPORTED_SURFACES else "unsupported_surface",
                             now, session_id),
                        )
                        _prune(conn, session_id)
                    else:
                        conn.execute(
                            "UPDATE sessions SET surface=?, last_seen=? WHERE session_id=?",
                            (surface, now, session_id),
                        )
                    return dict(conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone())

                row = self.store.transaction(claim)
            except StoreError as exc:
                lock.release()
                raise ServiceError("session discovery could not be saved") from exc
            except BaseException:
                lock.release()
                raise
            self._locks[session_id] = lock
            self._owner_tokens[session_id] = token
            self._local_disabled.discard(session_id)
            if row["state"] != "enabled":
                # Discovery is metadata-only (SPEC): a disabled session runs
                # nothing, so the exclusive flock is suspended and enable()
                # re-takes it before the session runs again.
                lock.suspend()
            if recovered:
                self._cache.pop(session_id, None)
                self._cache_unavailable[session_id] = "process_restart"
                self._pending.pop(session_id, None)
            else:
                self._cache_unavailable.setdefault(session_id, "no_captured_answer")
            return _discovery_result(row, recovered=recovered)

    # ---- Configuration and state -----------------------------------------

    def configure(
        self,
        session_id: str,
        settings: Mapping[str, Any],
        expected_revision: int,
    ) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative JSON integer")
        try:
            normalized = validate_settings(
                settings, route_validator=self.route_validator, require_route=True
            )
        except ConfigError as exc:
            raise ServiceError(str(exc)) from exc
        now = time.time()
        try:
            def apply(conn: Any) -> dict[str, Any]:
                current = conn.execute(
                    "SELECT revision FROM profile_config WHERE singleton=1"
                ).fetchone()
                if current is None:
                    raise StoreError("profile configuration row is missing")
                if current["revision"] != expected_revision:
                    raise ConfigurationConflict(
                        f"saved profile revision is {current['revision']}, expected {expected_revision}"
                    )
                row = conn.execute(
                    "SELECT * FROM sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                if row is None or row["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                new_revision = current["revision"] + 1
                new_generation = row["applied_generation"] + 1
                state = row["state"]
                reason = row["pause_reason"]
                if state == "enabled" and row["invocation_count"] >= normalized["session_allowance"]:
                    state, reason = "paused", "allowance_exhausted"
                conn.execute(
                    "UPDATE profile_config SET revision=?,settings=?,updated_at=? WHERE singleton=1",
                    (new_revision, _json(normalized), now),
                )
                conn.execute(
                    "UPDATE sessions SET applied_revision=?,applied_generation=?,applied_settings=?,"
                    "state=?,pause_reason=?,last_seen=? WHERE session_id=? AND owner_token=?",
                    (new_revision, new_generation, _json(normalized), state, reason, now, session_id, token),
                )
                _invalidate_attempts(conn, session_id, token, now, "configuration_changed")
                return {
                    "saved_profile_revision": new_revision,
                    "applied_revision": new_revision,
                    "applied_generation": new_generation,
                    "state": state,
                    "pause_reason": reason,
                }

            result = self.store.transaction(apply)
        except ConfigurationConflict:
            raise
        except (OwnerUnavailable, StoreError) as exc:
            if isinstance(exc, StoreError):
                self._set_storage_latch(session_id, "configuration could not be saved")
            raise ServiceError(str(exc)) from exc
        self._invalidate_local(session_id, cancel_active=True)
        with self._runtime_lock:
            if result["state"] == "enabled":
                self._local_disabled.discard(session_id)
            else:
                self._local_disabled.add(session_id)
            self._suspend_lock_if_idle_locked(session_id)
        return result

    def enable(self, session_id: str) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        with self._runtime_lock:
            if session_id in self._active or session_id in self._unconfirmed:
                raise ServiceError("session still has a cancelling in-flight transport")
        # A disabled session ran nothing, so its flock was suspended; take the
        # exclusive lock back before flipping the durable state. Contention
        # here means another worker claimed the session while it was disabled.
        lock = self._locks[session_id]
        was_suspended = lock.suspended
        try:
            lock.resume()
        except BlockingIOError as exc:
            raise OwnerUnavailable(
                "session owner is live or ambiguous; no state was changed"
            ) from exc
        enabled = False
        try:
            existing = self._load_session(session_id, token)
            settings = json.loads(existing["applied_settings"])
            if existing["surface"] not in self.SUPPORTED_SURFACES:
                raise ServiceError("unsupported_surface")
            route = settings.get("route")
            if not route:
                raise ServiceError("no_model: configure an explicit reviewer route first")
            if self.route_validator is not None:
                try:
                    accepted = self.route_validator(dict(route))
                except Exception as exc:
                    raise ServiceError("host route or grant validation failed") from exc
                if accepted is False:
                    raise ServiceError("host rejected the reviewer route or grant")

            def persist(conn: Any) -> dict[str, Any]:
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if row is None or row["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                applied = json.loads(row["applied_settings"])
                if row["invocation_count"] >= applied["session_allowance"]:
                    conn.execute(
                        "UPDATE sessions SET state='paused',pause_reason='allowance_exhausted' WHERE session_id=?",
                        (session_id,),
                    )
                    return {**dict(row), "state": "paused", "pause_reason": "allowance_exhausted"}
                now = time.time()
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='enable_reconciled_queue' WHERE session_id=? AND owner_token=? AND status='queued'",
                    (now, session_id, token),
                )
                conn.execute(
                    "UPDATE sessions SET state='enabled',pause_reason=NULL,last_seen=? WHERE session_id=? AND owner_token=?",
                    (now, session_id, token),
                )
                _prune(conn, session_id)
                return dict(conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone())

            row = self.store.transaction(persist)
            if row["state"] != "enabled":
                raise ServiceError("allowance_exhausted: reset the allowance explicitly")
            enabled = True
        except ServiceError:
            raise
        except (OwnerUnavailable, StoreError) as exc:
            if isinstance(exc, StoreError):
                self._set_storage_latch(session_id, "session could not be enabled durably")
            raise ServiceError(str(exc)) from exc
        finally:
            # Any failure leaves the session exactly as enable() found it: a
            # session that was suspended must not stay flocked, or the real
            # owner is locked out of its own session for the process' life.
            if not enabled and was_suspended:
                lock.suspend()
        with self._runtime_lock:
            self._storage_latches.pop(session_id, None)
            self._volatile_errors.pop(session_id, None)
            self._local_disabled.discard(session_id)
            if existing["state"] != "enabled" or session_id in self._cache_unavailable:
                self._cache.pop(session_id, None)
                self._cache_unavailable[session_id] = "new_answer_required"
        return _session_status(row)

    def disable(self, session_id: str) -> dict[str, Any]:
        """Stop local admission first; report if durable disable could not commit."""
        token = self._assert_local_owned(session_id)
        self._deactivate_local(session_id, cancel_active=True)
        now = time.time()
        try:
            def turn_off(conn: Any) -> dict[str, Any]:
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if row is None or row["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                conn.execute(
                    "UPDATE sessions SET state='disabled',pause_reason=NULL,"
                    "applied_generation=applied_generation+1,last_seen=? WHERE session_id=? AND owner_token=?",
                    (now, session_id, token),
                )
                _invalidate_attempts(conn, session_id, token, now, "disabled")
                return dict(conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone())

            row = self.store.transaction(turn_off)
        except (OwnerUnavailable, StoreError) as exc:
            message = "durable disable was not saved"
            self._set_storage_latch(session_id, message)
            return {"session_id": session_id, "state": "disabled", "durable_state_saved": False, "error": message}
        self._local_disabled.add(session_id)
        self._locks[session_id].suspend()
        return {**_session_status(row), "durable_state_saved": True}

    # ---- Capture and review admission -------------------------------------

    def capture(
        self,
        session_id: str,
        turn_id: str,
        request: str | None,
        answer: str | None,
        history: Sequence[Mapping[str, Any]] | None,
        *,
        snapshot: CapturedPacket | None = None,
        coverage: Mapping[str, Any] | None = None,
        expected_applied_revision: int | None = None,
        expected_applied_generation: int | None = None,
    ) -> dict[str, Any]:
        """Admit only the caller-classified successful completed main answer.

        ``snapshot`` lets an adapter build its required bounded hook snapshot
        before it can correlate the later successful end event. ``coverage`` is
        forwarded as ``prior_coverage`` only when building from raw arguments.
        In disabled/paused mode this method checks metadata and returns before
        inspecting/copying/digesting any content argument.
        """
        token = self._assert_local_owned(session_id)
        captured_mono = time.monotonic()
        captured_wall = time.time()
        with self._runtime_lock:
            if session_id in self._storage_latches:
                return {"accepted": False, "reason": "storage_error"}
            if self._closing:
                return {"accepted": False, "reason": "service_closing"}
        try:
            row = self._load_session(session_id, token)
        except StoreError:
            self._set_storage_latch(session_id, "storage_error")
            return {"accepted": False, "reason": "storage_error"}
        guarded = expected_applied_revision is not None or expected_applied_generation is not None
        if guarded and (type(expected_applied_revision) is not int
                        or type(expected_applied_generation) is not int
                        or expected_applied_revision < 0 or expected_applied_generation < 0):
            raise ValueError('capture generation preconditions must be paired nonnegative integers')
        if guarded and (row['applied_revision'] != expected_applied_revision
                        or row['applied_generation'] != expected_applied_generation):
            return {'accepted': False, 'reason': 'configuration_changed', 'requests': 0}
        # Admission's SQLite transaction rechecks this exact row generation;
        # a later configure cannot attach an old packet to the new settings.
        state = row["state"]
        if state != "enabled":
            return {"accepted": False, "reason": row["pause_reason"] or state}
        if row["surface"] not in self.SUPPORTED_SURFACES:
            return {"accepted": False, "reason": "unsupported_surface"}
        settings = json.loads(row["applied_settings"])
        if row["invocation_count"] >= settings["session_allowance"]:
            self._pause_for_allowance(session_id, token)
            return {"accepted": False, "reason": "allowance_exhausted"}
        # Local fail-closed admission (a disable/pause this owner could not
        # confirm durably) wins before any content argument is inspected.
        with self._runtime_lock:
            if session_id in self._local_disabled:
                return {"accepted": False, "reason": "disabled", "requests": 0}
        try:
            _validate_identifier(turn_id, "turn_id", max_chars=256)
            if snapshot is None:
                packet = build_packet(
                    request, answer, history,
                    packet_bytes=settings["packet_bytes"],
                    prior_coverage=coverage,
                )
            else:
                packet = _validate_captured_packet(snapshot, settings["packet_bytes"])
                if coverage is not None:
                    expected = _omitted_count(coverage)
                    actual = packet.coverage_dict()["upstream_omitted_tool_pairs"]
                    if expected != actual:
                        raise PacketError("snapshot coverage metadata does not match")
        except (PacketError, ValueError) as exc:
            return {"accepted": False, "reason": "invalid_packet", "error": str(exc)}
        with self._runtime_lock:
            if self._closing or self._closed:
                return {"accepted": False, "reason": "service_closing", "requests": 0}
            if session_id in self._storage_latches:
                return {"accepted": False, "reason": "storage_error", "requests": 0}
            if session_id in self._local_disabled:
                return {"accepted": False, "reason": "disabled", "requests": 0}
            work_result = self._enqueue(
                session_id=session_id,
                turn_id=turn_id,
                packet=packet,
                row=row,
                settings_json=row["applied_settings"],
                captured_mono=captured_mono,
                deadline_mono=captured_mono + settings["deadline_seconds"],
                deadline_wall=captured_wall + settings["deadline_seconds"],
                manual=False,
            )
        return {"accepted": not work_result.get("duplicate", False), **work_result}

    def review_now(self, session_id: str) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        requested_mono = time.monotonic()
        requested_wall = time.time()
        try:
            row = self._load_session(session_id, token)
        except StoreError:
            self._set_storage_latch(session_id, "storage_error")
            return {"accepted": False, "reason": "storage_error", "requests": 0}
        with self._runtime_lock:
            if session_id in self._storage_latches:
                return {"accepted": False, "reason": "storage_error", "requests": 0}
            if session_id in self._local_disabled or row["state"] != "enabled":
                return {"accepted": False, "reason": row["pause_reason"] or "disabled", "requests": 0}
            if row["surface"] not in self.SUPPORTED_SURFACES:
                return {"accepted": False, "reason": "unsupported_surface", "requests": 0}
            entry = self._get_live_cache(session_id)
            if entry is None:
                reason = self._cache_unavailable_reason(session_id)
                return {"accepted": False, "reason": reason, "requests": 0}
            if entry.owner_token != token or entry.applied_generation != row["applied_generation"]:
                self._cache.pop(session_id, None)
                return {"accepted": False, "reason": "configuration_changed", "requests": 0}
            settings = json.loads(entry.settings_json)
            if row["invocation_count"] >= settings["session_allowance"]:
                self._pause_for_allowance(session_id, token)
                return {"accepted": False, "reason": "allowance_exhausted", "requests": 0}
            active = self._active.get(session_id)
            if active and active.work.packet.digest == entry.packet.digest:
                return {"accepted": False, "reason": "already_running", "attempt_id": active.work.attempt_id, "requests": 0}
            pending = self._pending.get(session_id)
            if pending and pending.packet.digest == entry.packet.digest:
                return {"accepted": False, "reason": "already_queued", "attempt_id": pending.attempt_id, "requests": 0}

        try:
            latest = self.store.read(lambda conn: conn.execute(
                "SELECT attempt_id,status,cancellation_pending FROM attempts "
                "WHERE session_id=? AND packet_digest=? AND applied_generation=? "
                "ORDER BY created_at DESC,attempt_id DESC LIMIT 1",
                (session_id, entry.packet.digest, entry.applied_generation),
            ).fetchone())
        except StoreError:
            self._set_storage_latch(session_id, "storage_error")
            return {"accepted": False, "reason": "storage_error", "requests": 0}
        if latest is not None and latest["status"] in {"completed", "queued", "running"}:
            return {"accepted": False, "reason": "already_reviewed", "attempt_id": latest["attempt_id"], "requests": 0}
        with self._runtime_lock:
            if self._closing or self._closed:
                return {"accepted": False, "reason": "service_closing", "requests": 0}
            if session_id in self._storage_latches:
                return {"accepted": False, "reason": "storage_error", "requests": 0}
            if session_id in self._local_disabled:
                return {"accepted": False, "reason": "disabled", "requests": 0}
            work_result = self._enqueue(
                session_id=session_id,
                turn_id=entry.turn_id,
                packet=entry.packet,
                row=row,
                settings_json=entry.settings_json,
                captured_mono=entry.captured_mono,
                deadline_mono=requested_mono + settings["deadline_seconds"],
                deadline_wall=requested_wall + settings["deadline_seconds"],
                manual=True,
            )
        return {"accepted": True, **work_result}

    def _enqueue(
        self,
        *,
        session_id: str,
        turn_id: str,
        packet: CapturedPacket,
        row: Mapping[str, Any],
        settings_json: str,
        captured_mono: float,
        deadline_mono: float,
        deadline_wall: float,
        manual: bool,
    ) -> dict[str, Any]:
        token = self._owner_tokens[session_id]
        now = time.time()
        attempt_id = uuid.uuid4().hex
        settings = json.loads(settings_json)
        try:
            def queue(conn: Any) -> tuple[str, bool, str]:
                session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if session is None or session["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                if session["state"] != "enabled" or session["applied_generation"] != row["applied_generation"]:
                    raise ServiceError("session state changed before admission")
                if not manual:
                    old = conn.execute(
                        "SELECT d.attempt_id, a.status FROM dedup d LEFT JOIN attempts a ON a.attempt_id=d.attempt_id "
                        "WHERE d.session_id=? AND d.turn_id=? AND d.packet_digest=? AND d.applied_generation=?",
                        (session_id, turn_id, packet.digest, row["applied_generation"]),
                    ).fetchone()
                    if old is not None:
                        return old["attempt_id"], True, old["status"] or "pruned"
                conn.execute(
                    "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,"
                    "applied_generation,owner_token,created_at,deadline_at,status,coverage_json,"
                    "requested_provider,requested_model) VALUES(?,?,?,?,?,?,?,?,?,'queued',?,?,?)",
                    (attempt_id, session_id, turn_id, packet.digest, row["applied_revision"],
                     row["applied_generation"], token, now, deadline_wall,
                     _json(packet.coverage_dict()), settings["route"]["provider"], settings["route"]["model"]),
                )
                if not manual:
                    conn.execute(
                        "INSERT INTO dedup(session_id,turn_id,packet_digest,applied_generation,attempt_id) VALUES(?,?,?,?,?)",
                        (session_id, turn_id, packet.digest, row["applied_generation"], attempt_id),
                    )
                return attempt_id, False, "queued"

            actual_id, duplicate, status = self.store.transaction(queue)
        except StoreError as exc:
            self._set_storage_latch(session_id, "capture admission could not be saved")
            return {"accepted": False, "reason": "storage_error", "requests": 0}
        except (OwnerUnavailable, ServiceError) as exc:
            return {"accepted": False, "reason": str(exc), "requests": 0}
        if duplicate:
            return {"attempt_id": actual_id, "status": status, "duplicate": True, "requests": 0}

        work = _Work(
            session_id=session_id,
            attempt_id=actual_id,
            turn_id=turn_id,
            packet=packet,
            applied_revision=row["applied_revision"],
            applied_generation=row["applied_generation"],
            owner_token=token,
            settings_json=settings_json,
            deadline_mono=deadline_mono,
            deadline_wall=deadline_wall,
            context=contextvars.copy_context(),
        )
        with self._runtime_lock:
            if self._closing or self._closed or session_id in self._storage_latches or session_id in self._local_disabled:
                self._terminal_without_call(work, "cancelled", "service_not_admitting")
                reason = "storage_error" if session_id in self._storage_latches else "service_closing" if self._closing else "disabled"
                return {"accepted": False, "reason": reason, "requests": 0}
            if not manual:
                self._add_cache(session_id, _CacheEntry(
                    packet=packet,
                    turn_id=turn_id,
                    applied_revision=row["applied_revision"],
                    applied_generation=row["applied_generation"],
                    owner_token=token,
                    settings_json=settings_json,
                    captured_mono=captured_mono,
                    expires_mono=captured_mono + self.SNAPSHOT_TTL_SECONDS,
                    original_attempt_id=actual_id,
                ))
            current = self._pending.get(session_id)
            if current is not None:
                self._pending.pop(session_id, None)
                self._mark_attempt_skipped(current, "replaced_by_newer_turn")
            if (session_id in self._active or session_id in self._unconfirmed or self._closing
                    or len(self._active) >= self.PROCESS_CONCURRENCY):
                self._pending[session_id] = work
                return {"attempt_id": actual_id, "status": "queued", "requests": 0}
            if not self._dispatch_locked(work):
                return {"accepted": False, "reason": "worker_unavailable", "requests": 0}
        return {"attempt_id": actual_id, "status": "queued", "requests": 0}

    def _dispatch_locked(self, work: _Work) -> bool:
        if self._closing or work.session_id in self._active or work.session_id in self._unconfirmed:
            self._pending[work.session_id] = work
            return True
        active = _Active(work)
        self._active[work.session_id] = active
        try:
            future = self._executor.submit(work.context.run, self._run_work, work)
        except Exception:
            # A refused submission must not strand an _active entry (it would
            # wedge the session) or leave the durable attempt queued.
            self._active.pop(work.session_id, None)
            self._volatile_errors[work.session_id] = "review worker unavailable"
            self._terminal_without_call(work, "failed", "worker_unavailable")
            return False
        active.future = future
        callback = self._make_done_callback(work.session_id, work.attempt_id)
        future.add_done_callback(callback)
        return True

    def _make_done_callback(self, session_id: str, attempt_id: str):
        # Bound method, not a closure over the future: the future itself must
        # not be retained by its own done-callback (concurrent.futures keeps
        # every add_done_callback strongly referenced; a closure that captures
        # the future makes each completed future immortal).
        return self._work_done_for(session_id, attempt_id)

    def _work_done_for(self, session_id: str, attempt_id: str):
        def completed(future: Future[Any]) -> None:
            self._work_done(session_id, attempt_id, future)
        return completed

    def _pump_locked(self) -> None:
        if self._closing:
            return
        while self._pending and len(self._active) < self.PROCESS_CONCURRENCY:
            selected: tuple[str, _Work] | None = None
            for session_id, work in self._pending.items():
                if session_id not in self._active and session_id not in self._unconfirmed:
                    selected = (session_id, work)
                    break
            if selected is None:
                return
            session_id, work = selected
            self._pending.pop(session_id, None)
            self._dispatch_locked(work)

    def _work_done(self, session_id: str, attempt_id: str, future: Future[Any]) -> None:
        # Cancellation is settled by its canceller (durable shutdown/disable or a
        # storage latch), so it is not a worker failure.
        if not future.cancelled():
            try:
                future.result()
            except StoreError as exc:
                self._set_storage_latch(session_id, f"review worker failed ({type(exc).__name__[:64]})")
            except BaseException as exc:
                # A non-storage defect is not a storage failure. _run_work already
                # finalized the attempt as worker_error; report it as a volatile
                # error and leave healthy storage admitting work.
                with self._runtime_lock:
                    self._volatile_errors[session_id] = f"review worker failed ({type(exc).__name__[:64]})"
        with self._runtime_changed:
            active = self._active.get(session_id)
            if active and active.work.attempt_id == attempt_id:
                self._active.pop(session_id, None)
            self._pump_locked()
            # The transport has settled: if this session is now paused/disabled
            # (e.g. its allowance was just exhausted), release its flock.
            self._suspend_lock_if_idle_locked(session_id)
            self._finish_close_if_idle_locked()
            self._runtime_changed.notify_all()

    def _finish_close_if_idle_locked(self) -> None:
        if self._release_when_idle and not self._active and not self._unconfirmed and not self._closed:
            self._release_all_locks_locked()
            self._closed = True
            self._executor.shutdown(wait=False, cancel_futures=True)

    # ---- Review execution --------------------------------------------------

    def _run_work(self, work: _Work) -> None:
        while True:
            if work.cancel_event.is_set():
                self._terminal_without_call(work, "cancelled", "invalidated_before_invocation")
                return
            remaining = work.deadline_mono - time.monotonic()
            if remaining <= 0:
                self._terminal_without_call(work, "timed_out", "deadline_expired_in_queue")
                return
            if _PROCESS_CALL_SLOTS.acquire(timeout=min(0.05, remaining)):
                break
        try:
            self._run_work_with_slot(work)
        except BaseException as exc:
            # Never strand a reserved/queued attempt: finalize it durably, then
            # let the pool future carry the error (which latches the session).
            self._record_failure(work, "worker_error", f"review worker failed ({type(exc).__name__[:64]})")
            self._terminal_without_call(work, "failed", "worker_error")
            raise
        finally:
            if not work.slot_handed_off:
                _PROCESS_CALL_SLOTS.release()

    def _run_work_with_slot(self, work: _Work) -> None:
        settings = json.loads(work.settings_json)
        if time.monotonic() >= work.deadline_mono:
            self._terminal_without_call(work, "timed_out", "deadline_expired_in_queue")
            return
        if work.cancel_event.is_set() or not self._work_still_valid(work):
            self._terminal_without_call(work, "cancelled", "invalidated_before_invocation")
            return
        try:
            reservation = self._reserve_invocation(work, settings["session_allowance"])
        except StoreError:
            self._set_storage_latch(work.session_id, "allowance reservation failed")
            return
        if not reservation:
            return
        if work.cancel_event.is_set():
            try:
                self._cancel_before_call(work)
            except StoreError:
                self._set_storage_latch(work.session_id, "pre-call cancellation could not be saved")
            return
        if time.monotonic() >= work.deadline_mono:
            try:
                self._cancel_before_call(work, status="timed_out", code="deadline_expired_before_call")
            except StoreError:
                self._set_storage_latch(work.session_id, "pre-call timeout could not be saved")
            return
        try:
            response = self._run_invocation(self._invoke(work, settings), work)
        except _InvocationCancelled as exc:
            if not exc.abandoned:
                self._settle_cancellation(work)
            return
        except _InvocationTimedOut as exc:
            if not exc.abandoned:
                self._settle_cancellation(work)
            return
        except Exception as exc:
            self._record_exception(work, exc)
            return
        except BaseException as exc:
            # CancelledError/SystemExit/KeyboardInterrupt raised by the
            # transport itself: the invocation was charged, so settle it.
            self._record_failure(
                work, "reviewer_error", f"reviewer terminated abnormally ({type(exc).__name__[:64]})"
            )
            return
        if response is _NO_RESPONSE:
            return
        try:
            normalized = _normalize_reviewer_result(response)
            validated = validate_response(
                normalized.response, set(work.packet.references), max_findings=settings["max_findings"]
            )
        except (ReviewerResultError, ResponseValidationError) as exc:
            self._record_failure(work, "validation_error", "structured response failed local validation")
            return
        except Exception as exc:
            self._record_failure(work, "validation_error", "structured response failed local validation")
            return
        try:
            self._persist_success(work, validated, normalized)
        except StoreError:
            self._storage_failure_after_inference(work)

    def _run_invocation(self, coro: Any, work: _Work) -> Any:
        """Run one invocation on a private loop with hard-close semantics.

        Unlike ``asyncio.run``, loop shutdown never awaits pending transports:
        a reviewer that swallows cancellation would otherwise keep the loop
        (and the whole interpreter) alive. On abandonment the loop is closed
        immediately and the pending transport is dropped into a detached,
        retention-protected state instead.
        """
        loop = asyncio.new_event_loop()

        def quiet_destroyed_pending(current: Any, context: Any) -> None:
            # An abandoned uncancellable transport stays pending on purpose;
            # its GC-time "Task was destroyed" message is expected, not an
            # error. Every other diagnostic still reaches the default handler.
            if "Task was destroyed" in str(context.get("message", "")):
                return
            current.default_exception_handler(context)

        detached = False
        main: Any = None
        try:
            asyncio.set_event_loop(loop)
            main = loop.create_task(coro)
            return loop.run_until_complete(main)
        except (_InvocationCancelled, _InvocationTimedOut) as exc:
            if exc.abandoned and exc.task is not None and not exc.task.done():
                detached = self._detach_transport(loop, exc.task, work)
            raise
        finally:
            if detached:
                # The drain thread now owns the loop until the transport ends.
                asyncio.set_event_loop(None)
            else:
                loop.set_exception_handler(quiet_destroyed_pending)
                try:
                    # Give live handles one poll so clean transports finish
                    # their callbacks.
                    loop.run_until_complete(asyncio.sleep(0))
                except BaseException:
                    # A transport's SystemExit/KeyboardInterrupt may resurface
                    # while draining; it was already recorded. Still close.
                    pass
                if main is not None and main.done() and not main.cancelled():
                    main.exception()  # SystemExit/KeyboardInterrupt skip result()
                try:
                    loop.close()
                finally:
                    asyncio.set_event_loop(None)

    def _detach_transport(self, loop: Any, task: Any, work: _Work) -> bool:
        """Keep an uncancellable transport observable; it holds its slots.

        Termination is only ever *confirmed* by the task really finishing. Until
        then the attempt stays cancellation-pending, the session cannot start a
        second transport, and the process call slot is not released.
        """
        with self._runtime_changed:
            self._unconfirmed[work.session_id] = work.attempt_id
        work.slot_handed_off = True
        try:
            drain_detached_loop(
                loop, task, lambda: self._transport_confirmed(work),
                name=f"advisor-unconfirmed-{work.attempt_id[:8]}",
            )
        except BaseException:
            # No drain thread could be created: fall back to a hard-closed
            # loop (termination stays unconfirmed in the durable record).
            work.slot_handed_off = False
            with self._runtime_changed:
                self._unconfirmed.pop(work.session_id, None)
            return False
        return True

    def _transport_confirmed(self, work: _Work) -> None:
        """The abandoned transport really ended: settle, free slot, pump."""
        try:
            self._settle_cancellation(work)
        finally:
            _PROCESS_CALL_SLOTS.release()
        with self._runtime_changed:
            if self._unconfirmed.get(work.session_id) == work.attempt_id:
                self._unconfirmed.pop(work.session_id, None)
            if self._volatile_errors.get(work.session_id) == _UNRESOLVED_MESSAGE:
                self._volatile_errors.pop(work.session_id, None)
            self._pump_locked()
            self._finish_close_if_idle_locked()
            self._runtime_changed.notify_all()

    async def _invoke(self, work: _Work, settings: Mapping[str, Any]) -> Any:
        remaining = work.deadline_mono - time.monotonic()
        if remaining <= 0:
            self._cancel_before_call(work, status="timed_out", code="deadline_expired_before_invocation")
            return _NO_RESPONSE
        if work.cancel_event.is_set():
            self._cancel_before_call(work)
            return _NO_RESPONSE
        if work.session_id in self._storage_latches:
            self._cancel_before_call(work)
            return _NO_RESPONSE

        async def call_reviewer() -> Any:
            if work.cancel_event.is_set():
                return _PRECALL_CANCELLED
            if time.monotonic() >= work.deadline_mono:
                return _PRECALL_TIMED_OUT
            work.invocation_started.set()
            returned = self.reviewer(work.packet.as_payload(), json.loads(_json(settings)))
            if not inspect.isawaitable(returned):
                raise TypeError("async reviewer returned a non-awaitable result")
            return await returned

        task = asyncio.ensure_future(call_reviewer())
        while True:
            if task.done():
                result = await task
                if result is _PRECALL_CANCELLED:
                    self._cancel_before_call(work)
                    return _NO_RESPONSE
                if result is _PRECALL_TIMED_OUT:
                    self._cancel_before_call(work, status="timed_out", code="deadline_expired_before_call")
                    return _NO_RESPONSE
                if time.monotonic() >= work.deadline_mono:
                    self._mark_cancel_pending(work, "timed_out", "deadline_exceeded")
                    self._settle_cancellation(work)
                    return _NO_RESPONSE
                return result
            if work.cancel_event.is_set():
                return await self._abandon_after_grace(
                    task, work, status="cancelled", code="session_invalidated"
                )
            remaining = work.deadline_mono - time.monotonic()
            if remaining <= 0:
                return await self._abandon_after_grace(
                    task, work, status="timed_out", code="deadline_exceeded"
                )
            await asyncio.wait({task}, timeout=min(0.05, remaining))

    async def _abandon_after_grace(
        self,
        task: "asyncio.Future[Any]",
        work: _Work,
        *,
        status: str,
        code: str,
    ) -> Any:
        """Cancel the transport and refuse to wait on it indefinitely.

        A reviewer that swallows cancellation cannot confirm termination. The
        worker waits one bounded grace window, then abandons the attempt with
        ``cancellation_pending=1`` — local cancellation is not proof that the
        remote transport stopped — and unblocks the bounded worker thread. The
        still-pending transport is left on the private loop, which the runner
        force-closes right afterwards; it is never awaited again, so the
        interpreter can exit with the cancellation unresolved.
        """
        task.cancel()
        self._mark_cancel_pending(work, status, code)
        grace = max(0.0, float(self.TRANSPORT_CANCEL_GRACE_SECONDS))
        try:
            done, _pending = await asyncio.wait({task}, timeout=grace)
        except asyncio.CancelledError:  # loop shutdown racing the grace wait
            done = set()
        if task in done:
            try:
                await task
            except BaseException:
                pass
            raise _InvocationCancelled() if status == "cancelled" else _InvocationTimedOut()
        # Grace expired with the transport still pending: leave it behind for
        # the force-closed loop, mark the unresolved state visibly, and wake
        # any close() waiter now that this worker is unblocked. Retention keeps
        # protecting the attempt, so settling never happens for this attempt.
        self._volatile_errors.setdefault(work.session_id, _UNRESOLVED_MESSAGE)
        failure = _InvocationCancelled(abandoned=True) if status == "cancelled" else _InvocationTimedOut(abandoned=True)
        failure.task = task
        raise failure

    # ---- Durable attempt mutations ----------------------------------------

    def _reserve_invocation(self, work: _Work, allowance: int) -> bool:
        now = time.time()
        exhausted = False
        def reserve(conn: Any) -> bool:
            nonlocal exhausted
            session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (work.session_id,)).fetchone()
            attempt = conn.execute("SELECT status FROM attempts WHERE attempt_id=?", (work.attempt_id,)).fetchone()
            if attempt is None or attempt["status"] != "queued":
                return False
            if (
                session is None or session["owner_token"] != work.owner_token
                or session["state"] != "enabled"
                or session["applied_generation"] != work.applied_generation
            ):
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='invalidated_before_invocation' WHERE attempt_id=? AND status='queued'",
                    (now, work.attempt_id),
                )
                return False
            if session["invocation_count"] >= allowance:
                conn.execute(
                    "UPDATE sessions SET state='paused',pause_reason='allowance_exhausted' WHERE session_id=?",
                    (work.session_id,),
                )
                conn.execute(
                    "UPDATE attempts SET status='skipped',terminal_at=?,error_code='allowance_exhausted',"
                    "error_text='session invocation allowance exhausted' WHERE attempt_id=?",
                    (now, work.attempt_id),
                )
                return False
            next_count = session["invocation_count"] + 1
            if next_count >= allowance:
                exhausted = True
                conn.execute(
                    "UPDATE sessions SET invocation_count=?,state='paused',pause_reason='allowance_exhausted' "
                    "WHERE session_id=? AND owner_token=?",
                    (next_count, work.session_id, work.owner_token),
                )
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='allowance_exhausted' WHERE session_id=? AND owner_token=? "
                    "AND attempt_id<>? AND status='queued'",
                    (now, work.session_id, work.owner_token, work.attempt_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET invocation_count=? WHERE session_id=? AND owner_token=?",
                    (next_count, work.session_id, work.owner_token),
                )
            conn.execute(
                "UPDATE attempts SET status='running' WHERE attempt_id=? AND status='queued'",
                (work.attempt_id,),
            )
            _prune(conn, work.session_id)
            return True
        reserved = self.store.transaction(reserve)
        if not reserved:
            try:
                row = self._load_session(work.session_id, work.owner_token)
                if row["state"] == "paused" and row["pause_reason"] == "allowance_exhausted":
                    self._clear_cache_and_pending(work.session_id, cancel_active=False)
            except Exception:
                pass
        else:
            if exhausted:
                self._deactivate_local(
                    work.session_id, cancel_active=False, unavailable_reason="allowance_exhausted"
                )
        return reserved

    def _cancel_before_call(
        self, work: _Work, *, status: str = "cancelled", code: str = "cancelled_before_call"
    ) -> None:
        if work.invocation_started.is_set():
            return
        now = time.time()
        def cancel(conn: Any) -> None:
            attempt = conn.execute(
                "SELECT status,cancellation_pending FROM attempts WHERE attempt_id=?", (work.attempt_id,)
            ).fetchone()
            session = conn.execute("SELECT owner_token FROM sessions WHERE session_id=?", (work.session_id,)).fetchone()
            if (
                attempt is None or session is None or session["owner_token"] != work.owner_token
                or not (attempt["status"] == "running" or (attempt["status"] == "cancelled" and attempt["cancellation_pending"]))
            ):
                return
            conn.execute(
                "UPDATE sessions SET invocation_count=MAX(0,invocation_count-1) "
                "WHERE session_id=? AND owner_token=?",
                (work.session_id, work.owner_token),
            )
            conn.execute(
                "UPDATE attempts SET status=?,terminal_at=?,cancellation_pending=0,"
                "error_code=? WHERE attempt_id=?",
                (status, now, code, work.attempt_id),
            )
            _prune(conn, work.session_id)
        self.store.transaction(cancel)

    def _persist_success(
        self,
        work: _Work,
        validated: dict[str, Any],
        metadata: ReviewerResult,
    ) -> None:
        if work.cancel_event.is_set() or work.session_id in self._storage_latches:
            self._discard_inflight_result(work)
            return
        now = time.time()
        settings = json.loads(work.settings_json)
        served_provider = _safe_attribution(metadata.provider)
        served_model = _safe_attribution(metadata.model)
        usage_known, usage = _clean_usage(metadata.usage, metadata.usage_known)
        safe_findings = []
        for item in validated["findings"]:
            safe_findings.append({
                **item,
                "explanation": _safe_model_text(item["explanation"], 2000),
                "next_step": _safe_model_text(item["next_step"], 1000),
            })
        safe_outcome = _json({"outcome": validated["outcome"], "findings": safe_findings})

        def complete(conn: Any) -> bool:
            session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (work.session_id,)).fetchone()
            attempt = conn.execute("SELECT * FROM attempts WHERE attempt_id=?", (work.attempt_id,)).fetchone()
            if attempt is None:
                return False
            if attempt["status"] != "running":
                if attempt["cancellation_pending"]:
                    conn.execute(
                        "UPDATE attempts SET cancellation_pending=0 WHERE attempt_id=?", (work.attempt_id,)
                    )
                    _prune(conn, work.session_id)
                return False
            if (
                session is None or session["owner_token"] != work.owner_token
                or session["applied_generation"] != work.applied_generation
                or session["state"] == "disabled"
            ):
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='invalidated_before_publication' WHERE attempt_id=?",
                    (now, work.attempt_id),
                )
                _prune(conn, work.session_id)
                return False
            conn.execute(
                "UPDATE attempts SET status='completed',terminal_at=?,outcome=?,served_provider=?,served_model=?,"
                "usage_json=?,usage_known=?,error_code=NULL,error_text=NULL,cancellation_pending=0 "
                "WHERE attempt_id=?",
                (now, safe_outcome, served_provider, served_model,
                 _json(usage) if usage_known else None, 1 if usage_known else 0, work.attempt_id),
            )
            for ordinal, finding in enumerate(safe_findings, start=1):
                conn.execute(
                    "INSERT INTO findings(note_id,session_id,attempt_id,ordinal,severity,explanation,"
                    "evidence_refs_json,next_step) VALUES(?,?,?,?,?,?,?,?)",
                    (f"{work.attempt_id}-n{ordinal}", work.session_id, work.attempt_id, ordinal,
                     finding["severity"], finding["explanation"], _json(finding["evidence_refs"]), finding["next_step"]),
                )
            _prune(conn, work.session_id)
            return True

        self.store.transaction(complete)

    def _discard_inflight_result(self, work: _Work) -> None:
        now = time.time()
        try:
            def discard(conn: Any) -> None:
                conn.execute(
                    "UPDATE attempts SET status=CASE WHEN status='running' THEN 'cancelled' ELSE status END,"
                    "terminal_at=COALESCE(terminal_at,?),cancellation_pending=0,"
                    "error_code=COALESCE(error_code,'publication_invalidated') "
                    "WHERE attempt_id=? AND status IN ('running','cancelled','timed_out')",
                    (now, work.attempt_id),
                )
                _prune(conn, work.session_id)
            self.store.transaction(discard)
        except StoreError:
            self._set_storage_latch(work.session_id, "cancelled result could not be saved")

    def _record_exception(self, work: _Work, exc: Exception) -> None:
        message = str(exc).casefold()
        quota_auth = any(term in message for term in (
            "quota", "rate limit", "429", "unauthoriz", "authentication", "permission denied",
            "403", "401", "model not found", "no such model", "grant denied", "access denied",
        ))
        code = "quota_or_auth" if quota_auth else "reviewer_error"
        safe = "reviewer request failed with quota or authorization" if quota_auth else f"reviewer request failed ({type(exc).__name__[:64]})"
        self._record_failure(work, code, safe, pause=quota_auth)

    def _record_failure(self, work: _Work, code: str, safe_text: str, *, pause: bool = False) -> None:
        now = time.time()
        try:
            def fail(conn: Any) -> None:
                attempt = conn.execute("SELECT status FROM attempts WHERE attempt_id=?", (work.attempt_id,)).fetchone()
                session = conn.execute("SELECT * FROM sessions WHERE session_id=?", (work.session_id,)).fetchone()
                if attempt is None:
                    return
                if attempt["status"] in {"cancelled", "timed_out", "abandoned"}:
                    conn.execute(
                        "UPDATE attempts SET cancellation_pending=0 WHERE attempt_id=?", (work.attempt_id,)
                    )
                    _prune(conn, work.session_id)
                    return
                if attempt["status"] != "running":
                    return
                conn.execute(
                    "UPDATE attempts SET status='failed',terminal_at=?,error_code=?,error_text=?,"
                    "cancellation_pending=0 WHERE attempt_id=?",
                    (now, code, sanitize_text(safe_text, max_chars=1000), work.attempt_id),
                )
                if (
                    pause and session and session["owner_token"] == work.owner_token
                    and session["applied_generation"] == work.applied_generation
                ):
                    conn.execute(
                        "UPDATE sessions SET state='paused',pause_reason=? WHERE session_id=?",
                        (code, work.session_id),
                    )
                    conn.execute(
                        "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                        "error_code='session_paused' WHERE session_id=? AND owner_token=? AND status='queued'",
                        (now, work.session_id, work.owner_token),
                    )
                _prune(conn, work.session_id)
            self.store.transaction(fail)
        except StoreError:
            self._set_storage_latch(work.session_id, "review failure could not be saved")
            return
        if pause:
            self._deactivate_local(work.session_id, cancel_active=False)
            self._local_disabled.add(work.session_id)

    def _terminal_without_call(self, work: _Work, status: str, code: str) -> None:
        now = time.time()
        try:
            def finish(conn: Any) -> None:
                row = conn.execute("SELECT status FROM attempts WHERE attempt_id=?", (work.attempt_id,)).fetchone()
                if row and row["status"] in {"queued", "running"}:
                    conn.execute(
                        "UPDATE attempts SET status=?,terminal_at=?,error_code=?,cancellation_pending=0 "
                        "WHERE attempt_id=?",
                        (status, now, code, work.attempt_id),
                    )
                    _prune(conn, work.session_id)
            self.store.transaction(finish)
        except StoreError:
            self._set_storage_latch(work.session_id, "attempt terminal state could not be saved")

    def _mark_cancel_pending(self, work: _Work, status: str, code: str) -> None:
        now = time.time()
        def mark(conn: Any) -> None:
            row = conn.execute("SELECT status FROM attempts WHERE attempt_id=?", (work.attempt_id,)).fetchone()
            if row and row["status"] in {"queued", "running"}:
                conn.execute(
                    "UPDATE attempts SET status=?,terminal_at=?,error_code=?,error_text=?,cancellation_pending=1 "
                    "WHERE attempt_id=?",
                    (status, now, code, "local cancellation requested; transport termination is pending", work.attempt_id),
                )
        try:
            self.store.transaction(mark)
        except StoreError:
            self._set_storage_latch(work.session_id, "cancellation state could not be saved")

    def _settle_cancellation(self, work: _Work) -> None:
        try:
            def settle(conn: Any) -> None:
                conn.execute(
                    "UPDATE attempts SET cancellation_pending=0 WHERE attempt_id=? AND status IN ('cancelled','timed_out')",
                    (work.attempt_id,),
                )
                _prune(conn, work.session_id)
            self.store.transaction(settle)
        except StoreError:
            self._set_storage_latch(work.session_id, "cancellation completion could not be saved")

    def _storage_failure_after_inference(self, work: _Work) -> None:
        # The raw result remains only in this stack frame and is discarded. The
        # outcome is never published from memory when its canonical commit fails.
        self._volatile_errors[work.session_id] = "review outcome was not saved; session paused"
        self._storage_latches[work.session_id] = "storage_error"
        try:
            now = time.time()
            def failed(conn: Any) -> None:
                conn.execute(
                    "UPDATE attempts SET status='failed',terminal_at=?,error_code='storage_error',"
                    "error_text='review outcome was not saved',cancellation_pending=0 "
                    "WHERE attempt_id=? AND status='running'",
                    (now, work.attempt_id),
                )
                conn.execute(
                    "UPDATE sessions SET state='paused',pause_reason='storage_error' "
                    "WHERE session_id=? AND owner_token=?",
                    (work.session_id, work.owner_token),
                )
                _prune(conn, work.session_id)
            self.store.transaction(failed)
        except StoreError:
            # The publication failure can be trigger-specific: retry terminal
            # failure persistence without any result fields. If the DB remains
            # unavailable, keep the charged reservation and pause locally.
            try:
                def failed_without_publication(conn: Any) -> None:
                    conn.execute(
                        "UPDATE attempts SET status='failed',terminal_at=?,error_code='storage_error',"
                        "error_text='review outcome was not saved',outcome=NULL,served_provider=NULL,"
                        "served_model=NULL,usage_json=NULL,usage_known=0,cancellation_pending=0 "
                        "WHERE attempt_id=? AND status='running'",
                        (time.time(), work.attempt_id),
                    )
                    conn.execute(
                        "UPDATE sessions SET state='paused',pause_reason='storage_error' "
                        "WHERE session_id=? AND owner_token=?",
                        (work.session_id, work.owner_token),
                    )
                    _prune(conn, work.session_id)
                self.store.transaction(failed_without_publication)
            except StoreError:
                # Preserve the invocation charge and unresolved durable attempt;
                # the local latch blocks re-admission until explicit recovery.
                try:
                    self.store.transaction(lambda conn: conn.execute(
                        "UPDATE sessions SET state='paused',pause_reason='storage_error' "
                        "WHERE session_id=? AND owner_token=?",
                        (work.session_id, work.owner_token),
                    ))
                except StoreError:
                    pass
        self._deactivate_local(work.session_id, cancel_active=False)
        self._local_disabled.add(work.session_id)

    def _set_storage_latch(self, session_id: str, message: str) -> None:
        with self._runtime_lock:
            self._storage_latches[session_id] = "storage_error"
            self._volatile_errors[session_id] = message
            self._cache.pop(session_id, None)
            self._cache_unavailable[session_id] = "storage_error"
            self._local_disabled.add(session_id)
            pending = self._pending.pop(session_id, None)
            if pending is not None:
                pending.cancel_event.set()
            active = self._active.get(session_id)
            if active is not None:
                active.work.cancel_event.set()

    def _pause_for_allowance(self, session_id: str, token: str) -> None:
        try:
            def pause(conn: Any) -> None:
                conn.execute(
                    "UPDATE sessions SET state='paused',pause_reason='allowance_exhausted' "
                    "WHERE session_id=? AND owner_token=? AND state='enabled'",
                    (session_id, token),
                )
                now = time.time()
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='allowance_exhausted' WHERE session_id=? AND owner_token=? AND status='queued'",
                    (now, session_id, token),
                )
                _prune(conn, session_id)
            self.store.transaction(pause)
        except StoreError:
            self._set_storage_latch(session_id, "allowance pause could not be saved")
        self._deactivate_local(session_id, cancel_active=False, unavailable_reason="allowance_exhausted")
        self._local_disabled.add(session_id)

    def _mark_attempt_skipped(self, work: _Work, reason: str) -> None:
        now = time.time()
        try:
            def skip(conn: Any) -> None:
                conn.execute(
                    "UPDATE attempts SET status='skipped',terminal_at=?,error_code=?,error_text=? "
                    "WHERE attempt_id=? AND status='queued'",
                    (now, reason, "newer captured answer replaced this pending turn", work.attempt_id),
                )
                _prune(conn, work.session_id)
            self.store.transaction(skip)
        except StoreError:
            self._set_storage_latch(work.session_id, "coalescing could not be saved")

    # ---- Read actions, ratings, reset, lifecycle ---------------------------

    def status(self, session_id: str) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        try:
            row = self._load_session(session_id, token)
            config = self.store.read(lambda conn: dict(conn.execute(
                "SELECT revision,settings FROM profile_config WHERE singleton=1"
            ).fetchone()))
            attempts = self.store.read(lambda conn: [dict(item) for item in conn.execute(
                "SELECT attempt_id,status,outcome,requested_provider,requested_model,served_provider,"
                "served_model,usage_known,usage_json,error_code,error_text,cancellation_pending,turn_id,created_at,terminal_at "
                "FROM attempts WHERE session_id=? ORDER BY created_at DESC,attempt_id DESC LIMIT 1",
                (session_id,),
            ).fetchall()])
        except StoreError:
            return {
                "session_id": session_id, "state": "storage_error", "storage_error": True,
                "volatile_error": self._volatile_errors.get(session_id, "status could not be read"),
            }
        with self._runtime_lock:
            latch = self._storage_latches.get(session_id)
            if latch:
                state, reason = "paused", "storage_error"
            elif session_id in self._local_disabled and row["state"] == "enabled":
                state, reason = "disabled", "local_fail_closed"
            else:
                state, reason = row["state"], row["pause_reason"]
            entry = self._get_live_cache(session_id)
            if state != "enabled":
                available, unavailable = False, reason or state
            elif latch:
                available, unavailable = False, "storage_error"
            elif entry is None:
                available, unavailable = False, self._cache_unavailable_reason(session_id)
            else:
                available, unavailable = True, None
            settings = json.loads(row["applied_settings"])
            saved_settings = json.loads(config["settings"])
            return {
                "session_id": session_id,
                "surface": row["surface"],
                "surface_supported": row["surface"] in self.SUPPORTED_SURFACES,
                "state": state,
                "pause_reason": reason,
                "saved_profile_revision": config["revision"],
                "saved_settings": saved_settings,
                "applied_revision": row["applied_revision"],
                "applied_generation": row["applied_generation"],
                "settings": settings,
                "configuration_error": "no_model" if not settings.get("route") else None,
                "invocations_used": row["invocation_count"],
                "invocations_remaining": max(0, settings["session_allowance"] - row["invocation_count"]),
                "review_now_available": available,
                "review_now_unavailable_reason": unavailable,
                "review_now_expires_in_seconds": max(0, int(entry.expires_mono - time.monotonic())) if entry else None,
                "active": session_id in self._active,
                "unconfirmed_transport": session_id in self._unconfirmed,
                "pending": session_id in self._pending,
                "storage_error": bool(latch),
                "volatile_error": self._volatile_errors.get(session_id),
                "latest_attempt": _public_attempt(attempts[0]) if attempts else None,
            }

    def notes(self, session_id: str) -> list[dict[str, Any]]:
        token = self._assert_local_owned(session_id)
        try:
            self._load_session(session_id, token)
            def collect(conn: Any) -> list[dict[str, Any]]:
                attempts = conn.execute(
                    "SELECT * FROM attempts WHERE session_id=? ORDER BY COALESCE(terminal_at,created_at) DESC,attempt_id DESC LIMIT ?",
                    (session_id, self.RETENTION_PER_SESSION),
                ).fetchall()
                result = []
                for attempt in attempts:
                    findings = conn.execute(
                        "SELECT note_id,severity,explanation,evidence_refs_json,next_step FROM findings "
                        "WHERE attempt_id=? ORDER BY ordinal",
                        (attempt["attempt_id"],),
                    ).fetchall()
                    rating = conn.execute(
                        "SELECT rating,reason FROM ratings WHERE attempt_id=?", (attempt["attempt_id"],)
                    ).fetchone()
                    item = _public_attempt(dict(attempt))
                    item["coverage"] = json.loads(attempt["coverage_json"])
                    item["findings"] = [{
                        "note_id": f["note_id"], "severity": f["severity"],
                        "explanation": f["explanation"],
                        "evidence_refs": json.loads(f["evidence_refs_json"]),
                        "next_step": f["next_step"],
                    } for f in findings]
                    item["rating"] = dict(rating) if rating else None
                    result.append(item)
                return result
            return self.store.read(collect)
        except StoreError as exc:
            self._set_storage_latch(session_id, "notes could not be read")
            raise ServiceError("stored notes could not be read") from exc

    def rate(self, session_id: str, attempt_id: str, rating: str, reason: str | None = None) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        _validate_identifier(attempt_id, "attempt_id", max_chars=128)
        if not isinstance(rating, str) or rating not in {"useful", "not_useful", "not_rated"}:
            raise ValueError("rating must be useful, not_useful, or not_rated")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 500):
            raise ValueError("reason must be at most 500 code points")
        safe_reason = _safe_model_text(reason, 500) if reason else None
        now = time.time()
        try:
            def save(conn: Any) -> None:
                session = conn.execute("SELECT owner_token FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                attempt = conn.execute("SELECT session_id,status,cancellation_pending FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if session is None or session["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                if attempt is None or attempt["session_id"] != session_id:
                    raise ValueError("attempt was pruned or does not belong to this session")
                if attempt["status"] != "completed" or attempt["cancellation_pending"]:
                    raise ValueError("only settled completed reviews can be rated")
                conn.execute(
                    "INSERT INTO ratings(attempt_id,session_id,rating,reason,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(attempt_id) DO UPDATE SET rating=excluded.rating,reason=excluded.reason,updated_at=excluded.updated_at",
                    (attempt_id, session_id, rating, safe_reason, now),
                )
            self.store.transaction(save)
        except StoreError as exc:
            self._set_storage_latch(session_id, "rating could not be saved")
            raise ServiceError("rating could not be saved") from exc
        return {"attempt_id": attempt_id, "rating": rating, "reason": safe_reason, "saved": True}

    def reset_allowance(self, session_id: str, confirm: bool) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        if confirm is not True:
            raise ValueError("reset requires confirm=True")
        now = time.time()
        try:
            def reset(conn: Any) -> dict[str, Any]:
                row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if row is None or row["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                conn.execute(
                    "UPDATE sessions SET invocation_count=0,state='paused',pause_reason='allowance_reset',last_seen=? "
                    "WHERE session_id=? AND owner_token=?",
                    (now, session_id, token),
                )
                conn.execute(
                    "UPDATE attempts SET status='cancelled',terminal_at=?,cancellation_pending=0,"
                    "error_code='allowance_reset' WHERE session_id=? AND owner_token=? AND status='queued'",
                    (now, session_id, token),
                )
                _prune(conn, session_id)
                return dict(conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone())
            row = self.store.transaction(reset)
        except (OwnerUnavailable, StoreError) as exc:
            if isinstance(exc, StoreError):
                self._set_storage_latch(session_id, "allowance reset could not be saved")
            raise ServiceError(str(exc)) from exc
        self._deactivate_local(session_id, cancel_active=False)
        with self._runtime_lock:
            self._suspend_lock_if_idle_locked(session_id)
        return _session_status(row)

    def feedback(self, session_id: str, note_ids: Sequence[str]) -> dict[str, Any]:
        token = self._assert_local_owned(session_id)
        if not isinstance(note_ids, Sequence) or isinstance(note_ids, (str, bytes, bytearray)):
            raise ValueError("note_ids must be an array of IDs")
        if not 1 <= len(note_ids) <= 100 or any(not isinstance(item, str) for item in note_ids):
            raise ValueError("select one to 100 note IDs")
        if len(set(note_ids)) != len(note_ids):
            raise ValueError("note IDs must be distinct")
        try:
            def load(conn: Any) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
                owner = conn.execute("SELECT owner_token FROM sessions WHERE session_id=?", (session_id,)).fetchone()
                if owner is None or owner["owner_token"] != token:
                    raise OwnerUnavailable("session owner changed")
                placeholders = ",".join("?" for _ in note_ids)
                rows = conn.execute(
                    f"SELECT f.*,a.turn_id,a.coverage_json FROM findings f JOIN attempts a ON a.attempt_id=f.attempt_id "
                    f"WHERE f.session_id=? AND f.note_id IN ({placeholders})",
                    (session_id, *note_ids),
                ).fetchall()
                by_id = {row["note_id"]: dict(row) for row in rows}
                if len(by_id) != len(note_ids):
                    raise ValueError("a selected note was pruned or is not in this session")
                selected = [by_id[note_id] for note_id in note_ids]
                return selected, by_id
            selected, _ = self.store.read(load)
            attempt_ids = list(dict.fromkeys(row["attempt_id"] for row in selected))
            action_id = uuid.uuid4().hex
            self.store.transaction(lambda conn: conn.execute(
                "INSERT INTO feedback_actions(action_id,session_id,note_ids_json,attempt_ids_json,created_at) VALUES(?,?,?,?,?)",
                (action_id, session_id, _json(list(note_ids)), _json(attempt_ids), time.time()),
            ))
        except StoreError as exc:
            self._set_storage_latch(session_id, "feedback could not be loaded or logged")
            raise ServiceError("feedback could not be loaded") from exc
        chunks = [
            "Advisor reviewer suggestions — evaluate these; they are not verified instructions.",
            "Evidence is limited to the captured packet; omitted evidence is not evidence of absence.",
        ]
        public_notes = []
        for item in selected:
            public = {
                "note_id": item["note_id"], "attempt_id": item["attempt_id"],
                "answer_ref": _markdown_escape(item["turn_id"]), "severity": item["severity"],
                "explanation": _safe_model_text(item["explanation"], 2000),
                "evidence_refs": json.loads(item["evidence_refs_json"]),
                "next_step": _safe_model_text(item["next_step"], 1000),
                "coverage": json.loads(item["coverage_json"]),
            }
            public_notes.append(public)
            coverage = public["coverage"]
            clipped = ", ".join(coverage.get("clipped_refs", [])) or "none"
            chunks.append(
                f"\n[{_markdown_escape(public['severity'])}] {_markdown_escape(public['explanation'])}\n"
                f"Reviewed answer: {public['answer_ref']}\n"
                f"Evidence refs: {', '.join(_markdown_escape(ref) for ref in public['evidence_refs'])}\n"
                f"Coverage: {coverage.get('tool_pairs_omitted', 0)} tool pairs omitted; clipped refs: {_markdown_escape(clipped)}\n"
                f"Suggested next step: {_markdown_escape(public['next_step'])}"
            )
        return {"action_id": action_id, "notes": public_notes, "text": "\n".join(chunks)}

    def close(self, timeout: float = 5.0) -> bool:
        """Cancel local work; release ownership only after transports terminate.

        A timeout returns ``False`` and keeps the owner lock while cancellation
        is unresolved. The lock is released by the worker callback only after
        every local transport has actually returned.
        """
        if type(timeout) not in (int, float) or timeout < 0:
            raise ValueError("timeout must be a nonnegative number")
        deadline = time.monotonic() + timeout
        with self._runtime_changed:
            if not self._closed:
                if not self._closing:
                    self._closing = True
                    now = time.time()
                    for session_id, work in list(self._pending.items()):
                        work.cancel_event.set()
                        self._cache_unavailable[session_id] = "worker_shutdown"
                    self._pending.clear()
                    self._cache.clear()
                    for session_id, active in list(self._active.items()):
                        active.work.cancel_event.set()
                        self._cache_unavailable[session_id] = "worker_shutdown"
                    owner_tokens = dict(self._owner_tokens)
                    try:
                        def shutdown(conn: Any) -> None:
                            for sid, own in owner_tokens.items():
                                conn.execute(
                                    "UPDATE sessions SET state='disabled',pause_reason='worker_shutdown',"
                                    "applied_generation=applied_generation+1 WHERE session_id=? AND owner_token=?",
                                    (sid, own),
                                )
                                conn.execute(
                                    "UPDATE attempts SET status='cancelled',terminal_at=?,error_code='service_closing',"
                                    "cancellation_pending=0 WHERE session_id=? AND owner_token=? AND status='queued'",
                                    (now, sid, own),
                                )
                                conn.execute(
                                    "UPDATE attempts SET status='cancelled',terminal_at=?,error_code='service_closing',"
                                    "cancellation_pending=1 WHERE session_id=? AND owner_token=? AND status='running'",
                                    (now, sid, own),
                                )
                                _prune(conn, sid)
                        self.store.transaction(shutdown)
                    except StoreError:
                        for session_id in owner_tokens:
                            self._set_storage_latch(session_id, "shutdown state could not be saved")
                while self._active or self._unconfirmed:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        self._release_when_idle = True
                        return False
                    self._runtime_changed.wait(remaining)
                self._release_all_locks_locked()
                self._closed = True
        # Joining under the Service lock could block worker completion callbacks.
        # Give idle workers the remaining budget to exit outside that lock.
        self._executor.shutdown(
            wait=True, timeout=max(0, deadline - time.monotonic()), cancel_futures=True)
        # Ownership is the close contract: once every transport has settled and
        # the owner locks are released, report it, even if an idle daemon worker
        # has not yet been observed exiting (it holds no lock, slot or work).
        with self._runtime_lock:
            return self._closed and not self._active and not self._unconfirmed

    # ---- Internal state helpers -------------------------------------------

    def _assert_local_owned(self, session_id: str) -> str:
        _validate_identifier(session_id, "session_id", max_chars=256)
        with self._runtime_lock:
            self._ensure_open()
            token = self._owner_tokens.get(session_id)
            if token is None or session_id not in self._locks:
                raise SessionNotDiscovered("session must be discovered by the verified adapter first")
            return token

    def _load_session(self, session_id: str, token: str) -> dict[str, Any]:
        row = self.store.read(lambda conn: conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone())
        if row is None or row["owner_token"] != token:
            raise OwnerUnavailable("durable session ownership does not match this worker")
        return dict(row)

    def _work_still_valid(self, work: _Work) -> bool:
        try:
            row = self._load_session(work.session_id, work.owner_token)
        except StoreError:
            self._set_storage_latch(work.session_id, "pre-call validity check could not read storage")
            return False
        except Exception:
            return False
        return (
            row["state"] == "enabled"
            and row["applied_generation"] == work.applied_generation
            and work.session_id not in self._local_disabled
            and work.session_id not in self._storage_latches
        )

    def _purge_expired_cache_locked(self) -> None:
        now = time.monotonic()
        for expired in [sid for sid, item in self._cache.items() if now >= item.expires_mono]:
            del self._cache[expired]
            self._cache_unavailable[expired] = "expired"

    def _add_cache(self, session_id: str, entry: _CacheEntry) -> None:
        self._purge_expired_cache_locked()
        self._cache.pop(session_id, None)
        self._cache[session_id] = entry
        self._cache_unavailable.pop(session_id, None)
        while len(self._cache) > self.SNAPSHOT_CACHE_SIZE:
            evicted, _ = self._cache.popitem(last=False)
            self._cache_unavailable[evicted] = "evicted"

    def _get_live_cache(self, session_id: str) -> _CacheEntry | None:
        self._purge_expired_cache_locked()
        entry = self._cache.get(session_id)
        if entry is not None and time.monotonic() >= entry.expires_mono:
            self._cache.pop(session_id, None)
            self._cache_unavailable[session_id] = "expired"
            return None
        return entry

    def _cache_unavailable_reason(self, session_id: str) -> str:
        return self._cache_unavailable.get(session_id, "no_captured_answer")

    def _deactivate_local(
        self, session_id: str, *, cancel_active: bool, unavailable_reason: str = "disabled"
    ) -> None:
        with self._runtime_lock:
            self._cache.pop(session_id, None)
            self._cache_unavailable[session_id] = unavailable_reason
            pending = self._pending.pop(session_id, None)
            if pending is not None:
                pending.cancel_event.set()
            if cancel_active:
                active = self._active.get(session_id)
                if active is not None:
                    active.work.cancel_event.set()
            self._local_disabled.add(session_id)
            callbacks = tuple(self._invalidation_callbacks)
        # Never call plugin code while holding the service runtime lock.
        for callback in callbacks:
            try:
                callback(session_id)
            except Exception:
                continue

    def subscribe_invalidation(self, callback: Any):
        """Register a plugin callback for session-scoped privacy invalidations."""
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._runtime_lock:
            self._invalidation_callbacks.add(callback)

        def unsubscribe() -> None:
            with self._runtime_lock:
                self._invalidation_callbacks.discard(callback)
        return unsubscribe

    def _invalidate_local(self, session_id: str, *, cancel_active: bool) -> None:
        self._deactivate_local(session_id, cancel_active=cancel_active, unavailable_reason="configuration_changed")

    def _clear_cache_and_pending(self, session_id: str, *, cancel_active: bool) -> None:
        self._deactivate_local(session_id, cancel_active=cancel_active, unavailable_reason="allowance_exhausted")

    def _suspend_lock_if_idle_locked(self, session_id: str) -> None:
        """Drop a session's flock once it is neither enabled nor running work.

        Every transition out of ``enabled`` (explicit disable, configuration
        change, allowance reset, or allowance exhaustion at reservation) leaves
        a session that executes nothing, so it must not keep a descriptor plus
        exclusive flock: otherwise a process reviewing many one-call sessions
        accumulates one fd/flock per paused session for its whole life.
        ``enable()`` re-takes the flock. Caller holds the runtime lock; an
        in-flight or unconfirmed transport keeps the lock until it settles.
        """
        if session_id in self._active or session_id in self._unconfirmed or session_id in self._pending:
            return
        if session_id not in self._local_disabled:
            return
        lock = self._locks.get(session_id)
        if lock is not None:
            lock.suspend()

    def _release_all_locks_locked(self) -> None:
        for lock in self._locks.values():
            lock.release()
        self._locks.clear()
        self._owner_tokens.clear()
        # Ownership was released through the normal path, so disarm the
        # drop-time safety net; detach also frees the lock dict it retained.
        finalizer = getattr(self, "_lock_finalizer", None)
        if finalizer is not None:
            finalizer.detach()

    def _ensure_open(self) -> None:
        if self._closed:
            raise ServiceError("service is closed")


def _validate_identifier(value: Any, name: str, *, max_chars: int) -> None:
    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise ValueError(f"{name} must be a nonempty string of at most {max_chars} code points")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} must not contain terminal control characters")


def _discovery_result(row: Mapping[str, Any], *, recovered: bool) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "surface": row["surface"],
        "state": row["state"], "recovered": recovered,
        "applied_revision": row["applied_revision"],
        "applied_generation": row["applied_generation"],
    }


def _session_status(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "session_id": row["session_id"], "state": row["state"],
        "pause_reason": row["pause_reason"], "applied_revision": row["applied_revision"],
        "applied_generation": row["applied_generation"],
        "invocations_used": row["invocation_count"],
    }


def _invalidate_attempts(conn: Any, session_id: str, owner_token: str, now: float, reason: str) -> None:
    conn.execute(
        "UPDATE attempts SET status='cancelled',terminal_at=?,error_code=?,error_text=NULL "
        "WHERE session_id=? AND owner_token=? AND status='queued'",
        (now, reason, session_id, owner_token),
    )
    conn.execute(
        "UPDATE attempts SET status='cancelled',terminal_at=?,error_code=?,error_text=NULL,cancellation_pending=1 "
        "WHERE session_id=? AND owner_token=? AND status='running'",
        (now, reason, session_id, owner_token),
    )
    _prune(conn, session_id)


def _prune(conn: Any, session_id: str) -> None:
    settled = conn.execute(
        "SELECT attempt_id FROM attempts WHERE session_id=? AND terminal_at IS NOT NULL "
        "AND cancellation_pending=0 AND status IN ('completed','failed','timed_out','cancelled','skipped','abandoned') "
        "ORDER BY terminal_at DESC,attempt_id DESC LIMIT -1 OFFSET 100",
        (session_id,),
    ).fetchall()
    doomed = {row["attempt_id"] for row in settled}
    if not doomed:
        return
    placeholders = ",".join("?" for _ in doomed)
    note_rows = conn.execute(
        f"SELECT note_id FROM findings WHERE attempt_id IN ({placeholders})", tuple(doomed)
    ).fetchall()
    note_ids = {row["note_id"] for row in note_rows}
    actions = conn.execute("SELECT action_id,note_ids_json,attempt_ids_json FROM feedback_actions WHERE session_id=?", (session_id,)).fetchall()
    for action in actions:
        action_notes = set(json.loads(action["note_ids_json"]))
        action_attempts = set(json.loads(action["attempt_ids_json"]))
        if action_notes & note_ids or action_attempts & doomed:
            conn.execute("DELETE FROM feedback_actions WHERE action_id=?", (action["action_id"],))
    conn.execute(f"DELETE FROM findings WHERE attempt_id IN ({placeholders})", tuple(doomed))
    conn.execute(f"DELETE FROM ratings WHERE attempt_id IN ({placeholders})", tuple(doomed))
    conn.execute(f"DELETE FROM attempts WHERE attempt_id IN ({placeholders})", tuple(doomed))


def _public_attempt(row: Mapping[str, Any]) -> dict[str, Any]:
    outcome = row.get("outcome")
    if isinstance(outcome, str):
        try:
            outcome = json.loads(outcome)
        except json.JSONDecodeError:
            outcome = None
    if isinstance(outcome, Mapping):
        raw_outcome = outcome.get("outcome")
        safe_outcome = {"outcome": raw_outcome if raw_outcome in {"findings", "no_findings", "insufficient_evidence"} else None,
                        "findings": []}
        raw_findings = outcome.get("findings")
        if isinstance(raw_findings, list):
            for finding in raw_findings:
                if not isinstance(finding, Mapping):
                    continue
                safe_outcome["findings"].append({
                    "severity": finding.get("severity") if finding.get("severity") in {"nit", "concern", "blocker"} else None,
                    "explanation": _safe_model_text(finding["explanation"], 2000)
                        if isinstance(finding.get("explanation"), str) else "",
                    "evidence_refs": [_safe_model_text(ref, 256) for ref in finding.get("evidence_refs", [])
                                      if isinstance(ref, str)][:8]
                        if isinstance(finding.get("evidence_refs"), list) else [],
                    "next_step": _safe_model_text(finding["next_step"], 1000)
                        if isinstance(finding.get("next_step"), str) else "",
                })
        outcome = safe_outcome
    else:
        outcome = None
    usage = None
    if row.get("usage_known") and row.get("usage_json"):
        try:
            usage = json.loads(row["usage_json"])
        except (json.JSONDecodeError, TypeError):
            usage = None
    return {
        "attempt_id": row.get("attempt_id"),
        "turn_id": row.get("turn_id"),
        "status": row.get("status"),
        "outcome": outcome,
        "requested_route": {"provider": row.get("requested_provider"), "model": row.get("requested_model")},
        "served_route": {"provider": row.get("served_provider"), "model": row.get("served_model")},
        "usage": usage,
        "usage_known": bool(row.get("usage_known")),
        "cancellation_pending": bool(row.get("cancellation_pending")),
        "created_at": row.get("created_at"),
        "terminal_at": row.get("terminal_at"),
        "error_code": row.get("error_code"),
        "error": row.get("error_text"),
    }


def _normalize_reviewer_result(value: Any) -> ReviewerResult:
    if isinstance(value, ReviewerResult):
        return value
    if isinstance(value, Mapping):
        if set(value) == {"outcome", "findings"}:
            return ReviewerResult(response=value)
        allowed = {"response", "provider", "model", "usage", "usage_known"}
        if "response" in value and set(value).issubset(allowed):
            return ReviewerResult(
                response=value["response"], provider=value.get("provider"), model=value.get("model"),
                usage=value.get("usage"), usage_known=value.get("usage_known"),
            )
    raise ReviewerResultError("reviewer result must be a structured result or ReviewerResult envelope")


def _safe_attribution(value: Any) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 256:
        return None
    return sanitize_text(value, max_chars=256)


def _clean_usage(value: Mapping[str, Any] | None, explicitly_known: bool | None) -> tuple[bool, dict[str, Any] | None]:
    if explicitly_known is not None and type(explicitly_known) is not bool:
        return False, None
    if not isinstance(value, Mapping) or explicitly_known is False:
        return False, None
    cleaned: dict[str, Any] = {}
    secret_parts = ("password", "passwd", "secret", "authorization", "cookie", "credential", "apikey", "accesstoken", "refreshtoken")
    for index, (key, item) in enumerate(value.items()):
        if index >= 32 or not isinstance(key, str) or len(key) > 64:
            return False, None
        canonical = re.sub(r"[^a-z0-9]", "", key.casefold())
        if any(part in canonical for part in secret_parts):
            return False, None
        safe_key = sanitize_text(key, max_chars=64)
        if type(item) is int and item >= 0:
            cleaned[safe_key] = item
        elif type(item) is float and item >= 0 and item == item and item != float("inf"):
            cleaned[safe_key] = item
        elif isinstance(item, str) and len(item) <= 128:
            cleaned[safe_key] = _safe_model_text(item, 128)
        else:
            return False, None
    # Facades may normalize unreported counters to zero, so only a positive
    # measurement is evidence of known usage. An empty or all-zero mapping stays
    # unknown instead of claiming proven zero use or an invented zero price.
    if not any(type(item) in (int, float) and item > 0 for item in cleaned.values()):
        return False, None
    return True, cleaned


_A21_HTML_URI = re.compile(
    r"<\s*/?\s*[a-z][^>]*>"                      # any HTML tag (script, img onerror, a href, …)
    r"|&\s*#\d+;?|&\s*[a-z]+\s*;|&#x[0-9a-f]+;?"  # entity-encoded payloads
    r"|\b(?:data|javascript|vbscript|livescript|mocha)\s*:\s*[^\s\"'`<>]*(?:\s|\b|$)",
    re.IGNORECASE,
)


def _safe_model_text(value: str, max_chars: int) -> str:
    redacted = sanitize_text(value, max_chars=max_chars)
    return "".join(
        char if char == "\n" or (32 <= ord(char) != 127 and not 0x80 <= ord(char) <= 0x9F) else "�"
        for char in _A21_HTML_URI.sub(" ", redacted)
    )


def _markdown_escape(value: str) -> str:
    cleaned = _safe_model_text(value, max(1, len(value))).replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|<>])", lambda match: "\\" + match.group(1), cleaned)


def _validate_captured_packet(packet: CapturedPacket, cap: int) -> CapturedPacket:
    if not isinstance(packet, CapturedPacket) or packet.byte_count > cap:
        raise PacketError("snapshot is not a bounded CapturedPacket")
    try:
        payload = json.loads(packet.serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PacketError("snapshot serialization is invalid") from exc
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    import hashlib
    if canonical != packet.serialized or hashlib.sha256(packet.serialized.encode("utf-8")).hexdigest() != packet.digest:
        raise PacketError("snapshot digest or serialization is invalid")
    if not isinstance(payload, dict) or set(payload) != {"version", "evidence", "coverage"} or payload["version"] != 1:
        raise PacketError("snapshot shape is invalid")
    evidence = payload["evidence"]
    coverage = payload["coverage"]
    if not isinstance(evidence, list) or len(evidence) < 2 or not isinstance(coverage, dict):
        raise PacketError("snapshot evidence or coverage is invalid")
    expected_refs = []
    for index, item in enumerate(evidence):
        if not isinstance(item, dict) or set(item) != {"ref", "kind", "text", "clipped"}:
            raise PacketError("snapshot evidence item is invalid")
        if not isinstance(item["ref"], str) or not isinstance(item["text"], str) or type(item["clipped"]) is not bool:
            raise PacketError("snapshot evidence item types are invalid")
        if sanitize_text(item["text"], max_chars=len(item["text"])) != item["text"]:
            raise PacketError("snapshot contains an unredacted credential pattern")
        if index == 0 and (item["ref"], item["kind"]) != ("request", "request"):
            raise PacketError("snapshot request is missing")
        if index == 1 and (item["ref"], item["kind"]) != ("answer", "answer"):
            raise PacketError("snapshot answer is missing")
        expected_refs.append(item["ref"])
    if tuple(expected_refs) != packet.references:
        raise PacketError("snapshot references do not match serialized evidence")
    expected_coverage_keys = {
        "request_clipped", "answer_clipped", "tool_pairs_total", "tool_pairs_included",
        "tool_pairs_omitted", "upstream_omitted_tool_pairs", "clipped_refs",
    }
    if set(coverage) != expected_coverage_keys or not isinstance(coverage["clipped_refs"], list):
        raise PacketError("snapshot coverage markers are invalid")
    if any(type(coverage[key]) is not bool for key in ("request_clipped", "answer_clipped")):
        raise PacketError("snapshot coverage marker types are invalid")
    if any(type(coverage[key]) is not int or coverage[key] < 0 for key in (
        "tool_pairs_total", "tool_pairs_included", "tool_pairs_omitted", "upstream_omitted_tool_pairs"
    )):
        raise PacketError("snapshot coverage counts are invalid")
    if any(not isinstance(ref, str) or ref not in expected_refs for ref in coverage["clipped_refs"]):
        raise PacketError("snapshot clipped references are invalid")
    if packet.coverage_dict() != coverage:
        raise PacketError("snapshot coverage metadata does not match")
    return packet


def _omitted_count(coverage: Mapping[str, Any]) -> int:
    if not isinstance(coverage, Mapping) or set(coverage) != {"omitted_tool_pairs"}:
        raise PacketError("coverage must contain omitted_tool_pairs only")
    value = coverage["omitted_tool_pairs"]
    if type(value) is not int or value < 0:
        raise PacketError("omitted_tool_pairs must be a nonnegative integer")
    return value


class _InvocationCancelled(Exception):
    def __init__(self, abandoned: bool = False) -> None:
        super().__init__()
        self.abandoned = abandoned
        self.task: Any = None


class _InvocationTimedOut(Exception):
    def __init__(self, abandoned: bool = False) -> None:
        super().__init__()
        self.abandoned = abandoned
        self.task: Any = None


_UNRESOLVED_MESSAGE = "cancellation unresolved; transport still running after grace"


class _NoResponse:
    pass


_PROCESS_CALL_SLOTS = threading.BoundedSemaphore(2)
_NO_RESPONSE = _NoResponse()
_PRECALL_CANCELLED = object()
_PRECALL_TIMED_OUT = object()

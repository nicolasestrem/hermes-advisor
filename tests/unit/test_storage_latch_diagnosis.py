"""Instrumented, real-SQLite reproducer for the historical burst storage latch.

This is an offline fixture: the reviewer is a controlled coroutine, every
store is a fresh temporary SQLite database, and no provider or host API is used.
The public SQLiteStore boundary is subclassed only to record raised exceptions;
every exception is immediately re-raised unchanged so fail-closed behavior is
not weakened.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import threading
import time
import traceback
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import advisor_core.service as service_module
from advisor_core import Service, StoreError
from advisor_core.store import SQLiteStore

REPO_ROOT = Path(__file__).resolve().parents[2]
from tests._scratch_root import SCRATCH
SCRATCH.mkdir(parents=True, exist_ok=True)
ROUTE = {"provider": "nous", "model": "local-controlled-storage-latch-fixture"}
RUNS = 30
SESSION_COUNT = 6
TURNS_PER_SESSION = 8


def no_findings():
    return {"outcome": "no_findings", "findings": []}


def _exception_chain(exc: BaseException) -> list[dict[str, str]]:
    chain = []
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append({"type": type(current).__name__, "message": str(current)})
        current = current.__cause__ or current.__context__
    return chain


def _attempt_db_snapshot(data_dir: str) -> dict:
    connection = sqlite3.connect(Path(data_dir) / "advisor.sqlite3", timeout=2.0)
    connection.row_factory = sqlite3.Row
    try:
        sessions = [dict(row) for row in connection.execute(
            "SELECT session_id,state,pause_reason,invocation_count FROM sessions ORDER BY session_id"
        )]
        attempts = [dict(row) for row in connection.execute(
            "SELECT session_id,attempt_id,turn_id,status,error_code,cancellation_pending,terminal_at "
            "FROM attempts ORDER BY session_id,created_at,attempt_id"
        )]
    finally:
        connection.close()
    return {
        "sessions": sessions,
        "attempts": attempts,
        "status_counts": dict(sorted(Counter(row["status"] for row in attempts).items())),
        "unsettled_attempts": [
            {"session_id": row["session_id"], "attempt_id": row["attempt_id"],
             "status": row["status"], "terminal_at": row["terminal_at"]}
            for row in attempts if row["terminal_at"] is None
        ],
    }


def _run_burst(run_number: int) -> dict:
    started_monotonic = time.monotonic()
    store_errors: list[dict] = []
    store_operations: list[dict] = []
    store_errors_lock = threading.Lock()
    operation_sequence = {"next": 0}

    class ObservedSQLiteStore(SQLiteStore):
        """Public store subclass that records and then re-raises store errors."""

        def _record_operation(self, operation: str, callback, started: float, exc=None) -> None:
            finished = time.monotonic()
            with store_errors_lock:
                operation_sequence["next"] += 1
                event = {
                    "operation_id": operation_sequence["next"],
                    "operation": operation,
                    "callback": getattr(callback, "__name__", None),
                    "thread": threading.current_thread().name,
                    "start_s": round(started - started_monotonic, 6),
                    "end_s": round(finished - started_monotonic, 6),
                    "elapsed_s": round(finished - started, 6),
                }
                store_operations.append(event)
                if exc is not None:
                    event["exception_chain"] = _exception_chain(exc)
                    event["exception_traceback"] = "".join(traceback.format_exception(exc))
                    store_errors.append(event)

        def __init__(self, data_dir):
            try:
                super().__init__(data_dir)
            except BaseException as exc:
                self._record_operation("initialize", None, started_monotonic, exc)
                raise

        def read(self, callback):
            started = time.monotonic()
            try:
                result = super().read(callback)
            except BaseException as exc:
                self._record_operation("read", callback, started, exc)
                raise
            self._record_operation("read", callback, started)
            return result

        def transaction(self, callback):
            started = time.monotonic()
            try:
                result = super().transaction(callback)
            except BaseException as exc:
                self._record_operation("transaction", callback, started, exc)
                raise
            self._record_operation("transaction", callback, started)
            return result

        def acquire(self, session_id):
            started = time.monotonic()
            try:
                result = super().acquire(session_id)
            except BaseException as exc:
                self._record_operation("acquire", None, started, exc)
                raise
            self._record_operation("acquire", None, started)
            return result

    temp = tempfile.TemporaryDirectory(dir=SCRATCH, prefix=f"storage-latch-{run_number:02d}-")
    data_dir = temp.name
    lock = threading.Lock()
    live = {"all": 0, "peak_all": 0, "per_session": {}, "peak_per_session": 0}
    calls: list[dict[str, str]] = []
    capture_rejections: list[dict] = []
    capture_exceptions: list[dict] = []
    service = None
    run_record: dict = {
        "run": run_number,
        "data_dir_label": Path(data_dir).name,
        "store_exceptions": store_errors,
        "capture_rejections": capture_rejections,
        "capture_exceptions": capture_exceptions,
    }

    async def reviewer(packet, settings):
        evidence = packet["evidence"][0]["text"]
        session_id = evidence.split(":", 1)[0]
        with lock:
            calls.append({"session_id": session_id, "evidence": evidence})
            live["all"] += 1
            live["per_session"][session_id] = live["per_session"].get(session_id, 0) + 1
            live["peak_all"] = max(live["peak_all"], live["all"])
            live["peak_per_session"] = max(
                live["peak_per_session"], live["per_session"][session_id]
            )
        try:
            await asyncio.sleep(0.03)
            return no_findings()
        finally:
            with lock:
                live["all"] -= 1
                live["per_session"][session_id] -= 1

    try:
        with mock.patch.object(service_module, "SQLiteStore", ObservedSQLiteStore):
            service = Service(data_dir, reviewer)
            names = [f"burst-{index}" for index in range(SESSION_COUNT)]
            for name in names:
                service.discover(name, "cli")
                current_revision = service.status(name)["saved_profile_revision"]
                service.configure(name, {"route": ROUTE, "session_allowance": 20}, current_revision)
                service.enable(name)

            samples = {"pool": 0, "pending": 0, "active": 0}
            sample_lock = threading.Lock()
            stop_sampler = threading.Event()

            def sampler():
                while not stop_sampler.is_set():
                    with service._runtime_lock:
                        observed = {
                            "pool": service._executor._outstanding,
                            "pending": len(service._pending),
                            "active": len(service._active),
                        }
                    with sample_lock:
                        for key, value in observed.items():
                            samples[key] = max(samples[key], value)
                    time.sleep(0.001)

            watcher = threading.Thread(target=sampler, name=f"storage-latch-sampler-{run_number}", daemon=True)
            watcher.start()

            def feed(name):
                for turn in range(TURNS_PER_SESSION):
                    try:
                        result = service.capture(
                            name, f"turn-{turn}", f"{name}:{turn}", "controlled answer", []
                        )
                        if not result.get("accepted", False):
                            with lock:
                                capture_rejections.append({
                                    "session_id": name, "turn": turn, "result": result,
                                })
                    except BaseException as exc:
                        with lock:
                            capture_exceptions.append({
                                "session_id": name, "turn": turn,
                                "exception_chain": _exception_chain(exc),
                                "traceback": traceback.format_exc(),
                            })
                    time.sleep(0.005)

            feeder_threads = [
                threading.Thread(target=feed, args=(name,), name=f"storage-latch-feed-{run_number}-{name}")
                for name in names
            ]
            for thread in feeder_threads:
                thread.start()
            for thread in feeder_threads:
                thread.join(20)
            feeders_done = all(not thread.is_alive() for thread in feeder_threads)
            workers_done = _wait_for(lambda: not service._active and not service._pending, timeout=12.0)
            live_transport_done = _wait_for(lambda: live["all"] == 0, timeout=5.0)
            stop_sampler.set()
            watcher.join(2.0)

            with service._runtime_lock:
                runtime = {
                    "storage_latches": dict(service._storage_latches),
                    "volatile_errors": dict(service._volatile_errors),
                    "active_sessions": sorted(service._active),
                    "pending_sessions": sorted(service._pending),
                    "unconfirmed_sessions": dict(service._unconfirmed),
                }
            db = _attempt_db_snapshot(data_dir)
            sample_copy = dict(samples)
            live_copy = {
                "calls": list(calls),
                "reviewer_call_count": len(calls),
                "peak_all": live["peak_all"],
                "peak_per_session": live["peak_per_session"],
                "live_at_exit": live["all"],
            }

            session_state = {row["session_id"]: row for row in db["sessions"]}
            attempts_by_session: dict[str, list[dict]] = {name: [] for name in names}
            for attempt in db["attempts"]:
                attempts_by_session.setdefault(attempt["session_id"], []).append(attempt)
            calls_by_session = Counter(call["session_id"] for call in calls)
            reservations = {
                name: {
                    "invocations_used": session_state.get(name, {}).get("invocation_count"),
                    "reviewer_calls": calls_by_session.get(name, 0),
                    "state": session_state.get(name, {}).get("state"),
                    "pause_reason": session_state.get(name, {}).get("pause_reason"),
                    "attempt_count": len(attempts_by_session.get(name, [])),
                    "terminal_count": sum(
                        attempt["terminal_at"] is not None for attempt in attempts_by_session.get(name, [])
                    ),
                    "attempt_status_counts": dict(sorted(Counter(
                        attempt["status"] for attempt in attempts_by_session.get(name, [])
                    ).items())),
                    "attempts": attempts_by_session.get(name, []),
                }
                for name in names
            }

            failures = []
            if not feeders_done:
                failures.append("feeder_timeout")
            if not workers_done:
                failures.append("worker_drain_timeout")
            if not live_transport_done:
                failures.append("reviewer_transport_not_idle")
            if runtime["storage_latches"]:
                failures.append("storage_latch")
            if store_errors:
                failures.append("store_exception_observed")
            if capture_rejections:
                failures.append("capture_rejected")
            if capture_exceptions:
                failures.append("capture_exception")
            if runtime["active_sessions"] or runtime["pending_sessions"] or runtime["unconfirmed_sessions"]:
                failures.append("runtime_work_not_drained")
            if len(db["attempts"]) != SESSION_COUNT * TURNS_PER_SESSION:
                failures.append("attempt_count_mismatch")
            if db["unsettled_attempts"]:
                failures.append("nonterminal_attempts")
            if set(db["status_counts"]) - {"completed", "skipped"}:
                failures.append("unexpected_terminal_status")
            if db["status_counts"].get("completed", 0) != len(calls):
                failures.append("completed_call_count_mismatch")
            if db["status_counts"].get("completed", 0) + db["status_counts"].get("skipped", 0) != SESSION_COUNT * TURNS_PER_SESSION:
                failures.append("terminal_attempt_total_mismatch")
            if sum(row["invocation_count"] or 0 for row in db["sessions"]) != len(calls):
                failures.append("reservation_call_count_mismatch")
            if live["peak_all"] > 2:
                failures.append("process_concurrency_bound_exceeded")
            if live["peak_per_session"] > 1:
                failures.append("session_concurrency_bound_exceeded")
            if sample_copy["active"] > 2 or sample_copy["pool"] > 4 or sample_copy["pending"] > SESSION_COUNT:
                failures.append("queue_bound_exceeded")

            run_record.update({
                "exit_code": 0 if not failures else 1,
                "failure_reasons": failures,
                "feeders_done": feeders_done,
                "workers_done": workers_done,
                "reviewer_transport_done": live_transport_done,
                "runtime": runtime,
                "concurrency": {"observed": live_copy, "sampled_peaks": sample_copy},
                "reservations": reservations,
                "durable_state": db,
                "store_operations": list(store_operations),
            })
            run_record["store_exceptions"] = list(store_errors)
            run_record["capture_rejections"] = list(capture_rejections)
            run_record["capture_exceptions"] = list(capture_exceptions)
    except BaseException as exc:
        run_record["exit_code"] = 1
        run_record.setdefault("failure_reasons", []).append("run_exception")
        run_record["run_exception"] = {
            "exception_chain": _exception_chain(exc),
            "traceback": traceback.format_exc(),
        }
    finally:
        if service is not None:
            try:
                run_record["close_result"] = service.close(timeout=5.0)
            except BaseException as exc:
                run_record["close_exception"] = {
                    "exception_chain": _exception_chain(exc),
                    "traceback": traceback.format_exc(),
                }
                run_record["exit_code"] = 1
                run_record.setdefault("failure_reasons", []).append("close_exception")
        run_record.setdefault("exit_code", 1)
        run_record.setdefault("failure_reasons", ["incomplete_run"])
        temp.cleanup()
    return run_record


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class StorageLatchDiagnosisTests(unittest.TestCase):
    def test_controlled_threaded_burst_30_runs_records_actual_store_failures(self):
        results = [_run_burst(number) for number in range(1, RUNS + 1)]
        report = {
            "python": __import__("sys").version,
            "host_revision": os.environ.get("ADVISOR_HOST_REV"),
            "test": "tests.unit.test_storage_latch_diagnosis.StorageLatchDiagnosisTests.test_controlled_threaded_burst_30_runs_records_actual_store_failures",
            "bounded_runs_requested": RUNS,
            "runs_completed": len(results),
            "pass_count": sum(result["exit_code"] == 0 for result in results),
            "fail_count": sum(result["exit_code"] != 0 for result in results),
            "store_exception_count": sum(len(result.get("store_exceptions", [])) for result in results),
            "sqlite_lock_exception_count": sum(
                1 for result in results for event in result.get("store_exceptions", [])
                for item in event.get("exception_chain", [])
                if "sqlite" in item["type"].lower()
                and any(word in item["message"].lower() for word in ("locked", "busy", "timeout"))
            ),
            "runs": results,
        }
        output_path = os.environ.get("STORAGE_LATCH_DIAG_OUTPUT")
        if output_path:
            target = Path(output_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        failures = [result for result in results if result["exit_code"] != 0]
        self.assertEqual(
            [], failures,
            "controlled burst produced one or more diagnostic failures; full per-run "
            f"exception/reservation/state/terminal record follows:\n{json.dumps(failures, indent=2, sort_keys=True)}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

import asyncio
import multiprocessing
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from advisor_core import OwnerUnavailable, ReviewerResult, Service, ServiceError
from advisor_core.service import _prune

from tests._scratch_root import SCRATCH
SCRATCH.mkdir(parents=True, exist_ok=True)


def no_findings():
    return {"outcome": "no_findings", "findings": []}


def configure_one(service, session_id, expected_revision=0, *, allowance=20, deadline=120):
    return service.configure(session_id, {
        "route": {"provider": "nous", "model": "local-controlled-transport"},
        "session_allowance": allowance,
        "deadline_seconds": deadline,
    }, expected_revision)


def wait_for(predicate, timeout=4):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.data_dir = self.temp.name
        self.services = []

    def tearDown(self):
        for service in self.services:
            try:
                service.close(timeout=2)
            except Exception:
                pass
        self.temp.cleanup()

    def make_service(self, reviewer, **kwargs):
        service = Service(self.data_dir, reviewer, **kwargs)
        self.services.append(service)
        return service

    def test_no_model_and_unsupported_surface_fail_closed_with_zero_calls(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()
        service = self.make_service(reviewer)
        service.discover("not-configured", "cli")
        with self.assertRaisesRegex(ServiceError, "no_model"):
            service.enable("not-configured")
        service.discover("unsupported", "gateway")
        configure_one(service, "unsupported")
        with self.assertRaisesRegex(ServiceError, "unsupported_surface"):
            service.enable("unsupported")
        rejected = service.capture("unsupported", "turn", "request", "answer", [])
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["reason"], "unsupported_surface")
        self.assertEqual(calls, [])

    def test_two_global_calls_maximum_even_across_service_instances(self):
        gate = threading.Barrier(2)
        lock = threading.Lock()
        active = 0
        maximum = 0
        calls = []

        async def reviewer(packet, settings):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            calls.append(packet["evidence"][0]["text"])
            try:
                await asyncio.to_thread(gate.wait, 2)
            except threading.BrokenBarrierError:
                pass
            await asyncio.sleep(0.03)
            with lock:
                active -= 1
            return no_findings()

        first = self.make_service(reviewer)
        first.discover("s0", "cli")
        configure_one(first, "s0")
        first.enable("s0")
        # The saved profile default is inherited only by future sessions.
        second = self.make_service(reviewer)
        second.discover("s1", "desktop")
        second.enable("s1")
        third = self.make_service(reviewer)
        third.discover("s2", "cli")
        third.enable("s2")
        first.capture("s0", "t0", "zero", "answer", [])
        second.capture("s1", "t1", "one", "answer", [])
        third.capture("s2", "t2", "two", "answer", [])
        self.assertTrue(wait_for(lambda: len(calls) == 3 and active == 0))
        self.assertEqual(maximum, 2)
        self.assertEqual(len(calls), 3)

    def test_reservation_failure_calls_no_reviewer_and_requires_explicit_reenable(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()
        service = self.make_service(reviewer)
        service.discover("storage", "cli")
        configure_one(service, "storage")
        service.enable("storage")
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            database.execute(
                "CREATE TRIGGER fail_reservation BEFORE UPDATE OF invocation_count ON sessions "
                "WHEN NEW.invocation_count>OLD.invocation_count BEGIN "
                "SELECT RAISE(ABORT,'injected reservation failure'); END"
            )
            database.commit()
        finally:
            database.close()
        service.capture("storage", "t1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("storage")["storage_error"]))
        self.assertEqual(calls, [])
        self.assertEqual(service.status("storage")["state"], "paused")
        self.assertEqual(service.status("storage")["invocations_used"], 0)
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            database.execute("DROP TRIGGER fail_reservation")
            database.commit()
        finally:
            database.close()
        service.enable("storage")
        self.assertFalse(service.status("storage")["storage_error"])
        self.assertEqual(service.status("storage")["state"], "enabled")

    def test_final_persistence_failure_never_publishes_and_pauses_until_reenabled(self):
        calls = 0
        async def reviewer(packet, settings):
            nonlocal calls
            calls += 1
            return ReviewerResult(response={
                "outcome": "findings",
                "findings": [{"severity": "concern", "explanation": "Never persist this result", "evidence_refs": ["answer"], "next_step": "Verify."}],
            }, usage={"input_tokens": 1})
        service = self.make_service(reviewer)
        service.discover("commit-failure", "cli")
        configure_one(service, "commit-failure")
        service.enable("commit-failure")
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            database.execute(
                "CREATE TRIGGER fail_complete BEFORE UPDATE OF status ON attempts "
                "WHEN NEW.status='completed' BEGIN SELECT RAISE(ABORT,'injected final commit failure'); END"
            )
            database.commit()
        finally:
            database.close()
        service.capture("commit-failure", "t1", "request", "answer", [])
        def storage_failure_settled():
            status = service.status("commit-failure")
            return status["storage_error"] and not status["active"]
        self.assertTrue(wait_for(storage_failure_settled))
        self.assertEqual(service.status("commit-failure")["state"], "paused")
        self.assertEqual(service.status("commit-failure")["invocations_used"], 1)
        rows = service.notes("commit-failure")
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["error_code"], "storage_error")
        self.assertEqual(rows[0]["findings"], [])
        self.assertNotIn("Never persist this result", repr(rows))
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            database.execute("DROP TRIGGER fail_complete")
            database.commit()
        finally:
            database.close()
        service.enable("commit-failure")
        service.capture("commit-failure", "t2", "new request", "new answer", [])
        self.assertTrue(wait_for(lambda: service.status("commit-failure")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(calls, 2)

    def test_quota_failure_pauses_only_session_and_errors_do_not_echo_exception_body(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            raise RuntimeError("429 quota; echoed bearer Bearer ultraSecret123456789")
        service = self.make_service(reviewer)
        service.discover("quota", "cli")
        configure_one(service, "quota")
        service.enable("quota")
        service.discover("other", "cli")
        service.enable("other")
        service.capture("quota", "t1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("quota")["state"] == "paused"))
        attempt = service.notes("quota")[0]
        self.assertEqual(attempt["error_code"], "quota_or_auth")
        self.assertNotIn("ultraSecret", repr(attempt))
        self.assertFalse(service.capture("quota", "t2", "never processed", "answer", []) ["accepted"])
        self.assertEqual(service.status("other")["state"], "enabled")
        self.assertEqual(len(calls), 1)

    def test_close_retains_lock_while_cancellation_is_unresolved(self):
        started = threading.Event()
        release = threading.Event()
        async def reviewer(packet, settings):
            started.set()
            try:
                await asyncio.to_thread(release.wait)
            except asyncio.CancelledError:
                await asyncio.to_thread(release.wait)
                raise
        service = self.make_service(reviewer)
        service.discover("cancel", "cli")
        configure_one(service, "cancel", deadline=1)
        service.enable("cancel")
        service.capture("cancel", "t1", "request", "answer", [])
        try:
            self.assertTrue(started.wait(2))
            time.sleep(1.05)
            status = service.status("cancel")
            self.assertEqual(status["latest_attempt"]["status"], "timed_out")
            self.assertTrue(status["latest_attempt"]["cancellation_pending"])
            self.assertFalse(service.close(timeout=0.05))
            async def other_reviewer(packet, settings):
                return no_findings()
            other = Service(self.data_dir, other_reviewer)
            self.services.append(other)
            with self.assertRaises(OwnerUnavailable):
                other.discover("cancel", "cli")
        finally:
            release.set()
        self.assertTrue(wait_for(lambda: not service._active))
        self.assertTrue(service.close(timeout=2))
        info = other.discover("cancel", "cli")
        self.assertTrue(info["recovered"])
        self.assertEqual(other.notes("cancel")[0]["status"], "timed_out")
        self.assertFalse(other.notes("cancel")[0]["cancellation_pending"])

    def test_cache_is_capped_at_sixteen_captures_and_oldest_is_evicted(self):
        release = threading.Event()
        started = threading.Event()
        async def reviewer(packet, settings):
            started.set()
            await asyncio.to_thread(release.wait)
            return no_findings()
        service = self.make_service(reviewer)
        service.discover("cache-0", "cli")
        configure_one(service, "cache-0")
        service.enable("cache-0")
        for index in range(17):
            session_id = f"cache-{index}"
            if index:
                service.discover(session_id, "cli")
                service.enable(session_id)
            service.capture(session_id, f"turn-{index}", f"request-{index}", "answer", [])
        self.assertTrue(started.wait(2))
        try:
            self.assertEqual(len(service._cache), 16)
            self.assertNotIn("cache-0", service._cache)
            status = service.status("cache-0")
            self.assertFalse(status["review_now_available"])
            self.assertEqual(status["review_now_unavailable_reason"], "evicted")
        finally:
            release.set()
        self.assertTrue(wait_for(lambda: not service._active and not service._pending, timeout=8))
        self.assertTrue(all(service.status(f"cache-{index}")["latest_attempt"]["status"] == "completed" for index in range(17)))

    def test_retention_prunes_all_settled_terminal_statuses_and_linked_records_only(self):
        async def reviewer(packet, settings):
            return no_findings()
        service = self.make_service(reviewer)
        service.discover("retention", "cli")
        configure_one(service, "retention")
        owner = service._owner_tokens["retention"]
        statuses = ["completed", "failed", "timed_out", "cancelled", "skipped", "abandoned"]

        def seed(conn):
            conn.execute("UPDATE sessions SET invocation_count=7 WHERE session_id='retention'")
            for index in range(105):
                attempt_id = f"ret-{index:03d}"
                state = statuses[index % len(statuses)]
                conn.execute(
                    "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,applied_generation,"
                    "owner_token,created_at,deadline_at,terminal_at,status,coverage_json) "
                    "VALUES(?,?,?,?,1,1,?,?,?,?,?,?)",
                    (attempt_id, "retention", f"turn-{index}", f"digest-{index}", owner, index, index + 10, index, state, "{}"),
                )
                if state == "completed":
                    note_id = f"{attempt_id}-n1"
                    conn.execute(
                        "INSERT INTO findings(note_id,session_id,attempt_id,ordinal,severity,explanation,evidence_refs_json,next_step) "
                        "VALUES(?,?,?,1,'nit','safe','[\"answer\"]','verify')",
                        (note_id, "retention", attempt_id),
                    )
                    conn.execute(
                        "INSERT INTO ratings(attempt_id,session_id,rating,reason,updated_at) VALUES(?,?, 'useful',NULL,?)",
                        (attempt_id, "retention", index),
                    )
                    if index == 0:
                        conn.execute(
                            "INSERT INTO feedback_actions(action_id,session_id,note_ids_json,attempt_ids_json,created_at) "
                            "VALUES('action-old','retention',?,?,?)",
                            ('["ret-000-n1"]', '["ret-000"]', index),
                        )
            conn.execute(
                "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,applied_generation,"
                "owner_token,created_at,deadline_at,terminal_at,status,cancellation_pending,coverage_json) "
                "VALUES('pending','retention','pending-turn','pending-digest',1,1,?,200,210,200,'timed_out',1,'{}')",
                (owner,),
            )
            conn.execute(
                "INSERT INTO dedup(session_id,turn_id,packet_digest,applied_generation,attempt_id) "
                "VALUES('retention','turn-0','digest-0',1,'ret-000')"
            )
            _prune(conn, "retention")

        service.store.transaction(seed)
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            settled_count = database.execute(
                "SELECT COUNT(*) FROM attempts WHERE session_id='retention' AND cancellation_pending=0"
            ).fetchone()[0]
            pending = database.execute(
                "SELECT status,cancellation_pending FROM attempts WHERE attempt_id='pending'"
            ).fetchone()
            old_attempt = database.execute(
                "SELECT COUNT(*) FROM attempts WHERE attempt_id='ret-000'"
            ).fetchone()[0]
            old_note = database.execute(
                "SELECT COUNT(*) FROM findings WHERE note_id='ret-000-n1'"
            ).fetchone()[0]
            old_rating = database.execute(
                "SELECT COUNT(*) FROM ratings WHERE attempt_id='ret-000'"
            ).fetchone()[0]
            old_action = database.execute(
                "SELECT COUNT(*) FROM feedback_actions WHERE action_id='action-old'"
            ).fetchone()[0]
            dedup = database.execute(
                "SELECT COUNT(*) FROM dedup WHERE turn_id='turn-0'"
            ).fetchone()[0]
            count = database.execute("SELECT invocation_count FROM sessions WHERE session_id='retention'").fetchone()[0]
        finally:
            database.close()
        self.assertEqual(settled_count, 100)
        self.assertEqual(tuple(pending), ("timed_out", 1))
        self.assertEqual((old_attempt, old_note, old_rating, old_action), (0, 0, 0, 0))
        self.assertEqual(dedup, 1)
        self.assertEqual(count, 7)


if __name__ == "__main__":
    unittest.main()

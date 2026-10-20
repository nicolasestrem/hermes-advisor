"""Core bounds/ownership/recovery audit regressions (real SQLite/threads/processes).

FIXTURE LABELING: every reviewer, route, and usage value here is a synthetic
controlled fixture for deterministic fault injection. No provider request,
paid call, host import, or live runtime is exercised, and nothing here proves
remote-provider termination or billing behaviour.
"""

import asyncio
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from advisor_core import (
    ConfigurationConflict,
    OwnerUnavailable,
    Service,
    StoreError,
    build_packet,
)
from advisor_core.packet import CapturedPacket

REPO_ROOT = Path(__file__).resolve().parents[2]
HELPER = REPO_ROOT / "tests" / "unit" / "proc_helper.py"
from tests._scratch_root import SCRATCH
SCRATCH.mkdir(parents=True, exist_ok=True)
ROUTE = {"provider": "nous", "model": "local-controlled-transport"}


def no_findings():
    return {"outcome": "no_findings", "findings": []}


def wait_for(predicate, timeout=5):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def configure(service, session_id, revision=None, **settings):
    if revision is None:
        revision = service.status(session_id)["saved_profile_revision"]
    return service.configure(session_id, {"route": ROUTE, **settings}, revision)


def db_rows(data_dir, sql, args=()):
    connection = sqlite3.connect(Path(data_dir) / "advisor.sqlite3")
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def seed_settled(service, session, count, *, terminal_at=None, prefix="old"):
    owner = service._owner_tokens[session]

    def seed(conn):
        for index in range(count):
            conn.execute(
                "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,"
                "applied_generation,owner_token,created_at,deadline_at,terminal_at,status,coverage_json) "
                "VALUES(?,?,?,?,1,1,?,?,?,?,'failed','{}')",
                (f"{prefix}-{index:03d}", session, f"seed-{prefix}-{index}", f"digest-{prefix}-{index}",
                 owner, index, index + 10,
                 terminal_at if terminal_at is not None else float(index + 1)),
            )

    service.store.transaction(seed)


class CoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.data_dir = self.temp.name
        self.services = []
        self.releases = []
        self.processes = []

    def tearDown(self):
        for release in self.releases:
            release.set()
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(5)
        for service in self.services:
            try:
                service.close(timeout=3)
            except Exception:
                pass
        self.temp.cleanup()

    def service(self, reviewer, data_dir=None):
        instance = Service(data_dir or self.data_dir, reviewer)
        self.services.append(instance)
        return instance

    def enabled(self, service, session_id, **settings):
        service.discover(session_id, "cli")
        configure(service, session_id, **settings)
        service.enable(session_id)

    def spawn(self, *args):
        # Files, not pipes: a lingering forked child must never block reads.
        out = tempfile.NamedTemporaryFile(dir=SCRATCH, delete=False, suffix=".out")
        err = tempfile.NamedTemporaryFile(dir=SCRATCH, delete=False, suffix=".err")
        process = subprocess.Popen(
            [sys.executable, str(HELPER), *map(str, args)],
            stdout=out, stderr=err, stdin=subprocess.DEVNULL, close_fds=True,
        )
        out.close()
        err.close()
        process.out_path, process.err_path = out.name, err.name
        self.addCleanup(os.unlink, out.name)
        self.addCleanup(os.unlink, err.name)
        self.processes.append(process)
        return process

    @staticmethod
    def captured(process):
        return (Path(process.out_path).read_text().strip(), Path(process.err_path).read_text())


class ReviewerAbnormalExitTests(CoreCase):
    def test_non_exception_reviewer_exit_finalizes_attempt_without_storage_latch(self):
        for exc_type in (asyncio.CancelledError, SystemExit, KeyboardInterrupt):
            with self.subTest(exc=exc_type.__name__):
                data_dir = tempfile.mkdtemp(dir=SCRATCH)

                async def reviewer(packet, settings, exc_type=exc_type):
                    raise exc_type()

                service = self.service(reviewer, data_dir)
                self.enabled(service, "abnormal")
                self.assertTrue(service.capture("abnormal", "t1", "req", "ans", [])["accepted"])
                self.assertTrue(wait_for(lambda: not service._active))
                attempt = service.notes("abnormal")[0]
                # The invocation was reserved before the call, so it is charged
                # exactly once and the attempt must not be stranded 'running'.
                self.assertEqual(attempt["status"], "failed", attempt)
                self.assertEqual(attempt["error_code"], "reviewer_error")
                self.assertFalse(attempt["cancellation_pending"])
                status = service.status("abnormal")
                self.assertEqual(status["invocations_used"], 1)
                self.assertFalse(status["storage_error"], status)
                self.assertEqual(status["state"], "enabled")


class UnconfirmedTransportTests(CoreCase):
    def make_swallowing_reviewer(self):
        """Fixture transport: swallows cancellation while a real thread is busy."""
        release = threading.Event()
        self.releases.append(release)
        lock = threading.Lock()
        state = {"live": 0, "peak": 0, "entered": []}

        def blocking():
            with lock:
                state["live"] += 1
                state["peak"] = max(state["peak"], state["live"])
            try:
                release.wait()
            finally:
                with lock:
                    state["live"] -= 1

        async def reviewer(packet, settings):
            state["entered"].append(packet["evidence"][0]["text"])
            task = asyncio.ensure_future(asyncio.to_thread(blocking))
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    continue
            return no_findings()

        return reviewer, release, state

    def test_unconfirmed_transports_keep_process_and_session_slots_until_confirmed(self):
        reviewer, release, state = self.make_swallowing_reviewer()
        with mock.patch.object(Service, "TRANSPORT_CANCEL_GRACE_SECONDS", 0.2):
            service = self.service(reviewer)
            for name in "abcd":
                self.enabled(service, name, deadline_seconds=1)
                self.assertTrue(service.capture(name, "t1", f"request-{name}", "ans", [])["accepted"])
            self.assertTrue(wait_for(lambda: len(state["entered"]) == 2))
            # Both started transports are abandoned after deadline + grace.
            self.assertTrue(wait_for(lambda: not service._active, timeout=8))
            time.sleep(0.4)
            # No third/fourth physical call may start while two transports are
            # unconfirmed, even though local cancellation was attempted.
            self.assertEqual(len(state["entered"]), 2, state)
            self.assertLessEqual(state["peak"], 2)
            started = sorted(state["entered"])
            statuses = {
                name: service.notes(name)[0] for name in "abcd"
            }
            for name in "abcd":
                if f"request-{name}" in started:
                    self.assertEqual(statuses[name]["status"], "timed_out")
                    self.assertTrue(statuses[name]["cancellation_pending"], statuses[name])
                    self.assertTrue(service.status(name)["unconfirmed_transport"])
                else:
                    self.assertEqual(statuses[name]["status"], "timed_out")
                    self.assertEqual(statuses[name]["error_code"], "deadline_expired_in_queue")
                    self.assertEqual(service.status(name)["invocations_used"], 0)
            # Same-session work must not start a second physical transport.
            first = next(name for name in "abcd" if f"request-{name}" in started)
            service.capture(first, "t2", "second turn", "ans", [])
            time.sleep(0.3)
            self.assertEqual(len(state["entered"]), 2)
            # Shutdown is bounded and honest: ownership is retained while
            # termination is unconfirmed.
            began = time.monotonic()
            self.assertFalse(service.close(timeout=0.3))
            self.assertLess(time.monotonic() - began, 5.0)
            rival = Service(self.data_dir, reviewer)
            self.services.append(rival)
            with self.assertRaises(OwnerUnavailable):
                rival.discover(first, "cli")
            # Real termination confirms cancellation, frees the slots and
            # releases ownership.
            release.set()
            self.assertTrue(wait_for(lambda: state["live"] == 0))
            self.assertTrue(wait_for(lambda: not service._unconfirmed))
            self.assertEqual(db_rows(
                self.data_dir, "SELECT COUNT(*) FROM attempts WHERE cancellation_pending=1"), [(0,)])
            self.assertTrue(service.close(timeout=3))
            self.assertTrue(rival.discover(first, "cli")["recovered"])


class CacheBoundsTests(CoreCase):
    def test_expired_snapshot_content_is_purged_when_any_other_session_captures(self):
        async def reviewer(packet, settings):
            return no_findings()

        service = self.service(reviewer)
        for name in ("old", "new"):
            self.enabled(service, name)
        service.capture("old", "t1", "old request", "ans", [])
        self.assertTrue(wait_for(lambda: not service._active))
        entry = service._cache["old"]
        service._cache["old"] = _expired(entry)
        service.capture("new", "t1", "new request", "ans", [])
        self.assertNotIn("old", service._cache)
        self.assertEqual(service.status("old")["review_now_unavailable_reason"], "expired")

    def test_cap_32768_applies_to_built_and_adapter_supplied_packets(self):
        async def reviewer(packet, settings):
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "caps")
        result = service.capture("caps", "t1", "r" * 200_000, "a" * 200_000, [])
        self.assertTrue(result["accepted"])
        self.assertTrue(wait_for(lambda: not service._active))
        entry = service._cache["caps"]
        self.assertLessEqual(entry.packet.byte_count, 32768)
        oversized = CapturedPacket("x" * 32769, "0" * 64, ("request", "answer"), ())
        rejected = service.capture("caps", "t2", None, None, None, snapshot=oversized)
        self.assertEqual(rejected["reason"], "invalid_packet")
        small = build_packet("r" * 5000, "a" * 5000, [], packet_bytes=1024)
        self.assertLessEqual(small.byte_count, 1024)
        configure(service, "caps", packet_bytes=1024)
        service.enable("caps")
        too_big_for_applied = build_packet("r" * 5000, "a" * 5000, [], packet_bytes=2048)
        rejected = service.capture("caps", "t3", None, None, None, snapshot=too_big_for_applied)
        self.assertEqual(rejected["reason"], "invalid_packet")


def _expired(entry):
    import dataclasses

    return dataclasses.replace(entry, expires_mono=time.monotonic() - 1)


class OwnershipTests(CoreCase):
    def test_recovery_settles_cancellation_pending_of_proven_dead_owner(self):
        ready = Path(self.data_dir) / "ready"
        process = self.spawn("own_swallow", self.data_dir, "dead-owner", ready)
        self.assertTrue(wait_for(lambda: ready.exists(), 10), self.captured(process))
        self.assertTrue(wait_for(lambda: db_rows(
            self.data_dir, "SELECT status,cancellation_pending FROM attempts"
        ) == [("timed_out", 1)], 10))
        process.send_signal(signal.SIGKILL)
        process.wait(5)

        async def reviewer(packet, settings):
            self.fail("recovery must never replay inference")

        recovered = self.service(reviewer)
        self.assertTrue(recovered.discover("dead-owner", "cli")["recovered"])
        attempt = recovered.notes("dead-owner")[0]
        # Owner death proves the local transport is gone, so the attempt must
        # not stay retention-protected forever; remote termination stays
        # explicitly unconfirmed in its message.
        self.assertEqual(attempt["status"], "timed_out")
        self.assertFalse(attempt["cancellation_pending"], attempt)
        self.assertIn("unconfirmed", attempt["error"])
        self.assertEqual(recovered.status("dead-owner")["invocations_used"], 1)

    def test_forked_child_does_not_keep_dead_owners_flock_alive(self):
        pid_path = Path(self.data_dir) / "child.pid"
        process = self.spawn("fork_orphan", self.data_dir, "forked", pid_path)
        process.wait(10)
        self.assertEqual(process.returncode, 0, self.captured(process)[1])
        child_pid = int(pid_path.read_text())
        self.addCleanup(self._kill_quietly, child_pid)

        async def reviewer(packet, settings):
            return no_findings()

        rival = self.service(reviewer)
        # The owner is dead; a lingering forked child must not hold the
        # OS-released lock (flock belongs to the open file description).
        info = rival.discover("forked", "cli")
        self.assertEqual(info["state"], "disabled")
        self.assertTrue(info["recovered"])

    @staticmethod
    def _kill_quietly(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def test_live_owner_process_is_untouched_by_other_process_discovery(self):
        ready = Path(self.data_dir) / "ready"
        process = self.spawn("own", self.data_dir, "live-owned", ready)
        self.assertTrue(wait_for(lambda: ready.exists(), 10), self.captured(process))
        sql = "SELECT * FROM sessions ORDER BY session_id"
        before = db_rows(self.data_dir, sql)
        attempts_before = db_rows(self.data_dir, "SELECT attempt_id,status,owner_token FROM attempts")

        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        other = self.service(reviewer)
        with self.assertRaises(OwnerUnavailable):
            other.discover("live-owned", "cli")
        self.assertEqual(db_rows(self.data_dir, sql), before)
        self.assertEqual(
            db_rows(self.data_dir, "SELECT attempt_id,status,owner_token FROM attempts"),
            attempts_before,
        )
        self.enabled(other, "mine")
        other.capture("mine", "t1", "request", "ans", [])
        self.assertTrue(wait_for(lambda: len(calls) == 1))
        process.send_signal(signal.SIGKILL)
        process.wait(5)
        info = other.discover("live-owned", "cli")
        self.assertTrue(info["recovered"])
        self.assertEqual(len(calls), 1)
        self.assertEqual(other.notes("live-owned")[0]["status"], "abandoned")
        self.assertEqual(other.status("live-owned")["invocations_used"], 1)

    def test_simultaneous_processes_get_exactly_one_configuration_winner(self):
        async def reviewer(packet, settings):
            return no_findings()

        go_at = time.time() + 2.0
        procs = [self.spawn("configure_race", self.data_dir, f"racer-{index}", go_at)
                 for index in range(4)]
        outputs = []
        for process in procs:
            process.wait(30)
            out, err = self.captured(process)
            self.assertEqual(process.returncode, 0, err)
            outputs.append(out)
        self.assertEqual(sorted(outputs), ["conflict", "conflict", "conflict", "ok"], outputs)
        revision = db_rows(self.data_dir, "SELECT revision FROM profile_config")[0][0]
        self.assertEqual(revision, 1)


class ReservationReplayTests(CoreCase):
    def test_death_after_durable_reservation_charges_once_and_never_replays(self):
        process = self.spawn("crash_after_reserve", self.data_dir, "reserved")
        process.wait(15)
        self.assertEqual(process.returncode, 0, self.captured(process))
        self.assertEqual(db_rows(self.data_dir, "SELECT invocation_count FROM sessions"), [(1,)])
        self.assertEqual(db_rows(self.data_dir, "SELECT status FROM attempts"), [("running",)])
        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        recovered = self.service(reviewer)
        self.assertTrue(recovered.discover("reserved", "cli")["recovered"])
        time.sleep(0.3)
        self.assertEqual(calls, [])
        self.assertEqual(recovered.notes("reserved")[0]["status"], "abandoned")
        status = recovered.status("reserved")
        self.assertEqual((status["invocations_used"], status["state"]), (1, "disabled"))
        # Explicit re-enable and an unrelated answer are required for new work;
        # the dead owner's turn is never re-run on its own.
        recovered.enable("reserved")
        self.assertFalse(recovered.review_now("reserved")["accepted"])
        self.assertEqual(calls, [])

    def test_pruned_completion_is_never_eligible_for_another_paid_review(self):
        from advisor_core.service import _prune

        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "dedup")
        self.assertTrue(service.capture("dedup", "turn-0", "request", "answer", [])["accepted"])
        self.assertTrue(wait_for(lambda: len(calls) == 1 and not service._active))
        owner = service._owner_tokens["dedup"]
        generation = service.status("dedup")["applied_generation"]
        seed_settled(service, "dedup", 100, terminal_at=time.time() + 1000)
        service.store.transaction(lambda conn: _prune(conn, "dedup"))
        self.assertEqual(db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE turn_id='turn-0'"), [(0,)])
        again = service.capture("dedup", "turn-0", "request", "answer", [])
        self.assertFalse(again["accepted"], again)
        self.assertTrue(again["duplicate"])
        time.sleep(0.2)
        self.assertEqual(len(calls), 1)
        self.assertEqual(service.status("dedup")["invocations_used"], 1)
        self.assertEqual(service.status("dedup")["applied_generation"], generation)
        del owner


class StorageFaultTests(CoreCase):
    def test_single_shot_failure_at_every_lifecycle_step_is_visible_and_never_overcharges(self):
        # Transaction/read order for one automatic review is deterministic:
        # 1 load, 2 admission, 3 pre-call validity, 4 reservation, 5 publication.
        for failing_call in range(1, 6):
            with self.subTest(failing_call=failing_call):
                data_dir = tempfile.mkdtemp(dir=SCRATCH)
                calls = []

                async def reviewer(packet, settings):
                    calls.append(packet)
                    return no_findings()

                service = self.service(reviewer, data_dir)
                self.enabled(service, "fault")
                store = service.store
                original = (store.transaction, store.read)
                counter = {"n": 0}
                guard = threading.Lock()

                def wrap(function):
                    def inner(callback):
                        with guard:
                            counter["n"] += 1
                            hit = counter["n"] == failing_call
                        if hit:
                            raise StoreError("injected single-shot failure")
                        return function(callback)
                    return inner

                store.transaction, store.read = wrap(original[0]), wrap(original[1])
                result = service.capture("fault", "t1", "request", "answer", [])
                self.assertTrue(wait_for(lambda: not service._active and not service._pending))
                time.sleep(0.1)
                store.transaction, store.read = original
                self.assertLessEqual(len(calls), 1)
                status = service.status("fault")
                # Every storage fault is a visible fail-closed state.
                self.assertTrue(status["storage_error"], (failing_call, result, status))
                self.assertFalse(status["review_now_available"])
                charged = db_rows(data_dir, "SELECT invocation_count FROM sessions")[0][0]
                self.assertEqual(charged, len(calls) if failing_call >= 5 else 0)
                published = db_rows(data_dir, "SELECT COUNT(*) FROM attempts WHERE status='completed'")[0][0]
                self.assertEqual(published, 0)

    def test_real_sqlite_write_lock_fails_admission_closed_within_one_second_bound(self):
        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "locked")
        blocker = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3", isolation_level=None)
        try:
            blocker.execute("BEGIN IMMEDIATE")
            began = time.monotonic()
            result = service.capture("locked", "t1", "request", "answer", [])
            elapsed = time.monotonic() - began
        finally:
            blocker.execute("ROLLBACK")
            blocker.close()
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "storage_error")
        self.assertLess(elapsed, 3.0)
        self.assertEqual(calls, [])
        self.assertEqual(db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts")[0][0], 0)
        self.assertTrue(service.status("locked")["storage_error"])
        service.enable("locked")
        self.assertTrue(service.capture("locked", "t2", "request", "answer", [])["accepted"])
        self.assertTrue(wait_for(lambda: len(calls) == 1))

    def test_real_sqlite_write_lock_at_reservation_makes_zero_calls(self):
        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "resv")
        original = service._reserve_invocation
        path = Path(self.data_dir) / "advisor.sqlite3"

        def locked_reserve(work, allowance):
            blocker = sqlite3.connect(path, isolation_level=None)
            try:
                blocker.execute("BEGIN IMMEDIATE")
                return original(work, allowance)
            finally:
                blocker.execute("ROLLBACK")
                blocker.close()

        service._reserve_invocation = locked_reserve
        service.capture("resv", "t1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("resv")["storage_error"], 8))
        self.assertTrue(wait_for(lambda: not service._active))
        self.assertEqual(calls, [])
        self.assertEqual(db_rows(self.data_dir, "SELECT invocation_count FROM sessions")[0][0], 0)

    def test_pump_submission_failure_does_not_wedge_the_session(self):
        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "wedge")
        real_submit = service._executor.submit
        service._executor.submit = mock.Mock(side_effect=RuntimeError("worker pool queue is full"))
        result = service.capture("wedge", "t1", "request", "answer", [])
        service._executor.submit = real_submit
        self.assertFalse(result["accepted"], result)
        self.assertFalse(service._active)
        self.assertEqual(db_rows(self.data_dir, "SELECT status FROM attempts"), [("failed",)])
        service.enable("wedge")
        self.assertTrue(service.capture("wedge", "t2", "request two", "answer", [])["accepted"])
        self.assertTrue(wait_for(lambda: len(calls) == 1))


class ConcurrencyBoundTests(CoreCase):
    def test_threaded_burst_never_exceeds_process_session_or_pending_bounds(self):
        lock = threading.Lock()
        live = {"all": 0, "peak_all": 0, "per": {}, "peak_per": 0}
        calls = []

        async def reviewer(packet, settings):
            session = packet["evidence"][0]["text"].split(":")[0]
            with lock:
                calls.append(packet["evidence"][0]["text"])
                live["all"] += 1
                live["per"][session] = live["per"].get(session, 0) + 1
                live["peak_all"] = max(live["peak_all"], live["all"])
                live["peak_per"] = max(live["peak_per"], live["per"][session])
            await asyncio.sleep(0.03)
            with lock:
                live["all"] -= 1
                live["per"][session] -= 1
            return no_findings()

        service = self.service(reviewer)
        names = [f"burst-{index}" for index in range(6)]
        for name in names:
            self.enabled(service, name, session_allowance=20)
        samples = {"pool": 0, "pending": 0, "active": 0}
        stop = threading.Event()

        def sampler():
            while not stop.is_set():
                with service._runtime_lock:
                    samples["pool"] = max(samples["pool"], service._executor._outstanding)
                    samples["pending"] = max(samples["pending"], len(service._pending))
                    samples["active"] = max(samples["active"], len(service._active))
                time.sleep(0.001)

        watcher = threading.Thread(target=sampler, daemon=True)
        watcher.start()

        def feed(name):
            for turn in range(8):
                service.capture(name, f"turn-{turn}", f"{name}:{turn}", "answer", [])
                time.sleep(0.005)

        threads = [threading.Thread(target=feed, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(20)
        self.assertTrue(wait_for(lambda: not service._active and not service._pending, 20))
        stop.set()
        watcher.join(2)
        self.assertLessEqual(live["peak_all"], 2)
        self.assertLessEqual(live["peak_per"], 1)
        self.assertLessEqual(samples["active"], 2)
        self.assertLessEqual(samples["pool"], 4)
        self.assertLessEqual(samples["pending"], len(names))  # one per session
        self.assertFalse(
            service._storage_latches,
            f"unexpected storage failure during concurrency acceptance: {service._storage_latches}",
        )
        counted = sum(row[0] for row in db_rows(self.data_dir, "SELECT invocation_count FROM sessions"))
        self.assertEqual(counted, len(calls))
        completed = db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE status='completed'")[0][0]
        skipped = db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE status='skipped'")[0][0]
        self.assertEqual(completed, len(calls))
        breakdown = db_rows(self.data_dir, "SELECT status,error_code,COUNT(*) FROM attempts GROUP BY 1,2")
        self.assertEqual(completed + skipped, 6 * 8, breakdown)
        self.assertGreater(skipped, 0)


class CallbackReentrancyTests(CoreCase):
    def test_reviewer_may_call_service_including_close_without_deadlock(self):
        seen = {}

        async def reviewer(packet, settings):
            seen["status"] = service.status("reenter")["state"]
            began = time.monotonic()
            seen["closed"] = service.close(timeout=0.3)
            seen["close_elapsed"] = time.monotonic() - began
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "reenter")
        service.capture("reenter", "t1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: "closed" in seen, 10))
        self.assertEqual(seen["status"], "enabled")
        self.assertFalse(seen["closed"])
        self.assertLess(seen["close_elapsed"], 5.0)  # bounded, not hung; host load varies
        self.assertTrue(wait_for(lambda: not service._active))
        self.assertTrue(service.close(timeout=3))

    def test_blocking_reviewer_makes_close_bounded_and_retains_ownership(self):
        release = threading.Event()
        self.releases.append(release)
        entered = threading.Event()

        async def reviewer(packet, settings):
            entered.set()
            release.wait()  # blocks the transport's event loop thread itself
            return no_findings()

        service = self.service(reviewer)
        self.enabled(service, "blocking", deadline_seconds=1)
        service.capture("blocking", "t1", "request", "answer", [])
        self.assertTrue(entered.wait(5))
        began = time.monotonic()
        self.assertFalse(service.close(timeout=0.4))
        self.assertLess(time.monotonic() - began, 5.0)
        rival = self.service(reviewer)
        with self.assertRaises(OwnerUnavailable):
            rival.discover("blocking", "cli")
        release.set()
        self.assertTrue(wait_for(lambda: not service._active))
        self.assertTrue(service.close(timeout=3))
        self.assertTrue(rival.discover("blocking", "cli")["recovered"])


class RetentionTests(CoreCase):
    def test_ties_on_terminal_time_keep_largest_attempt_ids(self):
        from advisor_core.service import _prune

        async def reviewer(packet, settings):
            return no_findings()

        service = self.service(reviewer)
        service.discover("ties", "cli")
        seed_settled(service, "ties", 105, terminal_at=500.0)
        service.store.transaction(lambda conn: _prune(conn, "ties"))
        ids = [row[0] for row in db_rows(self.data_dir, "SELECT attempt_id FROM attempts ORDER BY attempt_id")]
        self.assertEqual(len(ids), 100)
        self.assertEqual(ids[0], "old-005")
        self.assertEqual(ids[-1], "old-104")

    def test_protected_cancellation_survives_then_is_pruned_once_confirmed(self):
        release = threading.Event()
        self.releases.append(release)
        entered = threading.Event()

        async def reviewer(packet, settings):
            entered.set()
            task = asyncio.ensure_future(asyncio.to_thread(release.wait))
            while True:
                try:
                    await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    continue
            return no_findings()

        with mock.patch.object(Service, "TRANSPORT_CANCEL_GRACE_SECONDS", 0.2):
            service = self.service(reviewer)
            self.enabled(service, "ret")
            seed_settled(service, "ret", 100)
            service.capture("ret", "t1", "request", "answer", [])
            self.assertTrue(entered.wait(5))
            # Configuration change invalidates the running transport, which is
            # now cancellation-pending and must be protected from pruning.
            configure(service, "ret")
            self.assertTrue(wait_for(lambda: not service._active, 8))
            total = db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts")[0][0]
            pending = db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE cancellation_pending=1")[0][0]
            self.assertEqual((pending, total), (1, 101))
            release.set()
            self.assertTrue(wait_for(lambda: db_rows(
                self.data_dir, "SELECT COUNT(*) FROM attempts WHERE cancellation_pending=1")[0][0] == 0, 8))
            # Settling re-runs retention: exactly the newest 100 remain.
            self.assertTrue(wait_for(lambda: db_rows(
                self.data_dir, "SELECT COUNT(*) FROM attempts")[0][0] == 100, 5))
            self.assertEqual(db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE attempt_id='old-000'")[0][0], 0)

    def test_queued_and_running_attempts_are_never_pruned(self):
        from advisor_core.service import _prune

        async def reviewer(packet, settings):
            return no_findings()

        service = self.service(reviewer)
        service.discover("live-rows", "cli")
        seed_settled(service, "live-rows", 120)
        owner = service._owner_tokens["live-rows"]

        def add(conn):
            for name, status in (("q", "queued"), ("r", "running")):
                conn.execute(
                    "INSERT INTO attempts(attempt_id,session_id,turn_id,packet_digest,applied_revision,"
                    "applied_generation,owner_token,created_at,deadline_at,status,coverage_json) "
                    "VALUES(?,?,?,?,1,1,?,0,1,?,'{}')",
                    (name, "live-rows", name, name, owner, status),
                )
            _prune(conn, "live-rows")

        service.store.transaction(add)
        rows = dict(db_rows(self.data_dir, "SELECT attempt_id,status FROM attempts WHERE status IN ('queued','running')"))
        self.assertEqual(rows, {"q": "queued", "r": "running"})
        self.assertEqual(db_rows(self.data_dir, "SELECT COUNT(*) FROM attempts WHERE terminal_at IS NOT NULL")[0][0], 100)


if __name__ == "__main__":
    unittest.main()

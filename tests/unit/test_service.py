import asyncio
import contextvars
import fcntl
import gc
import json
import multiprocessing
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

from advisor_core import (
    ConfigurationConflict,
    OwnerUnavailable,
    ReviewerResult,
    Service,
    SessionNotDiscovered,
    build_packet,
)

from tests._scratch_root import SCRATCH
SCRATCH.mkdir(parents=True, exist_ok=True)
REVIEW_CONTEXT = contextvars.ContextVar("review_context", default="missing")


def valid_result(ref="answer"):
    return {
        "outcome": "findings",
        "findings": [{
            "severity": "concern",
            "explanation": "The conclusion needs verification.",
            "evidence_refs": [ref],
            "next_step": "Check the claim against the supplied evidence.",
        }],
    }


def configured(service, session_id="session-1", *, route_model="m", allowance=20, deadline=120):
    service.discover(session_id, "cli")
    settings = {
        "route": {"provider": "nous", "model": route_model},
        "session_allowance": allowance,
        "deadline_seconds": deadline,
    }
    return service.configure(session_id, settings, expected_revision=0)


def wait_for(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def _crash_owner_process(data_dir, transport_started):
    async def blocked_reviewer(packet, settings):
        transport_started.set()
        await asyncio.Event().wait()

    service = Service(data_dir, blocked_reviewer)
    service.discover("crash-owned-session", "cli")
    service.configure("crash-owned-session", {"route": {"provider": "nous", "model": "m"}}, 0)
    service.enable("crash-owned-session")
    service.capture("crash-owned-session", "turn-crash", "request", "answer", [])
    transport_started.wait(10)
    time.sleep(60)


class ServiceTests(unittest.TestCase):
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

    def service(self, reviewer, **kwargs):
        instance = Service(self.data_dir, reviewer, **kwargs)
        self.services.append(instance)
        return instance

    def test_arbitrary_undiscovered_ids_are_rejected(self):
        async def reviewer(packet, settings):
            return valid_result()
        service = self.service(reviewer)
        with self.assertRaises(SessionNotDiscovered):
            service.status("caller-picked-session")
        with self.assertRaises(SessionNotDiscovered):
            service.enable("caller-picked-session")

    def test_disabled_discovery_is_metadata_only_and_never_touches_content(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            return valid_result()
        service = self.service(reviewer)
        discovered = service.discover("disabled-session", "cli")
        self.assertEqual(discovered["state"], "disabled")

        class Untouchable:
            def __str__(self):
                raise AssertionError("disabled content was inspected")
            def __len__(self):
                raise AssertionError("disabled content was inspected")
            def __iter__(self):
                raise AssertionError("disabled content was inspected")

        result = service.capture("disabled-session", "turn-1", Untouchable(), Untouchable(), Untouchable())
        self.assertFalse(result["accepted"])
        self.assertEqual(result["reason"], "disabled")
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            count = database.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        finally:
            database.close()
        self.assertEqual(count, 0)
        self.assertEqual(calls, [])

    def test_successful_review_persists_only_validated_notes_with_attribution_and_usage(self):
        observed = {}
        completed = threading.Event()

        async def reviewer(packet, settings):
            observed["context"] = REVIEW_CONTEXT.get()
            observed["packet"] = packet
            observed["settings"] = settings
            completed.set()
            return ReviewerResult(
                response=valid_result(), provider="nous", model="z-ai/glm-5.3-flash",
                usage={"input_tokens": 12, "output_tokens": 4},
            )

        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        REVIEW_CONTEXT.set("captured-context")
        started = time.monotonic()
        accepted = service.capture("session-1", "turn-1", "review this", "answer", [])
        self.assertTrue(accepted["accepted"])
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(completed.wait(2))
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(observed["context"], "captured-context")
        self.assertEqual(observed["settings"]["route"]["model"], "m")
        self.assertIn("answer", [item["ref"] for item in observed["packet"]["evidence"]])
        self.assertEqual(service.status("session-1")["invocations_used"], 1)
        note_set = service.notes("session-1")
        self.assertEqual(note_set[0]["status"], "completed")
        self.assertEqual(note_set[0]["served_route"], {"provider": "nous", "model": "z-ai/glm-5.3-flash"})
        self.assertEqual(note_set[0]["usage"], {"input_tokens": 12, "output_tokens": 4})
        self.assertEqual(note_set[0]["findings"][0]["evidence_refs"], ["answer"])
        self.assertEqual(service.review_now("session-1")["reason"], "already_reviewed")
        self.assertEqual(service.rate("session-1", accepted["attempt_id"], "useful", "clear evidence")["saved"], True)
        feedback = service.feedback("session-1", [note_set[0]["findings"][0]["note_id"]])
        self.assertIn("not verified instructions", feedback["text"])
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        stored = " ".join(row[0] or "" for row in database.execute("SELECT error_text,coverage_json,outcome FROM attempts"))
        self.assertNotIn("review this", stored)
        self.assertNotIn("answer\"", stored)
        database.close()

    def test_configuration_revisions_conflict_and_apply_only_to_named_session(self):
        async def reviewer(packet, settings):
            return valid_result()
        service = self.service(reviewer)
        service.discover("session-A", "cli")
        service.discover("session-B", "cli")
        first = {"route": {"provider": "nous", "model": "route-one"}}
        self.assertEqual(service.configure("session-A", first, 0)["saved_profile_revision"], 1)
        second = {"route": {"provider": "nous", "model": "route-one"}}
        self.assertEqual(service.configure("session-B", second, 1)["saved_profile_revision"], 2)
        third = {"route": {"provider": "nous", "model": "route-two"}}
        self.assertEqual(service.configure("session-A", third, 2)["saved_profile_revision"], 3)
        status_a = service.status("session-A")
        status_b = service.status("session-B")
        self.assertEqual(status_a["settings"]["route"]["model"], "route-two")
        self.assertEqual(status_b["settings"]["route"]["model"], "route-one")
        self.assertEqual(status_b["applied_revision"], 2)
        with self.assertRaises(ConfigurationConflict):
            service.configure("session-A", first, 2)

    def test_per_session_flock_prevents_second_live_owner_without_mutation(self):
        async def reviewer(packet, settings):
            await asyncio.sleep(0.05)
            return valid_result()
        first = self.service(reviewer)
        configured(first, "owned")
        first.enable("owned")
        second = Service(self.data_dir, reviewer)
        self.services.append(second)
        with self.assertRaises(OwnerUnavailable):
            second.discover("owned", "desktop")
        self.assertEqual(first.status("owned")["state"], "enabled")
        self.assertEqual(first.status("owned")["invocations_used"], 0)


    def test_dropped_service_releases_flock_via_finalizer(self):
        async def reviewer(packet, settings):
            return valid_result()
        # Deliberately NOT registered in self.services: this instance is
        # dropped without close() to exercise the finalizer safety net.
        service = Service(self.data_dir, reviewer)
        service.discover("finalizer-session", "cli")
        locks_dir = Path(service.store.data_dir) / "locks"
        lock_path = next(iter(locks_dir.iterdir()))
        self.assertIn("finalizer-session", service._locks)
        finalizer = service._lock_finalizer
        self.assertTrue(finalizer.alive)
        # Dropping the Service without close() must not keep the fd + flock
        # for the life of the process: the finalizer releases them at
        # collection time (before this fix, a new Service on the same profile
        # raised OwnerUnavailable for that session forever).
        del service
        gc.collect()
        self.assertFalse(finalizer.alive)
        # The flock itself is free: a non-blocking exclusive claim must not fail.
        probe = os.open(lock_path, os.O_RDWR)
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(probe, fcntl.LOCK_UN)
        finally:
            os.close(probe)
        # A fresh Service on the same profile can claim the session again.
        fresh = Service(self.data_dir, reviewer)
        try:
            result = fresh.discover("finalizer-session", "cli")
            self.assertEqual(result["session_id"], "finalizer-session")
        finally:
            fresh.close(timeout=2)

    def test_close_detaches_drop_finalizer_and_stays_safe(self):
        async def reviewer(packet, settings):
            return valid_result()
        service = Service(self.data_dir, reviewer)
        service.discover("detach-session", "cli")
        finalizer = service._lock_finalizer
        self.assertTrue(finalizer.alive)
        self.assertTrue(service.close(timeout=2))
        # close() disarms the drop-time finalizer through the normal release
        # path; dropping a properly closed Service never re-releases anything.
        self.assertFalse(finalizer.alive)
        service = None
        gc.collect()
        self.assertFalse(finalizer.alive)

    def test_disabled_discoveries_hold_no_fds_and_enable_reclaims_the_flock(self):
        async def reviewer(packet, settings):
            return valid_result()
        service = self.service(reviewer)

        def fds():
            return len(os.listdir("/proc/self/fd"))
        base = fds()
        for i in range(300):
            service.discover(f"fd-probe-{i:03d}", "cli")
        # 300 disabled discoveries used to hold 300 descriptors until close();
        # discovery is metadata-only, so the flock is suspended while disabled.
        self.assertLessEqual(fds() - base, 4)
        configured(service, "fd-probe-000")
        before = fds()
        service.enable("fd-probe-000")
        self.assertEqual(fds() - before, 1)
        # An enabled session stays exclusively locked against other workers.
        second = self.service(reviewer)
        with self.assertRaises(OwnerUnavailable):
            second.discover("fd-probe-000", "cli")
        # Disable suspends again; a later enable re-takes the flock.
        service.disable("fd-probe-000")
        self.assertEqual(fds() - before, 0)
        service.enable("fd-probe-000")
        self.assertEqual(fds() - before, 1)

    def test_disabled_claim_can_be_taken_over_and_steal_surfaces_at_enable(self):
        async def reviewer(packet, settings):
            return valid_result()
        first = self.service(reviewer)
        configured(first, "contested-session")
        # A disabled session runs nothing, so another worker may take over its
        # metadata claim while the flock is suspended.
        second = self.service(reviewer)
        second.discover("contested-session", "cli")
        # The former claimant cannot silently enable over the new owner.
        with self.assertRaises(OwnerUnavailable):
            first.enable("contested-session")
        # The takeover owner holds the exclusive lock and can enable.
        second.configure(
            "contested-session",
            {"route": {"provider": "nous", "model": "m"}, "session_allowance": 20, "deadline_seconds": 120},
            expected_revision=1,
        )
        second.enable("contested-session")

    def test_bounded_snapshot_can_be_built_before_success_classification(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            return valid_result()
        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        snapshot = build_packet("request", "answer", [], 1024, prior_coverage={"omitted_tool_pairs": 3})
        result = service.capture("session-1", "turn-1", None, None, (), snapshot=snapshot,
                                 coverage={"omitted_tool_pairs": 3})
        self.assertTrue(result["accepted"])
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(calls[0]["coverage"]["upstream_omitted_tool_pairs"], 3)

    def test_latest_pending_replaces_older_pending_and_records_skipped_turn(self):
        first_started = threading.Event()
        release_first = threading.Event()
        called = []

        async def reviewer(packet, settings):
            request_text = packet["evidence"][0]["text"]
            called.append(request_text)
            if request_text == "one":
                first_started.set()
                await asyncio.to_thread(release_first.wait)
            return {"outcome": "no_findings", "findings": []}

        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        first = service.capture("session-1", "turn-1", "one", "answer", [])
        self.assertTrue(first_started.wait(2))
        second = service.capture("session-1", "turn-2", "two", "answer", [])
        third = service.capture("session-1", "turn-3", "three", "answer", [])
        self.assertEqual(second["status"], "queued")
        self.assertEqual(third["status"], "queued")
        release_first.set()
        self.assertTrue(wait_for(lambda: len(called) == 2 and service.status("session-1")["active"] is False))
        self.assertEqual(called, ["one", "three"])
        database = sqlite3.connect(Path(self.data_dir) / "advisor.sqlite3")
        try:
            attempt_rows = database.execute(
                "SELECT turn_id,status FROM attempts ORDER BY created_at,attempt_id"
            ).fetchall()
        finally:
            database.close()
        statuses = {turn: status for turn, status in attempt_rows}
        self.assertEqual(statuses["turn-2"], "skipped")
        self.assertEqual(statuses["turn-1"], "completed")
        self.assertEqual(statuses["turn-3"], "completed")

    def test_configuration_change_invalidates_old_work_but_keeps_session_enabled(self):
        started = threading.Event()

        async def reviewer(packet, settings):
            started.set()
            await asyncio.sleep(0.2)
            return valid_result()

        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        old = service.capture("session-1", "turn-old", "request", "answer", [])
        self.assertTrue(started.wait(2))
        changed = service.configure("session-1", {
            "route": {"provider": "nous", "model": "new-model"},
        }, expected_revision=1)
        self.assertEqual(changed["state"], "enabled")
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "cancelled"))
        new = service.capture("session-1", "turn-new", "request-2", "answer-2", [])
        self.assertTrue(new["accepted"])

    def test_validation_error_is_not_salvaged_and_explicit_review_now_retries(self):
        count = 0

        async def reviewer(packet, settings):
            nonlocal count
            count += 1
            if count == 1:
                return {"outcome": "findings", "findings": [
                    valid_result()["findings"][0],
                    {"severity": "blocker", "explanation": "unsupported", "evidence_refs": ["missing"], "next_step": "check"},
                ]}
            return {"outcome": "no_findings", "findings": []}

        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        first = service.capture("session-1", "turn-1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "failed"))
        self.assertEqual(service.notes("session-1")[0]["findings"], [])
        retry = service.review_now("session-1")
        self.assertTrue(retry["accepted"])
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(count, 2)
        self.assertEqual(service.status("session-1")["invocations_used"], 2)

    def test_allowance_reservation_is_durable_and_exhaustion_does_not_replay(self):
        calls = []
        async def reviewer(packet, settings):
            calls.append(packet)
            return {"outcome": "no_findings", "findings": []}
        service = self.service(reviewer)
        configured(service, allowance=1)
        service.enable("session-1")
        first = service.capture("session-1", "turn-1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("session-1")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(service.status("session-1")["invocations_used"], 1)
        self.assertEqual(service.status("session-1")["state"], "paused")
        self.assertEqual(service.status("session-1")["pause_reason"], "allowance_exhausted")
        rejected = service.capture("session-1", "turn-2", "must not be digested", "answer", [])
        self.assertFalse(rejected["accepted"])
        self.assertEqual(rejected["reason"], "allowance_exhausted")
        self.assertEqual(service.status("session-1")["state"], "paused")
        self.assertEqual(len(calls), 1)
        self.assertFalse(service.review_now("session-1")["accepted"])
        with self.assertRaises(Exception):
            service.enable("session-1")
        reset = service.reset_allowance("session-1", confirm=True)
        self.assertEqual(reset["state"], "paused")
        self.assertEqual(service.status("session-1")["invocations_used"], 0)

    def test_many_exhausted_sessions_do_not_retain_a_flock_each(self):
        async def reviewer(packet, settings):
            return {"outcome": "no_findings", "findings": []}

        def fds():
            return len(os.listdir("/proc/self/fd"))

        service = self.service(reviewer)
        base = fds()
        for i in range(20):
            session_id = f"paused-{i:02d}"
            service.discover(session_id, "cli")
            service.configure(session_id, {
                "route": {"provider": "nous", "model": "m"},
                "session_allowance": 1,
                "deadline_seconds": 120,
            }, expected_revision=i)
            service.enable(session_id)
            self.assertTrue(service.capture(session_id, "turn-1", "request", "answer", [])["accepted"])
            self.assertTrue(wait_for(lambda s=session_id: service.status(s)["state"] == "paused"))
            # A paused session runs nothing, so once its last transport settles
            # the fd+flock must be released, not held until close()/rediscovery.
            self.assertTrue(wait_for(lambda s=session_id: service._locks[s].suspended), session_id)
        # Before the fix each one-call session kept one descriptor forever.
        self.assertLessEqual(fds() - base, 4)
        # A released flock lets another worker take over the metadata claim.
        second = self.service(reviewer)
        self.assertEqual(second.discover("paused-00", "cli")["session_id"], "paused-00")

    def test_process_death_recovery_uses_flock_and_preserves_counter_without_replay(self):
        context = multiprocessing.get_context("fork")
        transport_started = context.Event()
        child = context.Process(target=_crash_owner_process, args=(self.data_dir, transport_started))
        child.start()
        try:
            self.assertTrue(transport_started.wait(8), "child reviewer never entered controlled transport")
        finally:
            child.terminate()
            child.join(5)
        self.assertFalse(child.is_alive())
        async def reviewer(packet, settings):
            self.fail("recovery must not automatically replay inference")
        recovered = self.service(reviewer)
        info = recovered.discover("crash-owned-session", "cli")
        self.assertTrue(info["recovered"])
        status = recovered.status("crash-owned-session")
        self.assertEqual(status["state"], "disabled")
        self.assertEqual(status["invocations_used"], 1)
        self.assertEqual(recovered.notes("crash-owned-session")[0]["status"], "abandoned")

    def test_snapshot_expiry_reports_expired_without_extending_on_status(self):
        async def reviewer(packet, settings):
            await asyncio.Event().wait()
        service = self.service(reviewer)
        configured(service)
        service.enable("session-1")
        service.capture("session-1", "turn-1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("session-1")["active"]))
        entry = service._cache["session-1"]
        service._cache["session-1"] = type(entry)(
            entry.packet, entry.turn_id, entry.applied_revision, entry.applied_generation,
            entry.owner_token, entry.settings_json, entry.captured_mono, time.monotonic() - 1,
            entry.original_attempt_id,
        )
        first_status = service.status("session-1")
        second_status = service.status("session-1")
        self.assertFalse(first_status["review_now_available"])
        self.assertEqual(first_status["review_now_unavailable_reason"], "expired")
        self.assertEqual(first_status["review_now_unavailable_reason"], second_status["review_now_unavailable_reason"])


if __name__ == "__main__":
    unittest.main()

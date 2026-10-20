"""Preview regressions for Service close, worker-failure and capture-ordering contracts.

Covers audit findings H4 (close reports released ownership), H5 (only storage
failures latch ``storage_error``) and M5 (local fail-closed admission returns
before any content argument is inspected). Every fault injection below is
LABELLED; these are offline fixture tests of the pure core and of the host
Runtime driven through the controlled CLI fixture context. They are not native
Hermes proof.
"""
import tempfile
import time
import unittest
from concurrent.futures import Future
from pathlib import Path

from advisor_core import Service
from advisor_core.store import StoreError

from tests._scratch_root import SCRATCH
from tests.cli_feedback.harness import Lab

SCRATCH.mkdir(parents=True, exist_ok=True)

NO_FINDINGS = {"outcome": "no_findings", "findings": []}


async def no_findings_reviewer(packet, settings):
    return NO_FINDINGS


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def enable_session(service, session_id):
    service.discover(session_id, "cli")
    service.configure(session_id, {"route": {"provider": "nous", "model": "m"}}, expected_revision=0)
    service.enable(session_id)


def _touch(probe, name):
    object.__getattribute__(probe, "_seen").append(name)
    raise AssertionError(f"capture inspected a content argument via {name}")


class ContentTripwire:
    """LABELLED PROBE: records, then refuses, any inspection of a content argument.

    ``isinstance`` on this object reads ``__class__`` through
    ``__getattribute__``, so even a type check counts as inspection.
    """

    def __init__(self):
        object.__setattr__(self, "_seen", [])

    def __getattribute__(self, name):
        _touch(self, name)

    def __setattr__(self, name, value):
        _touch(self, "set " + name)

    def __repr__(self):
        return "<content tripwire>"


for _dunder in ("__len__", "__iter__", "__getitem__", "__contains__", "__str__", "__bytes__",
                "__bool__", "__eq__", "__hash__", "__format__", "__reversed__", "__fspath__"):
    setattr(ContentTripwire, _dunder, lambda self, *args, _name=_dunder: _touch(self, _name))


def touches(probe):
    return list(object.__getattribute__(probe, "_seen"))


class PreviewServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.data_dir = self.temp.name
        self.services = []
        self.runtimes = []

    def tearDown(self):
        for runtime in self.runtimes:
            runtime.close(timeout=2)
        for service in self.services:
            service.close(timeout=2)
        self.temp.cleanup()

    def service(self, reviewer=no_findings_reviewer):
        instance = Service(self.data_dir, reviewer)
        self.services.append(instance)
        return instance

    # ---- H4: close() reports ownership release, not idle-thread exit --------

    def test_close_reports_released_ownership_while_idle_workers_are_still_exiting(self):
        service = self.service()
        service.discover("h4-session", "cli")
        # LABELLED FAULT INJECTION: the pool has not yet observed its idle daemon
        # workers exit. That is a timing artefact, not retained ownership.
        real_shutdown = service._executor.shutdown

        def unobserved_exit(**kwargs):
            real_shutdown(**kwargs)
            return False

        service._executor.shutdown = unobserved_exit
        self.assertTrue(service.close(timeout=1))
        # The owner lock really was released: a replacement owner may take it.
        replacement = self.service()
        replacement.discover("h4-session", "cli")
        self.assertEqual(replacement.status("h4-session")["state"], "disabled")

    def test_runtime_can_reregister_after_close_confirms_release(self):
        lab = Lab(Path(self.data_dir) / "labs")
        first = lab.runtime
        self.runtimes.append(first)
        # LABELLED FAULT INJECTION, as above, on the real host Runtime's Service.
        real_shutdown = first.service._executor.shutdown

        def unobserved_exit(**kwargs):
            real_shutdown(**kwargs)
            return False

        first.service._executor.shutdown = unobserved_exit
        self.assertTrue(first.close(timeout=1))
        self.assertTrue(first.released)
        # Re-registering the same profile store must be admitted, not refused as
        # "Previous Advisor owner cleanup is unconfirmed".
        lab._register()
        self.runtimes.append(lab.runtime)
        self.assertIsNot(lab.runtime, first)
        self.assertFalse(lab.runtime.closed)

    # ---- H5: only a storage failure latches storage_error -------------------

    def test_non_storage_worker_failure_is_volatile_and_storage_keeps_admitting(self):
        calls = []

        async def reviewer(packet, settings):
            calls.append(settings)
            return NO_FINDINGS

        service = self.service(reviewer)
        enable_session(service, "h5-session")

        # LABELLED FAULT INJECTION: a non-storage defect after inference.
        def defective_persist(work, validated, normalized):
            raise RuntimeError("synthetic worker defect")

        service._persist_success = defective_persist
        self.assertTrue(service.capture("h5-session", "turn-1", "request", "answer", [])["accepted"])
        self.assertTrue(wait_for(
            lambda: (service.status("h5-session")["latest_attempt"] or {}).get("status") == "failed"))
        self.assertTrue(wait_for(lambda: not service.status("h5-session")["active"]))
        status = service.status("h5-session")
        self.assertFalse(status["storage_error"])
        self.assertEqual((status["state"], status["pause_reason"]), ("enabled", None))
        self.assertEqual(status["latest_attempt"]["error_code"], "worker_error")
        self.assertIn("RuntimeError", status["volatile_error"] or "")

        del service._persist_success
        self.assertTrue(service.capture("h5-session", "turn-2", "request two", "answer two", [])["accepted"])
        self.assertTrue(wait_for(
            lambda: service.status("h5-session")["latest_attempt"]["status"] == "completed"))
        self.assertEqual(len(calls), 2)

    def test_worker_completion_classifies_cancellation_and_storage_failure(self):
        service = self.service()
        enable_session(service, "h5-direct")
        # LABELLED FAULT INJECTION: completion callbacks for synthetic futures.
        cancelled = Future()
        self.assertTrue(cancelled.cancel())
        service._work_done("h5-direct", "attempt-not-active", cancelled)
        status = service.status("h5-direct")
        self.assertFalse(status["storage_error"])
        self.assertIsNone(status["volatile_error"])
        self.assertEqual(status["state"], "enabled")

        failed_storage = Future()
        failed_storage.set_exception(StoreError("synthetic storage failure"))
        service._work_done("h5-direct", "attempt-not-active", failed_storage)
        status = service.status("h5-direct")
        self.assertTrue(status["storage_error"])
        self.assertEqual((status["state"], status["pause_reason"]), ("paused", "storage_error"))

    # ---- M5: local fail-closed admission precedes content inspection ---------

    def test_local_fail_closed_capture_returns_before_inspecting_content(self):
        service = self.service()
        enable_session(service, "m5-session")
        # LABELLED FAULT INJECTION: the documented local fail-closed state, where
        # the durable row still says enabled but this owner stopped admission.
        with service._runtime_lock:
            service._local_disabled.add("m5-session")
        status = service.status("m5-session")
        self.assertEqual((status["state"], status["pause_reason"]), ("disabled", "local_fail_closed"))

        raw = ContentTripwire()
        result = service.capture("m5-session", "turn-raw", raw, raw, raw)
        self.assertEqual((result["accepted"], result["reason"]), (False, "disabled"))
        snapshot = ContentTripwire()
        result = service.capture("m5-session", "turn-snapshot", None, None, None, snapshot=snapshot)
        self.assertEqual((result["accepted"], result["reason"]), (False, "disabled"))
        self.assertEqual(touches(raw), [])
        self.assertEqual(touches(snapshot), [])
        self.assertIsNone(service.status("m5-session")["latest_attempt"])


if __name__ == "__main__":
    unittest.main()

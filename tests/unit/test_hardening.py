"""Hardening regressions for the pure Advisor core.

FIXTURE LABELING: every reviewer callable, route and usage value in this
module is a synthetic controlled fixture used only for deterministic fault
injection. No real provider request, paid call, or live host is exercised.
Assertions about served attribution assert the *absence* of attribution
(unknown stays visibly unknown); no usage total or price is ever invented
as if it came from a real host facade.
"""

import asyncio
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import advisor_core.service as service_module
from advisor_core import ReviewerResult, Service, build_packet

REPO_ROOT = Path(__file__).resolve().parents[2]
from tests._scratch_root import SCRATCH
SCRATCH.mkdir(parents=True, exist_ok=True)


def no_findings():
    return {"outcome": "no_findings", "findings": []}


def configure_one(service, session_id, expected_revision=0, *, deadline=120):
    return service.configure(session_id, {
        "route": {"provider": "nous", "model": "fixture-not-a-real-route"},
        "deadline_seconds": deadline,
    }, expected_revision)


def wait_for(predicate, timeout=5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class HardeningTests(unittest.TestCase):
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

    def service(self, reviewer):
        instance = Service(self.data_dir, reviewer)
        self.services.append(instance)
        return instance

    def test_secret_straddling_the_excerpt_boundary_is_redacted(self):
        # The answer is longer than the 16384-codepoint excerpt window, so it
        # clips head+tail with a midpoint cut. The secret must sit so its key
        # ("password=") straddles that cut; redaction runs before clipping, so
        # even a bare surviving fragment must be the redaction marker, never
        # the verbatim credential.
        secret = "SuperSecretValue123"
        secret_len = len(secret)  # 19
        mark_len = len(" …[clipped]… ")  # 13
        room = 16384 - mark_len
        left = (room + 1) // 2
        # Left window ends mid-secret (after 10 of 19 chars): the tail window
        # begins with the remaining fragment "...Value123" plus padding.
        request = "A" * 10000 + "answer"
        answer = "B" * (left - 10) + "password=" + secret + "C" * 9000
        packet = build_packet(request, answer, [])
        self.assertNotIn(secret, packet.serialized)
        self.assertNotIn("sword=SuperSecret", packet.serialized)
        self.assertIn("…[clipped]…", packet.serialized)

    def test_uncancellable_transport_abandons_after_grace_and_keeps_slot_occupied(self):
        # FIXTURE reviewer: swallows cancellation and keeps running until the
        # test releases it, modelling a transport whose termination is
        # unconfirmed. Releasing it in cleanup frees the process-wide slot.
        release = threading.Event()
        self.addCleanup(release.set)
        calls = []

        async def reviewer(packet, settings):
            calls.append(packet)
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                while not release.is_set():
                    await asyncio.sleep(0.01)
            return no_findings()

        previous_grace = Service.TRANSPORT_CANCEL_GRACE_SECONDS
        Service.TRANSPORT_CANCEL_GRACE_SECONDS = 0.3
        self.addCleanup(
            setattr, Service, "TRANSPORT_CANCEL_GRACE_SECONDS", previous_grace
        )
        service = self.service(reviewer)
        service.discover("abandon", "cli")
        configure_one(service, "abandon", deadline=1)
        service.enable("abandon")
        service.capture("abandon", "t1", "request", "answer", [])
        self.assertTrue(wait_for(lambda: service.status("abandon")["active"]))
        # The worker thread must give up awaiting the uncancellable transport
        # after the grace window instead of blocking forever.
        self.assertTrue(wait_for(lambda: not service.status("abandon")["active"], timeout=8))
        attempt = service.notes("abandon")[0]
        self.assertEqual(attempt["status"], "timed_out")
        # Termination was never confirmed, so the attempt stays
        # retention-protected and the transport keeps its slot and session.
        self.assertTrue(attempt["cancellation_pending"])
        self.assertTrue(service.status("abandon")["unconfirmed_transport"])
        self.assertEqual(len(calls), 1)
        # Shutdown is bounded and does NOT claim termination: ownership is
        # retained until the transport really ends.
        self.assertFalse(service.close(timeout=0.3))
        release.set()
        self.assertTrue(wait_for(lambda: not service._unconfirmed, timeout=5))
        self.assertTrue(wait_for(lambda: service._closed, timeout=5))
        self.assertTrue(service.close(timeout=3))

    def test_close_returns_and_process_exits_with_unresolved_transport(self):
        # Subprocess reproduction: an uncancellable reviewer transport must not
        # keep the interpreter alive after close() (daemon worker threads).
        script = SCRATCH / "hardening-exit-probe.py"
        script.write_text(
            "import asyncio, sys, time\n"
            f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
            "import advisor_core.service as svc\n"
            "svc.TRANSPORT_CANCEL_GRACE_SECONDS = 0.2\n"
            "from advisor_core import Service\n"
            "async def reviewer(packet, settings):\n"
            "    try:\n"
            "        await asyncio.sleep(30)\n"
            "    except asyncio.CancelledError:\n"
            "        await asyncio.Event().wait()\n"
            "service = Service(sys.argv[1], reviewer)\n"
            "service.discover('exit-session', 'cli')\n"
            "service.configure('exit-session', {'route': {'provider': 'nous', 'model': 'fixture'},"
            " 'deadline_seconds': 1}, 0)\n"
            "service.enable('exit-session')\n"
            "service.capture('exit-session', 't1', 'request', 'answer', [])\n"
            "end = time.monotonic() + 8\n"
            "while time.monotonic() < end:\n"
            "    attempt = service.status('exit-session')['latest_attempt']\n"
            "    if attempt and attempt['status'] == 'timed_out':\n"
            "        break\n"
            "    time.sleep(0.05)\n"
            "print('closed', service.close(timeout=0.5))\n"
        )
        started = time.monotonic()
        result = subprocess.run(
            [sys.executable, str(script), self.data_dir],
            capture_output=True, text=True, timeout=30,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("closed", result.stdout)
        self.assertLess(elapsed, 25, f"interpreter did not exit promptly ({elapsed:.1f}s)")

    def test_unknown_served_route_and_usage_stay_visibly_unknown(self):
        # A06: when the host facade reports no served route or usage, the
        # records must show unknown (None / usage_known False) — never a
        # fabricated route, token count, or price. Plain-dict envelope first.
        calls = []

        async def bare_reviewer(packet, settings):
            calls.append(packet)
            return no_findings()

        service = self.service(bare_reviewer)
        service.discover("unknown-a", "cli")
        configure_one(service, "unknown-a")
        service.enable("unknown-a")
        service.capture("unknown-a", "t1", "request", "answer", [])
        self.assertTrue(wait_for(
            lambda: service.status("unknown-a")["latest_attempt"]["status"] == "completed"
        ))
        attempt = service.notes("unknown-a")[0]
        self.assertEqual(attempt["served_route"], {"provider": None, "model": None})
        self.assertIsNone(attempt["usage"])
        self.assertFalse(attempt["usage_known"])

        # Explicit usage_known=False suppresses even a supplied usage mapping.
        async def labeled_reviewer(packet, settings):
            return ReviewerResult(
                response=no_findings(), provider=None, model=None,
                usage={"input_tokens": 999, "cost_usd": 42.0}, usage_known=False,
            )

        service2 = self.service(labeled_reviewer)
        service2.discover("unknown-b", "cli")
        configure_one(service2, "unknown-b", expected_revision=1)
        service2.enable("unknown-b")
        service2.capture("unknown-b", "t1", "request", "answer", [])
        self.assertTrue(wait_for(
            lambda: service2.status("unknown-b")["latest_attempt"]["status"] == "completed"
        ))
        attempt2 = service2.notes("unknown-b")[0]
        self.assertEqual(attempt2["served_route"], {"provider": None, "model": None})
        self.assertIsNone(attempt2["usage"])
        self.assertFalse(attempt2["usage_known"])
        # Scrub volatile clock fields before the substring scan: created_at /
        # terminal_at are float epoch values (e.g. 1791816622.9990797) whose
        # digits can incidentally contain "999" or "42.0". The assertion is
        # about the fabricated usage values, not about the clock.
        record = [dict(note) for note in service2.notes("unknown-b")]
        for note in record:
            note.pop("created_at", None)
            note.pop("terminal_at", None)
        database_text = repr(record)
        self.assertNotIn("999", database_text)
        self.assertNotIn("42.0", database_text)


if __name__ == "__main__":
    unittest.main()

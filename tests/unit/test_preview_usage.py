"""Preview regressions for audit finding B2: usage truth (SPEC R07).

The host facade zero-fills counters it was not given, so an empty mapping, an
all-zero mapping or a zero cost estimate is indistinguishable from "not
reported". Such usage must stay unknown and must never surface as proven zero
use or an invented zero price. Facades below are LABELLED FIXTURES; no model,
network or provider is used.
"""
import asyncio
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from advisor_core import ReviewerResult, Service

from tests._scratch_root import SCRATCH
from tests.cli_feedback.harness import Lab

SCRATCH.mkdir(parents=True, exist_ok=True)

NO_FINDINGS = {"outcome": "no_findings", "findings": []}
ZERO_FILLED = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
               "cache_read_tokens": 0, "cache_write_tokens": 0}


def facade_usage(**fields):
    """LABELLED FIXTURE with the host facade's usage shape: zero-filled counters, cost None."""
    values = dict(ZERO_FILLED, cost_usd=None)
    values.update(fields)
    return SimpleNamespace(**values)


def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


class CoreUsageTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)
        self.services = []

    def tearDown(self):
        for service in self.services:
            service.close(timeout=2)
        self.temp.cleanup()

    def latest_attempt(self, result):
        async def reviewer(packet, settings):
            return result

        service = Service(str(Path(self.temp.name) / uuid.uuid4().hex), reviewer)
        self.services.append(service)
        service.discover("usage-session", "cli")
        service.configure("usage-session", {"route": {"provider": "nous", "model": "m"}}, expected_revision=0)
        service.enable("usage-session")
        self.assertTrue(service.capture("usage-session", "turn-1", "request", "answer", [])["accepted"])
        self.assertTrue(wait_for(
            lambda: (service.status("usage-session")["latest_attempt"] or {}).get("status") == "completed"))
        return service.status("usage-session")["latest_attempt"]

    def test_zero_filled_usage_claimed_known_is_reported_unknown(self):
        attempt = self.latest_attempt(ReviewerResult(response=NO_FINDINGS, usage=dict(ZERO_FILLED), usage_known=True))
        self.assertIsNone(attempt["usage"])
        self.assertFalse(attempt["usage_known"])

    def test_empty_usage_mapping_is_unknown(self):
        attempt = self.latest_attempt(ReviewerResult(response=NO_FINDINGS, usage={}))
        self.assertIsNone(attempt["usage"])
        self.assertFalse(attempt["usage_known"])

    def test_positive_usage_remains_known_as_reported(self):
        attempt = self.latest_attempt(ReviewerResult(
            response=NO_FINDINGS, usage={"input_tokens": 700, "output_tokens": 120}))
        self.assertEqual(attempt["usage"], {"input_tokens": 700, "output_tokens": 120})
        self.assertTrue(attempt["usage_known"])


class HostUsageTruthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH)

    def tearDown(self):
        self.temp.cleanup()

    def review(self, usage):
        lab = Lab(Path(self.temp.name) / uuid.uuid4().hex)
        try:
            async def facade(**kwargs):
                return SimpleNamespace(parsed=NO_FINDINGS, provider="fixture-provider",
                                       model="fixture-model", usage=usage)

            lab.ctx.llm.acomplete_structured = facade  # LABELLED FIXTURE facade
            settings = {"route": {"provider": "fixture-provider", "model": "fixture-model"},
                        "max_findings": 4, "output_tokens": 2048, "deadline_seconds": 30}
            return asyncio.run(lab.runtime.review({"evidence": [{"ref": "answer"}]}, settings))
        finally:
            self.assertTrue(lab.runtime.close())

    def test_zero_filled_facade_usage_is_unknown(self):
        result = self.review(facade_usage())
        self.assertIsNone(result.usage)
        self.assertFalse(result.usage_known)

    def test_zero_filled_counters_are_dropped_and_reported_counts_kept(self):
        result = self.review(facade_usage(input_tokens=700, output_tokens=120, total_tokens=820))
        self.assertEqual(result.usage, {"input_tokens": 700, "output_tokens": 120, "total_tokens": 820})
        self.assertTrue(result.usage_known)

    def test_zero_cost_estimate_is_not_an_invented_price(self):
        result = self.review(facade_usage(input_tokens=5, total_tokens=5, cost_usd=0.0))
        self.assertNotIn("cost_usd", result.usage)
        priced = self.review(facade_usage(input_tokens=5, total_tokens=5, cost_usd=0.0125))
        self.assertEqual(priced.usage["cost_usd"], 0.0125)

    def test_zero_only_mapping_usage_is_unknown(self):
        result = self.review({"input_tokens": 0, "output_tokens": 0})
        self.assertIsNone(result.usage)
        self.assertFalse(result.usage_known)


if __name__ == "__main__":
    unittest.main()

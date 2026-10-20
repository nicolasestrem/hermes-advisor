import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from advisor_eval import common, runner  # noqa: E402
from advisor_eval.prepare import PreparationError  # noqa: E402

REPO = support.REPO
IDS = ["D01", "D02", "D03", "D04", "D05", "C01", "C02", "C03", "I01", "I02"]


def packets_doc(tmp):
    out, sha = support.prepared_in(Path(tmp))
    return json.loads((out / "packets.json").read_text()), out, sha


class Script:
    """Synthetic reviewer scripted per call index; counts calls."""

    def __init__(self, steps=None, default=None):
        self.calls = []
        self.steps = steps or {}
        self.default = default

    async def __call__(self, packet, settings):
        index = len(self.calls)
        self.calls.append((packet, settings))
        step = self.steps.get(index, self.default)
        if step is None:
            return support.result(support.NO_FINDINGS)
        if isinstance(step, Exception):
            raise step
        if callable(step):
            return await step()
        return step


def run(tmp, review, *, name="r", order=None, _doc=None, **kw):
    doc, _out, sha = _doc if _doc is not None else packets_doc(tmp)
    return runner.run_cases(repo=REPO, run_dir=Path(tmp) / name, packets_doc=doc, review=review,
                            mode="synthetic_test", manifest_sha256=sha,
                            facade_class="SYNTHETIC.Fake", order=order or IDS, run_id=name, **kw)


class RunnerTests(unittest.TestCase):
    def test_exactly_ten_calls_each_reserved_before_settled(self):
        with support.tmpdir() as tmp:
            review = Script()
            final = run(tmp, review)
            self.assertEqual(len(review.calls), 10)
            self.assertEqual(final["reserved_calls"], 10)
            events = [json.loads(l) for l in (Path(tmp) / "r" / "ledger.jsonl").read_text().splitlines()]
            self.assertEqual([e["event"] for e in events], ["reserve", "settled"] * 10)
            self.assertEqual(final["header"]["inference"], "SYNTHETIC_NOT_MODEL_EVIDENCE")
            for packet, settings in review.calls:
                self.assertEqual(settings["route"], common.FIXED_ROUTE)
                self.assertEqual((settings["output_tokens"], settings["deadline_seconds"], settings["max_findings"]),
                                 (2048, 90, 4))
                self.assertNotIn("kind\": \"defect", json.dumps(packet))

    def test_failures_are_retained_not_retried(self):
        with support.tmpdir() as tmp:
            review = Script(steps={0: RuntimeError("boom sk-" + "abcdefghijklmnopqrstuvwxyz"), 1: ValueError("x")})
            final = run(tmp, review)
            self.assertEqual(len(review.calls), 10)  # no replay of the two failed calls
            self.assertEqual(final["cases"]["D01"]["status"], "call_error")
            self.assertNotIn("sk-abcdefgh", json.dumps(final))
            self.assertEqual(final["cases"]["D03"]["status"], "completed")

    def test_invalid_responses_never_salvaged(self):
        bad = [
            {"outcome": "findings", "findings": [support.finding(refs=("tool-9999-call",))]},
            {"outcome": "findings", "findings": [support.finding(), {**support.finding(), "extra": 1}]},
            {"outcome": "no_findings", "findings": [support.finding()]},
            {"outcome": "findings", "findings": [], "reasoning": "hidden"},
            None,
        ]
        with support.tmpdir() as tmp:
            review = Script(steps={i: support.result(r) for i, r in enumerate(bad)})
            final = run(tmp, review)
            for case_id in IDS[:5]:
                rec = final["cases"][case_id]
                self.assertEqual(rec["status"], "invalid_response", case_id)
                self.assertNotIn("outcome", rec)
            self.assertNotIn("hidden", final["cases"]["D04"]["response_excerpt"])

    def test_usage_unknown_is_honest(self):
        self.assertFalse(runner.normalize_usage(None)["known"])
        self.assertFalse(runner.normalize_usage({})["known"])
        self.assertFalse(runner.normalize_usage({"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})["known"])
        known = runner.normalize_usage({"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100})
        self.assertTrue(known["known"])
        self.assertEqual(known["cost_usd_estimate"], "0.00016000")
        self.assertIn("not an invoice", known["cost_basis"])
        partial = runner.normalize_usage({"output_tokens": 50, "total_tokens": 50})
        self.assertTrue(partial["known"] and partial["cost_usd_estimate"] is None)

    def test_route_deviation_halts_remaining_calls(self):
        with support.tmpdir() as tmp:
            wrong = support.result(support.NO_FINDINGS, route={"provider": "other", "model": "m"})
            review = Script(steps={2: wrong})
            final = run(tmp, review)
            self.assertEqual(len(review.calls), 3)
            self.assertTrue(final["cases"]["D03"]["route_deviation"])
            self.assertEqual(final["cases"]["D04"]["status"], "not_run_halted")
            self.assertEqual(final["cases"]["I02"]["status"], "not_run_halted")
            self.assertIn("deviated", final["halted"])

    def test_unreported_served_route_is_unknown_not_assumed(self):
        with support.tmpdir() as tmp:
            r = support.result(support.NO_FINDINGS, route={})
            final = run(tmp, Script(default=r))
            self.assertTrue(final["cases"]["D01"]["served_route_unknown"])
            self.assertIsNone(final["halted"])

    def test_timeout_retained(self):
        async def slow():
            await asyncio.sleep(5)
        with support.tmpdir() as tmp:
            old = runner.PER_CALL_GRACE_SECONDS
            runner.PER_CALL_GRACE_SECONDS = 0
            try:
                settings = {**common.SETTINGS, "deadline_seconds": 1}
                review = Script(steps={0: slow})
                final = run(tmp, review, settings=settings)
            finally:
                runner.PER_CALL_GRACE_SECONDS = old
            self.assertEqual(final["cases"]["D01"]["status"], "timeout")
            self.assertEqual(len(review.calls), 10)

    def test_bounds_cannot_be_loosened_or_replayed(self):
        with support.tmpdir() as tmp:
            doc = packets_doc(tmp)
            review = Script()
            for bad in ({**common.SETTINGS, "output_tokens": 4096}, {**common.SETTINGS, "deadline_seconds": 300},
                        {**common.SETTINGS, "route": {"provider": "nous", "model": "other"}}):
                with self.assertRaises(runner.RunRefused):
                    run(tmp, review, name="b", settings=bad, _doc=doc)
            with self.assertRaises(runner.RunRefused):
                run(tmp, review, name="dup", order=IDS + ["D01"], _doc=doc)
            with self.assertRaises(runner.RunRefused):
                run(tmp, review, name="rep", order=["D01", "D01"], _doc=doc)
            self.assertEqual(review.calls, [])
            run(tmp, review, name="first", _doc=doc)
            with self.assertRaises(runner.RunRefused):  # same run dir => no replay
                run(tmp, review, name="first", _doc=doc)
            self.assertEqual(len(review.calls), 10)

    def test_repository_ships_every_required_evaluator_artifact(self):
        # Regression (review P1): the evaluator referenced scoring_rules.json but
        # the file was never committed, so a clone could not load the rules and
        # nine scoring tests failed. Every required run artifact must ship.
        from advisor_eval import prepare as prepare_mod
        self.assertEqual(prepare_mod.missing_artifacts(REPO), [])

    def test_missing_rules_artifact_is_refused_before_any_live_run(self):
        from advisor_eval import prepare as prepare_mod
        with support.tmpdir() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            missing = prepare_mod.missing_artifacts(empty)
            self.assertIn("tests/evaluation/_runner/scoring_rules.json", missing)
            with self.assertRaises(PreparationError):
                prepare_mod.build_manifest(empty)

    def test_live_gates_before_any_call(self):
        with support.tmpdir() as tmp:
            tmp = Path(tmp)
            out, sha = support.prepared_in(tmp)
            review = Script()
            kw0 = dict(repo=REPO, prepared=out, review=review, facade_class="SYNTHETIC.Fake", runs_root=tmp / "runs")
            for kw in ({"authorize": "", "expect_manifest_sha256": sha, "run_id": "g4"},
                       {"authorize": common.AUTHORIZE_TOKEN, "expect_manifest_sha256": "0" * 64, "run_id": "g4"},
                       {"authorize": common.AUTHORIZE_TOKEN, "expect_manifest_sha256": sha, "run_id": "../x"},
                       {"authorize": common.AUTHORIZE_TOKEN, "expect_manifest_sha256": sha, "run_id": ""}):
                with self.assertRaises((runner.RunRefused, PreparationError)):
                    runner.run_live(**kw0, **kw)
            self.assertEqual(review.calls, [])
            self.assertFalse((tmp / "runs").exists())
            final = runner.run_live(**kw0, authorize=common.AUTHORIZE_TOKEN, expect_manifest_sha256=sha, run_id="g4")
            self.assertEqual(final["mode"], "live")
            self.assertEqual(len(review.calls), 10)
            with self.assertRaises(runner.RunRefused):  # one live run only
                runner.run_live(**kw0, authorize=common.AUTHORIZE_TOKEN, expect_manifest_sha256=sha, run_id="g4b")
            self.assertEqual(len(review.calls), 10)


if __name__ == "__main__":
    unittest.main()

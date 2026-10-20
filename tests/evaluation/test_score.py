import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from advisor_eval import score, common  # noqa: E402
from test_runner import IDS, Script, packets_doc, run  # noqa: E402

F = support.finding


def scripted(outcomes):
    steps = {}
    for i, case_id in enumerate(IDS):
        if case_id in outcomes:
            steps[i] = support.result(outcomes[case_id])
    return Script(steps=steps)


def findings(*labels_refs):
    return {"outcome": "findings", "findings": [F(refs=r) for r in labels_refs]}


class ScoreTests(unittest.TestCase):
    def build(self, outcomes, adj=None):
        with support.tmpdir() as tmp:
            doc = packets_doc(tmp)
            gold = json.loads((doc[1] / "gold.json").read_text())
            results = run(tmp, scripted(outcomes), _doc=doc)
        return results, gold, score.score_run(results, gold, adj)

    def test_mechanical_table_without_adjudication(self):
        outcomes = {
            "D01": findings(("answer",)),
            "D02": support.NO_FINDINGS,
            "D03": {"outcome": "insufficient_evidence", "findings": []},
            "C01": support.NO_FINDINGS,
            "C02": findings(("answer",)),
            "C03": {"outcome": "insufficient_evidence", "findings": []},
            "I01": {"outcome": "insufficient_evidence", "findings": []},
            "I02": findings(("answer",)),
        }
        _r, _g, s = self.build(outcomes)
        c = s["cases"]
        self.assertEqual(c["D01"]["final_class"], "pending_adjudication")
        self.assertFalse(c["D01"]["success"])
        self.assertEqual((c["D02"]["final_class"], c["D03"]["final_class"]), ("missed", "missed"))
        self.assertEqual(c["C01"]["final_class"], "correct_control")
        self.assertTrue(c["C01"]["success"])
        self.assertEqual(c["C02"]["final_class"], "pending_adjudication")
        self.assertEqual(c["C03"]["final_class"], "over_abstention")
        self.assertEqual(c["I01"]["final_class"], "correct_abstention")
        self.assertEqual(c["I02"]["final_class"], "pending_adjudication")
        # D04, D05 default NO_FINDINGS => missed; I-case counted success only for I01
        self.assertEqual(s["by_kind"]["defect"]["success"], 0)
        self.assertEqual(s["by_kind"]["defect"]["denominator"], 5)
        self.assertEqual(s["by_kind"]["incomplete_evidence"]["success"], 1)
        self.assertEqual(s["pending_adjudication"], ["C02", "D01", "I02"])

    def test_failed_cases_never_count_as_success(self):
        with support.tmpdir() as tmp:
            doc = packets_doc(tmp)
            gold = json.loads((doc[1] / "gold.json").read_text())
            steps = {i: RuntimeError("x") for i in range(10)}
            results = run(tmp, Script(steps=steps), _doc=doc)
            s = score.score_run(results, gold)
        self.assertEqual(s["success_total"], 0)
        self.assertEqual(len(s["failed_retained"]), 10)
        self.assertEqual(s["usage"]["cases_unknown"], 10)
        self.assertEqual(s["by_kind"]["control"]["denominator"], 3)

    def test_adjudication_resolves_candidates_without_redefining_success(self):
        outcomes = {"D01": findings(("answer",), ("request",)), "C02": findings(("answer",)),
                    "I02": findings(("answer",)), "I01": findings(("answer",))}
        adj = {"schema": "advisor-eval/1/adjudication", "adjudicator": "parent", "cases": {
            "D01": {"finding_labels": ["supported_true_positive", "false_positive"], "rating": "Useful"},
            "C02": {"finding_labels": ["false_positive"], "rating": "Not useful"},
            "I02": {"finding_labels": ["supported_unverifiable_recommendation"]},
            "I01": {"finding_labels": ["unsupported_accusation"]},
        }}
        _r, _g, s = self.build(outcomes, adj)
        c = s["cases"]
        self.assertEqual(c["D01"]["final_class"], "detected_supported")
        self.assertEqual(c["C02"]["final_class"], "false_positive")
        self.assertEqual(c["I02"]["final_class"], "correct_abstention")
        self.assertEqual(c["I01"]["final_class"], "unsupported_accusation")
        self.assertEqual(s["finding_label_counts"]["false_positive"], 2)
        self.assertEqual(s["ratings"]["Useful"], 1)
        self.assertEqual(s["pending_adjudication"], [])

    def test_partial_labels_stay_pending_and_other_valid_issue_is_not_success(self):
        outcomes = {"D01": findings(("answer",), ("request",)), "C01": findings(("answer",))}
        adj = {"schema": "advisor-eval/1/adjudication", "cases": {
            "D01": {"finding_labels": ["supported_true_positive", None]},
            "C01": {"finding_labels": ["other_valid_issue"]}}}
        _r, _g, s = self.build(outcomes, adj)
        self.assertEqual(s["cases"]["D01"]["final_class"], "pending_adjudication")
        self.assertEqual(s["cases"]["C01"]["final_class"], "control_flagged_other_valid_issue")
        self.assertFalse(s["cases"]["C01"]["success"])

    def test_adjudication_validation_is_strict(self):
        outcomes = {"D01": findings(("answer",))}
        bad_docs = [
            {"schema": "advisor-eval/1/adjudication", "cases": {"D01": {"finding_labels": []}}},
            {"schema": "advisor-eval/1/adjudication", "cases": {"D01": {"finding_labels": ["nonsense"]}}},
            {"schema": "advisor-eval/1/adjudication", "cases": {"C01": {"finding_labels": [], "rating": "great"}}},
            {"schema": "advisor-eval/1/adjudication", "cases": {"ZZ": {"finding_labels": []}}},
            {"schema": "advisor-eval/1/adjudication", "cases": {"D01": {"finding_labels": ["supported_true_positive"], "x": 1}}},
            {"cases": {}},
        ]
        for adj in bad_docs:
            with self.assertRaises(score.AdjudicationError, msg=adj):
                self.build(outcomes, adj)
        # a control may not be labelled as a supported defect
        with self.assertRaises(score.AdjudicationError):
            self.build({"C01": findings(("answer",))}, {"schema": "advisor-eval/1/adjudication", "cases": {
                "C01": {"finding_labels": ["supported_true_positive"]}}})

    def test_report_banners_and_contents(self):
        with support.tmpdir() as tmp:
            doc = packets_doc(tmp)
            gold = json.loads((doc[1] / "gold.json").read_text())
            results = run(tmp, scripted({"D01": findings(("answer",))}), _doc=doc)
            scores = score.score_run(results, gold)
            text = score.render_report(results, scores, gold)
            self.assertIn("SYNTHETIC HARNESS TEST", text)
            self.assertIn("not model evidence", text.lower())
            self.assertIn("Overall success", text)
            self.assertIn("pending", text.lower())
            live = json.loads(json.dumps(results))
            live["mode"] = "live"
            live["header"]["facade_class"] = "tests.Fake"
            self.assertIn("not the host plugin facade", score.render_report(live, scores, gold))
            live["header"]["facade_class"] = "agent.plugin_llm.PluginLlm"
            self.assertIn("Live parent-run", score.render_report(live, scores, gold))
            manifest = json.loads((doc[1] / "manifest.json").read_text())
            dry = score.dry_run_report(manifest, gold)
            self.assertIn("NOT RUN", dry)
            self.assertNotIn("supported_defect", dry)

    def test_score_files_writes_outputs_and_template_without_gold(self):
        with support.tmpdir() as tmp:
            doc = packets_doc(tmp)
            results = run(tmp, scripted({"D01": findings(("answer",))}), _doc=doc)
            s = score.score_files(Path(tmp) / "r", doc[1] / "gold.json")
            self.assertEqual(s["run_id"], "r")
            template = (Path(tmp) / "r" / "adjudication-template.json").read_text()
            self.assertNotIn("observation", template)
            self.assertTrue((Path(tmp) / "r" / "report.md").exists())
            self.assertTrue((Path(tmp) / "r" / "scores.json").exists())

    def test_rules_file_matches_code_expectations(self):
        rules = score.load_rules()
        self.assertTrue(rules["frozen_before_any_run"])
        self.assertEqual(set(rules["mechanical"]), {"defect", "control", "incomplete_evidence"})
        self.assertEqual(rules["success_classes"]["defect"], ["detected_supported"])


if __name__ == "__main__":
    unittest.main()

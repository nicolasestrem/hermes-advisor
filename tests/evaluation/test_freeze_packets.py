import copy
import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402,F401
from advisor_eval import common, packets, prepare as prep  # noqa: E402
from advisor_eval.freeze import FreezeError, require_freeze, verify_freeze  # noqa: E402

REPO = support.REPO


class FreezeTests(unittest.TestCase):
    def test_real_freeze_verified_by_sha256sum(self):
        result = verify_freeze(REPO)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["tool_exit"], 0)
        self.assertEqual(result["tool_stdout"].count(": OK"), 2)

    def test_tampered_copy_is_refused(self):
        with support.tmpdir() as tmp:
            tmp = Path(tmp)
            for name in (*common.FROZEN_FILES, common.FREEZE_FILE):
                (tmp / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(REPO / name, tmp / name)
            (tmp / "evaluation/cases.json").write_text(
                (tmp / "evaluation/cases.json").read_text().replace("D01", "D0X", 1))
            result = verify_freeze(tmp)
            self.assertFalse(result["ok"])
            self.assertNotEqual(result["tool_exit"], 0)
            with self.assertRaises(FreezeError):
                require_freeze(tmp)

    def test_frozen_files_match_git_head(self):
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=REPO)
        if head.returncode != 0:
            # Unborn staging tree: no HEAD exists, so bind the bytes with the
            # freeze manifest instead of claiming a Git comparison that cannot run.
            check = subprocess.run(["sha256sum", "-c", common.FREEZE_FILE], cwd=REPO,
                                   capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stdout)
            return
        out = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *common.FROZEN_FILES, common.FREEZE_FILE],
                             cwd=REPO)
        self.assertEqual(out.returncode, 0)


class PacketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core, cls.host = common.load_product(REPO)
        cls.doc = packets.load_cases(REPO)
        cls.packets = packets.build_all(cls.core, cls.doc)

    def test_ten_packets_with_exact_refs_and_no_clipping(self):
        self.assertEqual(list(self.packets), ["D01", "D02", "D03", "D04", "D05", "C01", "C02", "C03", "I01", "I02"])
        for case in self.doc["cases"]:
            p = self.packets[case["id"]]
            self.assertEqual(p["refs"], ["request", "answer", "tool-0001-call", "tool-0001-result"])
            self.assertEqual(p["clipped_or_redacted_evidence"], [])
            self.assertEqual(p["coverage"]["tool_pairs_omitted"], 0)
            self.assertLessEqual(p["bytes"], 32768)

    def test_reviewer_view_is_whitelist(self):
        view = packets.reviewer_view(self.doc["cases"][0])
        self.assertEqual(set(view), {"request", "answer", "tool_evidence"})
        for entry in view["tool_evidence"]:
            self.assertFalse(set(entry) - {"tool", "call", "result", "status", "truncation"})

    def test_unknown_tool_key_fails_closed(self):
        case = copy.deepcopy(self.doc["cases"][0])
        case["capture"]["tool_evidence"][0]["expected"] = "label"
        with self.assertRaises(packets.PacketProjectionError):
            packets.reviewer_view(case)

    def test_no_gold_in_any_packet_and_leak_check_detects_injection(self):
        self.assertEqual(packets.leak_check(self.doc, self.packets), [])
        bad = copy.deepcopy(self.packets)
        obs = self.doc["cases"][0]["expected_observations"][0]["observation"]
        bad["D01"]["payload"]["evidence"][1]["text"] += " " + obs
        self.assertTrue(packets.leak_check(self.doc, bad))
        bad = copy.deepcopy(self.packets)
        bad["C01"]["payload"]["evidence"][0]["text"] += " correct_control"
        self.assertTrue(packets.leak_check(self.doc, bad))

    def test_serialized_packets_exclude_labels(self):
        text = json.dumps({k: v["payload"] for k, v in self.packets.items()})
        for token in ("supported_defect", "correct_control", "correct_abstention", "expected_observations",
                      "independent_defect_annotation", "run_status", "NOT RUN", '"kind": "defect"'):
            self.assertNotIn(token, text)

    def test_gold_ref_map_and_separation(self):
        gold = packets.build_gold(self.doc)
        self.assertEqual(gold["cases"]["D04"]["frozen_to_packet_refs"],
                         {"REQ": ["request"], "ANS": ["answer"], "TOOL1": ["tool-0001-call", "tool-0001-result"]})
        self.assertEqual(gold["cases"]["I01"]["capture_coverage"]["complete"], False)
        for obs in gold["cases"]["D01"]["expected_observations"]:
            self.assertTrue(set(obs["packet_refs"]) <= set(self.packets["D01"]["refs"]))

    def test_digests_deterministic(self):
        again = packets.build_all(self.core, packets.load_cases(REPO))
        self.assertEqual({k: v["digest"] for k, v in again.items()}, {k: v["digest"] for k, v in self.packets.items()})


class PrepareTests(unittest.TestCase):
    def test_dry_run_deterministic_not_run_and_separate_files(self):
        with support.tmpdir() as tmp:
            tmp = Path(tmp)
            a, sha_a = support.prepared_in(tmp / "a")
            b, sha_b = support.prepared_in(tmp / "b")
            self.assertEqual(sha_a, sha_b)
            self.assertEqual((a / "manifest.json").read_bytes(), (b / "manifest.json").read_bytes())
            manifest = json.loads((a / "manifest.json").read_text())
            self.assertEqual(manifest["inference"], "NOT RUN")
            self.assertEqual(manifest["route"], common.FIXED_ROUTE)
            self.assertEqual(manifest["max_calls"], 10)
            self.assertIn("NOT RUN", (a / "dry-run-report.md").read_text())
            packets_text = (a / "packets.json").read_text()
            gold = json.loads((a / "gold.json").read_text())
            for case in gold["cases"].values():
                self.assertNotIn(case["expected_observations"][0]["observation"], packets_text)
            check = subprocess.run(["sha256sum", "-c", "manifest.sha256"], cwd=a, capture_output=True, text=True)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertLess(float(manifest["cost_ceiling"]["with_3x_unobserved_host_retry_allowance_usd"]), 1.0)

    def test_load_prepared_gates(self):
        with support.tmpdir() as tmp:
            out, sha = support.prepared_in(Path(tmp))
            _m, _p, got = prep.load_prepared(REPO, out, sha)
            self.assertEqual(got, sha)
            with self.assertRaises(prep.PreparationError):
                prep.load_prepared(REPO, out, "0" * 64)
            manifest_path = out / "manifest.json"
            manifest_path.write_text(manifest_path.read_text().replace("NOT RUN", "NOT  RUN", 1))
            with self.assertRaises(prep.PreparationError):
                prep.load_prepared(REPO, out, sha)


if __name__ == "__main__":
    unittest.main()

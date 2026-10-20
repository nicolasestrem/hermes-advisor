"""Scoring and report pipeline. Applies the frozen scoring_rules.json; never redefines success.

Inputs: results.json (reviewer outputs) + gold.json (held out) + optional adjudication.json.
Mechanical classes use only the strictly validated outcome. Semantic judgements require
explicit human/parent labels; absent labels leave a case ``pending`` rather than guessed.
"""
from __future__ import annotations

import json
from collections import Counter
from decimal import Decimal
from pathlib import Path

from . import SCHEMA
from .common import RUNNER_DIR, canonical_json, sha256_bytes

RULES_PATH = RUNNER_DIR / "scoring_rules.json"
KINDS = ("defect", "control", "incomplete_evidence")
DENOM = {"defect": 5, "control": 3, "incomplete_evidence": 2}


def load_rules() -> dict:
    return json.loads(RULES_PATH.read_text(encoding="utf-8"))


class AdjudicationError(ValueError):
    pass


def validate_adjudication(adj: dict, results: dict, gold: dict, rules: dict) -> dict:
    """Strict: unknown case, wrong label count, label not allowed for the kind, bad rating."""
    if not isinstance(adj, dict) or adj.get("schema") != SCHEMA + "/adjudication" or not isinstance(adj.get("cases"), dict):
        raise AdjudicationError("adjudication must be {schema: advisor-eval/1/adjudication, adjudicator, cases{}}")
    out = {}
    for case_id, entry in adj["cases"].items():
        if case_id not in gold["cases"]:
            raise AdjudicationError(f"unknown case {case_id}")
        if set(entry) - {"finding_labels", "rating", "notes"}:
            raise AdjudicationError(f"{case_id}: unexpected keys")
        kind = gold["cases"][case_id]["kind"]
        record = results["cases"].get(case_id, {})
        n = len((record.get("outcome") or {}).get("findings", []))
        labels = entry.get("finding_labels", [])
        if not isinstance(labels, list) or len(labels) != n:
            raise AdjudicationError(f"{case_id}: need exactly {n} finding_labels (null = undecided)")
        for label in labels:
            if label is not None and label not in rules["allowed_labels_by_kind"][kind]:
                raise AdjudicationError(f"{case_id}: label {label!r} not allowed for {kind}")
        rating = entry.get("rating")
        if rating is not None and rating not in rules["ratings"]:
            raise AdjudicationError(f"{case_id}: rating must be one of {rules['ratings']} or null")
        out[case_id] = {"finding_labels": labels, "rating": rating, "notes": entry.get("notes", "")}
    return out


def mechanical_class(rules: dict, kind: str, record: dict) -> str:
    status = record.get("status")
    if status in (None, "not_run"):
        return "NOT_RUN"
    if status != "completed":
        return rules["non_completed_statuses_class"]
    return rules["mechanical"][kind][record["outcome"]["outcome"]]


def final_class(rules: dict, kind: str, mech: str, record: dict, adj: dict | None) -> tuple[str, str]:
    """Returns (class, adjudication_state: 'n/a'|'complete'|'pending')."""
    if mech not in rules["pending_classes"]:
        return mech, "n/a"
    labels = (adj or {}).get("finding_labels")
    if not labels or any(label is None for label in labels):
        return "pending_adjudication", "pending"
    rule = rules["adjudicated"][mech]
    if mech == "detection_candidate":
        return (rule["any_supported_true_positive"] if "supported_true_positive" in labels
                else rule["otherwise"]), "complete"
    if mech == "false_positive_candidate":
        return (rule["only_other_valid_issue_labels"] if set(labels) == {"other_valid_issue"}
                else rule["otherwise"]), "complete"
    bad = {"unsupported_accusation", "false_positive"} & set(labels)
    if bad:
        return rule["any_unsupported_accusation_or_false_positive"], "complete"
    if "supported_unverifiable_recommendation" in labels:
        return rule["any_supported_unverifiable_recommendation"], "complete"
    return rule["otherwise"], "complete"


def _p50(values):
    values = sorted(values)
    return values[len(values) // 2] if values else None


def score_run(results: dict, gold: dict, adjudication: dict | None = None) -> dict:
    rules = load_rules()
    adj = validate_adjudication(adjudication, results, gold, rules) if adjudication else {}
    cases = {}
    label_counts = Counter()
    for case_id, spec in gold["cases"].items():
        record = results["cases"].get(case_id, {"status": "not_run"})
        mech = mechanical_class(rules, spec["kind"], record)
        final, state = final_class(rules, spec["kind"], mech, record, adj.get(case_id))
        findings = (record.get("outcome") or {}).get("findings", [])
        for label in adj.get(case_id, {}).get("finding_labels", []):
            label_counts[label or "undecided"] += 1
        cases[case_id] = {
            "kind": spec["kind"], "status": record.get("status", "not_run"), "mechanical_class": mech,
            "final_class": final, "adjudication": state,
            "success": final in rules["success_classes"][spec["kind"]],
            "outcome": (record.get("outcome") or {}).get("outcome"), "finding_count": len(findings),
            "latency_seconds": record.get("latency_seconds"), "usage": record.get("usage"),
            "served_route": record.get("served_route"), "rating": adj.get(case_id, {}).get("rating"),
        }
    by_kind = {}
    for kind in KINDS:
        ids = [c for c, v in cases.items() if v["kind"] == kind]
        by_kind[kind] = {"denominator": DENOM[kind], "success": sum(cases[c]["success"] for c in ids),
                         "classes": dict(Counter(cases[c]["final_class"] for c in ids))}
    known = [v["usage"] for v in cases.values() if v["usage"] and v["usage"].get("known")]
    est = [Decimal(u["cost_usd_estimate"]) for u in known if u.get("cost_usd_estimate")]
    lat = [v["latency_seconds"] for v in cases.values() if v["latency_seconds"] is not None]
    ratings = Counter(v["rating"] or "unrated" for v in cases.values())
    return {
        "schema": SCHEMA + "/scores", "mode": results["mode"], "run_id": results["run_id"],
        "rules_sha256": sha256_bytes(RULES_PATH.read_bytes()),
        "cases": cases, "by_kind": by_kind,
        "success_total": sum(v["success"] for v in cases.values()), "denominator_total": 10,
        "pending_adjudication": sorted(c for c, v in cases.items() if v["adjudication"] == "pending"),
        "failed_retained": sorted(c for c, v in cases.items() if v["final_class"] == "failed_retained"),
        "finding_label_counts": dict(label_counts),
        "latency_seconds": {"count": len(lat), "p50": _p50(lat), "max": max(lat) if lat else None,
                            "sum": round(sum(lat), 3)},
        "usage": {"cases_known": len(known), "cases_unknown": 10 - len(known),
                  "input_tokens_known_sum": sum(u["input_tokens"] or 0 for u in known),
                  "output_tokens_known_sum": sum(u["output_tokens"] or 0 for u in known),
                  "cost_usd_estimate_known_sum": str(sum(est, Decimal(0))) if est else None,
                  "cost_cases_estimated": len(est),
                  "note": "Sums cover only cases with known usage; unknown cases contribute unknown (not zero). Estimates use payer rates, not an invoice."},
        "served_routes": dict(Counter(json.dumps(v["served_route"], sort_keys=True) if v["served_route"] else "unknown"
                                      for v in cases.values())),
        "ratings": dict(ratings), "halted": results.get("halted"),
    }


def adjudication_template(results: dict) -> dict:
    return {"schema": SCHEMA + "/adjudication", "adjudicator": None,
            "instructions": "Fill finding_labels (one per finding, in order) using scoring_rules.json labels; rating Useful|Not useful; leave null if undecided. Compare with gold.json only after reading the reviewer output.",
            "cases": {cid: {"finding_labels": [None] * len((rec.get("outcome") or {}).get("findings", [])),
                            "rating": None, "notes": ""}
                      for cid, rec in results["cases"].items()}}


def _banner(results: dict | None) -> str:
    if results is None:
        return "**NOT RUN — no inference was performed. Packets/hashes/schema validation only.**"
    if results["mode"] == "synthetic_test":
        return "**SYNTHETIC HARNESS TEST — fake reviewer, NOT model evidence.**"
    cls = results["header"]["facade_class"]
    if not cls.startswith("agent.plugin_llm."):
        return f"**WARNING: facade class `{cls}` is not the host plugin facade; do not treat as live host evidence.**"
    return "Live parent-run evaluation through the host plugin facade. Model usefulness result; deterministic product tests are reported separately."


def dry_run_report(manifest: dict, gold: dict) -> str:
    lines = ["# G4 frozen ten-case evaluation — dry run", "", _banner(None), "",
             f"- Route (fixed, no substitution): `{manifest['route']['provider']}/{manifest['route']['model']}`",
             f"- Bounds: max {manifest['max_calls']} calls, output_tokens {manifest['settings']['output_tokens']}, "
             f"deadline {manifest['settings']['deadline_seconds']}s/call, wall {manifest['total_wall_seconds_bound']}s, no replay",
             f"- Nominal cost ceiling: ${manifest['cost_ceiling']['nominal_total_usd']} "
             f"(${manifest['cost_ceiling']['with_3x_unobserved_host_retry_allowance_usd']} with a 3x unobserved-retry allowance); estimate only",
             f"- Frozen sha256: " + ", ".join(f"`{k}`={v[:12]}…" for k, v in manifest["freeze"]["frozen_sha256"].items()), "",
             "| case | kind | packet sha256 | bytes | status |", "|---|---|---|---|---|"]
    for cid, spec in gold["cases"].items():
        p = manifest["packet_digests"][cid]
        lines.append(f"| {cid} | {spec['kind']} | `{p['sha256'][:16]}…` | {p['bytes']} | NOT RUN |")
    lines += ["", "All 10 cases: NOT RUN. G0–G4 status is not changed by this report."]
    return "\n".join(lines) + "\n"


def render_report(results: dict, scores: dict, gold: dict) -> str:
    L = [f"# G4 frozen ten-case usefulness report — run `{results['run_id']}`", "", _banner(results), "",
         f"- Requested route: `{results['header']['requested_route']['provider']}/{results['header']['requested_route']['model']}`; "
         f"served routes: {scores['served_routes']}",
         f"- Manifest sha256: `{results['header']['manifest_sha256']}`; scoring rules sha256: `{scores['rules_sha256'][:16]}…`",
         f"- Calls reserved: {results['reserved_calls']} (bound 10); wall {results['wall_seconds']}s; halted: {results['halted']}",
         f"- Physical HTTP retries: {results['header']['physical_http_retry_count']}", ""]
    L += ["## Counts (fixed denominators; failures retained, never counted as success)", "",
          "| kind | success | denominator | classes |", "|---|---|---|---|"]
    for kind, v in scores["by_kind"].items():
        L.append(f"| {kind} | {v['success']} | {v['denominator']} | {v['classes']} |")
    L += ["", f"Overall success: {scores['success_total']}/10. Pending adjudication: {scores['pending_adjudication'] or 'none'}. "
          f"Failed/retained: {scores['failed_retained'] or 'none'}.",
          "Success counts only adjudicated or mechanically final classes; a `*_candidate`/pending case is not a success. "
          "No numeric usefulness threshold exists — the user decides.", "",
          f"Finding labels (adjudicated): {scores['finding_label_counts'] or 'none supplied'}. Ratings: {scores['ratings']}.", "",
          "## Latency and usage", "",
          f"- Latency: {scores['latency_seconds']}",
          f"- Usage known for {scores['usage']['cases_known']}/10 cases; unknown for {scores['usage']['cases_unknown']}. "
          f"Known token sums: in={scores['usage']['input_tokens_known_sum']}, out={scores['usage']['output_tokens_known_sum']}; "
          f"cost estimate over {scores['usage']['cost_cases_estimated']} case(s): {scores['usage']['cost_usd_estimate_known_sum']} USD "
          "(payer-rate estimate, not an invoice; unknown cases are unknown, not $0).", "", "## Per case", ""]
    for cid, spec in gold["cases"].items():
        rec = results["cases"][cid]
        sc = scores["cases"][cid]
        L += [f"### {cid} ({spec['kind']}) — {sc['final_class']}", "",
              f"- status `{rec['status']}`; mechanical `{sc['mechanical_class']}`; adjudication {sc['adjudication']}; rating {sc['rating'] or 'unrated'}",
              f"- latency {rec.get('latency_seconds')}s; served {rec.get('served_route')}; usage {rec.get('usage')}"]
        if rec.get("error"):
            L.append(f"- error: {rec['error']}")
        if rec.get("response_excerpt"):
            L.append(f"- invalid-response excerpt (sanitized): `{rec['response_excerpt'][:600]}`")
        if rec.get("halt_reason"):
            L.append(f"- not run: {rec['halt_reason']}")
        outcome = rec.get("outcome")
        if outcome:
            L.append(f"- outcome `{outcome['outcome']}`")
            for i, f in enumerate(outcome["findings"]):
                L.append(f"  - finding {i}: [{f['severity']}] refs={f['evidence_refs']} — {f['explanation']} (next: {f['next_step']})")
        L.append(f"- gold (held out until now): {spec['expected_observations'][0]['class']}: {spec['expected_observations'][0]['observation']}")
        L.append("")
    return "\n".join(L) + "\n"


def score_files(run_dir: Path, gold_path: Path, adjudication_path: Path | None = None) -> dict:
    run_dir = Path(run_dir)
    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    gold = json.loads(Path(gold_path).read_text(encoding="utf-8"))
    adjudication = json.loads(Path(adjudication_path).read_text(encoding="utf-8")) if adjudication_path else None
    scores = score_run(results, gold, adjudication)
    (run_dir / "scores.json").write_text(canonical_json(scores), encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(results, scores, gold), encoding="utf-8")
    template = run_dir / "adjudication-template.json"
    if not adjudication_path:
        template.write_text(canonical_json(adjudication_template(results)), encoding="utf-8")
    return scores

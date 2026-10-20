"""Dry-run preparation: freeze check, packets, gold, schema, hashes. NOT RUN inference."""
from __future__ import annotations

import json
import math
from decimal import Decimal
from pathlib import Path

from . import SCHEMA
from .common import (AUTHORIZE_TOKEN, FIXED_ROUTE, FROZEN_FILES, MAX_CALLS, RATES_USD_PER_MILLION,
                     RUNNER_DIR, RUNNER_FILES, SETTINGS, SOURCE_FILES, TOTAL_WALL_SECONDS,
                     canonical_json, load_product, sha256_bytes, sha256_file)
from .freeze import require_freeze
from .packets import build_all, build_gold, leak_check, load_cases

EXPECTED_IDS = ["D01", "D02", "D03", "D04", "D05", "C01", "C02", "C03", "I01", "I02"]
EXPECTED_KINDS = {"defect": 5, "control": 3, "incomplete_evidence": 2}


class PreparationError(RuntimeError):
    pass


def cost_ceiling(packet_chars: dict, instructions: str, schema_chars: dict) -> dict:
    """Nominal worst-case token cost for MAX_CALLS calls. Estimate, not a provider cap."""
    rin, rout = (Decimal(RATES_USD_PER_MILLION[k]) / Decimal(1_000_000) for k in ("input", "output"))
    per_case = {}
    total = Decimal(0)
    for case_id, chars in packet_chars.items():
        in_tokens = math.ceil((len(instructions) + schema_chars[case_id] + chars) / 2.5) + 200
        cost = in_tokens * rin + SETTINGS["output_tokens"] * rout
        per_case[case_id] = {"input_tokens_upper_estimate": in_tokens,
                             "output_tokens_cap": SETTINGS["output_tokens"], "usd": str(cost.quantize(Decimal("0.000001")))}
        total += cost
    return {
        "basis": "chars/2.5 + 200 input-token upper estimate; output at the 2048-token cap; payer-reported rates; NOT an invoice or a provider-enforced cap",
        "rates_usd_per_million_tokens": RATES_USD_PER_MILLION,
        "per_case": per_case,
        "nominal_total_usd": str(total.quantize(Decimal("0.000001"))),
        "with_3x_unobserved_host_retry_allowance_usd": str((total * 3).quantize(Decimal("0.000001"))),
        "note": "The public facade does not expose physical HTTP retries; the 3x line is a stated allowance, not an observed bound.",
    }


def source_hashes(repo: Path, names) -> dict:
    return {name: sha256_file(repo / name) for name in names if (repo / name).exists()} | {
        name: "MISSING" for name in names if not (repo / name).exists()}


def missing_artifacts(repo: Path) -> list[str]:
    """Run inputs the evaluator cannot score without (the frozen rules plus product source)."""
    return sorted(name for name in (*RUNNER_FILES, *SOURCE_FILES) if not (repo / name).exists())


def build_manifest(repo: Path) -> tuple[dict, dict, dict, dict]:
    """Return (manifest, packets, gold, freeze_result). Raises on any validation failure."""
    repo = Path(repo)
    # A packaged evaluator whose scoring rules (or product source) are absent can
    # still record the file as MISSING and complete the paid live run, then be
    # unable to score it. Refuse before any inference instead.
    missing = missing_artifacts(repo)
    if missing:
        raise PreparationError(
            "required evaluator artifacts are missing: " + ", ".join(missing)
        )
    freeze = require_freeze(repo)
    cases_doc = load_cases(repo)
    cases = cases_doc["cases"]
    if [c["id"] for c in cases] != EXPECTED_IDS or cases_doc["case_count"] != 10:
        raise PreparationError("case ids/count differ from the frozen ten")
    kinds = {}
    for case in cases:
        kinds[case["kind"]] = kinds.get(case["kind"], 0) + 1
    if kinds != EXPECTED_KINDS:
        raise PreparationError(f"kind counts {kinds} != {EXPECTED_KINDS}")
    if len(cases) > MAX_CALLS:
        raise PreparationError("case count exceeds the call bound")

    core, host = load_product(repo)
    packets = build_all(core, cases_doc)
    for case in cases:
        packet = packets[case["id"]]
        if packet["bytes"] > SETTINGS["packet_bytes"]:
            raise PreparationError(f"{case['id']}: packet exceeds byte cap")
        if packet["refs"][:2] != ["request", "answer"]:
            raise PreparationError(f"{case['id']}: packet must begin with request, answer")
        if len(packet["refs"]) != 2 + 2 * len(case["capture"]["tool_evidence"]):
            raise PreparationError(f"{case['id']}: tool evidence not fully represented")
        if packet["coverage"]["tool_pairs_omitted"] != 0 or packet["coverage"]["clipped_refs"]:
            raise PreparationError(f"{case['id']}: packet clipped/omitted evidence unexpectedly")
        if packet["clipped_or_redacted_evidence"]:
            raise PreparationError(f"{case['id']}: redaction/clipping altered evidence {packet['clipped_or_redacted_evidence']}")
    leaks = leak_check(cases_doc, packets)
    if leaks:
        raise PreparationError("gold leaked into reviewer packets: " + "; ".join(leaks))

    # Strict response schema + validator agree with each packet's refs (offline; no model).
    schema_chars = {}
    schema_checks = {}
    for case_id, packet in packets.items():
        schema = host.response_schema(packet["refs"], SETTINGS["max_findings"])
        item = schema["properties"]["findings"]["items"]
        ok = (schema["additionalProperties"] is False and item["additionalProperties"] is False
              and item["properties"]["evidence_refs"]["items"]["enum"] == packet["refs"]
              and schema["properties"]["findings"]["maxItems"] == SETTINGS["max_findings"]
              and set(schema["required"]) == {"outcome", "findings"})
        if not ok:
            raise PreparationError(f"{case_id}: response schema does not match packet refs/limits")
        schema_chars[case_id] = len(json.dumps(schema))
        schema_checks[case_id] = {"schema_sha256": sha256_bytes(canonical_json(schema, indent=None).encode())}
        # validator accepts the empty-result contract and rejects a fabricated ref (offline sanity)
        core.validate_response({"outcome": "no_findings", "findings": []}, set(packet["refs"]),
                               max_findings=SETTINGS["max_findings"])
        try:
            core.validate_response({"outcome": "findings", "findings": [
                {"severity": "nit", "explanation": "x", "evidence_refs": ["not-a-ref"], "next_step": "y"}]},
                set(packet["refs"]), max_findings=SETTINGS["max_findings"])
        except core.ResponseValidationError:
            pass
        else:
            raise PreparationError("validator accepted an unavailable evidence ref")
    core.validate_settings(SETTINGS)
    import inspect
    if not inspect.iscoroutinefunction(host.Runtime.review):
        raise PreparationError("advisor_host.Runtime.review is not an async function")

    gold = build_gold(cases_doc)
    packets_doc = {"schema": SCHEMA + "/packets", "inference": "NOT RUN",
                   "reviewer_view_only": True,
                   "cases": {cid: {k: v for k, v in p.items() if k != "source_chars"} for cid, p in packets.items()}}
    packets_bytes = canonical_json(packets_doc).encode()
    gold_bytes = canonical_json(gold).encode()

    manifest = {
        "schema": SCHEMA + "/manifest",
        "inference": "NOT RUN",
        "purpose": "Frozen G4 ten-case usefulness evaluation preparation; no model call made",
        "freeze": {"tool": freeze["tool"], "tool_exit": freeze["tool_exit"],
                   "frozen_sha256": freeze["frozen_sha256"], "freeze_file_sha256": freeze["freeze_file_sha256"]},
        "route": FIXED_ROUTE, "settings": SETTINGS, "max_calls": MAX_CALLS,
        "total_wall_seconds_bound": TOTAL_WALL_SECONDS, "replay": "none (reserve-before-call ledger; no retries)",
        "authorize_token_required_for_live": AUTHORIZE_TOKEN,
        "reviewer_entry_point": "advisor_host.Runtime.review (called with a ctx-only shim; no Service/store/worker)",
        "instructions_sha256": sha256_bytes(host.INSTRUCTIONS.encode()),
        "packet_digests": {cid: {"sha256": p["digest"], "bytes": p["bytes"], "refs": p["refs"]} for cid, p in packets.items()},
        "response_schemas": schema_checks,
        "packets_json_sha256": sha256_bytes(packets_bytes),
        "gold_json_sha256": sha256_bytes(gold_bytes),
        "cost_ceiling": cost_ceiling({c: p["source_chars"] for c, p in packets.items()}, host.INSTRUCTIONS, schema_chars),
        "product_source_sha256": source_hashes(repo, SOURCE_FILES),
        "runner_source_sha256": source_hashes(repo, RUNNER_FILES),
    }
    return manifest, packets_doc, gold, freeze


def prepare(repo: Path, out: Path) -> dict:
    from .score import dry_run_report
    manifest, packets_doc, gold, freeze = build_manifest(repo)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    files = {"packets.json": canonical_json(packets_doc), "gold.json": canonical_json(gold),
             "manifest.json": canonical_json(manifest),
             "freeze-check.json": canonical_json(freeze),
             "dry-run-report.md": dry_run_report(manifest, gold)}
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    manifest_sha = sha256_bytes(files["manifest.json"].encode())
    (out / "manifest.sha256").write_text(f"{manifest_sha}  manifest.json\n", encoding="utf-8")
    return {"out": str(out), "manifest_sha256": manifest_sha, "inference": "NOT RUN",
            "packets": len(packets_doc["cases"])}


def load_prepared(repo: Path, prepared: Path, expect_manifest_sha256: str) -> tuple[dict, dict, str]:
    """Live-run gate: rebuild the manifest now and require it to equal both the stored
    prepared manifest and the parent-supplied hash. Returns (manifest, packets_doc, sha)."""
    manifest, packets_doc, _gold, _freeze = build_manifest(repo)
    current = canonical_json(manifest).encode()
    current_sha = sha256_bytes(current)
    stored = (Path(prepared) / "manifest.json").read_bytes()
    if sha256_bytes(stored) != current_sha:
        raise PreparationError("prepared manifest is stale: re-run dry-run and re-pin its sha256")
    if current_sha != expect_manifest_sha256:
        raise PreparationError("manifest sha256 does not match --expect-manifest-sha256")
    return manifest, packets_doc, current_sha

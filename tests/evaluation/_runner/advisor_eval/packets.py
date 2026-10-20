"""Reviewer-visible packet projection (whitelist), gold extraction, leak check.

The reviewer view is built ONLY from capture.request.text, capture.answer.text and
capture.tool_evidence entries. capture.coverage, expected_observations,
independent_defect_annotation, kind, tags, ids and run_status never enter a packet.
Packets are built by the product's public ``advisor_core.build_packet``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .common import SETTINGS

TOOL_KEYS_ALLOWED = {"ref", "tool", "call", "result", "status", "truncation"}
TOOL_CALL_KEYS = ("tool", "call")
TOOL_RESULT_KEYS = ("result", "status", "truncation")


class PacketProjectionError(ValueError):
    pass


def load_cases(repo: Path) -> dict:
    return json.loads((Path(repo) / "evaluation" / "cases.json").read_text(encoding="utf-8"))


def reviewer_view(case: dict) -> dict:
    """Whitelist projection: the only data a reviewer may see for a case."""
    capture = case["capture"]
    tools = []
    for entry in capture["tool_evidence"]:
        unknown = set(entry) - TOOL_KEYS_ALLOWED
        if unknown:  # fail closed: an unexpected key could carry a label
            raise PacketProjectionError(f"{case['id']}: unexpected tool_evidence keys {sorted(unknown)}")
        tools.append({k: entry[k] for k in TOOL_CALL_KEYS + TOOL_RESULT_KEYS if k in entry})
    return {"request": capture["request"]["text"], "answer": capture["answer"]["text"],
            "tool_evidence": tools}


def history_from_view(view: dict) -> list[dict]:
    history = []
    for entry in view["tool_evidence"]:
        history.append({
            "call": {k: entry[k] for k in TOOL_CALL_KEYS if k in entry},
            "result": {k: entry[k] for k in TOOL_RESULT_KEYS if k in entry},
        })
    return history


def ref_map(case: dict) -> dict:
    """Frozen REQ/ANS/TOOLn labels -> product packet refs (by tool_evidence position)."""
    mapping = {case["capture"]["request"]["ref"]: ["request"],
               case["capture"]["answer"]["ref"]: ["answer"]}
    for index, entry in enumerate(case["capture"]["tool_evidence"], start=1):
        mapping[entry["ref"]] = [f"tool-{index:04d}-call", f"tool-{index:04d}-result"]
    return mapping


def _map_refs(refs, mapping):
    out = []
    for ref in refs:
        out.extend(mapping[ref])
    return out


def build_packet_for_case(core, case: dict):
    view = reviewer_view(case)
    return core.build_packet(view["request"], view["answer"], history_from_view(view),
                             packet_bytes=SETTINGS["packet_bytes"])


def build_all(core, cases_doc: dict) -> dict:
    """id -> {digest, bytes, refs, coverage, payload, redaction_changed}."""
    out = {}
    for case in cases_doc["cases"]:
        packet = build_packet_for_case(core, case)
        payload = packet.as_payload()
        view = reviewer_view(case)
        source_text = [view["request"], view["answer"]] + [
            json.dumps(t, ensure_ascii=False, sort_keys=True) for t in view["tool_evidence"]]
        out[case["id"]] = {
            "digest": packet.digest, "bytes": packet.byte_count, "refs": list(packet.references),
            "coverage": packet.coverage_dict(), "payload": payload,
            "clipped_or_redacted_evidence": [e["ref"] for e in payload["evidence"]
                                             if e["clipped"] or "[REDACTED]" in e["text"]],
            "source_chars": sum(len(s) for s in source_text),
        }
    return out


def build_gold(cases_doc: dict) -> dict:
    gold = {"schema": "advisor-eval-gold/1", "note": "Held out of every reviewer packet.",
            "cases": {}}
    for case in cases_doc["cases"]:
        mapping = ref_map(case)
        gold["cases"][case["id"]] = {
            "kind": case["kind"], "tags": case["tags"],
            "frozen_to_packet_refs": mapping,
            "capture_coverage": case["capture"]["coverage"],
            "expected_observations": [
                {**obs, "packet_refs": _map_refs(obs["evidence_refs"], mapping)}
                for obs in case["expected_observations"]],
            "independent_defect_annotation": case["independent_defect_annotation"],
        }
    return gold


def gold_strings(case: dict) -> list[str]:
    # Label tokens (snake_case: natural words such as "pagination" may legitimately
    # appear in a request) plus complete free-text gold sentences.
    labels = [case["kind"], *case["tags"], *(o["class"] for o in case["expected_observations"])]
    strings = [s for s in labels if "_" in s]
    strings.append(case["capture"]["coverage"]["notes"])
    strings += [o["observation"] for o in case["expected_observations"]]
    ann = case["independent_defect_annotation"]
    strings += [v for v in (ann.get("annotation"), ann.get("defect"), ann.get("why_independently_verifiable"))
                if isinstance(v, str)]
    return strings


def leak_check(cases_doc: dict, packets: dict) -> list[str]:
    """Return a list of leak descriptions (empty = no gold string, id or label in a packet)."""
    leaks = []
    for case in cases_doc["cases"]:
        serialized = json.dumps(packets[case["id"]]["payload"], ensure_ascii=False)
        for text in gold_strings(case):
            if text in serialized:
                leaks.append(f"{case['id']}: gold string present in packet: {text[:60]!r}")
        if re.search(rf"\b{re.escape(case['id'])}\b", serialized):
            leaks.append(f"{case['id']}: case id present in packet")
    return leaks

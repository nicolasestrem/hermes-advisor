"""Whole-response schema validation against the exact supplied packet refs."""

from __future__ import annotations

from collections.abc import Mapping, Set
from typing import Any


class ResponseValidationError(ValueError):
    """Raised when any portion of a reviewer result violates the contract."""


_OUTCOMES = {"findings", "no_findings", "insufficient_evidence"}
_SEVERITIES = {"nit", "concern", "blocker"}
_RESULT_KEYS = {"outcome", "findings"}
_FINDING_KEYS = {"severity", "explanation", "evidence_refs", "next_step"}


def validate_response(
    result: Any,
    supplied_refs: Set[str],
    *,
    max_findings: int = 4,
) -> dict[str, Any]:
    """Validate every field; never salvage a valid-looking subset."""
    if type(max_findings) is not int or not 1 <= max_findings <= 4:
        raise ResponseValidationError("invalid applied finding limit")
    if not isinstance(result, Mapping) or set(result) != _RESULT_KEYS:
        raise ResponseValidationError("response must contain exactly outcome and findings")
    outcome = result["outcome"]
    findings = result["findings"]
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise ResponseValidationError("unknown outcome")
    if not isinstance(findings, list) or len(findings) > max_findings:
        raise ResponseValidationError("findings must be an array within the applied limit")
    if outcome == "findings" and not findings:
        raise ResponseValidationError("findings outcome requires at least one finding")
    if outcome != "findings" and findings:
        raise ResponseValidationError("non-findings outcomes require an empty findings array")
    if not isinstance(supplied_refs, Set) or any(not isinstance(ref, str) for ref in supplied_refs):
        raise ResponseValidationError("invalid packet reference set")

    normalized: list[dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, Mapping) or set(finding) != _FINDING_KEYS:
            raise ResponseValidationError("finding has an invalid shape")
        severity = finding["severity"]
        if not isinstance(severity, str) or severity not in _SEVERITIES:
            raise ResponseValidationError("unknown finding severity")
        explanation = finding["explanation"]
        next_step = finding["next_step"]
        if not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 2000:
            raise ResponseValidationError("explanation must be nonempty and within 2000 code points")
        if not isinstance(next_step, str) or not next_step.strip() or len(next_step) > 1000:
            raise ResponseValidationError("next_step must be nonempty and within 1000 code points")
        refs = finding["evidence_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ResponseValidationError("evidence_refs must contain one to eight IDs")
        if any(not isinstance(ref, str) or ref not in supplied_refs for ref in refs):
            raise ResponseValidationError("finding cites an unavailable evidence reference")
        if len(set(refs)) != len(refs):
            raise ResponseValidationError("evidence references must be distinct")
        normalized.append({
            "severity": severity,
            "explanation": explanation,
            "evidence_refs": list(refs),
            "next_step": next_step,
        })
    return {"outcome": outcome, "findings": normalized}

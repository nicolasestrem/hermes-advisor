"""Bounded, no-replay sequential run of the frozen packets through an injected reviewer.

``run_cases`` is mode-agnostic; the parent-run plugin passes the real product reviewer
(mode="live"). Tests pass a synthetic reviewer (mode="synthetic_test"), whose outputs are
stamped SYNTHETIC and are never model evidence.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from pathlib import Path

from . import SCHEMA
from .common import (FIXED_ROUTE, MAX_CALLS, PER_CALL_GRACE_SECONDS, RATES_USD_PER_MILLION, SETTINGS,
                     TOTAL_WALL_SECONDS, canonical_json, load_product)

REASONING_KEYS = {"reasoning", "reasoning_content", "reasoningcontent", "thinking", "analysis",
                  "chain_of_thought", "chainofthought", "internal_reasoning", "thought_process"}


class RunRefused(RuntimeError):
    pass


def _clean(obj, depth=0):
    if depth > 6:
        return "[depth-limited]"
    if isinstance(obj, dict):
        return {str(k): _clean(v, depth + 1) for k, v in obj.items()
                if str(k).lower().replace("-", "_") not in REASONING_KEYS}
    if isinstance(obj, (list, tuple)):
        return [_clean(v, depth + 1) for v in obj[:32]]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return f"<{type(obj).__name__}>"


def safe_excerpt(core, obj, limit=4000) -> str:
    """Sanitized, reasoning-stripped, bounded excerpt for retained failures."""
    try:
        text = json.dumps(_clean(obj), ensure_ascii=False, sort_keys=True, default=repr)
    except Exception:
        text = f"<unserializable {type(obj).__name__}>"
    return core.sanitize_text(text, max_chars=limit)


def normalize_usage(usage) -> dict:
    """Unknown stays unknown. The host facade zero-fills missing usage, so an all-zero or
    empty mapping is NOT treated as a known zero."""
    if usage is None:
        return {"known": False, "reason": "not_reported"}
    if not isinstance(usage, dict):
        return {"known": False, "reason": "unrecognized_shape"}

    def num(*names):
        for name in names:
            value = usage.get(name)
            if type(value) is int and value >= 0:
                return value
        return None

    inp, out = num("input_tokens", "prompt_tokens"), num("output_tokens", "completion_tokens")
    total = num("total_tokens")
    if not total and (inp or 0) + (out or 0) > 0:
        total = (inp or 0) + (out or 0)
    if not total:
        return {"known": False, "reason": "zero_or_empty_usage_treated_as_unknown (facade zero-fills missing usage)"}
    record = {"known": True, "input_tokens": inp, "output_tokens": out, "total_tokens": total}
    if inp and out:
        from decimal import Decimal
        cost = (Decimal(inp) * Decimal(RATES_USD_PER_MILLION["input"])
                + Decimal(out) * Decimal(RATES_USD_PER_MILLION["output"])) / Decimal(1_000_000)
        record["cost_usd_estimate"] = str(cost.quantize(Decimal("0.00000001")))
        record["cost_basis"] = "estimate from token counts x payer-reported rates; not an invoice"
    else:
        record["cost_usd_estimate"] = None
        record["cost_basis"] = "unknown: input or output tokens missing"
    if usage.get("cost_usd") not in (None, 0, 0.0):
        record["cost_usd_reported_by_host"] = usage["cost_usd"]
    return record


def _write(path: Path, text: str):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


class Ledger:
    """Append-only, fsynced. A reservation is written BEFORE the call and never retried."""

    def __init__(self, path: Path):
        self.path = path
        self.reserved = 0

    def append(self, event: dict):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def reserve(self, seq: int, case_id: str):
        if self.reserved >= MAX_CALLS:
            raise RunRefused("call bound reached")
        self.reserved += 1
        self.append({"event": "reserve", "seq": seq, "case": case_id, "t": time.time()})


def _served_route(result):
    provider = getattr(result, "provider", None)
    model = getattr(result, "model", None)
    return {"provider": provider, "model": model}


async def _one(core, review, case_id, packet, settings, record):
    refs = set(packet["refs"])
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(review(copy.deepcopy(packet["payload"]), dict(settings)),
                                        timeout=settings["deadline_seconds"] + PER_CALL_GRACE_SECONDS)
    except asyncio.TimeoutError:
        record.update(status="timeout", latency_seconds=round(time.monotonic() - started, 3),
                      error={"class": "TimeoutError", "detail": "outer deadline elapsed"})
        return
    except Exception as exc:  # retained, never retried, never hidden
        record.update(status="call_error", latency_seconds=round(time.monotonic() - started, 3),
                      error={"class": type(exc).__name__, "detail": core.sanitize_text(str(exc), max_chars=400)})
        return
    record["latency_seconds"] = round(time.monotonic() - started, 3)
    record["served_route"] = _served_route(result)
    record["usage"] = normalize_usage(getattr(result, "usage", None))
    response = getattr(result, "response", result)
    try:
        validated = core.validate_response(response, refs, max_findings=settings["max_findings"])
    except Exception as exc:
        record.update(status="invalid_response",
                      error={"class": type(exc).__name__, "detail": core.sanitize_text(str(exc), max_chars=400)},
                      response_excerpt=safe_excerpt(core, response))
        return
    record.update(status="completed", outcome=validated)


def run_cases(*, repo: Path, run_dir: Path, packets_doc: dict, review, mode: str, manifest_sha256: str,
              facade_class: str, order: list[str], settings: dict | None = None,
              runs_root_lock: Path | None = None, run_id: str) -> dict:
    """Run every case once, in order. mode: 'live' | 'synthetic_test'."""
    if mode not in ("live", "synthetic_test"):
        raise RunRefused("unknown mode")
    settings = dict(settings or SETTINGS)
    if settings["route"] != FIXED_ROUTE:
        raise RunRefused("route is fixed; no substitution")
    for key, limit in (("output_tokens", SETTINGS["output_tokens"]), ("deadline_seconds", SETTINGS["deadline_seconds"]),
                       ("max_findings", SETTINGS["max_findings"])):
        if settings[key] > limit:
            raise RunRefused(f"{key} exceeds the approved bound")
    if len(order) > MAX_CALLS or len(set(order)) != len(order):
        raise RunRefused("case list exceeds the call bound or repeats a case (no replay)")
    core, _host = load_product(repo)
    core.validate_settings(settings)

    run_dir = Path(run_dir)
    if run_dir.exists():
        raise RunRefused(f"run directory exists: {run_dir}")
    if mode == "live" and runs_root_lock is not None:
        Path(runs_root_lock).parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(runs_root_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError as exc:
            raise RunRefused(f"live run already started ({runs_root_lock}); a second paid run needs fresh parent authorization") from exc
        with os.fdopen(fd, "w") as handle:
            handle.write(json.dumps({"run_id": run_id, "manifest_sha256": manifest_sha256}) + "\n")
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "cases").mkdir()

    header = {
        "schema": SCHEMA + "/run", "run_id": run_id, "mode": mode,
        "inference": "LIVE host facade (parent-run)" if mode == "live" else "SYNTHETIC_NOT_MODEL_EVIDENCE",
        "facade_class": facade_class, "manifest_sha256": manifest_sha256,
        "requested_route": settings["route"], "settings": settings, "max_calls": MAX_CALLS,
        "order": order, "started_unix": time.time(),
        "physical_http_retry_count": "not established (host-owned; not exposed by the public facade)",
        "replay": "none",
    }
    _write(run_dir / "run-header.json", canonical_json(header))
    ledger = Ledger(run_dir / "ledger.jsonl")
    results = {}
    halted = None
    wall_start = time.monotonic()

    async def drive():
        nonlocal halted
        for seq, case_id in enumerate(order, start=1):
            record = {"case_id": case_id, "seq": seq, "requested_route": settings["route"],
                      "packet_sha256": packets_doc["cases"][case_id]["digest"]}
            if halted:
                record.update(status="not_run_halted", halt_reason=halted)
            elif time.monotonic() - wall_start > TOTAL_WALL_SECONDS:
                halted = "total_wall_clock_bound"
                record.update(status="not_run_halted", halt_reason=halted)
            else:
                ledger.reserve(seq, case_id)
                await _one(core, review, case_id, packets_doc["cases"][case_id], settings, record)
                served = record.get("served_route")
                if served and served["provider"] and served["model"] and served != FIXED_ROUTE:
                    record["route_deviation"] = True
                    halted = f"served route deviated at {case_id}: {served}"
                elif served and not (served["provider"] and served["model"]):
                    record["served_route_unknown"] = True
            record["settled"] = True
            ledger.append({"event": "settled", "seq": seq, "case": case_id, "status": record["status"]})
            _write(run_dir / "cases" / f"{case_id}.json", canonical_json(record))
            results[case_id] = record

    asyncio.run(drive())
    final = {"schema": SCHEMA + "/results", "run_id": run_id, "mode": mode, "header": header,
             "halted": halted, "reserved_calls": ledger.reserved,
             "wall_seconds": round(time.monotonic() - wall_start, 3),
             "cases": {cid: results[cid] for cid in order}}
    _write(run_dir / "results.json", canonical_json(final))
    return final


def run_live(*, repo: Path, prepared: Path, expect_manifest_sha256: str, authorize: str, run_id: str,
             review, facade_class: str, runs_root: Path) -> dict:
    """Entry for the parent-run plugin. All gates here; no inference outside ``review``."""
    from .common import AUTHORIZE_TOKEN
    from .prepare import load_prepared
    if authorize != AUTHORIZE_TOKEN:
        raise RunRefused("missing/incorrect live authorization token")
    if not run_id or any(ch in run_id for ch in "/\\") or run_id.startswith("."):
        raise RunRefused("run id must be a simple name")
    manifest, packets_doc, sha = load_prepared(repo, prepared, expect_manifest_sha256)
    order = [cid for cid in packets_doc["cases"]]
    return run_cases(repo=repo, run_dir=Path(runs_root) / run_id, packets_doc=packets_doc, review=review,
                     mode="live", manifest_sha256=sha, facade_class=facade_class, order=order,
                     runs_root_lock=Path(runs_root) / "LIVE-RUN-ONCE.lock", run_id=run_id)

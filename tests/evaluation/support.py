"""Shared helpers for tests/evaluation. Fake reviewers here are SYNTHETIC — never model evidence."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "tests" / "evaluation" / "_runner"
for path in (str(RUNNER), str(REPO)):
    if path not in sys.path:
        sys.path.insert(0, path)

from advisor_eval import common  # noqa: E402
from advisor_eval.prepare import prepare  # noqa: E402

ROUTE = dict(common.FIXED_ROUTE)
NO_FINDINGS = {"outcome": "no_findings", "findings": []}


def finding(refs=("answer",), severity="concern"):
    return {"severity": severity, "explanation": "synthetic", "evidence_refs": list(refs), "next_step": "synthetic"}


def result(response, *, route=None, usage="real"):
    route = ROUTE if route is None else route
    if usage == "real":
        usage = {"input_tokens": 600, "output_tokens": 100, "total_tokens": 700}
    return SimpleNamespace(response=response, provider=route.get("provider"), model=route.get("model"),
                           usage=usage, usage_known=usage is not None)


def tmpdir():
    return tempfile.TemporaryDirectory(prefix="advisor-eval-test-", dir=_scratch())


def _scratch():
    import os
    base = os.environ.get("TMPDIR")
    return base if base and Path(base).is_dir() else None


def prepared_in(tmp: Path) -> tuple[Path, str]:
    out = tmp / "prepared"
    summary = prepare(REPO, out)
    return out, summary["manifest_sha256"]

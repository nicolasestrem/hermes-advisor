"""Shared constants, hashing and product loading. No network, no credentials."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
RUNNER_DIR = Path(__file__).resolve().parents[1]

FROZEN_FILES = ("evaluation/cases.json", "evaluation/README.md")
FREEZE_FILE = "tests/evaluation/FREEZE.sha256"

# Fixed by the approved task; never substituted.
FIXED_ROUTE = {"provider": "nous", "model": "z-ai/glm-5.3-flash"}
# Hard bounds (the approved ceiling). Tightening is allowed; loosening is refused.
MAX_CALLS = 10
SETTINGS = {
    "route": dict(FIXED_ROUTE),
    "packet_bytes": 32768,
    "output_tokens": 2048,
    "max_findings": 4,
    "deadline_seconds": 90,
    "session_allowance": 10,
}
PER_CALL_GRACE_SECONDS = 10          # asyncio wait_for margin above the facade timeout
TOTAL_WALL_SECONDS = MAX_CALLS * (SETTINGS["deadline_seconds"] + PER_CALL_GRACE_SECONDS)
AUTHORIZE_TOKEN = "I-AUTHORIZE-G4-10-CALLS-NOUS-Z-AI-GLM-5.3-FLASH"

# Payer-reported rates (historical budget ledger); estimates only, never an invoice.
RATES_USD_PER_MILLION = {"input": "0.12", "output": "0.40"}

SOURCE_FILES = (
    "advisor_host.py", "host_adapter.py", "plugin.yaml", "__init__.py",
    "advisor_core/__init__.py", "advisor_core/config.py", "advisor_core/packet.py",
    "advisor_core/response.py", "advisor_core/service.py", "advisor_core/store.py",
    "advisor_core/worker.py",
)
RUNNER_FILES = (
    "tests/evaluation/_runner/scoring_rules.json",
    "tests/evaluation/_runner/advisor_eval/__init__.py",
    "tests/evaluation/_runner/advisor_eval/__main__.py",
    "tests/evaluation/_runner/advisor_eval/common.py",
    "tests/evaluation/_runner/advisor_eval/freeze.py",
    "tests/evaluation/_runner/advisor_eval/packets.py",
    "tests/evaluation/_runner/advisor_eval/prepare.py",
    "tests/evaluation/_runner/advisor_eval/runner.py",
    "tests/evaluation/_runner/advisor_eval/score.py",
    "tests/evaluation/_runner/plugin/advisor-eval/__init__.py",
    "tests/evaluation/_runner/plugin/advisor-eval/plugin.yaml",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(obj, *, indent: int | None = 2) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=indent) + "\n"


def load_product(repo: Path):
    """Import the product's public modules from ``repo`` (read-only, no runtime built)."""
    root = str(Path(repo).resolve())
    if root not in sys.path:
        sys.path.insert(0, root)
    import advisor_core  # noqa: WPS433
    import advisor_host  # noqa: WPS433
    for module in (advisor_core, advisor_host):
        origin = Path(module.__file__).resolve()
        if Path(root) not in origin.parents:
            raise RuntimeError(f"{module.__name__} imported from {origin}, outside {root}")
    return advisor_core, advisor_host

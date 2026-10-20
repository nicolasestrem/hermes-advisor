"""Resolve the single parent directory for scratch state created by offline tests.

One-line rule: ``ADVISOR_SCRATCH`` wins when set and non-empty; otherwise this
repository's tests use a per-user directory under the platform temporary root.
No caller is required to know a lab checkout path.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def resolve_scratch_root() -> Path:
    env = os.environ.get("ADVISOR_SCRATCH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(tempfile.gettempdir()) / "hermes-advisor-offline-tests"


SCRATCH = resolve_scratch_root()

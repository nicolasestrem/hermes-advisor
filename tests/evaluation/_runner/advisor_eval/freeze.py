"""Freeze verification through the real ``sha256sum -c`` tool (plus git cleanliness)."""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from .common import FREEZE_FILE, FROZEN_FILES, sha256_file


class FreezeError(RuntimeError):
    pass


def verify_freeze(repo: Path) -> dict:
    repo = Path(repo)
    proc = subprocess.run(["sha256sum", "-c", FREEZE_FILE], cwd=repo, capture_output=True,
                          text=True, timeout=60)
    ok_lines = [line for line in proc.stdout.splitlines() if line.endswith(": OK")]
    listed = {}
    for line in (repo / FREEZE_FILE).read_text(encoding="utf-8").splitlines():
        if line.strip():
            digest, _, name = line.partition("  ")
            listed[name.strip()] = digest.strip()
    recomputed = {name: sha256_file(repo / name) for name in FROZEN_FILES if (repo / name).exists()}
    problems = []
    if proc.returncode != 0:
        problems.append(f"sha256sum -c exit {proc.returncode}")
    if set(listed) != set(FROZEN_FILES):
        problems.append(f"FREEZE.sha256 covers {sorted(listed)}, expected {sorted(FROZEN_FILES)}")
    if len(ok_lines) != len(FROZEN_FILES):
        problems.append("sha256sum did not report OK for every frozen file")
    for name in FROZEN_FILES:
        if recomputed.get(name) != listed.get(name):
            problems.append(f"in-process digest mismatch for {name}")
    git = {"available": False}
    try:
        head = subprocess.run(["git", "rev-parse", "--verify", "HEAD"],
                              cwd=repo, capture_output=True, text=True, timeout=60)
        if head.returncode != 0:
            # A staging tree can contain every file before its first commit. There
            # is no HEAD to compare against; the sha256sum checks above still bind
            # the frozen bytes, so record the state instead of inventing a diff.
            git = {"available": True, "head": False}
        else:
            diff = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", *FROZEN_FILES, FREEZE_FILE],
                                  cwd=repo, capture_output=True, text=True, timeout=60)
            status = subprocess.run(["git", "status", "--porcelain", "--", *FROZEN_FILES, FREEZE_FILE],
                                    cwd=repo, capture_output=True, text=True, timeout=60)
            git = {"available": True, "head": True, "diff_vs_HEAD_exit": diff.returncode,
                   "status_porcelain": status.stdout.strip()}
            if diff.returncode not in (0,) or status.stdout.strip():
                problems.append("frozen files differ from git HEAD or are untracked/modified")
    except (OSError, subprocess.SubprocessError) as exc:  # git missing is recorded, not hidden
        git = {"available": False, "error": type(exc).__name__}
    return {
        "ok": not problems,
        "problems": problems,
        "tool": "sha256sum -c " + FREEZE_FILE,
        "tool_exit": proc.returncode,
        "tool_stdout": proc.stdout,
        "tool_stderr": proc.stderr,
        "frozen_sha256": recomputed,
        "freeze_file_sha256": hashlib.sha256((repo / FREEZE_FILE).read_bytes()).hexdigest(),
        "git": git,
    }


def require_freeze(repo: Path) -> dict:
    result = verify_freeze(repo)
    if not result["ok"]:
        raise FreezeError("; ".join(result["problems"]))
    return result

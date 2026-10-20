#!/usr/bin/env python3
"""Build or check SOURCE-MANIFEST.sha256 for the exact staged source tree.

Stage the intended publication files before running this command. The manifest
excludes only itself, so it can be regenerated without a circular digest. Git
history, ignored caches, and untracked files are not part of this source receipt.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = "SOURCE-MANIFEST.sha256"


def build_manifest() -> tuple[str, int]:
    index = subprocess.check_output(
        ["git", "-C", str(REPO), "ls-files", "--stage", "-z"]
    )
    lines = []
    for entry in sorted(
        (part for part in index.split(b"\0") if part),
        key=lambda part: part.split(b"\t", 1)[1],
    ):
        metadata, raw_path = entry.split(b"\t", 1)
        mode, oid, stage = metadata.split()
        path = raw_path.decode("utf-8")
        if path == MANIFEST:
            continue
        if stage != b"0" or mode not in {b"100644", b"100755"}:
            raise ValueError(f"non-regular or unmerged index entry: {path}")
        if any(character in path for character in "\r\n\\"):
            raise ValueError("manifest paths must not contain control characters or backslashes")
        target = REPO / path
        if target.is_symlink() or not target.is_file():
            raise ValueError(f"missing or non-regular working-tree file: {path}")
        content = subprocess.check_output(
            ["git", "-C", str(REPO), "cat-file", "blob", oid.decode("ascii")]
        )
        if target.read_bytes() != content:
            raise ValueError(f"unstaged content differs from the index: {path}")
        lines.append(f"{hashlib.sha256(content).hexdigest()}  {path}\n")
    if not lines:
        raise ValueError("stage the intended publication files first")
    return "".join(lines), len(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="check without rewriting")
    args = parser.parse_args()
    try:
        content, count = build_manifest()
        target = REPO / MANIFEST
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != content:
                print(f"FAIL: {MANIFEST} differs from the staged source tree", file=sys.stderr)
                return 1
            print(f"PASS: {MANIFEST} matches {count} staged source files")
        else:
            target.write_text(content, encoding="utf-8", newline="\n")
            print(f"Wrote {MANIFEST} for {count} staged source files")
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

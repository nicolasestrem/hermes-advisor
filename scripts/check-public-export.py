#!/usr/bin/env python3
"""Scan the public export for private identifiers, network locations and credentials.

The scan covers the export as Git sees it: tracked files plus untracked files that
are not ignored (``git ls-files --cached --others --exclude-standard``). In a
non-Git directory it walks the tree instead, skipping only VCS/cache directories.

Failures are intentional: this script is a release gate, not a report. Authorized
public identity is limited to the MIT copyright holder and the GitHub noreply
address documented in the release plan. RFC 2606/6761 reserved domains are
accepted as synthetic test fixtures, never as a place to hide real addresses.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

AUTHORIZED_EMAILS = {
    "103498114+nicolasestrem@users.noreply.github.com",
}
RESERVED_EMAIL_DOMAINS = {
    "example.com",
    "example.net",
    "example.org",
}
RESERVED_EMAIL_SUFFIXES = (".example", ".invalid", ".test", ".internal", ".localhost")
EXCLUDED_EXPORT_DIRS = ("evidence", "verification", "NEWverification", ".omh")
SKIP_DIR_NAMES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}

# Private identifiers are never written here. Candidate tokens are hashed with
# SHA-256 (lowercased) and compared against this set, so the public scanner can
# reject them without republishing their spelling.
PRIVATE_TOKEN_SHA256 = {
    "64e66eb6490b4a3756a1407b511c921c263a9b1922080df6c28af50501e2a037",
    "4e7112185da4b197c0fc728aeebb9ca4be8ffc86f260cc85660d9318807d646d",
}

RULES = (
    ("private-home-path", re.compile(r"""(?<![\w.-])/(?:home|Users|root)/[^\s"'`)]+""")),
    ("private-windows-path", re.compile(r"""[A-Za-z]:\\\\Users\\\\[^\s"'`)]+""")),
    (
        "private-ip",
        re.compile(
            r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
            r"|192\.168\.\d{1,3}\.\d{1,3}"
            r"|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b"
            r"|\b(?:127\.0\.0\.1|0\.0\.0\.0)(?::\d+)?\b"
        ),
    ),
    (
        "credential-token",
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}"
            r"|github_pat_[A-Za-z0-9_]{20,}"
            r"|AKIA[0-9A-Z]{16}"
            r"|ASIA[0-9A-Z]{16}"
            r"|xox[baprs]-[A-Za-z0-9-]{10,}"
            r"|sk_live_[A-Za-z0-9]{10,}"
            r"|rk_live_[A-Za-z0-9]{10,})\b"
        ),
    ),
    ("credential-sk-token", re.compile(r"\bsk-(?:proj-|live_|test_)?[A-Za-z0-9_-]{24,}\b")),
    (
        "credential-jwt",
        re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ),
    (
        "credential-private-key",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )?PRIVATE KEY-----"),
    ),
    (
        "credential-aws-secret",
        re.compile(r"(?i)\baws_secret_access_key\s*[=:]\s*[\"']?[A-Za-z0-9/+=]{40}"),
    ),
)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
TOKEN_RE = re.compile(r"[A-Za-z0-9._-]+")
PRIVATE_FILE_NAMES = {
    ".env",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials.json",
    "auth.json",
    "id_rsa",
    "id_ed25519",
}
PRIVATE_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db")
BINARY_SUFFIXES = (".pyc", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".woff", ".woff2")


class Scanner:
    def __init__(self, root: Path):
        self.root = root
        self.findings: list[tuple[str, int, str, str]] = []
        self.scanned = 0

    def add(self, path: Path, line: int, rule: str, detail: str) -> None:
        try:
            shown = path.relative_to(self.root).as_posix()
        except ValueError:
            shown = str(path)
        self.findings.append((shown, line, rule, detail.strip()[:180]))

    def files(self) -> list[Path]:
        if (self.root / ".git").exists():
            proc = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip())
            names = [n for n in proc.stdout.decode("utf-8", "replace").split("\0") if n]
            return sorted(self.root / n for n in names if (self.root / n).is_file())

        found: list[Path] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
            for name in filenames:
                found.append(Path(dirpath) / name)
        return sorted(found)

    def scan(self) -> None:
        for path in self.files():
            rel = path.relative_to(self.root)
            first = rel.parts[0] if rel.parts else ""
            if first in EXCLUDED_EXPORT_DIRS:
                self.add(path, 0, "excluded-directory", f"{first}/ is not part of the public export")
                continue
            if path.is_symlink():
                target = path.resolve()
                if self.root not in target.parents:
                    self.add(path, 0, "external-symlink", f"points outside the export: {target}")
                continue
            if path.name in PRIVATE_FILE_NAMES or path.name == ".env" or path.name.startswith(".env."):
                self.add(path, 0, "private-file", "credential or environment file must not be exported")
                continue
            if path.suffix.lower() in PRIVATE_FILE_SUFFIXES:
                self.add(path, 0, "private-file", f"private/database artifact {path.suffix} must not be exported")
                continue

            self.scanned += 1
            data = path.read_bytes()
            if b"\x00" in data:
                rule = "binary-artifact" if path.suffix.lower() not in BINARY_SUFFIXES else "binary-artifact"
                self.add(path, 0, rule, "binary file in the public export")
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                self.add(path, 0, "non-utf8-text", f"not valid UTF-8: {exc}")
                continue

            for number, line in enumerate(text.splitlines(), start=1):
                for rule, pattern in RULES:
                    match = pattern.search(line)
                    if match:
                        self.add(path, number, rule, "")
                for match in EMAIL_RE.finditer(line):
                    address = match.group(0)
                    if not email_is_allowed(address):
                        self.add(path, number, "private-email", "")
                for token in TOKEN_RE.findall(line):
                    if hashlib.sha256(token.lower().encode("utf-8")).hexdigest() in PRIVATE_TOKEN_SHA256:
                        self.add(path, number, "private-identifier-token", "")

    def check_freeze(self) -> None:
        freeze = self.root / "tests" / "evaluation" / "FREEZE.sha256"
        if not freeze.is_file():
            return
        for number, line in enumerate(freeze.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) != 2:
                self.add(freeze, number, "freeze-format", "expected '<sha256>  <path>'")
                continue
            expected, name = parts[0].lower(), parts[1].lstrip("*")
            target = self.root / name
            if not target.is_file():
                self.add(freeze, number, "freeze-missing", name)
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected:
                self.add(freeze, number, "freeze-mismatch", f"{name} is not byte-identical")


def email_is_allowed(address: str) -> bool:
    lowered = address.lower()
    if lowered in AUTHORIZED_EMAILS:
        return True
    domain = lowered.rsplit("@", 1)[-1]
    if domain in RESERVED_EMAIL_DOMAINS:
        return True
    return any(domain.endswith(suffix) for suffix in RESERVED_EMAIL_SUFFIXES)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Scan the public export for private data and credentials.")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)

    root = args.root.resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2

    scanner = Scanner(root)
    try:
        scanner.scan()
        scanner.check_freeze()
    except (OSError, RuntimeError) as exc:
        print(f"error: scan failed: {exc}", file=sys.stderr)
        return 2

    if scanner.findings:
        for path, line, rule, _detail in scanner.findings:
            location = f"{path}:{line}" if line else path
            print(f"{location}: [{rule}]")
        print(f"\nFAIL: {len(scanner.findings)} finding(s) across {scanner.scanned} scanned file(s)")
        return 1

    print(f"OK: {scanner.scanned} file(s) scanned; no private paths, hosts, addresses, or credentials found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Shared helpers for the privacy/packet audit tests (no provider calls, no host access).

Run from the repository root:
    python3 -m unittest discover -s tests/privacy -p 'test_acceptance_*.py'   # must pass
    python3 -m unittest discover -s tests/privacy -p 'test_red_*.py'          # known defects (expectedFailure)
    PRIVACY_RED_RAW=1 python3 -m unittest discover -s tests/privacy -p 'test_red_*.py'  # raw RED reproduction

Conventions
- Synthetic secrets only. Assertion messages carry LABELS, never secret values.
- RED tests are decorated with ``known_defect``: reported as "expected failure" while the
  defect exists, and as "unexpected success" (=> remove the decorator) once it is fixed.
  With PRIVACY_RED_RAW=1 the decorator is a no-op so the real failure is reproduced.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import io
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from advisor_core import Service  # noqa: E402
from host_adapter import EventAdapter  # noqa: E402

ROUTE = {"provider": "nous", "model": "z-ai/glm-5.3-flash"}
RAW_RED = os.environ.get("PRIVACY_RED_RAW") == "1"

# Synthetic secret body: obviously fake, long enough for every pattern length floor.
BODY = "SYNTHCANARY9Zq3LmN8pTr2KvWx"          # 27 chars
FRAGMENT_LEN = 8                                # any >=8-char slice of BODY surviving = leak


def fragments(body: str = BODY, n: int = FRAGMENT_LEN):
    return {body[i:i + n] for i in range(0, len(body) - n + 1)}


def leaked_fragment(text: str, body: str = BODY) -> bool:
    return any(frag in text for frag in fragments(body))


def known_defect(defect_id: str, summary: str):
    """Mark a RED reproduction. No-op in RAW mode so the true failure is shown."""
    def deco(fn):
        fn.defect_id = defect_id
        fn.defect_summary = summary
        return fn if RAW_RED else unittest.expectedFailure(fn)
    return deco


# --------------------------------------------------------------------------- traps
class ContentTouched(AssertionError):
    pass


def _boom(*_a, **_k):
    raise ContentTouched("message/history content was accessed while disabled/paused")


class TrapStr(str):
    __len__ = __iter__ = __getitem__ = __contains__ = __eq__ = __ne__ = __hash__ = _boom
    __add__ = __radd__ = __mod__ = __format__ = __str__ = __repr__ = _boom
    encode = startswith = endswith = find = split = replace = strip = lower = _boom


class TrapList(list):
    __len__ = __iter__ = __getitem__ = __contains__ = __reversed__ = __bool__ = _boom


class TrapDict(dict):
    __len__ = __iter__ = __getitem__ = __contains__ = items = keys = values = get = _boom


# ------------------------------------------------------------------------ harness
async def _noop_reviewer(packet, settings):
    return {"outcome": "no_findings", "findings": []}


class Harness:
    """Real Service + real EventAdapter over a private temp SQLite store."""

    def __init__(self, reviewer=None, surfaces=("cli",), clock=None):
        self._tmp = tempfile.TemporaryDirectory(prefix="privacy-audit-")
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.calls = []        # (deep-copied payload, wire bytes) per reviewer invocation
        self._reviewer = reviewer or _noop_reviewer
        self.service = Service(str(self.data_dir), self._record, route_validator=lambda r: r == ROUTE)
        kwargs = {"clock": clock} if clock else {}
        self.adapter = EventAdapter(self.service, surfaces=set(surfaces), **kwargs)

    async def _record(self, packet, settings):
        # Same wire format advisor_host.Runtime.review serialises.
        wire = json.dumps(packet, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.calls.append((copy.deepcopy(packet), wire))
        return await self._reviewer(packet, settings)

    # -- session lifecycle
    def enable(self, sid="s1", turn="t1", platform="cli", **settings):
        self.service.discover(sid, platform)
        status = self.service.status(sid)
        cfg = {"route": dict(ROUTE), **settings}
        self.service.configure(sid, cfg, status["saved_profile_revision"])
        self.service.enable(sid)
        self.adapter.start(session_id=sid, turn_id=turn, platform=platform)
        return sid

    def discover_only(self, sid="s1", turn="t1", platform="cli"):
        self.adapter.start(session_id=sid, turn_id=turn, platform=platform)
        return sid

    def post(self, sid="s1", turn="t1", platform="cli", request="request", answer="answer", history=None):
        self.adapter.post(session_id=sid, turn_id=turn, platform=platform, user_message=request,
                          assistant_response=answer, conversation_history=history if history is not None else [])

    def end(self, sid="s1", turn="t1", platform="cli"):
        return self.adapter.end(session_id=sid, turn_id=turn, platform=platform,
                                completed=True, failed=False, interrupted=False)

    def run_turn(self, request="request", answer="answer", history=None, sid="s1", turn="t1", **settings):
        self.enable(sid, turn, **settings)
        self.post(sid, turn, request=request, answer=answer, history=history)
        return self.end(sid, turn)

    def pending_packets(self):
        with self.adapter._lock:
            return [entry[1] for entry in self.adapter._pending.values()]

    def settle(self, sid="s1", timeout=6.0):
        deadline = time.monotonic() + timeout
        notes = []
        while time.monotonic() < deadline:
            notes = self.service.notes(sid)
            if notes and notes[0]["status"] in {"completed", "failed", "cancelled", "timed_out", "skipped"}:
                return notes
            time.sleep(0.02)
        return notes

    def close(self):
        with contextlib.suppress(Exception):
            self.adapter.close()
        try:
            self.service.close(timeout=5)
        finally:
            self._tmp.cleanup()

    # -- persistence projection (reads only the test's own temp store)
    def db_files_bytes(self):
        blobs = []
        for path in sorted(self.data_dir.glob("advisor.sqlite3*")):
            blobs.append(path.read_bytes())
        return b"".join(blobs)

    def schema_projection(self):
        conn = sqlite3.connect(self.data_dir / "advisor.sqlite3")
        try:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1")]
            return {t: [c[1] for c in conn.execute(f"PRAGMA table_info({t})")] for t in tables}
        finally:
            conn.close()

    def text_cells(self):
        """Yield (table, column, text) for every TEXT cell - values stay in-process."""
        conn = sqlite3.connect(self.data_dir / "advisor.sqlite3")
        try:
            for table, cols in self.schema_projection().items():
                for col in cols:
                    for (val,) in conn.execute(f"SELECT {col} FROM {table}"):
                        if isinstance(val, str):
                            yield table, col, val
        finally:
            conn.close()


class HarnessCase(unittest.TestCase):
    def harness(self, **kwargs):
        h = Harness(**kwargs)
        self.addCleanup(h.close)
        return h

    # Label-only assertions: never echo values into reports.
    def assertNoLeaks(self, leaked, what):
        self.assertEqual([], sorted(leaked), f"{what}: leaked variants (labels only): {sorted(leaked)}")


def packet_text(packet) -> str:
    return packet.serialized


def tool_rows(call_args="{}", result_content="ok", call_id="c1", name="tool", user="request"):
    """One current-turn tool pair in Hermes/OpenAI row shape."""
    return [
        {"role": "user", "content": user},
        {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": call_args}}]},
        {"role": "tool", "tool_call_id": call_id, "content": result_content},
    ]


@contextlib.contextmanager
def captured_output():
    """Capture stdout/stderr/logging for leak scanning; yields a callable returning the text."""
    out, err = io.StringIO(), io.StringIO()
    log = io.StringIO()
    handler = logging.StreamHandler(log)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            yield lambda: out.getvalue() + err.getvalue() + log.getvalue()
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)

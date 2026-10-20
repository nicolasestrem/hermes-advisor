"""ACCEPTANCE (must pass): disabled/paused never read content, pending bounds, persistence scope.

Real Service + EventAdapter over a private SQLite store; reviewer is a local async fixture
(no provider calls). SQLite is inspected through schema/column projection only.
"""
import hashlib
import json
import re
import time
import unittest
from unittest import mock

from _support import (BODY, ContentTouched, Harness, HarnessCase, TrapDict, TrapList, TrapStr,
                      captured_output, leaked_fragment, tool_rows)

import advisor_core.service as service_mod
import host_adapter
from advisor_core import packet as P
from advisor_core.packet import CapturedPacket


class PacketBuildSpies:
    """Count every packet-construction/sanitisation entry point."""

    def __init__(self):
        self.counts = {}
        self._patches = []

    def __enter__(self):
        targets = [(host_adapter, "build_packet"), (service_mod, "build_packet"),
                   (service_mod, "_validate_captured_packet"), (P, "sanitize_text"), (P, "_sanitize_value"),
                   (P, "_bounded_excerpt")]
        for module, name in targets:
            original = getattr(module, name)

            def wrapper(*a, _orig=original, _key=f"{module.__name__}.{name}", **k):
                self.counts[_key] = self.counts.get(_key, 0) + 1
                return _orig(*a, **k)

            patcher = mock.patch.object(module, name, wrapper)
            patcher.start()
            self._patches.append(patcher)
        return self

    def __exit__(self, *exc):
        for patcher in self._patches:
            patcher.stop()

    @property
    def total(self):
        return sum(self.counts.values())


class DisabledPausedNeverReadContentTests(HarnessCase):
    def _make_state(self, h, state):
        if state == "never_enabled":
            h.discover_only()
        elif state == "disabled_after_enable":
            h.enable()
            h.service.disable("s1")
        elif state == "paused_by_reset":
            h.enable()
            h.service.reset_allowance("s1", True)
            self.assertEqual("paused", h.service.status("s1")["state"])
        elif state == "unsupported_surface":
            h.discover_only(platform="weird")
        else:
            raise AssertionError(state)

    def test_no_content_inspection_copy_digest_or_retention_in_any_inactive_state(self):
        for state in ("never_enabled", "disabled_after_enable", "paused_by_reset", "unsupported_surface"):
            platform = "weird" if state == "unsupported_surface" else "cli"
            h = self.harness(surfaces=("cli", "weird"))
            self._make_state(h, state)
            with PacketBuildSpies() as spies:
                try:
                    h.adapter.post(session_id="s1", turn_id="t1", platform=platform,
                                   user_message=TrapStr("x"), assistant_response=TrapStr("y"),
                                   conversation_history=TrapList())
                    h.end(platform=platform)
                    capture = h.service.capture(session_id="s1", turn_id="t1", request=TrapStr("x"),
                                                answer=TrapStr("y"), history=TrapList([{"call": 1}]))
                    manual = h.service.review_now("s1")
                except ContentTouched as exc:                    # pragma: no cover - failure path
                    self.fail(f"{state}: {exc}")
            self.assertEqual(0, spies.total, f"{state}: packet build/sanitise ran {spies.counts}")
            self.assertFalse(capture.get("accepted"), state)
            self.assertFalse(manual.get("accepted"), state)
            self.assertEqual(0, manual.get("requests", 0), state)
            self.assertEqual(0, h.adapter.pending_count, state)
            self.assertEqual([], h.calls, state)
            status = h.service.status("s1")
            self.assertFalse(status["review_now_available"], state)
            self.assertIsNone(status["latest_attempt"], state)

    def test_positive_control_enabled_session_does_run_the_pipeline(self):
        h = self.harness()
        h.enable()
        with PacketBuildSpies() as spies:
            h.post(request="r", answer="a", history=tool_rows())
        self.assertGreater(spies.total, 0)
        self.assertEqual(1, h.adapter.pending_count)

    def test_disable_between_post_and_end_discards_without_service_content_access(self):
        h = self.harness()
        h.enable()
        h.post(request="r", answer="a", history=tool_rows())
        h.service.disable("s1")
        with PacketBuildSpies() as spies:
            result = h.end()
        self.assertIsNone(result)
        self.assertEqual(0, spies.total)
        self.assertEqual(0, h.adapter.pending_count)
        self.assertEqual([], h.calls)
        self.assertIsNone(h.service.status("s1")["latest_attempt"])

    def test_discovery_while_disabled_persists_metadata_only(self):
        h = self.harness()
        marker = "DISABLEDMARK" + BODY
        h.discover_only()
        h.post(request=marker, answer=marker, history=tool_rows(call_args=marker, result_content=marker))
        h.end()
        self.assertEqual(0, h.adapter.pending_count)
        blob = h.db_files_bytes()
        self.assertNotIn(b"DISABLEDMARK", blob)
        self.assertFalse(leaked_fragment(blob.decode("latin-1")))
        sessions = h.schema_projection()["sessions"]
        self.assertNotIn("title", sessions)
        rows = [(t, c) for t, c, v in h.text_cells() if t == "attempts"]
        self.assertEqual([], rows, "no attempt rows while disabled")

    def test_trap_objects_really_trap(self):
        for trap in (TrapStr("x"), TrapList(), TrapDict()):
            with self.assertRaises(ContentTouched):
                len(trap)


class PendingBoundsTests(HarnessCase):
    def test_pending_capacity_16_ttl_1800_and_discard_without_admission(self):
        now = [1000.0]
        h = self.harness(clock=lambda: now[0])
        for i in range(17):
            sid = f"s{i:02d}"
            h.enable(sid, turn="t1")
            h.post(sid, "t1", request=f"req{i}", answer="a", history=tool_rows())
            now[0] += 1
        self.assertEqual(16, h.adapter.pending_count)
        self.assertEqual(1, h.adapter.metrics["discarded"])
        with h.adapter._lock:
            self.assertNotIn(("s00", "t1"), h.adapter._pending, "oldest pending is evicted first")
            for entry in h.adapter._pending.values():
                kinds = [type(item) for item in entry]
                self.assertIn(CapturedPacket, kinds)
                self.assertNotIn(str, kinds, "pending entries hold only the immutable sanitized packet + ints/floats")
        # TTL: s01 captured at t=1001 => alive at +1799, gone at +1800.
        base = 1001.0
        now[0] = base + 1799
        h.adapter._expire()
        self.assertIn(("s01", "t1"), h.adapter._pending)
        now[0] = base + 1800
        self.assertEqual(h.adapter.pending_count, len([k for k, v in h.adapter._pending.items()]))
        self.assertNotIn(("s01", "t1"), h.adapter._pending)
        self.assertIsNone(h.end("s01", "t1"))
        self.assertEqual([], h.calls, "expired pending must never reach the reviewer")

    def test_one_pending_entry_per_session_turn_and_close_clears_everything(self):
        h = self.harness()
        h.enable("s1", "t1")
        h.post("s1", "t1", request="r", answer="a")
        h.post("s1", "t1", request="r2", answer="a2")
        self.assertEqual(1, h.adapter.pending_count)
        h.adapter.close()
        self.assertEqual(0, h.adapter.pending_count)
        self.assertEqual([], h.pending_packets())


class PersistenceAndExportScopeTests(HarnessCase):
    REQ, ANS, ARG, RES = "REQMARKER7f3a", "ANSMARKER91bc", "ARGMARKER52de", "RESMARKER08aa"
    EXPECTED_SCHEMA = {
        "profile_config": {"singleton", "revision", "settings", "updated_at"},
        "sessions": {"session_id", "surface", "owner_token", "state", "pause_reason", "invocation_count",
                     "applied_revision", "applied_generation", "applied_settings", "discovered_at", "last_seen"},
        "attempts": {"attempt_id", "session_id", "turn_id", "packet_digest", "applied_revision",
                     "applied_generation", "owner_token", "created_at", "deadline_at", "terminal_at", "status",
                     "cancellation_pending", "outcome", "requested_provider", "requested_model", "served_provider",
                     "served_model", "usage_json", "usage_known", "coverage_json", "error_code", "error_text"},
        "findings": {"note_id", "session_id", "attempt_id", "ordinal", "severity", "explanation",
                     "evidence_refs_json", "next_step"},
        "ratings": {"attempt_id", "session_id", "rating", "reason", "updated_at"},
        "feedback_actions": {"action_id", "session_id", "note_ids_json", "attempt_ids_json", "created_at"},
        "dedup": {"session_id", "turn_id", "packet_digest", "applied_generation", "attempt_id"},
    }

    def _content(self):
        secret = BODY
        history = tool_rows(call_args=json.dumps({"path": self.ARG, "api_key": secret}),
                            result_content=f"{self.RES} password={secret}")
        return f"{self.REQ} sk-{secret}", f"{self.ANS} Bearer {secret}", history

    def _everything_exposed(self, h):
        notes = h.service.notes("s1")
        note_ids = [f["note_id"] for n in notes for f in n["findings"]]
        blobs = [json.dumps(h.service.status("s1"), default=str), json.dumps(notes, default=str)]
        if note_ids:
            blobs.append(json.dumps(h.service.feedback("s1", note_ids[:1]), default=str))
        blobs.append(h.db_files_bytes().decode("latin-1"))
        blobs.extend(v for _, _, v in h.text_cells())
        return "\n".join(blobs)

    def test_nothing_from_the_packet_is_persisted_logged_or_exported(self):
        async def reviewer(packet, settings):
            return {"outcome": "findings", "findings": [{
                "severity": "concern", "explanation": "Answer is unsupported by the cited tool result.",
                "evidence_refs": ["answer", "tool-0001-result"], "next_step": "Verify with a fresh test run."}]}

        h = self.harness(reviewer=reviewer)
        request, answer, history = self._content()
        with captured_output() as output:
            h.run_turn(request=request, answer=answer, history=history)
            notes = h.settle()
            exposed = self._everything_exposed(h)
        self.assertEqual("completed", notes[0]["status"])
        for marker in (self.REQ, self.ANS, self.ARG, self.RES):
            self.assertNotIn(marker, exposed, f"{marker[:3]} content persisted/exposed")
            self.assertNotIn(marker, output(), f"{marker[:3]} content logged/printed")
        self.assertFalse(leaked_fragment(exposed), "secret body persisted/exposed")
        self.assertFalse(leaked_fragment(output()), "secret body logged/printed")
        # The reviewer DID receive the content (control: test is not vacuous).
        self.assertIn(self.REQ, json.dumps(h.calls[0][0]))

    def test_sqlite_schema_has_no_raw_packet_column_and_digest_binds_full_packet(self):
        h = self.harness()
        request, answer, history = self._content()
        h.run_turn(request=request, answer=answer, history=history)
        h.settle()
        self.assertEqual(self.EXPECTED_SCHEMA, {t: set(c) for t, c in h.schema_projection().items()},
                         "schema drift: review any new column for raw packet/reply storage")
        conn = __import__("sqlite3").connect(h.data_dir / "advisor.sqlite3")
        try:
            digest, coverage_json = conn.execute("SELECT packet_digest,coverage_json FROM attempts").fetchone()
        finally:
            conn.close()
        self.assertTrue(re.fullmatch(r"[0-9a-f]{64}", digest))
        self.assertEqual(digest, hashlib.sha256(h.calls[0][1]).hexdigest(), "digest must bind the exact wire packet")
        cov = json.loads(coverage_json)
        self.assertEqual({"request_clipped", "answer_clipped", "tool_pairs_total", "tool_pairs_included",
                          "tool_pairs_omitted", "upstream_omitted_tool_pairs", "clipped_refs"}, set(cov))
        self.assertTrue(all(isinstance(v, (bool, int, list)) for v in cov.values()))
        self.assertTrue(all(isinstance(r, str) and len(r) < 40 for r in cov["clipped_refs"]))

    def test_reviewer_exception_text_never_reaches_status_or_database(self):
        for exc in (RuntimeError(f"quota exceeded {self.REQ} sk-{BODY}"),
                    ValueError(f"bad things {self.ANS} password={BODY}"),
                    KeyError(self.RES)):
            async def reviewer(packet, settings, _exc=exc):
                raise _exc

            h = self.harness(reviewer=reviewer)
            h.run_turn(request=f"{self.REQ}", answer="a", history=tool_rows())
            notes = h.settle()
            self.assertIn(notes[0]["status"], {"failed", "cancelled"})
            exposed = self._everything_exposed(h)
            for marker in (self.REQ, self.ANS, self.RES):
                self.assertNotIn(marker, exposed)
            self.assertFalse(leaked_fragment(exposed))

    def test_invalid_reviewer_reply_is_not_persisted(self):
        async def reviewer(packet, settings):
            return {"outcome": "findings", "findings": [{"severity": "concern", "explanation": f"RAWREPLY{BODY}",
                                                         "evidence_refs": ["nonexistent-ref"], "next_step": "x"}]}

        h = self.harness(reviewer=reviewer)
        h.run_turn()
        notes = h.settle()
        self.assertEqual("failed", notes[0]["status"])
        self.assertNotIn("RAWREPLY", self._everything_exposed(h))

    def test_rating_reason_is_redacted_with_known_patterns(self):
        h = self.harness()
        h.run_turn()
        notes = h.settle()
        saved = h.service.rate("s1", notes[0]["attempt_id"], "useful", f"leaked sk-{BODY} and Bearer {BODY}")
        self.assertTrue(saved["saved"])
        self.assertFalse(leaked_fragment(self._everything_exposed(h)))

    def test_error_and_attribution_fields_are_redacted(self):
        async def reviewer(packet, settings):
            from advisor_core import ReviewerResult
            return ReviewerResult(response={"outcome": "no_findings", "findings": []},
                                  provider=f"sk-{BODY}", model="m", usage={"input_tokens": 3}, usage_known=True)

        h = self.harness(reviewer=reviewer)
        h.run_turn()
        notes = h.settle()
        self.assertEqual("completed", notes[0]["status"])
        self.assertFalse(leaked_fragment(self._everything_exposed(h)))


if __name__ == "__main__":
    unittest.main()

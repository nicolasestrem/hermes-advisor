"""Ordinary regression tests for fixed privacy/packet defects.

These tests assert required behavior using synthetic inputs and local fixtures. Historical raw RED
receipts remain preserved under verification; this module now runs as ordinary passing tests.
"""
import json
import unittest

from _support import BODY, HarnessCase, leaked_fragment, tool_rows

from advisor_core import packet as P
from advisor_core.packet import PacketError, build_packet

HIDDEN = "HIDDEN" + BODY


def packet_texts_for(text):
    """Serialized packets where `text` appears as request, answer, tool-call leaf and tool-result leaf."""
    yield "request", build_packet(text, "a", []).serialized
    yield "answer", build_packet("r", text, []).serialized
    yield "tool_call", build_packet("r", "a", [{"call": {"cmd": text}, "result": {"ok": 1}}]).serialized
    yield "tool_result", build_packet("r", "a", [{"call": {"x": 1}, "result": {"out": text}}]).serialized


def leaks(case_map, needle=None):
    """case_map: label -> text. Returns labels whose secret survives in ANY packet position."""
    out = []
    for label, text in case_map.items():
        for where, serialized in packet_texts_for(text):
            hit = (needle(label) in serialized) if needle else leaked_fragment(serialized)
            if hit:
                out.append(f"{label}@{where}")
                break
    return out


class SecretPatternGapTests(HarnessCase):
    def test_env_style_credential_assignments_are_redacted(self):
        cases = {
            "OPENAI_API_KEY=": f"OPENAI_API_KEY={BODY}",
            "export ANTHROPIC_API_KEY=": f"export ANTHROPIC_API_KEY={BODY}",
            "GITHUB_TOKEN=": f"GITHUB_TOKEN={BODY}",
            "AWS_SECRET_ACCESS_KEY=": f"AWS_SECRET_ACCESS_KEY={BODY}",
            "DB_PASSWORD=": f"DB_PASSWORD={BODY}",
            "SLACK_BOT_TOKEN:": f"SLACK_BOT_TOKEN: {BODY}",
            "MYAPP_PASSWD=": f"MYAPP_PASSWD={BODY}",
            ".env multiline": f"A=1\nSTRIPE_SECRET_KEY={BODY}\nB=2",
            "client_secret=": f"client_secret={BODY}",
        }
        self.assertNoLeaks(leaks(cases), "env-style credential assignment survived redaction")

    def test_json_encoded_credential_fields_inside_strings_are_redacted(self):
        cases = {
            'json api_key': json.dumps({"api_key": BODY, "path": "x"}),
            'json password': '{"password":"' + BODY + '"}',
            'json refresh_token': json.dumps({"refresh_token": BODY}),
            'json nested Authorization': json.dumps({"headers": {"Authorization": BODY}}),
            'python-literal secret': "{'secret': '" + BODY + "'}",
            'json client_secret': json.dumps({"client_secret": BODY}),
        }
        self.assertNoLeaks(leaks(cases), "JSON-in-string credential survived redaction")

    def test_adapter_path_json_string_arguments_and_content(self):
        h = self.harness()
        rows = tool_rows(call_args=json.dumps({"api_key": BODY, "path": "keep"}),
                         result_content=json.dumps({"ok": True, "password": BODY}))
        h.enable()
        h.post(history=rows)
        self.assertFalse(leaked_fragment(h.pending_packets()[0].serialized),
                         "credential in JSON-string arguments/content reached the packet")

    def test_http_credential_headers_are_redacted(self):
        cases = {
            "Authorization: Basic": f"Authorization: Basic {BODY}",
            "Proxy-Authorization: Basic": f"Proxy-Authorization: Basic {BODY}",
            "Cookie:": f"Cookie: sid={BODY}; theme=dark",
            "Set-Cookie:": f"Set-Cookie: sid={BODY}; HttpOnly; Secure",
            "curl basic auth": "curl " + f"-u admin:{BODY} https://host.invalid/x",
        }
        self.assertNoLeaks(leaks(cases), "HTTP credential header/flag survived redaction")

    def test_quoted_multiword_values_are_fully_redacted(self):
        cases = {
            'password: "a b c"': f'password: "alpha {BODY} charlie delta"',
            "secret='a b c'": f"secret='alpha {BODY} charlie delta'",
        }
        self.assertNoLeaks(leaks(cases, needle=lambda _l: "charlie delta"), "tail of quoted credential survived")

    def test_common_vendor_token_formats_are_redacted(self):
        cases = {
            "github_pat_": "github_pat_11" + BODY,
            "xoxb-": "xoxb-" + "1234567890-" + BODY,
            "JWT": ('eyJhb' + 'GciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.') + BODY,
            "sk_live_": "sk_live_" + BODY,
            "AIza": "AIzaSy" + BODY + "abcdef",
            "glpat-": "glpat-" + BODY[:20],
        }
        self.assertNoLeaks(leaks(cases), "vendor token format survived redaction")


class ReasoningExclusionGapTests(HarnessCase):
    def test_reasoning_variants_are_excluded(self):
        variants = {
            "reasoning_details": {"reasoning_details": [{"text": HIDDEN}], "ok": 1},
            "reasoning_summary": {"reasoning_summary": HIDDEN},
            "reasoning_text": {"reasoning_text": HIDDEN},
            "codex_reasoning_items": {"codex_reasoning_items": [{"summary": HIDDEN}]},
            "thinking_blocks[text]": {"thinking_blocks": [{"text": HIDDEN}]},
            "content block type=thinking": {"content": [{"type": "thinking", "text": HIDDEN}, {"type": "text", "text": "ok"}]},
            "content block type=reasoning": {"content": [{"type": "reasoning", "summary": [{"text": HIDDEN}]}]},
            "encrypted_content": {"encrypted_content": HIDDEN},
            "thought_signature": {"extra_content": {"google": {"thought_signature": HIDDEN}}},
            "scratchpad": {"scratchpad": HIDDEN},
        }
        leaked = []
        for label, value in variants.items():
            for where, payload in (("call", {"call": value, "result": {"ok": 1}}), ("result", {"call": {"x": 1}, "result": value})):
                if leaked_fragment(build_packet("r", "a", [payload]).serialized, HIDDEN):
                    leaked.append(f"{label}@{where}")
                    break
        self.assertNoLeaks(leaked, "exposed reasoning survived")

    def test_inline_think_blocks_are_excluded(self):
        leaked = []
        for tag in ("think", "thinking", "reasoning"):
            text = f"before <{tag}>{HIDDEN}</{tag}> after"
            for where, serialized in packet_texts_for(text):
                if leaked_fragment(serialized, HIDDEN):
                    leaked.append(f"<{tag}>@{where}")
                    break
        self.assertNoLeaks(leaked, "inline reasoning block survived")


class PersistenceRedactionTests(HarnessCase):
    def test_reviewer_quoted_secret_is_redacted_everywhere_it_is_stored_or_returned(self):
        secret = "sk-" + BODY            # a KNOWN pattern: findings.explanation is redacted, outcome is not

        async def reviewer(packet, settings):
            return {"outcome": "findings", "findings": [{
                "severity": "concern", "explanation": f"The tool printed {secret}",
                "evidence_refs": ["answer"], "next_step": f"rotate {secret}"}]}

        h = self.harness(reviewer=reviewer)
        h.run_turn()
        notes = h.settle()
        self.assertEqual("completed", notes[0]["status"])
        where = {
            "notes()[].outcome": json.dumps(notes[0]["outcome"]),
            "notes()[].findings": json.dumps(notes[0]["findings"]),
            "status().latest_attempt": json.dumps(h.service.status("s1")["latest_attempt"]),
            "sqlite bytes": h.db_files_bytes().decode("latin-1"),
        }
        self.assertNoLeaks([k for k, v in where.items() if leaked_fragment(v)],
                           "reviewer-quoted credential stored/returned unredacted")


class AdapterLifecycleGapTests(HarnessCase):
    def test_off_pause_and_apply_clear_adapter_pending_packets(self):
        leftovers = []
        for action in ("disable", "reset_allowance", "configure"):
            h = self.harness()
            h.enable()
            h.post(request="r", answer="a", history=tool_rows())
            self.assertEqual(1, h.adapter.pending_count)
            if action == "disable":
                h.service.disable("s1")
            elif action == "reset_allowance":
                h.service.reset_allowance("s1", True)
            else:
                st = h.service.status("s1")
                h.service.configure("s1", {"route": dict(st["settings"]["route"]), "packet_bytes": 2048},
                                    st["saved_profile_revision"])
            if h.adapter.pending_count or h.pending_packets():
                leftovers.append(action)
        self.assertNoLeaks(leftovers, "pending packet retained in adapter memory after invalidating event")

    def test_coverage_not_silent_when_turn_boundary_unknown_or_split(self):
        def coverage(rows):
            h = self.harness()
            h.enable()
            h.post(history=rows)
            return h.pending_packets()[0].coverage_dict()

        pair = tool_rows()[1:]                                             # assistant tool_calls + tool row, no user row
        split = (tool_rows(call_id="c1")
                 + [{"role": "user", "content": "steer"}]
                 + tool_rows(call_id="c2", user="x")[1:])                  # a user row in the middle of the turn
        bad = []
        cov = coverage(pair)
        if cov["tool_pairs_included"] + cov["tool_pairs_omitted"] < 1:
            bad.append("no_user_row")
        cov = coverage(split)
        if cov["tool_pairs_included"] + cov["tool_pairs_omitted"] < 2:
            bad.append("mid_turn_user_row")
        self.assertNoLeaks(bad, "tool pairs vanished without any coverage marker")

    def test_fanout_over_20_counts_each_dropped_pair_once(self):
        calls = [{"id": f"c{i}", "function": {"name": "t", "arguments": "{}"}} for i in range(25)]
        rows = [{"role": "user", "content": "r"}, {"role": "assistant", "tool_calls": calls}] + [
            {"role": "tool", "tool_call_id": c["id"], "content": "ok"} for c in calls]
        h = self.harness()
        h.enable()
        h.post(history=rows)
        cov = h.pending_packets()[0].coverage_dict()
        self.assertEqual((25, 20, 5), (cov["tool_pairs_total"], cov["tool_pairs_included"], cov["tool_pairs_omitted"]))


class PacketBuilderGapTests(HarnessCase):
    def test_clip_marker_adjacent_to_key_word_does_not_invalidate_snapshot(self):
        left, _ = (lambda room: ((room + 1) // 2, room - (room + 1) // 2))(P._TEXT_CHARS - len(P._CLIP_MARK))
        text = "a" * (left - len(" token:")) + " token:" + ", " + "b" * 20000
        h = self.harness()
        result = h.run_turn(request=text, answer="a")
        self.assertTrue(result and result.get("accepted"),
                        f"capture refused a correctly redacted packet: {result and result.get('reason')}")

    def test_clip_marker_adjacent_to_key_word_in_tool_text(self):
        left4096 = (4096 - len(P._CLIP_MARK) + 1) // 2
        prefix = len('{"function":{"arguments":{"cmd":"')     # keys sort: function, id; arguments, name
        leaf = "a" * (left4096 - prefix - len(" token:")) + " token:" + ", " + "b" * 6000
        h = self.harness()
        result = h.run_turn(history=[{"role": "user", "content": "r"},
                                     {"role": "assistant", "tool_calls": [
                                         {"id": "c1", "function": {"name": "t", "arguments": {"cmd": leaf}}}]},
                                     {"role": "tool", "tool_call_id": "c1", "content": "ok"}])
        self.assertTrue(result and result.get("accepted"), f"{result and result.get('reason')}")

    def test_in_band_omissions_set_clipped_marker(self):
        deep = "DEEP"
        for _ in range(8):
            deep = {"n": deep}
        values = {
            "list>64": list(range(100)),
            "mapping>64": {f"k{i}": i for i in range(100)},
            "depth>6": deep,
            "bytes": b"xx",
            "set": {1, 2},
            "nan": float("nan"),
        }
        bad = []
        for label, value in values.items():
            payload = build_packet("r", "a", [{"call": {"v": value}, "result": {"ok": 1}}]).as_payload()
            call = payload["evidence"][2]
            if not call["clipped"] or call["ref"] not in payload["coverage"]["clipped_refs"]:
                bad.append(label)
        self.assertNoLeaks(bad, "content was omitted in-band without a coverage marker")

    def test_unencodable_text_and_huge_ints_do_not_abort_capture(self):
        bad = []
        trials = {
            "request surrogate": dict(request="a\ud800b", answer="a", history=[]),
            "answer surrogate": dict(request="r", answer="a\udcffb", history=[]),
            "tool result surrogate": dict(request="r", answer="a", history=[{"call": {"x": 1}, "result": {"t": "a\udcff"}}]),
            "huge int": dict(request="r", answer="a", history=[{"call": {"n": 10 ** 5000}, "result": {}}]),
        }
        for label, kwargs in trials.items():
            try:
                packet = build_packet(**kwargs)
                packet.serialized.encode("utf-8")
            except Exception as exc:                                    # noqa: BLE001
                bad.append(f"{label}:{type(exc).__name__}")
        self.assertNoLeaks(bad, "packet construction raised")
        h = self.harness()
        h.enable()
        h.post(history=tool_rows(result_content="bytes decoded with surrogateescape: \udcff"))
        self.assertEqual(1, h.adapter.pending_count, "turn silently produced no packet")

    def test_redaction_work_is_bounded_independent_of_input_size(self):
        seen = []

        class Spy:
            def __init__(self, inner):
                self.inner = inner

            def sub(self, repl, string, *a, **k):
                seen.append(len(string))
                return self.inner.sub(repl, string, *a, **k)

        original = P._SECRET_PATTERNS
        P._SECRET_PATTERNS = tuple(Spy(p) for p in original)
        try:
            build_packet("x" * 3_000_000, "a", [])
        finally:
            P._SECRET_PATTERNS = original
        self.assertLessEqual(max(seen), 1_048_576, "regex input unbounded: hook latency grows linearly with answer size")

    def test_token_counter_fields_survive(self):
        packet = build_packet("r", "a", [{"call": {"max_tokens": 111}, "result": {
            "usage": {"total_tokens": 222, "prompt_tokens": 333}, "token_count": 444}}])
        missing = [n for n in ("111", "222", "333", "444") if f":{n}" not in packet.serialized]
        self.assertNoLeaks(missing, "numeric token-count fields dropped as if credentials (no coverage marker)")


if __name__ == "__main__":
    unittest.main()

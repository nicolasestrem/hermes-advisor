"""ACCEPTANCE (must pass): real build_packet / real EventAdapter->Service packet construction.

Covers clip-straddling credentials, nested secret fields / tool arguments, orphan/missing/fan-out
results, previous-turn + exposed-reasoning exclusion, immutable snapshots, the actual serialized
UTF-8 cap, and coverage honesty. Defects found by this audit live in test_red_defects.py.
"""
import copy
import dataclasses
import hashlib
import json
import random
import time
import unittest

from _support import BODY, HarnessCase, leaked_fragment, tool_rows

from advisor_core import packet as P
from advisor_core.packet import CapturedPacket, PacketError, build_packet

SECRETS = {
    "sk": "sk-" + BODY,
    "bearer": "Bearer " + BODY,
    "ghp": "ghp_" + BODY[:25],
    "pem": "-----BEGIN " + "PRIVATE KEY-----\n" + BODY + "\n-----END PRIVATE KEY-----",
    "url": "https://user:" + BODY + "@host.invalid/x",
    "kv": "password=" + BODY,
    "authz": "Authorization: " + BODY,
}


def _geometry(max_chars):
    room = max_chars - len(P._CLIP_MARK)
    left = (room + 1) // 2
    return left, room - left


def _positions(total, secret_len, max_chars, span=14):
    """Secret start offsets around the left cut and the right cut of a clipped excerpt."""
    left, right = _geometry(max_chars)
    around_left = range(max(0, left - secret_len - span), left + span)
    right_edge = total - right
    around_right = range(max(0, right_edge - span), min(total - secret_len, right_edge + secret_len + span))
    return list(around_left) + list(around_right)


def _embed(secret, pos, total):
    filler = "x" * total
    return filler[:pos] + " " + secret + " " + filler[pos:]


class ClipStraddlingCredentialTests(HarnessCase):
    def _sweep(self, label, make_packet, max_chars, total):
        leaked = []
        for name, secret in SECRETS.items():
            for pos in _positions(total, len(secret), max_chars):
                if leaked_fragment(make_packet(_embed(secret, pos, total)).serialized):
                    leaked.append(f"{label}/{name}@{pos}")
                    break
        self.assertNoLeaks(leaked, "clip-straddling credential fragment survived")

    def test_request_and_answer_text_clip_boundary(self):
        self._sweep("request", lambda t: build_packet(t, "a", []), P._TEXT_CHARS, P._TEXT_CHARS + 4000)
        self._sweep("answer", lambda t: build_packet("r", t, []), P._TEXT_CHARS, P._TEXT_CHARS + 4000)

    def test_tool_json_excerpt_boundary_call_and_result(self):
        # Leaf string stays under 16384 chars so the 4096-char tool-text excerpt is the clip.
        self._sweep("call", lambda t: build_packet("r", "a", [{"call": {"cmd": t}, "result": {"ok": 1}}]),
                    P._TOOL_TEXT_CHARS, 6000)
        self._sweep("result", lambda t: build_packet("r", "a", [{"call": {"x": 1}, "result": {"out": t}}]),
                    P._TOOL_TEXT_CHARS, 6000)

    def test_two_stage_clip_leaf_then_json_excerpt(self):
        self._sweep("call2", lambda t: build_packet("r", "a", [{"call": {"cmd": t}, "result": {}}]),
                    P._TEXT_CHARS, P._TEXT_CHARS + 4000)

    def test_cap_driven_reduction_excerpts(self):
        # packet_bytes=1024 forces repeated 0.7x re-excerpting of request/answer.
        leaked = []
        for name, secret in SECRETS.items():
            for pos in range(0, 20000, 241):
                text = _embed(secret, pos, 20000)
                if leaked_fragment(build_packet(text, text, [], packet_bytes=1024).serialized):
                    leaked.append(f"{name}@{pos}")
                    break
        self.assertNoLeaks(leaked, "secret fragment survived cap-driven reduction")

    def test_secret_beyond_source_char_limit_is_still_redacted(self):
        for name, secret in SECRETS.items():
            text = "y" * (P._SOURCE_CHARS + 5000) + " " + secret
            self.assertFalse(leaked_fragment(build_packet(text, "a", []).serialized), name)

    def test_real_adapter_path_clip_boundary(self):
        h = self.harness()
        left, _ = _geometry(P._TEXT_CHARS)
        text = _embed(SECRETS["sk"], left - 5, P._TEXT_CHARS + 4000)
        h.enable()
        h.post(request=text, answer=text, history=tool_rows(call_args=json.dumps({"p": text}), result_content=text))
        packets = h.pending_packets()
        self.assertEqual(1, len(packets))
        self.assertFalse(leaked_fragment(packets[0].serialized))


class NestedSecretFieldTests(HarnessCase):
    KEYS = ["password", "Password", "PASSWD", "secret", "client_secret", "clientSecret", "Authorization",
            "authorization", "cookie", "Set-Cookie", "credentials", "api_key", "apiKey", "X-Api-Key",
            "private_key", "privateKey", "access_token", "refreshToken", "auth_token", "token",
            "session_token", "id_token", "bearer_token"]

    def _wrap(self, key, value):
        return {
            "top": {key: value},
            "deep": {"a": {"b": {"c": {key: value, "keep_me_visible": "ok"}}}},
            "list": [{"x": 1}, {"y": [{key: value}]}],
            "function": {"name": "t", "arguments": {"headers": {key: value}, "path": "keep_path"}},
        }

    def test_secret_valued_fields_removed_in_call_and_result_at_every_depth(self):
        leaked = []
        for key in self.KEYS:
            value = BODY + key
            packet = build_packet("r", "a", [{"call": self._wrap(key, value), "result": self._wrap(key, value)}])
            if leaked_fragment(packet.serialized, value):
                leaked.append(key)
        self.assertNoLeaks(leaked, "credential-shaped field values present in packet")

    def test_non_secret_siblings_are_kept(self):
        packet = build_packet("r", "a", [{"call": self._wrap("password", BODY), "result": {"ok": 1}}])
        self.assertIn("keep_me_visible", packet.serialized)
        self.assertIn("keep_path", packet.serialized)

    def test_exposed_reasoning_keys_removed_everywhere(self):
        keys = ["reasoning", "reasoning_content", "reasoningContent", "analysis", "thinking",
                "chain_of_thought", "internalReasoning", "thought-process"]
        leaked = []
        for key in keys:
            value = "HIDDEN" + BODY + key
            packet = build_packet("r", "a", [{"call": self._wrap(key, value), "result": self._wrap(key, value)}])
            if leaked_fragment(packet.serialized, value):
                leaked.append(key)
        self.assertNoLeaks(leaked, "reasoning-key values present in packet")

    def test_secret_patterns_inside_nested_free_text_and_dict_keys(self):
        packet = build_packet("r", "a", [{
            "call": {"outer": [{"cmd": "curl -H 'Authorization: Bearer " + BODY + "'"}],
                     "sk-" + BODY: "v"},
            "result": {"lines": ["ok", "key=sk-" + BODY, {"n": "ghp_" + BODY[:25]}]},
        }])
        self.assertFalse(leaked_fragment(packet.serialized))

    def test_adapter_tool_call_dict_arguments_and_result_row_fields(self):
        h = self.harness()
        rows = [
            {"role": "user", "content": "request"},
            {"role": "assistant", "reasoning_content": "HIDDEN" + BODY, "tool_calls": [
                {"id": "c1", "reasoning": "HIDDEN" + BODY,
                 "function": {"name": "t", "arguments": {"password": BODY, "api_key": BODY, "path": "keep"}}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "ok", "reasoning": "HIDDEN" + BODY, "token": BODY},
        ]
        h.enable()
        h.post(history=rows)
        self.assertFalse(leaked_fragment(h.pending_packets()[0].serialized))


class ToolPairIntegrityTests(HarnessCase):
    def _post(self, rows):
        h = self.harness()
        h.enable()
        h.post(history=rows)
        packet = h.pending_packets()[0]
        return packet, packet.as_payload()

    def test_fanout_results_match_by_id_in_call_order(self):
        calls = [{"id": f"c{i}", "function": {"name": f"tool_{i}", "arguments": "{}"}} for i in (1, 2, 3)]
        rows = [{"role": "user", "content": "r"}, {"role": "assistant", "tool_calls": calls}] + [
            {"role": "tool", "tool_call_id": f"c{i}", "content": f"body_{i}"} for i in (3, 1, 2)]
        _, payload = self._post(rows)
        ev = {e["ref"]: e["text"] for e in payload["evidence"]}
        for ordinal, i in enumerate((1, 2, 3), start=1):
            self.assertIn(f"tool_{i}", ev[f"tool-{ordinal:04d}-call"])
            self.assertIn(f"body_{i}", ev[f"tool-{ordinal:04d}-result"])
            self.assertNotIn("body_", ev[f"tool-{ordinal:04d}-call"])
        cov = payload["coverage"]
        self.assertEqual((3, 3, 0), (cov["tool_pairs_total"], cov["tool_pairs_included"], cov["tool_pairs_omitted"]))

    def test_orphan_duplicate_idless_and_missing_halves_are_omitted_not_fabricated(self):
        calls = [{"id": f"c{i}", "function": {"name": f"tool_{i}", "arguments": json.dumps({"a": f"ARGS{i}"})}}
                 for i in (1, 2, 3)]
        rows = [
            {"role": "user", "content": "r"},
            {"role": "assistant", "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c2", "content": "body_2"},
            {"role": "tool", "tool_call_id": "c1", "content": "body_1"},
            {"role": "tool", "tool_call_id": "ghost", "content": "ORPHANBODY"},
            {"role": "tool", "tool_call_id": "c1", "content": "DUPBODY"},
            {"role": "tool", "content": "IDLESSBODY"},
            # c3 never receives a result
        ]
        packet, payload = self._post(rows)
        text = packet.serialized
        for leaked in ("ORPHANBODY", "DUPBODY", "IDLESSBODY", "ARGS3", "tool_3"):
            self.assertNotIn(leaked, text, leaked)
        cov = payload["coverage"]
        self.assertEqual(2, cov["tool_pairs_included"])
        self.assertEqual(4, cov["tool_pairs_omitted"], "orphan+duplicate+id-less+missing-result each counted once")
        self.assertEqual(cov["tool_pairs_total"], cov["tool_pairs_included"] + cov["tool_pairs_omitted"])
        self.assertEqual(4, cov["upstream_omitted_tool_pairs"])

    def test_result_before_its_call_is_not_paired(self):
        rows = [
            {"role": "user", "content": "r"},
            {"role": "tool", "tool_call_id": "c1", "content": "EARLYRESULT"},
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]},
        ]
        packet, payload = self._post(rows)
        self.assertNotIn("EARLYRESULT", packet.serialized)
        self.assertEqual(0, payload["coverage"]["tool_pairs_included"])
        self.assertGreaterEqual(payload["coverage"]["tool_pairs_omitted"], 1)

    def test_malformed_calls_never_become_pairs(self):
        rows = [
            {"role": "user", "content": "r"},
            {"role": "assistant", "tool_calls": [
                "not-a-dict", {"id": "", "function": {"name": "t"}}, {"id": "ok", "function": "bad"},
                {"id": "d", "function": {"name": "t", "arguments": "{}"}},
                {"id": "d", "function": {"name": "dup", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "d", "content": "RES"},
        ]
        _, payload = self._post(rows)
        self.assertEqual(1, payload["coverage"]["tool_pairs_included"])
        self.assertEqual(4, payload["coverage"]["tool_pairs_omitted"])

    def test_more_than_64_pairs_keeps_newest_and_reports_omissions(self):
        rows = [{"role": "user", "content": "r"}]
        for start in range(0, 70, 20):          # adapter keeps <=20 declared calls per assistant row
            chunk = [{"id": f"c{i}", "function": {"name": "t", "arguments": json.dumps({"i": i})}}
                     for i in range(start, min(start + 20, 70))]
            rows.append({"role": "assistant", "tool_calls": chunk})
            rows.extend({"role": "tool", "tool_call_id": c["id"], "content": f"res-{c['id']}"} for c in chunk)
        _, payload = self._post(rows)
        cov = payload["coverage"]
        self.assertEqual(70, cov["tool_pairs_total"])
        self.assertLessEqual(cov["tool_pairs_included"], 64)
        self.assertEqual(70, cov["tool_pairs_included"] + cov["tool_pairs_omitted"])
        texts = " ".join(e["text"] for e in payload["evidence"])
        self.assertIn("res-c69", texts)          # newest retained
        self.assertNotIn("res-c0\"", texts)      # oldest dropped


class TurnAndReasoningExclusionTests(HarnessCase):
    def test_previous_turn_system_prose_and_reasoning_rows_never_enter_packet(self):
        old = "OLDTURN" + BODY
        rows = [
            {"role": "system", "content": "SYSTEMPROMPT" + BODY},
            {"role": "user", "content": old},
            {"role": "assistant", "content": old, "reasoning_content": old,
             "tool_calls": [{"id": "o1", "function": {"name": "old_tool", "arguments": json.dumps({"p": old})}}]},
            {"role": "tool", "tool_call_id": "o1", "content": old},
            {"role": "assistant", "content": "old final " + old, "reasoning": old},
            {"role": "user", "content": "current request"},
            {"role": "assistant", "content": "interim prose INTERIMMARK",
             "reasoning_content": "CURHIDDEN" + BODY,
             "tool_calls": [{"id": "n1", "function": {"name": "new_tool", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "n1", "content": "new result"},
        ]
        h = self.harness()
        h.enable()
        h.post(request="current request", answer="current answer", history=rows)
        packet = h.pending_packets()[0]
        text = packet.serialized
        for marker in ("OLDTURN", "SYSTEMPROMPT", "old_tool", "CURHIDDEN", "INTERIMMARK"):
            self.assertNotIn(marker, text, marker)
        self.assertIn("new_tool", text)
        self.assertEqual(1, packet.coverage_dict()["tool_pairs_included"])

    def test_orphan_result_from_previous_turn_is_not_adopted(self):
        rows = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "tool_calls": [{"id": "o1", "function": {"name": "old_tool", "arguments": "{}"}}]},
            {"role": "user", "content": "current"},
            {"role": "tool", "tool_call_id": "o1", "content": "STALERESULT"},
        ]
        h = self.harness()
        h.enable()
        h.post(history=rows)
        text = h.pending_packets()[0].serialized
        self.assertNotIn("STALERESULT", text)
        self.assertNotIn("old_tool", text)


class SnapshotImmutabilityTests(HarnessCase):
    def test_captured_packet_is_frozen_and_views_are_fresh_copies(self):
        packet = build_packet("r", "a", [{"call": {"x": 1}, "result": {"y": 2}}])
        digest = packet.digest
        with self.assertRaises(dataclasses.FrozenInstanceError):
            packet.serialized = "{}"
        with self.assertRaises((dataclasses.FrozenInstanceError, AttributeError, TypeError)):
            packet.digest = "0" * 64
        view = packet.as_payload()
        view["evidence"][0]["text"] = "MUTATED"
        view["evidence"].append({"ref": "evil"})
        cov = packet.coverage_dict()
        cov["tool_pairs_included"] = 999
        cov["clipped_refs"].append("evil")
        self.assertNotIn("MUTATED", packet.serialized)
        self.assertNotIn("evil", packet.serialized)
        self.assertEqual(digest, hashlib.sha256(packet.serialized.encode()).hexdigest())
        self.assertEqual(1, packet.coverage_dict()["tool_pairs_included"])
        self.assertIsInstance(packet.references, tuple)
        self.assertIsInstance(packet.coverage, tuple)

    def test_host_mutation_after_post_cannot_change_snapshot_or_digest(self):
        h = self.harness()
        rows = tool_rows(call_args=json.dumps({"path": "stable"}), result_content="stable body")
        h.enable()
        h.post(history=rows)
        packet = h.pending_packets()[0]
        before = (packet.serialized, packet.digest)
        rows[-1]["content"] = "MUTATEDLATER"
        rows[1]["tool_calls"][0]["function"]["arguments"] = "MUTATEDLATER"
        rows.append({"role": "tool", "tool_call_id": "c1", "content": "MUTATEDLATER"})
        self.assertEqual(before, (packet.serialized, packet.digest))
        self.assertNotIn("MUTATEDLATER", packet.serialized)

    def test_reviewer_mutating_its_payload_cannot_alter_retained_snapshot(self):
        async def evil_reviewer(payload, settings):
            payload["evidence"][0]["text"] = "TAMPERED"
            payload["evidence"].clear()
            return {"outcome": "bogus", "findings": []}       # invalid => failed, explicit retry allowed

        h = self.harness(reviewer=evil_reviewer)
        self.assertTrue(h.run_turn(request="original request", answer="ans", history=tool_rows()).get("accepted"))
        notes = h.settle()
        self.assertEqual("failed", notes[0]["status"])
        retry = h.service.review_now("s1")
        self.assertTrue(retry.get("accepted"), retry)
        for _ in range(200):
            if len(h.calls) >= 2:
                break
            time.sleep(0.02)
        self.assertEqual(2, len(h.calls))
        self.assertEqual(h.calls[0][0], h.calls[1][0], "retry must see the originally captured packet")
        self.assertNotIn("TAMPERED", json.dumps(h.calls[1][0]))

    def test_forged_or_tampered_snapshots_are_rejected_by_core(self):
        h = self.harness()
        h.enable()
        good = build_packet("r", "a", [])
        tampered = good.serialized.replace('"text":"r"', '"text":"sk-' + BODY + '"')
        forged = CapturedPacket(tampered, hashlib.sha256(tampered.encode()).hexdigest(), good.references, good.coverage)
        res = h.service.capture(session_id="s1", turn_id="t1", request=None, answer=None, history=(), snapshot=forged)
        self.assertFalse(res.get("accepted"), "snapshot with unredacted credential was admitted")
        wrong_digest = CapturedPacket(good.serialized, "0" * 64, good.references, good.coverage)
        res = h.service.capture(session_id="s1", turn_id="t1", request=None, answer=None, history=(),
                                snapshot=wrong_digest)
        self.assertFalse(res.get("accepted"))
        oversize = build_packet("r" * 20000, "a" * 20000, [], packet_bytes=32768)
        st = h.service.status("s1")
        h.service.configure("s1", {"route": dict(st["settings"]["route"]), "packet_bytes": 1024},
                            st["saved_profile_revision"])
        h.service.enable("s1")
        res = h.service.capture(session_id="s1", turn_id="t1", request=None, answer=None, history=(),
                                snapshot=oversize)
        self.assertFalse(res.get("accepted"), "snapshot larger than applied cap was admitted")
        self.assertEqual([], h.calls)


class SerializedCapTests(HarnessCase):
    CHARS = ["a", "😀", "\\", "\x01"]

    def test_actual_utf8_json_bytes_never_exceed_cap(self):
        bad = []
        for cap in (1024, 32768):
            for ch in self.CHARS:
                pairs = [{"call": {"c": ch * 6000}, "result": {"t": ch * 6000}} for _ in range(70)]
                packet = build_packet(ch * 70000, ch * 70000, pairs, packet_bytes=cap)
                actual = len(packet.serialized.encode("utf-8"))
                wire = len(json.dumps(packet.as_payload(), ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                if not (actual == packet.byte_count == wire <= cap):
                    bad.append(f"{cap}/{ch!r}:{actual}/{wire}")
        self.assertNoLeaks(bad, "serialized bytes exceed cap or disagree with wire form")

    def test_real_service_reviewer_receives_at_most_applied_cap(self):
        for cap in (1024, 5000, 32768):
            h = self.harness()
            big = "😀é\n\"\\" * 9000
            h.run_turn(request=big, answer=big, packet_bytes=cap,
                       history=tool_rows(call_args=json.dumps({"p": big}), result_content=big))
            h.settle()
            self.assertEqual(1, len(h.calls), cap)
            self.assertLessEqual(len(h.calls[0][1]), cap, cap)

    def test_invalid_caps_rejected(self):
        for bad in (1023, 32769, 0, -1, True, 32768.0, "32768", None):
            with self.assertRaises(PacketError, msg=repr(bad)):
                build_packet("r", "a", [], packet_bytes=bad)
        for ok in (1024, 32768):
            self.assertLessEqual(build_packet("r", "a", [], packet_bytes=ok).byte_count, ok)

    def test_lowering_applied_cap_invalidates_larger_cached_packet(self):
        h = self.harness()
        h.run_turn(request="r" * 20000, answer="a", history=[])
        h.settle()
        st = h.service.status("s1")
        h.service.configure("s1", {"route": dict(st["settings"]["route"]), "packet_bytes": 1024},
                            st["saved_profile_revision"])
        self.assertFalse(h.service.status("s1")["review_now_available"])


class CoverageHonestyTests(HarnessCase):
    def test_every_clipped_marker_is_flagged_and_counts_are_consistent(self):
        rng = random.Random(7)
        mark = P._CLIP_MARK
        for _ in range(25):
            pairs = [{"call": {"c": "c" * rng.choice((10, 3000, 20000))},
                      "result": {"t": "t" * rng.choice((10, 5000, 30000))}} for _ in range(rng.randint(0, 80))]
            upstream = rng.randint(0, 3)
            packet = build_packet("r" * rng.choice((10, 20000, 90000)), "a" * rng.choice((10, 20000)), pairs,
                                  packet_bytes=rng.choice((1024, 4096, 32768)),
                                  prior_coverage={"omitted_tool_pairs": upstream})
            payload = packet.as_payload()
            cov = payload["coverage"]
            for item in payload["evidence"]:
                if mark in item["text"]:
                    self.assertTrue(item["clipped"], item["ref"])
            self.assertEqual(sorted(i["ref"] for i in payload["evidence"] if i["clipped"]), sorted(cov["clipped_refs"]))
            self.assertTrue(set(cov["clipped_refs"]) <= set(packet.references))
            self.assertEqual(cov["request_clipped"], payload["evidence"][0]["clipped"])
            self.assertEqual(cov["answer_clipped"], payload["evidence"][1]["clipped"])
            self.assertEqual(cov["tool_pairs_total"], len(pairs) + upstream)
            self.assertEqual(cov["tool_pairs_total"], cov["tool_pairs_included"] + cov["tool_pairs_omitted"])
            self.assertEqual(cov["tool_pairs_included"], sum(r.endswith("-call") for r in packet.references))
            self.assertEqual(cov["tool_pairs_included"], sum(r.endswith("-result") for r in packet.references))

    def test_pairs_are_never_split_by_cap_reduction(self):
        pairs = [{"call": {"c": "c" * 3000}, "result": {"t": "t" * 3000}} for _ in range(30)]
        packet = build_packet("r", "a", pairs, packet_bytes=8192)
        calls = [r for r in packet.references if r.endswith("-call")]
        results = [r for r in packet.references if r.endswith("-result")]
        self.assertEqual([c[:-5] for c in calls], [r[:-7] for r in results])
        self.assertEqual(30, packet.coverage_dict()["tool_pairs_total"])
        self.assertEqual(30 - len(calls), packet.coverage_dict()["tool_pairs_omitted"])

    def test_prior_coverage_is_metadata_only_and_strict(self):
        packet = build_packet("r", "a", [], prior_coverage={"omitted_tool_pairs": 7})
        cov = packet.coverage_dict()
        self.assertEqual((7, 7, 0), (cov["upstream_omitted_tool_pairs"], cov["tool_pairs_omitted"],
                                     cov["tool_pairs_included"]))
        for bad in ({"omitted_tool_pairs": -1}, {"omitted_tool_pairs": True},
                    {"omitted_tool_pairs": 1, "text": "x"}, {}):
            with self.assertRaises(PacketError):
                build_packet("r", "a", [], prior_coverage=bad)

    def test_huge_answer_marks_answer_clipped_in_persisted_coverage(self):
        h = self.harness()
        h.run_turn(answer="y" * 200000)
        notes = h.settle()
        self.assertEqual("completed", notes[0]["status"])
        self.assertTrue(notes[0]["coverage"]["answer_clipped"])
        self.assertIn("answer", notes[0]["coverage"]["clipped_refs"])


if __name__ == "__main__":
    unittest.main()

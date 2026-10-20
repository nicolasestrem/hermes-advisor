import unittest

from advisor_core.packet import PacketError, build_packet


class PacketBuilderTests(unittest.TestCase):
    def test_redacts_credential_fields_patterns_and_exposed_reasoning(self):
        packet = build_packet(
            "send Authorization: Bearer abcdefghijklmnop",
            "Answer with sk-" + "1234567890abcdefghijklmnop",
            [{"call": {"tool": "x", "api_key": "inert-secret"}, "result": {"text": "ok", "reasoning": "hidden"}}],
        )
        serialized = packet.serialized
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("inert-secret", serialized)
        self.assertNotIn("hidden", serialized)
        self.assertIn("[REDACTED]", serialized)
        self.assertIn("request", packet.references)
        self.assertIn("answer", packet.references)
        self.assertIn("tool-0001-call", packet.references)
        self.assertIn("tool-0001-result", packet.references)

    def test_serialized_packet_respects_byte_cap_and_marks_every_clipped_component(self):
        packet = build_packet("r" * 10000, "a" * 10000, [
            {"call": {"cmd": "c" * 3000}, "result": {"text": "t" * 3000}}
            for _ in range(8)
        ], packet_bytes=1024, prior_coverage={"omitted_tool_pairs": 2})
        self.assertLessEqual(len(packet.serialized.encode("utf-8")), 1024)
        payload = packet.as_payload()
        self.assertTrue(payload["evidence"][0]["clipped"])
        self.assertTrue(payload["evidence"][1]["clipped"])
        self.assertGreater(payload["coverage"]["tool_pairs_omitted"], 0)
        self.assertEqual(payload["coverage"]["upstream_omitted_tool_pairs"], 2)
        self.assertTrue(set(payload["coverage"]["clipped_refs"]).issubset(set(packet.references)))

    def test_full_digest_changes_when_earlier_tool_evidence_changes(self):
        history = [
            {"call": {"command": "same"}, "result": {"text": f"x{i}"}}
            for i in range(3)
        ]
        before = build_packet("request", "same answer", history)
        history[0]["result"]["text"] = "changed after packet prefix"
        after = build_packet("request", "same answer", history)
        self.assertNotEqual(before.digest, after.digest)
        self.assertEqual(len(before.digest), 64)

    def test_rejects_invalid_essential_inputs_and_invalid_coverage(self):
        with self.assertRaises(PacketError):
            build_packet(None, "answer", [])
        with self.assertRaises(PacketError):
            build_packet("request", "answer", [], packet_bytes=True)
        with self.assertRaises(PacketError):
            build_packet("request", "answer", [], prior_coverage={"raw": "not allowed"})


if __name__ == "__main__":
    unittest.main()

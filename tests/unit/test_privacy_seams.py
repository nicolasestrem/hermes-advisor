"""Ordinary packet regressions for malformed reasoning and JSON-string seams."""
from __future__ import annotations

import json

from advisor_core.packet import build_packet
from host_adapter import EventAdapter


class _Service:
    def __init__(self):
        self.callback = None

    def subscribe_invalidation(self, callback):
        self.callback = callback
        return lambda: None

    def discover(self, session_id, surface):
        return None

    def status(self, session_id):
        return {
            "state": "enabled",
            "applied_revision": 1,
            "applied_generation": 1,
            "settings": {"packet_bytes": 32768},
        }


def test_reasoning_tags_fail_closed_for_nested_and_unterminated_blocks():
    hidden = "HIDDEN_REASONING_SEAM"
    packet = build_packet(
        "request",
        "before <think>outer <think>inner</think>" + hidden + "</think> after <thinking>" + hidden + " <reasoning attribute" + hidden,
        [],
    )

    assert hidden not in packet.serialized
    assert "before " in packet.serialized
    assert " after " in packet.serialized


def test_json_string_reasoning_keys_are_filtered_recursively_and_exactly():
    first, second, third = "HIDDEN_JSON_REASONING_A", "HIDDEN_JSON_REASONING_B", "HIDDEN_JSON_REASONING_C"
    arguments = json.dumps({
        "path": "keep-path",
        "reasoning": first,
        "nested": [{"analysis": second, "reasoning_mode": "keep-mode"}],
    })
    result = json.dumps({"items": [{"thinking": third}], "status": "keep-status"})
    packet = build_packet("request", "answer", [{
        "call": {"function": {"arguments": arguments}},
        "result": {"content": result},
    }])

    for hidden in (first, second, third):
        assert hidden not in packet.serialized
    assert "keep-path" in packet.serialized
    assert "keep-status" in packet.serialized
    assert "reasoning_mode" in packet.serialized
    assert "keep-mode" in packet.serialized

    top_level_hidden = "HIDDEN_TOP_LEVEL_JSON_REASONING"
    request_packet = build_packet(
        json.dumps({"prompt": "keep-prompt", "nested": [{"reasoning": top_level_hidden}]}),
        "answer",
        [],
    )
    assert top_level_hidden not in request_packet.serialized
    assert "keep-prompt" in request_packet.serialized


def test_real_adapter_filters_json_string_tool_arguments_and_results():
    first, second = "HIDDEN_ADAPTER_ARGUMENT_REASONING", "HIDDEN_ADAPTER_RESULT_REASONING"
    service = _Service()
    adapter = EventAdapter(service, surfaces={"cli"})
    adapter.start(session_id="session", turn_id="turn", platform="cli")
    try:
        adapter.post(
            session_id="session",
            turn_id="turn",
            platform="cli",
            user_message="request",
            assistant_response="answer",
            conversation_history=[
                {"role": "user", "content": "request"},
                {"role": "assistant", "tool_calls": [{
                    "id": "call-1",
                    "function": {"name": "tool", "arguments": json.dumps({"reasoning": first, "path": "keep"})},
                }]},
                {"role": "tool", "tool_call_id": "call-1", "content": json.dumps({"meta": {"analysis": second}, "ok": True})},
            ],
        )
        pending = adapter._pending[("session", "turn")][1]
        assert first not in pending.serialized
        assert second not in pending.serialized
        payload = json.loads(pending.serialized)
        call = json.loads(payload["evidence"][2]["text"])
        arguments = json.loads(call["function"]["arguments"])
        assert arguments["path"] == "keep"
    finally:
        adapter.close()

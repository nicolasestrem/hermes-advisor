"""Regression tests for adapter-side tool-pair omission accounting."""

import json

from host_adapter import EventAdapter


class _Service:
    def subscribe_invalidation(self, callback):
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


def _post(history):
    adapter = EventAdapter(_Service(), surfaces={"cli"})
    adapter.start(session_id="session", turn_id="turn", platform="cli")
    adapter.post(
        session_id="session",
        turn_id="turn",
        platform="cli",
        user_message="request",
        assistant_response="answer",
        conversation_history=history,
    )
    return adapter, adapter._pending[("session", "turn")][1]


def test_completed_pairs_across_assistant_rows_are_not_counted_twice():
    history: list[dict[str, object]] = [{"role": "user", "content": "request"}]
    for start in range(0, 70, 20):
        chunk = [
            {
                "id": f"call-{index}",
                "function": {"name": "tool", "arguments": json.dumps({"i": index})},
            }
            for index in range(start, min(start + 20, 70))
        ]
        history.append({"role": "assistant", "tool_calls": chunk})
        history.extend(
            {"role": "tool", "tool_call_id": call["id"], "content": f"result-{call['id']}"}
            for call in chunk
        )

    adapter, packet = _post(history)
    try:
        coverage = packet.coverage_dict()
        assert coverage["tool_pairs_total"] == 70
        assert coverage["tool_pairs_included"] == 64
        assert coverage["tool_pairs_omitted"] == 6
        assert coverage["upstream_omitted_tool_pairs"] == 0
        results = {
            json.loads(item["text"])["content"]
            for item in packet.as_payload()["evidence"]
            if item["kind"] == "tool_result"
        }
        assert "result-call-69" in results
        assert "result-call-6" in results
        assert "result-call-5" not in results
    finally:
        adapter.close()


def test_missing_result_remains_one_upstream_omission():
    history = [
        {"role": "user", "content": "request"},
        {
            "role": "assistant",
            "tool_calls": [
                {"id": "complete", "function": {"name": "tool", "arguments": "{}"}},
                {"id": "missing", "function": {"name": "tool", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "complete", "content": "ok"},
    ]
    adapter, packet = _post(history)
    try:
        coverage = packet.coverage_dict()
        assert coverage["tool_pairs_total"] == 2
        assert coverage["tool_pairs_included"] == 1
        assert coverage["tool_pairs_omitted"] == 1
        assert coverage["upstream_omitted_tool_pairs"] == 1
    finally:
        adapter.close()

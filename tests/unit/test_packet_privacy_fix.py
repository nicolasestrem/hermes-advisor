"""Focused packet privacy regressions (raw assertions, no expectedFailure wrappers)."""
import json

from advisor_core import packet as P
from advisor_core.packet import build_packet

BODY = "QWERTYSECRET123456789"


def texts(value):
    return [build_packet(value, "a", []).serialized,
            build_packet("r", value, []).serialized,
            build_packet("r", "a", [{"call": {"x": value}, "result": {}}]).serialized,
            build_packet("r", "a", [{"call": {}, "result": {"x": value}}]).serialized]


def test_credentials_in_assignments_json_headers_quotes_and_vendor_tokens():
    examples = [f"OPENAI_API_KEY={BODY}", json.dumps({"password": BODY}),
                f"Cookie: sid={BODY}; theme=dark", f'password: "alpha {BODY} charlie delta"',
                "github_pat_11" + BODY, "xoxb-" + BODY, "sk_live_" + BODY,
                "AIzaSy" + BODY + "abcdef", "glpat-" + BODY]
    for example in examples:
        assert all(BODY not in packet for packet in texts(example))


def test_reasoning_fields_and_inline_think_are_excluded():
    hidden = "HIDDEN_REASONING_123456"
    packet = build_packet("r", "before <thinking>" + hidden + "</thinking> after", [
        {"call": {"reasoning_details": hidden, "thinking_blocks": [{"text": hidden}]},
         "result": {"content": [{"type": "reasoning", "text": hidden}, {"type": "text", "text": "ok"}]}}
    ])
    assert hidden not in packet.serialized
    assert "before " in packet.serialized and " after" in packet.serialized


def test_numeric_token_counters_are_retained():
    packet = build_packet("r", "a", [{"call": {"max_tokens": 111}, "result": {
        "usage": {"total_tokens": 222, "prompt_tokens": 333}, "token_count": 444}}])
    assert all(f":{n}" in packet.serialized for n in (111, 222, 333, 444))


def test_nested_omissions_are_marked_clipped():
    deep = "leaf"
    for _ in range(8):
        deep = {"n": deep}
    for val in ([*range(100)], {f"k{i}": i for i in range(100)}, deep,
                b"xx", {1, 2}, float("nan")):
        payload = build_packet("r", "a", [{"call": {"v": val}, "result": {}}]).as_payload()
        call = payload["evidence"][2]
        assert call["clipped"] and call["ref"] in payload["coverage"]["clipped_refs"]


def test_surrogates_huge_ints_and_long_source_are_safe():
    for kwargs in (dict(request="a\ud800b", answer="a", history=[]),
                   dict(request="r", answer="a\udcffb", history=[]),
                   dict(request="r", answer="a", history=[{"call": {"n": 10**5000}, "result": {}}])):
        assert build_packet(**kwargs).serialized.encode("utf-8")
    seen = []
    class Spy:
        def __init__(self, inner): self.inner = inner
        def sub(self, repl, string, *a, **k):
            seen.append(len(string)); return self.inner.sub(repl, string, *a, **k)
    original = P._SECRET_PATTERNS
    P._SECRET_PATTERNS = tuple(Spy(p) for p in original)
    try:
        build_packet("x" * 3_000_000, "a", [])
    finally:
        P._SECRET_PATTERNS = original
    assert max(seen) <= 1_048_576


def test_quoted_assignment_redacts_fully_without_rewinding_the_frontier():
    # Regression (over-redaction + remnant leak in _redact_assignments): the
    # opening quote must be consumed with the value, and an inner assignment
    # match must never rewind the redaction frontier to re-expose text.
    packet = build_packet('password="abc token=def XYZSECRET"', "a", []).serialized
    assert "XYZSECRET" not in packet
    assert "def" not in packet
    assert "abc" not in packet


def test_quoted_value_pass_cannot_swallow_intervening_prose():
    # Regression: the second quoted-value pass must not span across the
    # redaction seam and collapse prose between stray quote pairs.
    packet = build_packet("r", 'password: "v" he said "hi"', []).serialized
    assert "he said" in packet


def test_bracket_prefixed_prose_is_not_discarded_as_invalid_json():
    # Regression: strings that merely OPEN with [ or { but are not JSON are
    # real content and must fall back to the prose path.
    assert P._sanitize_value("[TODO] Fix pagination", depth=0) == ("[TODO] Fix pagination", False)
    assert P._sanitize_value("[a link](https://x.example)", depth=0) == ("[a link](https://x.example)", False)
    assert P._sanitize_value('{"not": "json"} trailing', depth=0)[0] == '{"not": "json"} trailing'


def test_secret_bearing_bracket_strings_are_still_discarded():
    # The prose fallback is only safe when nothing looks secret-bearing.
    for value in ('{"session_cookie_value": "SID=xyz123456"}', '{"password": "hunter2"}'):
        safe, omitted = P._sanitize_value(value, depth=0)
        assert omitted and "SID=" not in safe and "hunter2" not in safe


def test_valid_json_strings_are_still_structured_not_prose():
    assert P._sanitize_value('{"ok": 1}', depth=0) == ('{"ok": 1}', False)


def test_non_http_url_userinfo_credentials_are_redacted_like_http():
    # Regression (astro #2, probe-confirmed): only http(s) userinfo was
    # redacted; postgres/mysql/redis/mongodb connection strings leaked.
    examples = ['DATABASE_URL=postgres://alice:PW42secret@db.internal/app',
                'mysql://bob:hunter2pw@localhost/db',
                'redis://:cachepw@redis.internal:6379/0',
                'mongodb+srv://carol:gridpw@cluster.example/db',
                'DATABASE_URL=http://alice:PW42secret@db.internal/app']
    markers = ("PW42secret", "hunter2pw", "cachepw", "gridpw")
    for example in examples:
        assert all(m not in packet for packet in texts(example) for m in markers)


def test_url_redaction_keeps_credential_free_urls_intact():
    assert "[REDACTED]" not in build_packet(
        "r", "see https://example.com/docs and postgres://db.internal/app", []).serialized


def test_empty_password_urls_are_left_intact():
    # No password after the colon: nothing to redact.
    assert "ftp://files@example.com" in build_packet(
        "r", "mirror ftp://files@example.com/pub", []).serialized


def test_long_non_http_userinfo_is_redacted_without_a_regex_window_limit():
    # Regression (review P2): the userinfo pattern was regex-window bounded, so a
    # non-HTTP password longer than the 1_048_576-char window left no window
    # containing both the scheme and the terminating '@' and survived into the
    # bounded excerpt. The stateful scan now covers every scheme unbounded.
    secret = "PW" + ("a" * 1_100_000) + "TAILMARK"
    packet = build_packet("r", f"postgres://alice:{secret}@db.internal/app", []).serialized
    assert "TAILMARK" not in packet
    assert "PWaaa" not in packet
    assert "[REDACTED]" in packet

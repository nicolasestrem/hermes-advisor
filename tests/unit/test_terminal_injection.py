"""A21 — terminal-control injection is neutralized in model/tool-derived text.

ANSI CSI/OSC escapes, the bell, and stray C0 control bytes must never survive
into text the CLI prints or the Desktop pane renders/copies (SPEC R08).
"""
from advisor_core.service import _safe_model_text, _markdown_escape

_CONTROLS = [
    "\x1b[31mRED\x1b[0m",
    "\x1b]8;;http://evil.example\x1b\\click\x1b]8;;\x1b\\",
    "bell\x07x",
    "\x1b]0;title\x07",
    "\x00null",
    "a\x1fb",
    "a\x0bb\x0cc",
    # C1 controls, including the single-byte CSI/OSC introducers
    "a\x85b\x9bc\x9dd",
    "\x9b31mSBCSI\x9bm",
]

_HTML_INJECTIONS = [
    '<script>alert(1)</script>',
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    'javascript:alert(1)',
    'vbscript:msgbox',
    'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
    'data:text/html,<script>alert(1)</script>',
    '&#60;script&#62;',
    '&lt;img src=x&gt;',
]


def _no_control_bytes(s: str) -> bool:
    return all(ord(c) == 10 or (32 <= ord(c) and ord(c) != 127 and not 0x80 <= ord(c) <= 0x9F) for c in s)


def _no_html_or_uri(s: str) -> bool:
    low = s.lower()
    return (
        "<" not in s
        and not low.lstrip().startswith(("javascript:", "vbscript:", "livescript:", "mocha:"))
        and "javascript:" not in low
        and "vbscript:" not in low
        and "data:" not in low
        and "<script" not in low
        and "onerror" not in low
    )


def test_safe_model_text_strips_html_js_datauri_injection():
    for evil in _HTML_INJECTIONS:
        out = _safe_model_text(evil, 2000)
        assert _no_html_or_uri(out), f"html/js/data-URI survived in {out!r}"


def test_markdown_escape_neutralizes_html_js_datauri_injection():
    for evil in _HTML_INJECTIONS:
        out = _markdown_escape(evil)
        assert _no_html_or_uri(out), f"html/js/data-URI survived in {out!r}"
        assert out.startswith(("\\", "[")) or "<" not in out


def test_safe_model_text_strips_terminal_controls():
    for evil in _CONTROLS:
        out = _safe_model_text(evil, 2000)
        assert _no_control_bytes(out), f"control bytes survived in {out!r}"


def test_markdown_escape_strips_terminal_controls():
    for evil in _CONTROLS:
        out = _markdown_escape(evil)
        assert "\x1b" not in out, f"ESC survived in {out!r}"
        assert "\x07" not in out, f"bell survived in {out!r}"
        assert _no_control_bytes(out), f"control bytes survived in {out!r}"


def test_safe_model_text_strips_controls_alongside_secret_redaction():
    out = _safe_model_text("api_key=sk-abc123\x1b[31m", 2000)
    assert "\x1b" not in out
    assert "sk-abc123" not in out
    assert _no_control_bytes(out)
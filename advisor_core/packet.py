"""Bounded, redacted packet construction; packets remain memory-only."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class PacketError(ValueError):
    """Raised when a bounded packet cannot safely be constructed."""


_SOURCE_CHARS = 65536
_TEXT_CHARS = 16384
_JSON_STRING_CHARS = _TEXT_CHARS
_PATTERN_WINDOW_CHARS = 1_048_576
_PATTERN_OVERLAP_CHARS = 65_536
_TOOL_TEXT_CHARS = 4096
_HISTORY_ITEMS = 64
_MAX_DEPTH = 6
_MAX_REASONING_NESTING = 64
_MAX_COLLECTION_ITEMS = 64
_REDACTED = "[REDACTED]"
_CLIP_MARK = " …[clipped]… "

_REASONING_KEYS = {
    "analysis", "reasoning", "reasoningcontent", "chainofthought", "thinking",
    "internalreasoning", "thoughtprocess",
}
_SECRET_KEY_PARTS = (
    "password", "passwd", "secret", "authorization", "cookie", "credential",
    "apikey", "privatekey", "accesstoken", "refreshtoken", "authtoken", "token",
)
_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}"),
    re.compile(r"(?i)\b(?:authorization|x-api-key|token|api[_-]?key|password|passwd|secret|access[_-]?token|refresh[_-]?token)\s*[:=]\s*[^\s,;]+"),
    # Simple expressions only: identifier detectors use one unbounded prefix
    # plus a fixed marker, which was quadratic on long near-matches in Python's
    # backtracking regex engine. A direct marker scan is linear and still
    # catches embedded credentials (including ``xsk-`` and ``_sk-``).
    re.compile(r"(?i)sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
)


_VENDOR_MARKERS = ("github_pat_", "xoxb-", "xoxp-", "xoxa-", "xoxr-", "xoxs-", "sk-", "sk_live_", "sk_test_", "AIza", "glpat-", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "AKIA", "eyJ")


@dataclass(frozen=True, slots=True)
class CapturedPacket:
    """Immutable serialized snapshot passed to the reviewer by copy."""

    serialized: str
    digest: str
    references: tuple[str, ...]
    coverage: tuple[tuple[str, Any], ...]

    @property
    def byte_count(self) -> int:
        return len(self.serialized.encode("utf-8"))

    def as_payload(self) -> dict[str, Any]:
        """Return a fresh JSON object so reviewer mutation cannot alter the cache."""
        return json.loads(self.serialized)

    def coverage_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(dict(self.coverage), ensure_ascii=False))


def sanitize_text(value: str, *, max_chars: int = 4096) -> str:
    """Redact common credential patterns and cap text without retaining raw tails.

    Redaction runs over the complete source BEFORE clipping: a credential that
    straddles the excerpt boundary must not survive as a bare fragment.
    """
    if not isinstance(value, str):
        raise PacketError("text value must be a string")
    redacted = _redact_text(value)
    redacted = _strip_reasoning(redacted)
    # Preserve the visible omission notice when redaction itself shrinks an
    # over-window source below the excerpt limit; coverage records that loss.
    if len(value) > max_chars and len(redacted) <= max_chars and _CLIP_MARK not in redacted:
        room = max(0, max_chars - len(_CLIP_MARK))
        return _bounded_excerpt(redacted, room) + _CLIP_MARK
    return _bounded_excerpt(redacted, max_chars)


def _redact_text(value: str) -> str:
    # Scan the complete source for assignments, unbounded credentials and token
    # markers before any excerpt is taken. Fixed-pattern passes use overlapping
    # bounded windows, so no independent hard chunk can lose a seam.
    value = _redact_assignments(value)
    value = _redact_bearer_tokens(value)
    value = _redact_url_credentials(value)
    value = _redact_private_key_blocks(value)
    for pattern in _SECRET_PATTERNS:
        value = _pattern_sub_full_scan(pattern, value)
    return _redact_vendor_tokens(value)


def _pattern_sub_full_scan(pattern, text: str) -> str:
    """Apply a fixed-pattern regex over all text using overlapping bounded windows."""
    if len(text) <= _PATTERN_WINDOW_CHARS:
        return pattern.sub(_REDACTED, text)
    core_chars = _PATTERN_WINDOW_CHARS - _PATTERN_OVERLAP_CHARS
    pieces = []
    position = 0
    while position < len(text):
        core_end = min(position + core_chars, len(text))
        window_end = min(core_end + _PATTERN_OVERLAP_CHARS, len(text))
        window = text[position:window_end]
        core_length = core_end - position
        matched = []

        def replace(match):
            if match.start() < core_length:
                matched.append((match.start(), match.end()))
                return _REDACTED
            return match.group(0)

        transformed = pattern.sub(replace, window)
        consumed = core_length
        for _start, end in matched:
            consumed = max(consumed, end)
        output_end = consumed + sum(
            len(_REDACTED) - (end - start)
            for start, end in matched
        )
        pieces.append(transformed[:output_end])
        position += consumed
    return "".join(pieces)


def _redact_bearer_tokens(text: str) -> str:
    """Redact Bearer credentials across arbitrary source lengths, without regex windows."""
    out, pos, index = [], 0, 0
    token_chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~+/-"
    while index + 6 <= len(text):
        if (text[index:index + 6].casefold() != "bearer"
                or (index and (text[index - 1].isalnum() or text[index - 1] == "_"))):
            index += 1
            continue
        cursor = index + 6
        if cursor >= len(text) or not text[cursor].isspace():
            index += 1
            continue
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        token_start = cursor
        while cursor < len(text) and text[cursor] in token_chars:
            cursor += 1
        if cursor - token_start < 8:
            index += 1
            continue
        out.extend((text[pos:index], _REDACTED))
        pos = cursor
        index = cursor
    out.append(text[pos:])
    return "".join(out)


_SCHEME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
)


def _redact_url_credentials(text: str) -> str:
    """Remove userinfo from ``scheme://user:password@host`` for *any* scheme.

    A bounded-window regex cannot match a credential whose userinfo spans more
    than one window (no single window contains both the scheme and the
    terminating ``@``), so this stateful scan walks the complete source. It
    covers every scheme (``postgres://``, ``redis://``, ``mysql://``,
    ``mongodb+srv://``, …, not just HTTP(S)) and both the ``user:pw@`` and the
    empty-user ``:pw@`` form, and it only starts at a ``\\b``-style boundary so
    a scheme embedded in a longer word is left alone exactly as before.
    """
    out, pos, index = [], 0, 0
    length = len(text)
    while index < length:
        char = text[index]
        if not (char.isascii() and char.isalpha()):
            index += 1
            continue
        previous = text[index - 1] if index else ""
        if previous.isalnum() or previous == "_":
            index += 1
            continue
        cursor = index + 1
        while cursor < length and text[cursor] in _SCHEME_CHARS:
            cursor += 1
        if text[cursor:cursor + 3] != "://":
            index = cursor if cursor > index + 1 else index + 1
            continue
        scheme_end = cursor + 3
        cursor = scheme_end
        while cursor < length and text[cursor] not in ":/@ \t\r\n":
            cursor += 1
        # The user part may be empty (redis://:pw@host); the ':' must follow it.
        if cursor >= length or text[cursor] != ":":
            index = scheme_end
            continue
        cursor += 1
        password_start = cursor
        while cursor < length and text[cursor] not in "/@ \t\r\n":
            cursor += 1
        if cursor == password_start or cursor >= length or text[cursor] != "@":
            index = scheme_end
            continue
        out.extend((text[pos:index], text[index:scheme_end], _REDACTED))
        pos = cursor + 1
        index = pos
    out.append(text[pos:])
    return "".join(out)


def _redact_private_key_blocks(text: str) -> str:
    """Redact private-key blocks, including blocks crossing any regex window."""
    begin_prefix = "-----BEGIN "
    key_suffix = "PRIVATE KEY-----"
    end_prefix = "-----END "
    out, pos, search = [], 0, 0
    while True:
        start = text.find(begin_prefix, search)
        if start < 0:
            break
        label_start = start + len(begin_prefix)
        begin_end = text.find(key_suffix, label_start)
        if begin_end < 0:
            search = label_start
            continue
        label = text[label_start:begin_end]
        if any(char != " " and not "A" <= char <= "Z" for char in label):
            search = label_start
            continue
        cursor = begin_end + len(key_suffix)
        block_end = None
        while True:
            end_start = text.find(end_prefix, cursor)
            if end_start < 0:
                break
            end_key = text.find(key_suffix, end_start + len(end_prefix))
            if end_key < 0:
                break
            end_label = text[end_start + len(end_prefix):end_key]
            if all(char == " " or "A" <= char <= "Z" for char in end_label):
                block_end = end_key + len(key_suffix)
                break
            cursor = end_start + len(end_prefix)
        if block_end is None:
            block_end = len(text)
        out.extend((text[pos:start], _REDACTED))
        pos = block_end
        search = block_end
    out.append(text[pos:])
    return "".join(out)



def _redact_assignments(text: str) -> str:
    key = re.compile(r'''(?i)(?<![\w-])(?:export\s+)?([A-Z0-9_.-]*(?:API[_-]?KEY|ACCESS[_-]?TOKEN|REFRESH[_-]?TOKEN|AUTH[_-]?TOKEN|PASSWORD|PASSWD|SECRET|PRIVATE[_-]?KEY|CREDENTIAL|TOKEN)[A-Z0-9_.-]*)["']?\s*[:=]\s*["']?''')
    out, pos = [], 0
    for match in key.finditer(text):
        start = match.end()
        quote = text[start - 1] if start > 0 and text[start - 1] in "\\\"'" else None
        if quote:
            # Retain text only up to the char BEFORE the opening quote: the
            # quotes are replaced together with the value, so the later
            # quoted-value passes cannot span across the redaction seam and
            # swallow intervening prose between stray quote pairs.
            value_start = start - 1
            end = text.find(quote, start)
            end = len(text) if end < 0 else end + 1
        else:
            value_start = start
            end = start
            while end < len(text) and text[end] not in " \t\r\n,;}":
                end += 1
        if value_start < pos:
            # Match starts inside an already-redacted value (e.g. an inner
            # assignment swallowed by an unterminated-looking quote): keep the
            # redaction frontier, never rewind it and re-expose redacted text.
            continue
        out.append(text[pos:value_start])
        out.append(_REDACTED)
        pos = end
    out.append(text[pos:])
    text = "".join(out)
    text = re.sub(r'''(?i)(password|passwd|secret|private[_-]?key)\s*:\s*["']([^"']*)["']''', lambda m: m.group(1) + ': ' + _REDACTED, text)
    text = re.sub(r'''(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+''', lambda m: m.group(1) + _REDACTED, text)
    text = re.sub(r'''(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd|secret|private[_-]?key|client[_-]?secret)\s*[:=]\s*([^\s,;}]+)''', lambda m: m.group(0)[:m.start(1)-m.start()] + _REDACTED, text)
    # JSON/dict-literal fields embedded in strings aren't represented as mappings.
    text = re.sub(
        r'''(?i)(["'](?:api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|password|passwd|secret|private[_-]?key|client[_-]?secret|authorization|cookie)["']\s*:\s*["'])([^"']*)(["'])''',
        lambda m: m.group(1) + _REDACTED + m.group(3), text,
    )
    return re.sub(r'(?i)(\bcurl\s+-u\s+)[^\s]+', lambda m: m.group(1) + _REDACTED, text)


def _redact_vendor_tokens(text: str) -> str:
    low = text.lower()
    spans = []
    for marker in _VENDOR_MARKERS:
        at, needle = 0, marker.lower()
        while True:
            i = low.find(needle, at)
            if i < 0:
                break
            j = i + len(marker)
            if needle == "sk-":
                while j < len(text) and text[j] in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-":
                    j += 1
                if j - i >= len(marker) + 12:
                    spans.append((i, j))
            else:
                while j < len(text) and not text[j].isspace() and text[j] not in {",", ";", '"', "'", "<", ">", "}"}:
                    j += 1
                if j > i + len(marker):
                    spans.append((i, j))
            at = max(j, i + 1)
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + _REDACTED + text[end:]
    return text


def _reasoning_tag_at(text: str, index: int):
    """Return a recognized reasoning tag token at ``index`` without regex backtracking."""
    pos = index + 1
    closing = pos < len(text) and text[pos] == "/"
    if closing:
        pos += 1
    for name in ("thinking", "reasoning", "think"):
        end = pos + len(name)
        if text[pos:end].casefold() != name:
            continue
        # Match the old word-boundary convention while requiring valid close-tag
        # syntax. Opening tags may carry attributes; their content is still hidden.
        if end < len(text) and (text[end].isalnum() or text[end] == "_"):
            continue
        if closing:
            close_end = end
            while close_end < len(text) and text[close_end].isspace():
                close_end += 1
            if close_end < len(text) and text[close_end] == ">":
                return "close", name, close_end + 1, False
            continue
        tag_end = text.find(">", end)
        if tag_end < 0:
            return "open", name, len(text), False
        last = tag_end - 1
        while last >= end and text[last].isspace():
            last -= 1
        self_closing = last >= end and text[last] == "/"
        return "open", name, tag_end + 1, self_closing
    return None


def _strip_reasoning(text: str) -> str:
    """Remove balanced reasoning blocks, dropping malformed/unclosed tails fail-closed."""
    output = []
    kept_from = 0
    block_start = None
    stack = []
    index = 0
    while index < len(text):
        if text[index] != "<":
            index += 1
            continue
        token = _reasoning_tag_at(text, index)
        if token is None:
            index += 1
            continue
        kind, name, end, self_closing = token
        if kind == "open" and not self_closing:
            if block_start is None:
                block_start = index
            if end == len(text) and (not text or text[-1] != ">"):
                return "".join(output) + text[kept_from:block_start]
            if len(stack) >= _MAX_REASONING_NESTING:
                return "".join(output) + text[kept_from:block_start]
            stack.append(name)
        elif kind == "close" and stack and stack[-1] == name:
            stack.pop()
            if not stack:
                output.append(text[kept_from:block_start])
                kept_from = end
                block_start = None
        index = end
    if block_start is None:
        output.append(text[kept_from:])
    else:
        output.append(text[kept_from:block_start])
    return "".join(output)


def build_packet(
    request: str,
    answer: str,
    history: Sequence[Mapping[str, Any]],
    packet_bytes: int = 32768,
    *,
    prior_coverage: Mapping[str, Any] | None = None,
) -> CapturedPacket:
    """Build a canonical, sanitized packet bounded by serialized UTF-8 bytes.

    ``history`` contains complete ``{"call": ..., "result": ...}`` pairs.
    An adapter that bounded its event window before calling this helper may pass
    ``prior_coverage={"omitted_tool_pairs": N}``; this value is metadata only.
    The helper performs no I/O and does not persist either raw or sanitized text.
    """
    if not isinstance(request, str) or not isinstance(answer, str):
        raise PacketError("request and final answer must be strings")
    if type(packet_bytes) is not int or not 1024 <= packet_bytes <= 32768:
        raise PacketError("packet_bytes must be an integer from 1024 through 32768")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        raise PacketError("history must be a bounded sequence of tool call/result pairs")
    upstream_omitted = 0
    if prior_coverage is not None:
        if not isinstance(prior_coverage, Mapping) or set(prior_coverage) != {"omitted_tool_pairs"}:
            raise PacketError("prior_coverage may contain only omitted_tool_pairs")
        upstream_omitted = prior_coverage["omitted_tool_pairs"]
        if type(upstream_omitted) is not int or not 0 <= upstream_omitted <= 1_000_000_000:
            raise PacketError("omitted_tool_pairs must be a nonnegative integer")

    req_source_clipped = len(request) > _SOURCE_CHARS
    ans_source_clipped = len(answer) > _SOURCE_CHARS
    req_base, req_structured_omitted = _sanitize_value(
        request, depth=0, exact_reasoning_keys=True)
    ans_base, ans_structured_omitted = _sanitize_value(
        answer, depth=0, exact_reasoning_keys=True)
    req_text = req_base
    ans_text = ans_base
    req_clipped = req_source_clipped or len(request) > _TEXT_CHARS or req_structured_omitted
    ans_clipped = ans_source_clipped or len(answer) > _TEXT_CHARS or ans_structured_omitted

    history_count = len(history)
    start = max(0, history_count - _HISTORY_ITEMS)
    invalid_or_cut = start
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for index in range(start, history_count):
        item = history[index]
        if not isinstance(item, Mapping) or "call" not in item or "result" not in item:
            invalid_or_cut += 1
            continue
        call_value, call_omitted = _sanitize_value(item["call"], depth=0)
        result_value, result_omitted = _sanitize_value(item["result"], depth=0)
        call_text = json.dumps(call_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        result_text = json.dumps(result_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        call_clipped = call_omitted or len(call_text) > _TOOL_TEXT_CHARS
        result_clipped = result_omitted or len(result_text) > _TOOL_TEXT_CHARS
        call_text = _bounded_excerpt(call_text, _TOOL_TEXT_CHARS)
        result_text = _bounded_excerpt(result_text, _TOOL_TEXT_CHARS)
        ordinal = index + 1
        pair = (
            {"ref": f"tool-{ordinal:04d}-call", "kind": "tool_call", "text": call_text, "clipped": call_clipped},
            {"ref": f"tool-{ordinal:04d}-result", "kind": "tool_result", "text": result_text, "clipped": result_clipped},
        )
        pairs.append(pair)

    # Short histories are bounded by their existing sequence length. For longer
    # histories the first entries were excluded above and summarized here.
    total_tool_pairs = history_count + upstream_omitted
    base_omitted = invalid_or_cut + upstream_omitted
    initial_pair_count = len(pairs)

    def make_payload() -> dict[str, Any]:
        evidence: list[dict[str, Any]] = [
            {"ref": "request", "kind": "request", "text": req_text, "clipped": req_clipped},
            {"ref": "answer", "kind": "answer", "text": ans_text, "clipped": ans_clipped},
        ]
        for call_item, result_item in pairs:
            evidence.extend((call_item, result_item))
        clipped_refs = [item["ref"] for item in evidence if item["clipped"]]
        coverage = {
            "request_clipped": req_clipped,
            "answer_clipped": ans_clipped,
            "tool_pairs_total": total_tool_pairs,
            "tool_pairs_included": len(pairs),
            "tool_pairs_omitted": base_omitted + (initial_pair_count - len(pairs)),
            "upstream_omitted_tool_pairs": upstream_omitted,
            "clipped_refs": clipped_refs,
        }
        return {"version": 1, "evidence": evidence, "coverage": coverage}

    payload = make_payload()
    while _encoded_size(payload) > packet_bytes and pairs:
        pairs.pop(0)
        payload = make_payload()

    # Keep the request and final answer in the packet, but use labeled excerpts
    # when necessary. Each reduction operates on already-redacted text.
    while _encoded_size(payload) > packet_bytes:
        req_len = len(req_text)
        ans_len = len(ans_text)
        if max(req_len, ans_len) <= 32:
            raise PacketError("packet metadata cannot fit the configured byte cap")
        if req_len >= ans_len and req_len > 32:
            target = max(16, int(req_len * 0.70))
            req_text = _bounded_excerpt(req_base, target)
            req_clipped = True
        elif ans_len > 32:
            target = max(16, int(ans_len * 0.70))
            ans_text = _bounded_excerpt(ans_base, target)
            ans_clipped = True
        payload = make_payload()

    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > packet_bytes:
        raise PacketError("serialized packet exceeds configured byte cap")
    references = tuple(item["ref"] for item in payload["evidence"])
    coverage_tuple = tuple(
        (key, tuple(value) if isinstance(value, list) else value)
        for key, value in sorted(payload["coverage"].items())
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return CapturedPacket(serialized, digest, references, coverage_tuple)


def _encoded_size(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _bounded_excerpt(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= len(_CLIP_MARK) + 2:
        return "…"[:max_chars]
    room = max_chars - len(_CLIP_MARK)
    left = (room + 1) // 2
    right = room - left
    return value[:left] + _CLIP_MARK + (value[-right:] if right else "")


def _is_secret_key(key: str, *, exact_reasoning_keys: bool = False) -> bool:
    canonical = re.sub(r"[^a-z0-9]", "", key.casefold())
    is_reasoning = canonical in _REASONING_KEYS if exact_reasoning_keys else _is_reasoning_key(canonical)
    if is_reasoning:
        return True
    if canonical in {"maxtokens", "totaltokens", "prompttokens", "completiontokens", "tokencount"}:
        return False
    return any(part in canonical for part in _SECRET_KEY_PARTS)


def _is_reasoning_key(canonical: str) -> bool:
    return canonical in _REASONING_KEYS or any(term in canonical for term in ("reasoning", "think", "chainofthought", "thoughtsignature", "scratchpad", "encryptedcontent", "codexreasoning"))


def _sanitize_value(
    value: Any, *, depth: int, exact_reasoning_keys: bool = False,
) -> tuple[Any, bool]:
    if depth > _MAX_DEPTH:
        return "[depth omitted]", True
    if value is None or type(value) in (bool, int):
        if type(value) is int:
            try:
                str(value)
            except ValueError:
                return "[large integer omitted]", True
        return value, False
    if type(value) is float:
        return (value, False) if value == value and abs(value) != float("inf") else ("[non-finite number omitted]", True)
    if isinstance(value, str):
        first = 0
        scan_limit = min(len(value), _JSON_STRING_CHARS + 1)
        while first < scan_limit and value[first].isspace():
            first += 1
        if first > _JSON_STRING_CHARS:
            return "[structured JSON omitted]", True
        if first < len(value) and value[first] in "[{":
            if len(value) > _JSON_STRING_CHARS:
                return "[structured JSON omitted]", True
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                # Invalid JSON that merely opens with a bracket/brace is often
                # real prose (e.g. "[TODO] Fix pagination"). Fall back to the
                # prose path unless the value looks secret-bearing, in which
                # case discarding stays the safe default.
                folded = value.casefold()
                if any(part in folded for part in _SECRET_KEY_PARTS) or any(
                        marker in folded for marker in _VENDOR_MARKERS):
                    return "[invalid JSON omitted]", True
                safe = _safe_text(value, _TEXT_CHARS)
                return safe, len(value) > _TEXT_CHARS or safe != value
            safe_value, omitted = _sanitize_value(
                parsed, depth=depth + 1, exact_reasoning_keys=True)
            if not omitted:
                safe_text = _safe_text(value, _TEXT_CHARS)
                return safe_text, safe_text != value
            try:
                safe_text = json.dumps(
                    safe_value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            except (ValueError, RecursionError):
                return "[structured JSON omitted]", True
            if len(safe_text) > _TEXT_CHARS:
                return _bounded_excerpt(safe_text, _TEXT_CHARS), True
            return safe_text, True
        safe = _safe_text(value, _TEXT_CHARS)
        return safe, len(value) > _TEXT_CHARS or safe != value
    if isinstance(value, Mapping):
        result, omitted = {}, False
        content_type = value.get("type")
        if isinstance(content_type, str) and content_type.casefold() in {"thinking", "reasoning"}:
            return {}, True
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= _MAX_COLLECTION_ITEMS:
                result["[omitted_fields]"] = len(value) - _MAX_COLLECTION_ITEMS
                omitted = True
                break
            key = raw_key if isinstance(raw_key, str) else "[nontext_key]"
            if _is_secret_key(key, exact_reasoning_keys=exact_reasoning_keys):
                omitted = True
                continue
            safe_key = sanitize_text(key, max_chars=128)
            result[safe_key], child_omitted = _sanitize_value(
                raw_value, depth=depth + 1, exact_reasoning_keys=exact_reasoning_keys)
            omitted = omitted or child_omitted
        return result, omitted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        values, omitted = [], False
        for item in value[:_MAX_COLLECTION_ITEMS]:
            safe, child_omitted = _sanitize_value(
                item, depth=depth + 1, exact_reasoning_keys=exact_reasoning_keys)
            values.append(safe)
            omitted = omitted or child_omitted
        if len(value) > _MAX_COLLECTION_ITEMS:
            values.append(f"[omitted {len(value) - _MAX_COLLECTION_ITEMS} items]")
            omitted = True
        return values, omitted
    return "[unsupported value omitted]", True


def _safe_text(value: str, limit: int) -> str:
    value = value.encode("utf-8", "replace").decode("utf-8")
    return sanitize_text(value, max_chars=limit)

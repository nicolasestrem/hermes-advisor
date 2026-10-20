# Pure Core API — Adapter Contract (verified)

Audience: the host integration adapter (child owner of `host_adapter.py`,
runtime hooks, dashboard, and `tests/integration/`). This describes the
authoritative public surface of `advisor_core` as of this review, including
the exact `capture` snapshot and `status` state semantics. Everything below is
backed by unit tests in `tests/unit/` (34 passing).

## Imports (stable public exports)

`advisor_core.__init__` exports exactly:

```
CapturedPacket, ConfigError, ConfigurationConflict, OwnerUnavailable,
PacketError, ResponseValidationError, ReviewerResult, SQLiteStore, Service,
ServiceError, SessionNotDiscovered, StoreError, build_packet, default_settings,
sanitize_text, validate_response, validate_settings
```

Adapter code must use only these names. Everything else in `advisor_core.*`
is internal and may change without notice.

## Construction

```python
service = Service(data_dir, reviewer, route_validator=None)
```

- `data_dir`: the active profile's plugin-data directory. Session IDs never
  select a path; scope comes from `data_dir` only.
- `reviewer`: a required **async callable** `(packet_payload: dict,
  settings: dict) -> Awaitable[result]`. It is injected by the host and never
  performs inference in core. It is always invoked on a private event loop on
  a bounded worker thread, with a **contextvars copy** per attempt — thread
  state set before `capture` is visible inside the reviewer; later mutations
  are not.
- `route_validator`: optional host callback `(route: {"provider","model"}) ->
  Any`; returning `False` (or raising) rejects the configuration. Core never
  resolves models or credentials itself.
- The reviewer may return either the raw `{"outcome","findings"}` mapping or a
  `ReviewerResult(response=..., provider=..., model=..., usage=...,
  usage_known=...)` envelope.

## Lifecycle and control ownership

1. `discover(session_id, surface)` → metadata-only status dict. Surfaces
   accepted: `cli`, `desktop`, `interactive_cli`, `native_desktop` (a
   surface-specific capability decision stays with the host; see `status`).
   Recovery from a dead owner uses flock; invocation counters are preserved,
   never replayed.
2. `configure(session_id, settings, expected_revision)` → validates with
   `validate_settings` (strict, no coercion; complete route required).
   Configuration changes bump the **applied generation**, which cancels
   queued attempts and invalidates running ones at their next checkpoint.
3. `enable(session_id)` → arms capture; snapshot cache cleared.
4. `capture(session_id, turn_id, request, answer, history, *, snapshot=None,
   coverage=None)` → admission. See below for the exact contract.
5. `review_now(session_id)` → manual trigger; same validation path.
6. `status`, `notes`, `rate`, `reset_allowance(confirm=True)`, `disable`,
   `feedback`, `close(timeout)`.

Control ownership: exactly one local `Service` per durable session (flock-
guaranteed). Multiple `Service` instances on different sessions, or sequential
instances across process restarts, are safe and tested. A caller picking a
session id that was not discovered gets `SessionNotDiscovered` from any
action. Two concurrent `Service` objects for the same session: the second
fails acquisition (`OwnerUnavailable` semantics); OS-released flock recovery
after process death is the sanctioned path.

## Exact `capture` snapshot semantics

`capture` never persists raw content. The adapter has two modes:

**Mode A — core builds the packet (pass `request`, `answer`, `history`):**

```python
result = service.capture("session", "turn-1", request_text, answer_text, tool_history)
# -> {"accepted": True, "requests": 1, ...} or {"accepted": False, "reason": ...}
```

Core runs `build_packet(...)`: redaction runs over the **complete source
before any clipping** (a credential straddling the excerpt boundary cannot
survive as a bare fragment — regression-tested), reasoning keys are dropped,
secret-named keys and credential patterns become `[REDACTED]`, and the
serialized packet is byte-capped at the configured `packet_bytes`.

**Mode B — adapter supplies a prebuilt snapshot (pass `snapshot` + optional
`coverage`):**

```python
packet = build_packet(request, answer, history, packet_bytes=N)   # or equivalent
result = service.capture("session", "turn-1", None, None, None,
                         snapshot=packet, coverage={"omitted_tool_pairs": 0})
```

`snapshot` must be a validated `CapturedPacket` whose `byte_count` ≤ the
applied `packet_bytes`. Core re-validates the snapshot locally: canonical
serialization, digest match, exact `{version, evidence, coverage}` shape,
evidence items exactly `{ref, kind, text, clipped}`, first/second evidence
items must be `request`/`answer`, every text re-passes credential redaction,
and references/coverage must match the serialization. Any mismatch returns
`{"accepted": False, "reason": "invalid_packet", "error": ...}` — nothing is
stored. If `coverage` is supplied, its `omitted_tool_pairs` must equal the
snapshot's `upstream_omitted_tool_pairs` exactly.

`CapturedPacket` is a frozen dataclass with `serialized`, `digest`
(sha256 hex of `serialized`), `references` (evidence ref order), `coverage`.
`.as_payload()` returns a fresh dict each call (reviewer mutation cannot
poison the snapshot cache).

**Packet structure** (what the reviewer receives as `packet_payload`):

```json
{"version": 1,
 "evidence": [{"ref": "request", "kind": "request", "text": "...", "clipped": false},
              {"ref": "answer",  "kind": "answer",  "text": "...", "clipped": false},
              {"ref": "tool-0001-call",  "kind": "tool_call",  ...},
              {"ref": "tool-0001-result","kind": "tool_result", ...}, ...],
 "coverage": {"request_clipped": false, "answer_clipped": false,
              "tool_pairs_total": N, "tool_pairs_included": N,
              "tool_pairs_omitted": N, "upstream_omitted_tool_pairs": N,
              "clipped_refs": ["..."]}}
```

Evidence refs for the reviewer are exactly: `request`, `answer`,
`tool-NNNN-call`, `tool-NNNN-result`. A finding's `evidence_refs` must cite
only these.

**Disabled/paused sessions:** `capture` checks metadata only and returns
`{"accepted": False, "reason": ...}` **before inspecting, copying, or
digesting any content argument** (a caller-passed object whose `__str__` raises
proves this in tests). Content is never touched when the session is off.

## Exact `status` state semantics

`service.status(session_id)` returns one dict per discovered session; **the
`state` field is a string, never a bool**:

| state | meaning |
|---|---|
| `"disabled"` | discovered but never enabled, or explicitly disabled |
| `"enabled"` | armed for capture |
| `"paused"` | temporarily not admitting; see `pause_reason` |

`pause_reason` (only when paused, `None` otherwise) is one of:
`allowance_exhausted`, `storage_error`, `worker_shutdown`, `allowance_reset`,
`configuration_changed` — or the session's stored pause reason. Distinction:
`state="disabled"` with `pause_reason="worker_shutdown"` (or `storage_error`
latched) means fail-closed shutdown, not user disable.

Full status shape (adapter-relevant fields):

```python
{"session_id", "surface", "surface_supported", "state", "pause_reason",
 "saved_profile_revision", "saved_settings", "applied_revision",
 "applied_generation", "settings", "configuration_error",  # "no_model" or None
 "invocations_used", "invocations_remaining",
 "review_now_available": bool, "review_now_unavailable_reason",  # "expired", "no_captured_answer", "storage_error", ...
 "review_now_expires_in_seconds": int|None,
 "active": bool, "pending": bool,
 "storage_error": bool, "volatile_error": str|None,
 "latest_attempt": {...}|None}
```

`latest_attempt` (`_public_attempt`) shape:

```python
{"attempt_id", "turn_id", "status",  # queued|running|completed|failed|timed_out|cancelled|skipped|abandoned
 "outcome": {...}|None,              # validated {"outcome","findings"} when completed
 "requested_route": {"provider", "model"},   # from applied config
 "served_route":  {"provider", "model"},     # None/None unless the reviewer explicitly reported attribution via ReviewerResult
 "usage": dict|None, "usage_known": bool,    # usage known=False stays (None, False); never fabricated
 "cancellation_pending": bool,
 "created_at", "terminal_at", "error_code", "error"}
```

Attribution rule: `served_route` is populated **only** from an explicit
`ReviewerResult(provider=..., model=...)`. A plain-dict reviewer return, or a
`ReviewerResult` with `usage_known=False`, keeps `served_route` null-pair and
`usage=None` — unknown stays visibly unknown; the core never invents a route,
token count, or price (regression-tested, including suppression of a supplied
usage mapping when `usage_known=False`).

## Snapshot cache, expiry, retention

- `review_now_available` requires a live cached captured packet: cache TTL is
  1800 s; a TTL-expired cache entry is dropped non-destructively on read
  (reported as `expired`, no attempt record is created or extended) —
  regression-tested. `no_captured_answer` when nothing was captured.
- Retention keeps the most recent 100 settled attempts per session
  (`cancellation_pending=1` attempts are never pruned while the transport is
  unaccounted for). An abandoned attempt keeps `status` terminal
  (`timed_out`/`cancelled`) **and** `cancellation_pending=1` until the
  transport terminally settles it; retention protects it in the meantime.

## Cancellation, deadline, graceful shutdown

- Attempt deadline (`deadline_seconds`, 1–120) is enforced at enqueue, before
  call, and during invocation.
- If the injected reviewer swallows `CancelledError`, core abandons the
  transport after a bounded grace window (`TRANSPORT_CANCEL_GRACE_SECONDS`,
  class attribute, default 2.0): the worker thread is unblocked, the attempt
  is persisted with `cancellation_pending=1` and a volatile
  "cancellation unresolved" note, and the private loop is force-closed.
- `close(timeout)` cancels local work, waits for **confirmed** transport
  termination, then releases ownership. If transports stay unresolved past
  the timeout it returns `False` and keeps the owner lock for the worker
  callback to release later. Worker threads are daemonized, so an unresolved
  transport cannot keep the interpreter alive after `close` returns —
  regression-tested at process level (a subprocess with an uncancellable
  reviewer exits promptly).
- `close()` always marks running attempts `cancelled` with
  `cancellation_pending=1` (fail-closed: a transport that later returns must
  not publish over a shutdown).

## Privacy invariants (verified by tests)

1. Redaction precedes clipping everywhere (`build_packet`, `sanitize_text`).
2. Reasoning/thinking keys are never included in packets.
3. Secret-named keys and credential patterns (Bearer, api keys, private keys,
   URL credentials, sk-/AKIA/ghp_ tokens) are `[REDACTED]`.
4. Packets are memory-only; the SQLite schema has no raw-content column
   (attempts store digest, outcome, bounded attribution, usage, coverage).
5. Persisted notes/errors are sanitized (`sanitize_text` / fixed generic
   messages); exception text is never persisted raw — only exception class
   name plus a quota/auth classification.
6. Validation failures are never salvaged: a partial-looking valid response
   fails the whole attempt (`validate_response` is whole-response strict).

## Config envelope (reference)

`validate_settings` accepts exactly `{route{provider,model}, packet_bytes
1024–32768, output_tokens 128–2048, max_findings 1–4, deadline_seconds 1–120,
session_allowance 1–20}` — strict types, no coercion, unknown keys rejected;
`route_validator` callback can reject a route. `default_settings()` returns
the unconfigured envelope with `route=None`.

## Explicitly out of scope for core (host owns these)

- Model/credential resolution, real provider transport, retries/backoff.
- Surface-specific enablement policy (`surface_supported` is informational).
- Any paid call — core performs none; unit tests use synthetic fixture
  reviewers only, never a live provider.

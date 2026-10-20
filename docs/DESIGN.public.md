# Design (public preview)

This page describes the architecture that is actually in the preview tree. The
normative behavior target is in [`../specs/SPEC.md`](../specs/SPEC.md); the pure
core's adapter contract is in
[`CORE_API_FOR_ADAPTER.md`](CORE_API_FOR_ADAPTER.md) and is preserved
byte-for-byte. Where this page and the target differ, the target remains binding
and the difference is a known issue or preview deviation.

## Shape

```text
Hermes host
  ├─ hooks / commands / dashboard mount / profile plugin-data dir
  │
  ├─ advisor_host.py          entry, Runtime, route grants, command parser
  │    └─ host_adapter.py     event adapter: discovery, packet snapshot, end gate
  │
  ├─ advisor_core/
  │    ├─ service.py          admission, worker pool, attempts, ratings, lifecycle
  │    ├─ store.py            profile-scoped SQLite + POSIX advisory locks
  │    ├─ packet.py           sanitize/redact/clip + coverage + digest
  │    ├─ response.py         strict reviewer response validation
  │    └─ worker.py           bounded daemon worker pool + detached-loop drain
  │
  ├─ dashboard/plugin_api.py  authenticated host-mounted HTTP router
  └─ desktop/
       ├─ pane.mjs            React pane (source of truth)
       ├─ logic.mjs           pure view decisions
       └─ plugin.js           generated single-file Desktop entry
```

The plugin does not modify the host or import private host internals. It uses the
host's public plugin context for registration, profile data directory, hooks,
commands, structured LLM calls, and the dashboard router. One deliberate exception
is **Low4**: profile resolution currently calls the undocumented
`hermes_constants.profile_name_for_home` helper. That dependency is disclosed, not
approved as a permanent API.

## Registration and ownership

`__init__.py` defers the import of `advisor_host.register` until the host invokes
the plugin. `register(ctx)`:

1. resolves the active profile's plugin-data directory;
2. creates or reuses one `Runtime` for that directory;
3. registers five hooks (`pre_llm_call`, `post_llm_call`, `on_session_end`,
   `subagent_start`, `subagent_stop`);
4. registers the `/advisor` command;
5. registers an unload cleanup.

Runtimes are kept in a plugin-owned module registry keyed by the resolved
plugin-data directory. The dashboard router only looks up a runtime for the
request's authenticated profile; it never constructs a worker or accepts a caller
profile/path. No runtime means HTTP 503, not a new owner. This preserves the
profile-isolation boundary and prevents HTTP input from discovering a session.

The adapter tracks active `(session_id, turn_id)` keys, a bounded child-session
exclusion set, and a bounded global pending-snapshot map. Discovery is
metadata-only; content is not copied, sanitized, digested, or retained while a
session is disabled or paused.

## Review flow

1. **Start.** A verified main-session hook registers the session with its surface.
   Child sessions and unsupported surfaces are excluded. Discovery is
   metadata-only.
2. **Post.** On a successful completed main answer, the adapter reads the session
   metadata, builds a packet from the captured request/answer and paired tool
   rows, and stores the packet snapshot in memory. The packet is bounded and
   redacted; the original history is not retained.
3. **End.** The end hook checks completion flags, re-checks session state,
   revision, and generation, and admits a review. A stale or disabled session
   discards the snapshot.
4. **Admission.** The core validates the packet, checks the session's applied
   settings and allowance, reserves an invocation, and enqueues one attempt.
5. **Transport.** A bounded daemon worker calls the injected reviewer (the host's
   structured completion API) with the explicit route, schema, token budget,
   deadline, and temperature `0.0`.
6. **Validation.** The response is validated locally against the packet's evidence
   refs and the configured finding limit. Findings are sanitized before storage.
7. **Settlement.** The attempt is recorded as completed/failed/timed out/cancelled
   with usage and attribution when known. Settled attempts are pruned to the most
   recent 100 per session.
8. **Read/copy.** CLI/dashboard return status and findings. `feedback` returns a
   copyable text block. The Desktop pane can copy selected text. Nothing is
   inserted, sent, or fed back into the model context.

`review_now` reuses the latest eligible in-memory snapshot for up to 30 minutes. If
the snapshot is absent, expired, evicted, or tied to a different applied
generation, the action is refused with a reason. That refusal is honest: the
preview does not re-read private history to reconstruct a packet.

## Trust boundaries

| Boundary | Rule |
|---|---|
| Host → plugin | Host supplies the authenticated profile, data directory, hooks, and public context. Plugin code is trusted as plugin code; capabilities are consent, not a sandbox. |
| Plugin → reviewer | Only the bounded packet and instructions leave the plugin. The route is user-selected; there is no default or fallback. Redaction is best effort. |
| HTTP → runtime | The router only looks up the runtime registered for the authenticated profile. No caller profile/path/session discovery. |
| Session → store | Ownership is an owner token plus a POSIX advisory lock. A second live owner is rejected; ambiguous ownership fails closed. |
| Content → packet | Sanitization/redaction/clipping happen before storage/send. Coverage records what was included/omitted, though M2/Low2 make that record incomplete in some cases. |
| Desktop → host | The pane uses host-provided React and `@hermes/plugin-sdk`; it never writes to the composer or submits. Copy is the only handoff. |

## Storage

`SQLiteStore` is scoped to one profile's plugin-data directory. It creates the
directory with mode `0700`, enables WAL, foreign keys, and `synchronous=FULL`, and
serializes writes issued through one store instance before opening a connection.
Independent processes rely on SQLite's bounded busy handler and per-session OS
locks.

Schema version 1 contains:

| Table | Purpose |
|---|---|
| `profile_config` | Singleton settings envelope + revision |
| `sessions` | Session ID, surface, owner token, state, generation, applied settings, timestamps |
| `attempts` | Attempt status, packet digest, route attribution, usage, error, cancellation, timestamps |
| `findings` | Validated findings with evidence refs and coverage |
| `ratings` | User usefulness rating and sanitized reason |
| `feedback_actions` | Copy-action audit record with selected notes/attempts |
| `dedup` | Attempt deduplication keys |

The store keeps the latest 100 settled attempts per session. It does not delete
data when the plugin is uninstalled. A schema version newer than the code is
refused rather than downgraded.

## Concurrency and failure behavior

- Two process call slots; one transport per session; one pending snapshot per
  session/attempt.
- The worker pool is plugin-owned, bounded, and daemon-based. Shutdown uses
  sentinels, does not self-join, and reports unconfirmed termination honestly.
- A transport that ignores cancellation is detached with its event loop kept alive
  until it actually finishes; its attempt stays cancellation-pending and retains
  its slot. This is bounded by one daemon thread per held slot, not a hidden
  retry.
- A storage failure latches the session into a visible error state instead of
  silently continuing.
- A reviewer failure/timeout/cancellation settles the attempt with an error and
  releases the slot only when the local transport is confirmed or detached.
- A second owner/ambiguous lock fails closed (`OwnerUnavailable`), not by stealing
  the session.

The preview still has evidence-limited races: H2/H3 can lose an in-flight paid
result across disable/re-discovery; H1 can silently suppress a review; M2/M4 can
under-report coverage or evict a snapshot; M1 can stall a hook thread on storage.
See [../KNOWN-ISSUES.md](../KNOWN-ISSUES.md). No no-loss guarantee is made.

## Dashboard API

`dashboard/plugin_api.py` mounts an authenticated router. It provides:

- `GET /metadata`
- `GET /status?session_id=...&expected_instance=...`
- `GET /notes?session_id=...&expected_instance=...`
- `POST /control` with exactly
  `{expected_instance, session_id, action, params}`

The router never creates a runtime, never accepts a caller profile/path, and never
discovers a session from HTTP input. Error mapping is deliberately class-only to
avoid leaking provider/storage exception bodies. A malformed, empty or non-object
`/control` body, or a missing or empty `expected_instance`, returns the documented
`400 invalid_control_shape`, never the framework's generated 422.

## Desktop

`desktop/plugin.js` is generated by `scripts/build-desktop-plugin.py` from
`desktop/logic.mjs` and `desktop/pane.mjs`. It inlines the owned sources and keeps
only allowlisted imports: `@hermes/plugin-sdk`, `react`,
`react/jsx-runtime`, `react/jsx-dev-runtime`. The build fails if a relative or
non-allowlisted import appears.

The pane is experimental and Copy-only. It implements status, atomic settings
save, review-now, rating, note selection, and clipboard copy. It does not
implement Add-to-draft. Its tests use a prepared host/React/Vitest workspace; a
fixture/DOM pass is not native packaged-UI proof.

## What this design does not solve

- Full R10 CLI (B1).
- Add-to-draft.
- Windows support.
- Native acceptance evidence or a stable release.
- A guarantee that a paid review is delivered (H1/H2/H3/M2/M4).
- Complete secret detection in captured content.

For the authoritative target and acceptance state, use
[`../specs/SPEC.md`](../specs/SPEC.md),
[`../specs/ACCEPTANCE.md`](../specs/ACCEPTANCE.md), and
[`../EVIDENCE.md`](../EVIDENCE.md).

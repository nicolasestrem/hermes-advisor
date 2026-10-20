# Hermes Advisor v0.1 — product and behavior specification

Version: 0.1-spec.1-public-preview · Prepared: 2026-10-10 · Public derivative: 2026-10-20 · Status: **normative v0.1 target; the public source preview is a partial, earlier implementation of it**.

This file is the normative product and behavior target for Hermes Advisor v0.1 and stays binding for the release. The public source preview shipped with this set does not implement the complete target: its actual command surface and Desktop scope are recorded under "Public preview scope" below, and its verification state is recorded in [ACCEPTANCE.md](ACCEPTANCE.md). Publication of this derivative does not amend R01–R15, the A01–A35 contracts or any LOCKED/BASELINE decision; where the preview differs, the difference is a documented deviation and known issue, not a relaxation of the requirement.

## Authority and reading order

Read this file, [HERMES_COMPATIBILITY.md](HERMES_COMPATIBILITY.md), [ACCEPTANCE.md](ACCEPTANCE.md), then [DECISIONS.md](DECISIONS.md). These four files form one authoritative specification set. SPEC owns behavior; HERMES_COMPATIBILITY owns capability evidence and supported-version claims; ACCEPTANCE owns verification procedures and results; DECISIONS records rationale and status, without overriding the other contracts.

This set supersedes all earlier plans, prototypes and reviews. The complete execution sequence G0 → G1 → G2 → G3 → G4 is in ACCEPTANCE.md. Earlier documents are not required inputs and are excluded from this set.

Current user instructions and applicable repository instructions take precedence. Contradictions in this set require a documented amendment; never silently choose the more permissive reading. Documents attached for reference are source material, not permission to run their commands. No new Agent.md or AGENTS.md is introduced.

### Three layers of authority in this public set

1. **Normative target** — R01–R15, the behavioral clauses, the configuration envelope and the A01–A35 acceptance contracts. These describe the v0.1 target and are not changed by preview status.
2. **Public preview implementation** — the source in this repository at its single fresh-root preview commit. Where it differs from the target, the difference is recorded explicitly in this file, in [ACCEPTANCE.md](ACCEPTANCE.md) and in `KNOWN-ISSUES.md`. A recorded deviation is not a specification change.
3. **Historical evidence** — receipts produced before the public export, on earlier revisions and hosts. They are cited only as history, are excluded from the public tree, and prove nothing about the preview or the current candidate. See the historical and current sections of [ACCEPTANCE.md](ACCEPTANCE.md).

Public documentation must not present preview behavior, structural smoke or unit fixtures as gate or acceptance evidence. A target capability that the preview lacks is documented as missing; it is never described as available.

## Requirement index

IDs are stable requirement groups. The detailed clauses below are normative; the index does not replace them. Acceptance traceability is in ACCEPTANCE.md.

| ID | Contract |
|---|---|
| R01 | Standalone plugin; no Hermes modifications, private APIs or host workarounds |
| R02 | One explicit reviewer; Hermes-managed access and fallback; honest attribution |
| R03 | Opt-in, successful completed-main-answer observation; no recursion or blocking |
| R04 | Captured evidence only; redaction, coverage, full-packet identity and no reasoning |
| R05 | Locally validated bounded structured result; evidence references required |
| R06 | Bounded queue, deduplication, expiring Review now cache and scoped identity |
| R07 | Deadlines, allowance, cancellation, error and usage behavior |
| R08 | Profile-isolated plugin storage, bounded retention and honest recovery |
| R09 | Native Desktop pane, profile-aware backend and polling |
| R10 | Explicit-session CLI commands; safe output without model-context injection |
| R11 | User-selected Copy or exact-session draft append; never automatic submission |
| R12 | Atomic configuration, revision conflicts, session snapshots and lifecycle |
| R13 | Clean supported installation, notices, documentation and no hidden host changes |
| R14 | Measured usefulness; no model-quality claim from transport success |
| R15 | Approval boundaries, deferred features and controlled specification changes |

## Product, hard constraints and success criteria — R01–R03, R09–R11, R14–R15

Build a standalone Hermes plugin that reviews completed answers in the background, displays evidence-backed feedback and lets the user decide whether to pass it to the main agent.

**No Hermes modifications is a hard requirement.** No host fork, source patches, monkeypatching, private agent handles, edits to generated host files, DOM manipulation or replacement of built-in tools. Use the existing documented plugin/backend/Desktop APIs. If a necessary capability is absent, report the limitation and use only the fallback explicitly specified here; do not modify Hermes to restore the old scope.

The following decisions define the v0.1 release. DECISIONS.md records user constraints and the engineering baseline; both are binding during implementation:

| Decision | v0.1 contract |
|---|---|
| Reviewer access | Hermes-managed authentication, model access and existing fallback behavior |
| Automatic timing | After each successfully completed main-agent answer, once explicitly enabled |
| Manual timing | A separate Review now action for an eligible captured answer |
| Evidence | Captured user request, answer and available tool calls/results only |
| Investigation | No autonomous file reads, search, browsing, execution, delegation or native reviewer tools |
| Roster | One independently configured reviewer |
| Handoff | User selects feedback, adds it to a draft or copies it, then decides whether to send |
| Control | No automatic steering, interruption, continuation, tool blocking or next-turn context injection |

`nit`, `concern` and `blocker` describe findings. A blocker cannot halt work, gate a handoff or start another turn. The interface explains this rather than borrowing omp's control semantics.

Success means:

1. A real completed answer produces an actual reviewer request and an honest result or error.
2. Feedback is visible in a Desktop Advisor pane and retrievable through CLI commands, including when the main agent is idle.
3. Publication does not change the main agent's conversation or cause another main-model request.
4. Where the verified composer API is available, Add to draft preserves existing draft text and targets the intended session; otherwise Copy is the declared handoff. Only the user's normal Send action passes feedback to the main agent.
5. Failures, partial evidence, fallback attribution and unknown usage are visible.
6. A small, predefined evaluation measures whether the advice is useful before expanding the product.

This is a feedback inbox, not an exact replica of omp. A useful first release does not require native transcript cards, a tool-capable auxiliary loop or new host APIs.

## Interfaces and user flow — R09–R11

### Supported release target

Required: interactive CLI and native Desktop connected to the approved lab Hermes host. The plugin uses only captured evidence, so it does not claim to inspect any local/container/remote filesystem independently.

Remote Desktop connections, multiple simultaneous profiles and reconnect are supported only when their scoped backend/pane tests pass. A socket is not required: profile-aware polling is the baseline transport. Standalone TUI, messaging gateways, ACP and headless/one-shot execution are unclaimed for v0.1.

Do not advertise headless support while losing late reviews on process exit. When the plugin observes an unsupported surface, do not start automatic reviews; report that limitation through available status output. Records already completed remain available later.

### Desktop

Provide one **Advisor** pane using the native SDK and theme components. It shows the selected connection/profile/session, enabled state, pending/running review, answer reference, findings, coverage limits, errors, requested/served route and available usage.

Actions:

- Enable/disable for the selected session.
- Review now for an eligible captured answer.
- Configure the single reviewer and bounds.
- Select findings; Add to draft; Copy.
- Mark feedback Useful / Not useful / Not rated, with an optional short reason.

Display completed reviews while idle. No transcript footer, hidden assistant message, model-authored directive or toast substitutes for the pane's retained findings. A pane may display both a current result and older results, always with the reviewed answer identified.

### CLI

Register one `/advisor` command with these subcommands:

| Command | Behavior |
|---|---|
| `/advisor` | Help and status; never an ambiguous implicit toggle |
| `/advisor sessions` | List discovered sessions in the owning profile; metadata only while disabled |
| `/advisor on <session-id>` | Enable future completed-answer reviews for that session after configuration |
| `/advisor off <session-id>` | Cancel queued work and invalidate in-flight results for that session |
| `/advisor status <session-id>` | State, coverage, route, budget and failures |
| `/advisor notes <session-id>` | Render stored findings as escaped human-readable text |
| `/advisor review <session-id>` | When enabled and not paused, queue the latest eligible captured answer or explicitly retry its failed attempt |
| `/advisor reset-allowance <session-id> confirm` | Reset the invocation counter after explicit confirmation; leave the session paused until enabled again |
| `/advisor copy <session-id> <note-id>…` | Print a copyable feedback block; no clipboard assumption and no injection |
| `/advisor configure <session-id>` | Show saved/applied configuration and validation errors |
| `/advisor configure <session-id> set <key> <json-value>` | Validate the complete updated settings, save atomically and apply to the named session through the shared configuration service |

Explicit session IDs are the portable command contract. An installed public API may provide an exact current-session shortcut, but it must never use a global last-active-session fallback. Read-only command output must be verified not to become model-bound content automatically.

No raw transcript dump or autonomous model-visible tool for retrieving notes is included in v0.1. Users can copy findings; this plugin never silently supplies them to the main agent.

### Manual handoff

Serialize only the selected stored notes, their reviewed-answer reference, evidence citations and coverage warning into a feedback block. Identify it as reviewer suggestions for the main agent to evaluate, not as verified instructions or an authority override.

Desktop calls `host.composer.insertText` with the exact mounted session ID and append/block mode. Never use `setDraft` to overwrite the draft, `submit`, `inject_message`, a synthetic keyboard event or a `null` target that could follow focus elsewhere.

On success, record **added to draft**. On failure, retain the findings and offer Copy. A connection/profile switch invalidates the pending UI action. Copy and draft insertion do not mean sent, received, used or fixed. The plugin does not infer those states from later prose.

Sending is entirely the user's normal Hermes action. If Hermes then treats that user submission as steering during work, that is existing user-driven behavior, not automatic Advisor steering.

## Public preview scope (target versus preview)

The public source preview ships plugin source, the offline test suites that can run from a clean checkout, and documentation. It is untagged source, not a v0.1 release, and claims no stable, verified, supported or generally-available status. No paid native review is part of the preview.

### CLI: R10 target versus actual preview surface

The normative target remains the full explicit-session command surface in "CLI" above: `/advisor`, `sessions`, `on`, `off`, `status`, `notes`, `review`, `reset-allowance`, `copy` and `configure`, including `configure <session-id> set <key> <json-value>`.

The preview implementation exposes a narrower, differently spelled surface:

| Preview command | Actual behavior |
|---|---|
| `/advisor` or `/advisor help` | Help text and command summary; never an implicit toggle |
| `/advisor metadata` | Plugin metadata/status summary |
| `/advisor --session <session-id> status` | State, coverage, route, budget and failure summary |
| `/advisor --session <session-id> notes` | Stored findings as returned text |
| `/advisor --session <session-id> enable` / `disable` | Session enable/disable control |
| `/advisor --session <session-id> review_now` | Queue a manual review for the latest eligible packet |
| `/advisor --session <session-id> configure --json <json>` | Whole-configuration update with an explicit expected revision |
| `/advisor --session <session-id> rate --json <json>` | Usefulness rating control |
| `/advisor --session <session-id> reset_allowance --json <json>` | Confirmed allowance reset |
| `/advisor --session <session-id> feedback --json <json>` | Return a copyable feedback block; no insertion |

Not implemented in the preview: `/advisor sessions`, the target spellings `on`, `off`, `review`, `copy`, `reset-allowance`, and single-key `configure ... set`. This is known deviation **B1** in `KNOWN-ISSUES.md`. It is not a reduction of R10: the target contract above stands, and this table exists so no reader infers capabilities the preview does not have. Preview command output is returned for the user to copy; it is not injected into model context.

### Implementation amendments and known deviations

The preview carries a bounded implementation-fix scope: **B2** (usage-known handling), **H4** (close reports ownership release), **H5** (only storage failures latch `storage_error`), **M3** (guarded POSIX lock import, fail-closed elsewhere), **M5** (local fail-closed admission before content inspection), the **HTTP 422→400** control-contract fix and the **worker lifecycle** fix. Each fix ships with a regression test in the preview source; the run that proves them at the published revision is recorded in `EVIDENCE.md`, and source inspection alone does not prove a fix. **H1, H2, H3, M1, M2, M4** and the low-severity findings are deferred to the named post-preview campaign in `KNOWN-ISSUES.md`.

Deviations recorded without amending the target:

- **B1 — narrower CLI.** The preview command surface is the table above, not the full R10 surface. R10 remains normative.
- **Low9 — hook surface.** The preview registers a `pre_llm_call` hook. The normative capture contract in "Capture" (R03) permits observation/lifecycle hooks only and prohibits prompt transforms. Although the registered callback returns `None` and does not transform a prompt, registration itself deviates from the required hook surface; it stays a finding and is not permission to relax R03.
- **Low4 — profile identity helper.** Preview profile resolution uses the undocumented `profile_name_for_home` helper. R01 and G0 require existing supported public APIs; this remains an unresolved finding under D01, not an approved dependency.
- **Desktop Copy-only.** Copy is the exercised preview handoff. Add to draft remains the normative target and is unsupported until the public composer API is verified.

### Desktop: Copy-only experimental

The preview Desktop pane is **experimental and Copy-only**. Copy is the declared handoff and the only feedback action the preview exercises. Add to draft remains the normative target, conditional on a supported public `host.composer.insertText` exact-session API that preserves existing draft text. The preview does not claim draft insertion; a missing or unverified composer API is a documented unsupported state, never permission to use `setDraft`, `submit`, synthetic keyboard events or another session's target.

### Verification status of the preview

The current candidate has **0 of 35** acceptance rows proven natively. A03, A16 and A17 have no receipt at any revision. Historical receipts from earlier revisions are historical only. Unit, fixture, offline-suite and structural-smoke results are **not** native review or gate evidence. Public-preview checks P0.1–P0.8 are tracked in [ACCEPTANCE.md](ACCEPTANCE.md); until a receipt is recorded there and in `EVIDENCE.md`, they are not run, not pending-green and not passed.

## Review and evidence pipeline — R03–R06

### Discovery, enablement and capture

Discovery is metadata-only and independent of review enablement. A verified public session-open/current-session context may register a session; if unavailable, the first successful completion registers its profile/session ID, surface and timestamps only. Do not copy, sanitize, digest or retain request/answer/tool content while disabled or paused. Never use content-derived titles as discovery metadata. A first-time user may therefore need to complete one ordinary answer, then select its ID from `/advisor sessions`, configure and enable Advisor for subsequent answers. The UI explains this limitation; that discovery answer cannot later be reviewed from private history.

Session states are disabled, enabled or paused with a reason. Only enabled, non-paused sessions on supported surfaces may capture packets or invoke either automatic or manual reviews. `on` validates configuration, grants, ownership, healthy storage and remaining allowance, then enables future captures; it does not replay past answers. `off` disables, invalidates the session generation and clears pending/latest packets. A pause for quota/auth/model failure, exhausted allowance or storage failure stops new capture/admission and clears pending/latest packets. Existing in-flight work follows its own validity/deadline rules; a storage failure invalidates its publication. Manual Review now/retry while disabled or paused reports the reason and makes zero calls. Recovery requires an explicit `on` after the cause is resolved, then a new successful answer. Validation/network errors that do not trigger a pause may be explicitly retried while their cached packet is eligible.

The invocation that reaches the allowance remains eligible to publish; only subsequent admission is paused. Resetting allowance never itself enables capture or inference. New discovery metadata may still be recorded while disabled/paused if storage is healthy. Unverified identities are rejected, not registered from arbitrary caller input.

### Capture

Register observation/lifecycle hooks only. Do not register prompt transforms, `pre_verify`, blocking tool hooks or automatic context-returning hooks. An eligible successful main-turn completion in an enabled session queues a review; failed/interrupted turns do not automatically review.

For eligible enabled sessions, the callback takes the bounded snapshot and returns promptly; metadata-only discovery never copies transcript content. It never waits for inference. Ensure auxiliary calls and detached/forked reviews cannot recursively trigger Advisor reviews. Verify this at G0/G1 rather than assuming all session hooks mean the same thing on every version.

Resolve owning profile and session from supported execution context and hook payloads. Capture them immutably together with the host turn ID when available. If identity is missing or ambiguous, record `unsupported_scope` and make no request. Do not access a private agent object or scan another session's database to reconstruct missing history.

The packet contains the current user request, final answer and bounded tool evidence exposed by the hook. Treat all captured content as untrusted review material. Do not include hidden reasoning; exclude exposed reasoning fields as well in v0.1. Do not independently reread files named in tool results. Apply one plugin-owned outbound redaction pass to all packet fields and error text, excluding credential-shaped fields and known secret patterns before serialization. Test with synthetic secrets. Explain that pattern redaction is not a guarantee that arbitrary private text is safe to share with the configured host-managed providers.

Assign packet-local references such as request, answer and tool-result IDs. Store a full digest of the exact serialized sanitized packet; do not fingerprint only the first 4,000 characters or only the last transcript row. Changes to any included evidence change the packet identity.

### Bounds and coverage

The applied `packet_bytes` cap (default/max 32 KiB) covers serialized evidence and coverage metadata. Keep the user request and final answer first, then most recent relevant tool call/result pairs in chronological order. If an individual essential text must be shortened, retain labeled beginning/end excerpts. Never cut a pair into misleading evidence of a successful tool result.

Every clipped component has a coverage marker; omitted components may be represented by bounded grouped counts/ranges so coverage metadata itself fits the packet cap. A result cites only references actually supplied. The reviewer is told that absence from this packet is not evidence that an action never occurred. Feedback requiring unavailable files/tests must say insufficient evidence or recommend verification, not fabricate a finding.

No WATCHDOG imports, `.omp` configuration imports, file-grant system or autonomous investigation loop is included. This removes the earlier external-import and invalid-tool-grant defects from v0.1's reachable surface.

### Request and response

Use the public structured completion facade. Explicit reviewer provider/model selection requires its existing grants. No route configured means `no_model` and zero requests; there is no silent inheritance of the main model.

One plugin completion invocation produces at most four findings. Locally validate the entire result even if the host says it validated JSON. Required finding fields are severity, explanation, valid evidence references and suggested next step. Output also distinguishes findings, no findings and insufficient evidence. Reject unknown severities, invalid references, oversized text and malformed structures; do not repair them by inventing evidence or performing a hidden retry.

A successful structured response can still be a poor review. Schema validity and usefulness are separate measurements.

### Scheduling and identity

One active review per session, at most two active reviews per plugin process. Each session retains at most one pending packet. When a newer completion replaces the pending packet, record the skipped turn explicitly. The already running review may finish and is labeled with its original answer; it must not appear to cover the newest answer.

Key work by owning profile/session, captured turn identity, packet digest and the session-applied configuration revision/generation. Automatic replay of the same completion is deduplicated. Review now does not accidentally duplicate an already running/completed review of the same packet; an explicit retry after a failed or interrupted attempt creates a new attempt and consumes allowance.

Keep one additional latest eligible sanitized packet per session in memory for Review now/retry, bounded by its applied packet_bytes (at most 32 KiB), expiring 30 minutes after capture. Retain at most 16 such session snapshots per process, evicting the least recently captured first; this cache is separate from active/pending packets. Expose `review_now_available` and an expiry/unavailable reason. After expiry, eviction or restart, Review now/retry is unavailable until a new answer is captured. Never reconstruct the packet from private host databases or imply that persisted notes contain the full evidence. Do not extend expiry merely by viewing status.

Session IDs are the host's supported identities. A new ID after compaction/resume is treated as a new captured session unless the public API supplies a verified lineage. Do not invent logical-session continuity or claim account-wide limits. Show continuity limits in compatibility documentation.

## Model access, budgets and failure behavior — R02, R07

The user explicitly chose **Hermes-managed access**. The plugin accepts the host's existing credential handling, retries and provider/model fallback. It must not claim strict-route execution or block release waiting for a new strict-route host API.

Show requested provider/model and the served provider/model when the public result reports them. A difference is visible as a fallback/route change; missing attribution is unknown. Preserve picker aliases separately from normalized/served IDs. Do not send a picker-only alias literally by bypassing host resolution.

Configuration explains that Hermes may use another configured route under its existing fallback policy, with associated data-sharing and cost implications. The plugin does not alter that policy, create fallback chains, read credential files, extract tokens or call providers directly.

### Initial defaults

| Setting | Default and meaning |
|---|---|
| Enabled | Off; explicit configuration and session enablement required |
| Reviewer | One explicitly selected provider/model; none by default |
| Cadence | Each successful completed answer after enablement |
| Packet cap | 32 KiB UTF-8 serialized evidence/coverage |
| Output limit | 2,048 tokens requested through the public API |
| Findings | At most four |
| Concurrency | One active review/session, two/process |
| Pending | Latest one per session, skipped turns recorded |
| Local deadline | 120 seconds including the plugin queue wait |
| Plugin retries | None; explicit user retry only |
| Session allowance | 20 public review invocations, then pause |
| UI refresh | Poll visible selected-session pane every three seconds; suspend polling when unmounted |
| Stored history | Last 100 settled terminal attempts per session, of every terminal status; counters survive pruning |

The allowance counts public-facade invocations, not invisible provider attempts. Host retries/fallbacks may create more than one charge per invocation. Do not label it a hard monetary limit. Any live test needs an explicit authorized budget; if a strict spending ceiling is required, use an operator-established provider cap or stop rather than promise enforcement the API cannot supply.

At the deadline, attempt cancellation and mark the result timed out. Suppress late publication from that attempt. Do not release a concurrency slot and launch another call while the previous transport remains unresolved; show `cancellation_pending` if necessary. The main agent must remain usable even when the review worker cannot confirm cancellation. Do not claim that local cancellation prevented billing.

| Outcome | Handling |
|---|---|
| Valid review | Persist findings/no-findings/insufficient-evidence result with exact coverage |
| Missing model or grants | Disabled/error state; zero requests |
| Host fallback | Accept under chosen policy; display actual attribution when available |
| Quota/auth/model failure | Pause affected session's reviewer, retain records; no plugin retry loop |
| Network/5xx/timeout after host recovery | Error or timeout with unknown remote outcome; no invented successful review |
| Invalid structured response | Validation error, no accepted findings; user may explicitly retry |
| Disable/unload/session-applied generation change | Cancel that session’s queued work and invalidate its in-flight publication; other sessions and counters remain |
| Main user Stop | Never resume main work; no automatic review for its interrupted answer; existing review may be cancelled through lifecycle support without claiming remote cancellation |
| Process exit/restart | Recover only proven-dead owners’ sessions/attempts under the ownership contract below; no automatic paid replay |

Use public usage fields as reported, but do not equate missing data normalized to zero with proven zero usage. The inspected facade can zero-fill fields. Show unavailable/uncertain totals where it cannot distinguish absence. Price is unknown unless trustworthy pricing/charge information is actually available; never display an invented `$0`.

## Persistence, configuration and lifecycle — R08, R12

Use a plugin-owned SQLite store in the active profile's plugin-data area, outside the project and Hermes's internal session database. G0 must establish the supported profile-bound directory resolution. If profile scope cannot be proved, fail closed rather than writing to a guessed global home.

Persist review metadata, compact notes, coverage markers, available usage, errors, attempt IDs, session-applied configuration revision/generation, owner token, allowance counters, draft-insertion markers and usefulness ratings. Do not persist raw packets, raw provider replies, hidden reasoning or credentials by default. Packet data exists only for active/pending work and the bounded latest-snapshot cache defined above; release it when those uses end.

One transaction records terminal review state and notes; stable note IDs deduplicate display. UI polling reads the same canonical store, so reconnect does not create another review or another note. Store migrations are versioned and tested; back up before a destructive schema migration. Never prune an active attempt or erase allowance counters to make room.

The backend derives profile scope from the authenticated host connection. Caller-supplied session IDs must belong to that profile's verified discovered sessions. Do not accept filesystem paths, arbitrary profile names or another connection's identifiers as storage selectors. Escape terminal controls and use safe Markdown rendering for all model/tool-derived text.

Reviewer settings are profile-scoped; enablement and invocation allowance are session-scoped. Distinguish `saved_profile_revision` (optimistic concurrency for saving) from each session's `applied_revision` and monotonically increasing `applied_generation` (validity of work). Save & apply atomically checks the expected saved profile revision, saves a complete valid configuration, installs that snapshot for the named session and increments only its applied generation. Clear that session's pending/latest packets and invalidate its old in-flight publication. Saving does not enable a disabled/paused session. Other sessions retain their applied snapshot and generation until explicitly reapplied; their results remain publishable even if the saved profile revision advances. Future sessions read saved settings. Concurrent edits cause a visible conflict, not a silent overwrite. Cancel writes nothing and rebuilds nothing. Before publication, compare with the owning session's applied generation and owner token, never the latest saved profile revision. Delayed draft actions recheck the target session/connection and the generation captured when the user initiated that action; historical notes remain selectable after configuration changes.

Use host-supported lifecycle cleanup/supervision where available. In CLI, use an explicitly managed bounded worker if there is no running async loop; capture Python execution context at the hook boundary so host-managed routing keeps the correct context. This must not inspect credential values or private host fields. Tests verify that context from one session cannot influence another's request.

No local operator UI setting grants itself host LLM permissions. Capability consent remains the host's process. Enabling the Desktop renderer alone does not imply the Python backend is enabled; status must diagnose this distinction.

## Additional contracts that remove implementation ambiguity

These are the engineering contracts for implementation. DECISIONS.md records their rationale. Choose internal classes and SQL layout within these contracts; do not reopen the specified defaults during routine implementation.

### Structured response — R05

The reviewer returns one JSON object with exactly `outcome` and `findings`. `outcome` is `findings`, `no_findings`, or `insufficient_evidence`. `findings` is an array of zero to four objects, each with exactly:

- `severity`: `nit`, `concern`, or `blocker`.
- `explanation`: nonempty text, at most 2,000 Unicode code points.
- `evidence_refs`: one to eight distinct IDs present in the supplied packet.
- `next_step`: nonempty text, at most 1,000 Unicode code points.

`findings` outcome requires at least one finding. The other outcomes require an empty findings array. Reject unknown keys, wrong types, empty/whitespace-only required text, invalid references and over-limit output as one validation error; never accept a valid-looking subset. Findings can coexist with incomplete coverage, but coverage must remain visible. Coverage is computed by the plugin, not taken from the model. A no-findings result means only that this review returned no findings; it is not a correctness certificate. Errors are attempt states, never successful `outcome` values.

### Worker ownership and recovery — R06–R08, R12

Exactly one live worker owner may control a given profile/session at a time. Other processes may own different sessions and read the same profile store. Each worker incarnation has a unique owner token; attempts and session ownership record it. Acquire exclusive session ownership before capture/admission or changes to its running state. Use a plugin-owned, OS-released local lock plus durable ownership metadata, or an equivalent mechanism demonstrated at G0. The SQLite store/locks are local to one host; sharing this store through a network filesystem across hosts is unsupported.

Starting process B never resets process A's enabled sessions, counters or live attempts. Only after proving the former owner has terminated and acquiring its exclusive ownership may recovery disable those recovered sessions, clear their volatile-cache availability and mark that owner's unfinished attempts abandoned. A PID alone or heartbeat timeout is not proof: PID reuse, a slow live worker and unresolved cancellation must not permit takeover. Ambiguous ownership yields `owner_unavailable`, zero new requests and no changes to the potentially live owner's records. Graceful ownership release requires all local transports to have terminated; local process death still does not prove that a provider stopped billing.

CLI/Desktop controls must reach the existing owner through a verified supported path, or return an explicit unavailable/conflict response without changing that session. They must not start a second worker to service Review now or read another process's memory cache. G0 must prove how the observed hook worker, command/backend controls and memory-only snapshot cache connect on the target host. If this required control path cannot be supported with the existing APIs, stop for compatibility review; do not add host patches or silently omit actions.

### Storage failure contract — R07–R08, R12

Database locks, disk-full, permission errors, corruption and failed transactions are visible `storage_error` states. Storage waits occur outside the main-agent hook and have a maximum one-second lock wait per operation; no unbounded retry loop. A failed ownership check or invocation-counter reservation means zero LLM calls. Configuration, enablement, reset and rating mutations are acknowledged as saved only after a successful transaction; rollback leaves the prior durable state intact.

If final-result persistence fails after inference, do not publish accepted findings or report completion. Show a volatile error stating that the outcome was not saved, retain the previously reserved allowance, discard the unsaved response, and pause affected owned sessions without initiating another review. Existing durable records remain intact; no raw reply is written to fallback files. Subsequent recovery reconciles that attempt as failed if its outcome is known locally, or abandoned if its owner died; it never automatically replays inference. If storage cannot record the error/pause, a local fail-closed latch blocks further capture/admission until storage is healthy and the user explicitly re-enables. The main agent stays usable. Startup cannot bypass durable reservation by using in-memory counters.

`off` always stops the receiving owner's local capture/admission even if persisting the disabled state fails; report that durable state was not saved. A non-owning caller that cannot reach the owner must not falsely report that it stopped it. If Copy or acknowledged draft insertion succeeds but its action-log write fails, report the actual action and the logging failure separately; never repeat the external action automatically. Read-only existing notes are shown only if successfully read; failed reads are errors, not empty histories. No destructive database repair or reset is automatic.

### Logical records and state — R06–R08, R12

Use these logical entities; exact SQL and indexes are implementation choices subject to tests:

| Entity | Required content and invariants |
|---|---|
| Configuration | Profile scope, monotonically increasing revision, validated non-secret route/settings; session-applied revision retained |
| Session | Verified profile/session key, owner token, disabled/enabled/paused state, invocation counter, applied settings/revision/generation and pause reason |
| Review attempt | Stable unique ID, captured answer/turn reference, packet digest, session-applied settings revision/generation, owner token, timestamps, coverage, status, route/usage availability, sanitized error |
| Finding | Stable note ID, attempt ID, validated severity/text/references/next step; references identify captured evidence, not a claim that raw evidence is stored |
| Feedback action | Selected note IDs, target session, Copy or acknowledged draft append, timestamp; no sent/received/used claim |
| Rating | Attempt ID, Useful/Not useful/Not rated and optional bounded reason |

Attempt states are queued, running, completed, failed, timed_out, cancelled, skipped or abandoned. A timed-out transport may additionally be `cancellation_pending`; it retains its active slot until termination is confirmed. `completed` requires a valid structured response. Disable or configuration replacement for that session invalidates publication from its earlier applied generations even if a transport later succeeds. Retained prior completed reviews remain visible with their original configuration/answer references.

Use the session-applied generation and owner-token checks immediately before accepting output; recheck the user-action target/generation before delayed UI insertion. Session ownership and configuration revision must not be inferred from current focus. Admission reserves the invocation allowance atomically just before a public LLM call; even a call that throws consumes one invocation. Queued work cancelled, expired or skipped before a call consumes none. Counters are durable and cannot reset through history pruning, disable/enable or restart. Multiple plugin processes sharing one profile must coordinate the counter in SQLite; the concurrency limit remains per process and is not advertised as account-wide.

On recovery of a proven-dead owner, saved reviewer settings and counters survive; only its recovered sessions start disabled and only its unfinished attempts become abandoned. Other live owners are untouched. Restart never initiates inference. Disable immediately removes queued/latest-cache packets for that session; active packet ownership ends when its worker terminates. A Stop on the main agent must never be counteracted by Advisor. Operator allowance reset is an explicit, separately confirmed session action in Desktop and `/advisor reset-allowance <session-id> confirm` in CLI. A successful reset atomically sets the counter to zero and the session to paused, then clears pending/latest packets; an already admitted invocation may finish. Reset never enables or resumes reviews; explicit on and a new answer are required.

For automatic reviews, the applied `deadline_seconds` deadline (default 120 seconds) starts when the completion is captured. For every manual Review now or retry, it starts at the user request. Both include queue wait. Expired queued work never starts a call. Cache expiry is measured from original capture and cannot be extended by retry. Retention has one rule: retain the latest 100 settled terminal attempts per session across completed, failed, timed_out, cancelled, skipped and abandoned states, ordered by terminal timestamp then stable attempt ID. Queued/running attempts and any unresolved transport (including cancellation_pending) are protected and do not count until settled. Prune older settled attempts and their findings, ratings and action records atomically; preserve session configuration, ownership, allowance counters and the minimum replay-deduplication metadata independently. Pruning must never make an old completion eligible for another automatic paid review. Linked records disappear together and the UI reports when a previously selected note was pruned rather than handing off stale data. Store compact evidence reference labels and coverage, not tool-output excerpts disguised as metadata. Findings may quote source text, so persisted notes are still potentially sensitive data; sanitize and protect them accordingly.

### Configuration envelope — R07, R12

Initial engineering limits are defaults, not promises of model success or maximum provider cost. These are the complete accepted keys for Desktop and CLI configuration. Numeric values must be JSON integers (not booleans, strings or fractional numbers); reject unknown keys, coercion and out-of-range values atomically.

| Key | Type and allowed values | Default |
|---|---|---|
| `route` | Object with exactly `provider` and `model`, each a nonempty trimmed string of 1–256 code points; resolved/validated by the public host interface; credentials and endpoint overrides disallowed | Unconfigured until explicitly selected |
| `packet_bytes` | Integer 1,024–32,768; serialized UTF-8 packet including coverage | 32,768 |
| `output_tokens` | Integer 128–2,048; requested host output bound, not guaranteed charge | 2,048 |
| `max_findings` | Integer 1–4; local schema and prompt both enforce the applied value | 4 |
| `deadline_seconds` | Integer 1–120; includes queue wait | 120 |
| `session_allowance` | Integer 1–20; cumulative public invocations since explicit reset | 20 |

Set `route` as one object so provider/model selection is atomic. The unconfigured state may exist after installation, but Save & apply requires a complete valid configuration with route. CLI's single-key update is applied to the full current saved draft and uses its read revision for compare-and-save. Host grant denial or unsupported route is a visible error, never implicit route inheritance. Unknown served attribution after a valid host-managed invocation remains allowed.

Process concurrency (two), session concurrency (one), pending capacity (one/session), latest-cache capacity (16/process), cache lifetime (30 minutes), result retention (100 settled terminal attempts/session), polling (three seconds while visible), and response text/reference bounds are fixed, non-configurable v0.1 constants. Thus sessions cannot disagree about process-wide concurrency. New controls or changed ranges/constants require a specification amendment.

All admission, truncation, prompt/schema validation and deadline checks use the session's applied values. Lowering allowance below/equal to its already consumed count immediately pauses further admission; it does not reset usage. Applying changes invalidates pending/cache packets, so a lowered packet cap cannot reuse a larger old packet. Tests exercise both defaults and non-default boundaries. Raising an allowed setting within its specified range still requires explicit Save & apply but no separate specification amendment.

### Privacy and UI boundary — R04, R08–R11

No original vault document, arbitrary project file, credential value or raw packet is added to normal logs or telemetry. The implementation does not add a third-party telemetry service. Use a protected profile directory and inherited user-only permissions where the host supports them; G0 must record the actual protection available. Notes may contain sensitive quotations even after redaction. Show this alongside the host-managed fallback disclosure before first enablement. Copy is a user action exposing selected text; it must not automatically read or overwrite clipboard contents in CLI.

Usefulness ratings stay local. Packet references are stable within the reviewed packet; the UI must not invent clickable file locations or expose a raw-evidence viewer when the payload is no longer retained. Validation-error logging records safe categories, not the full rejected model reply.

## Deferred features and change control — R15

Deferred: during-work reviews, autonomous investigation, multiple reviewers, project guidance imports, raw transcript dumps, strict provider routing, native transcript insertion, automatic steering/interruption/continuation and exact account-wide cost enforcement.

Future automatic delivery requires a separate approved design and proof that existing public APIs provide adequate scope, attribution, Stop precedence and acknowledgment. **No Hermes modifications remains in force.** An upstream feature becoming available does not automatically authorize enabling it.

Keep the plugin useful on its advertised supported versions; capability checks should explain incompatible versions rather than crash or pretend a feature ran. Do not infer all future Hermes releases are compatible from a broad minimum-version declaration.

This baseline authorizes implementation within its stated scope. After that, proceed through G0–G4 as their technical exit criteria pass; do not stop for repeated design or phase approval. Resolve routine engineering choices within the contract and document them. Ask only for an actual missing access/cost input or a necessary departure from scope, constraints or the authorized environment. A failing mandatory capability stops the dependent step and is reported with evidence; it does not authorize a host patch. The implementation agent must not edit expected outcomes merely to make a failing implementation pass.

## Public preview handoff

This public set is the read-only normative documentation shipped with the preview. Implementation work happens in the plugin repository; verification status changes are recorded in [ACCEPTANCE.md](ACCEPTANCE.md) with their evidence class. Do not edit expected outcomes, acceptance rows or LOCKED/BASELINE decisions to make a preview build appear to pass. Publication, tagging, production enablement and any paid native review need separate explicit authorization; the preview ships untagged source and performs no paid inference.

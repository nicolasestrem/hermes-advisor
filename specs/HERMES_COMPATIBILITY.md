# Hermes Advisor — compatibility and evidence register

Version: 0.1-spec.1-public-preview · Prepared: 2026-10-10 · Public derivative: 2026-10-20 · Status: **normative compatibility target; no released host version is certified supported**.

This file owns the capability matrix and supported-version claims for the v0.1 target. [SPEC.md](SPEC.md) owns required behavior; [ACCEPTANCE.md](ACCEPTANCE.md) owns gate execution and current verification state. The public preview does not change any capability requirement: where a capability is unverified or absent in the preview, it remains required for the target and is listed as a known limitation.

## Evidence policy

This document owns the capability matrix and support claims. [SPEC.md](SPEC.md) owns required behavior; [ACCEPTANCE.md](ACCEPTANCE.md) owns gate execution. The baseline below was researched during the preceding planning work. It has not been revalidated against an installed host while creating this set. A source-supported API is not a passed integration test.

Evidence classes: SOURCE = observed in pinned source/docs; RUNTIME = reproduced on an identified installed version with raw evidence; HISTORICAL = produced on an earlier revision/host before the public export, excluded from the public tree and not transferable; UNKNOWN = unavailable or not yet checked. Only RUNTIME evidence at the current revision could support a support claim. **G0 has not passed and no released Hermes/Desktop version is certified supported.**

## Research baseline

Source inspected during planning:

- Hermes: `46d7718a52ff33accb15dc0501736fbdb6833cab`.
- oh-my-Hermes: `rlaope/oh-my-hermes@7cd0d0232661ecb7f317f556d619ac44b6db9c83`.
- Original omp behavior reference: `can1357/oh-my-pi@703a261df9533e74f16aa8d38e64eb41977ccf89`.

The Hermes catalog identifies `rlaope/oh-my-hermes` as its `omh` plugin. Other repositories share the name; their capabilities must not be attributed to this one.

OMH's **Advisor-As-Reviewer** document describes an asynchronous reviewer lane with visible evidence and recommendations. It explicitly rejects prompt editing, run cancellation and workspace writes. Its blocker can hold an OMH handoff gate; this plan intentionally gives blockers no control effect.

OMH also defines an **ask** workflow for configured external critique: prepare the question/context, capture the response boundary, evaluate advice and keep implementation separate. Its Jev support answers typed questions/probabilities and is not a substitute for a general generative reviewer. The researched documentation is a design reference, not proof of a complete shipped background Advisor UI. Do not install OMH as a dependency or run its installers to implement this plan.

Verified Hermes foundations:

| Existing surface | Intended use | Limit |
|---|---|---|
| `post_llm_call` plus `on_session_end` observers | Capture bounded evidence, then classify the matching completed turn | On installed `dce1e9b`, `post_llm_call` alone can fire for failed answers; require matching successful end flags before review admission |
| `ctx.llm.acomplete_structured` | One bounded structured review request through the public facade | No native tools; host fallback/retries remain host-owned |
| Plugin commands | CLI controls, status and finding retrieval | Handler scope must be explicit if the public context cannot resolve it |
| Plugin backend namespace | Profile/session-scoped note/configuration endpoints | Backend must be enabled separately from the Desktop renderer |
| Desktop panes | Independent feedback inbox while main agent works or is idle | Not independent transcript insertion |
| `host.composer.insertText(sessionId, …)` | Append selected findings to a draft | False/unavailable means no insertion; never fall back to another session |
| Plugin unload/supervised-task facilities | Cleanup where the owning runtime supports them | CLI may require an explicitly managed background worker |

The installed remote Hermes runtime and Desktop build must be checked at Gate G0. Earlier documents report another source revision and runtime; those reports do not establish the current binary or the availability of every API above.

## G0 capability matrix

The table records the required checks, not passing predictions. Runtime progress is tracked in the execution observations below; unexecuted cells remain **NOT RUN**. Final G0 certification requires exact public signature/payload, reproducible command/test, exit code and evidence location for every mandatory capability.

| Capability | Baseline | Runtime observation required | Allowed fallback / stop condition |
|---|---|---|---|
| Successful-turn hook | SOURCE: `post_llm_call` | Main/auxiliary/fork classification, failure/Stop behavior, immutable final request/answer/tool snapshot | Stop if reliable completed-main-answer observation is unavailable |
| Identity | Partial source foundation | Exact profile/session and optional turn identity on CLI/Desktop; compaction/resume behavior | Explicit session IDs for commands; missing/ambiguous scope means no request; no global last-session fallback |
| Public structured LLM | SOURCE: `ctx.llm.acomplete_structured` | Grant request/denial, real structured shape, route normalization, error behavior, copied execution context | Stop without public LLM access; no provider bypass |
| Route and usage | SOURCE: fallback and zero-filled usage possible | Reported requested/served identifiers and presence information | Accept host fallback; unknown attribution/usage visibly unknown |
| Profile-bound storage | UNKNOWN exact resolver on target | Public data-directory resolution, permissions, authenticated profile association | Stop if ownership/isolation cannot be proven; no guessed global directory |
| Lifecycle/worker | SOURCE: unload/task helpers | Runtime async loop, bounded background execution, main responsiveness, teardown and unresolved transport | Plugin-owned bounded CLI worker if needed; no private host handles |
| Session ownership/control | UNKNOWN on target | Exclusive owner proof, safe recovery, hook worker/backend/CLI control path and owner-local memory cache access | Stop required actions if the owner cannot be reached safely; no second worker or host patch |
| Discovery while disabled | UNKNOWN on target | Public identity metadata without copying transcript content; first-completion discovery fallback | First answer may register metadata only; missing trustworthy identity stops enablement |
| CLI commands | SOURCE: command rendering | Registration, explicit session validation, safe printing and zero automatic model-history insertion | Explicit IDs; stop CLI release if output cannot be kept out of model context |
| Desktop backend | SOURCE: plugin backend namespace | Clean loading/enabling separately from renderer; host-authenticated profile scope | Diagnose disabled backend; no ad-hoc unauthenticated server |
| Native pane | SOURCE: Desktop SDK | Actual pane with correct selected connection/profile/session and idle visibility | Native pane required for Desktop target; transcript/toast workaround disallowed |
| Polling | SOURCE-supported transport design | Profile-scoped reads every 3s only while mounted/visible; reconnect without duplicate work | Polling baseline; sockets optional and unnecessary |
| Composer | SOURCE: exact-session `insertText` | Supported argument shape, append/block behavior, acknowledgment, unmounted/connection-change failures | Copy only if unavailable; never submit/setDraft/null target |
| Packaging | SOURCE: plugin package mechanisms | Backend plus ESM asset discovery in clean directory/git installation | Wheel support unclaimed unless separately tested |
| Unsupported surfaces | UNKNOWN runtime identification | Detect unclaimed TUI/gateway/ACP/headless before automatic inference | No auto-review on unidentified/unsupported execution surface |

Tool evidence can be incomplete if the hook exposes only part of it: preserve coverage warnings. Missing user request/final answer or untrustworthy main-turn identity is not merely missing tool evidence and cannot be papered over with this fallback.

## Runtime baseline to record at G0

Record repository revision/version and dirty state of the lab Hermes host; actual runtime/interpreter and dependencies; local lock/SQLite guarantees, worker-owner/control topology and storage failure behavior; Desktop build/SDK version; connection mode; plugin loader/package shape; approved lab profile and storage-scope evidence; applicable plugin grants; source of route/usage attribution; and exact verified API signatures. Use safe identifiers, never secret values. Installation commands must come from the target's supported interfaces rather than a guessed version-independent command.

Only advertise combinations actually exercised. The initial intended target is interactive CLI plus native Desktop against the approved lab host. Remote connection, multiple profiles and reconnect claims depend on their respective tests. Do not convert a source revision into a broad minimum-version compatibility promise. After host upgrades, re-run affected G0/G3 checks before declaring support; no automatic host upgrades are part of this project.

A source-supported but runtime-failing API is an incompatibility, not permission to patch Hermes. Record the failed observation and either select an explicit fallback in this matrix or stop that required capability's implementation/release path for user review.

## Execution observations (historical; G0 not passed)

Everything in this section is **historical**: it was produced on earlier host revisions and disposable profiles during implementation, is excluded from the public export, and is not evidence for the preview or the current candidate. No local paths, private profile names, procurement notes or private archive references are reproduced here. Sanitized decisive outputs for the preview itself, once a parent receipt exists, belong in `EVIDENCE.md`.

- **Host pin, 2026-10-12.** Offline-suite receipts had recorded host revision `66605471e9f0b0832bbefaf625ce08e948ca540` (the live host HEAD on 2026-10-11, v0.21.6+529). That object no longer existed in the host checkout after the 2026-10-12 update; the runner default was re-qualified to `590af66a58c2b3eb3c5edd1dcfefa09c625ae18a` (v0.21.7) after confirming it still exported all five `plugin.yaml` hooks and both `llm.*_override` capabilities. No affected G0/G3 check was re-run against `590af66a…`, so those observations support neither revision.
- **Earlier installed host and Desktop.** A disposable lab profile on Hermes `v0.21.6+387.gdce1e9b` (full revision `dce1e9b37581dd62e480a9064dc04a709c2940d3`, runtime Python 3.14.7) exercised public plugin discovery, Doctor, storage-path resolution, grant denial, owner-local bounded worker control and metadata-only discovery. Flock live-owner/death and successful/failure/interrupted event classifications were **controlled fixtures**, not a real successful model turn. A native Desktop build from an independent clean clone at that revision (`npm ci`, `npm run pack`, packaged Electron 40.10.2) rendered a window and native bridge without page errors, but only on the isolated offline setup screen; pane Save/Enable clicks and the composer draft flow were **not run**. A single native automatic review later completed end-to-end on candidate `7959ee2e…`, which differs from the later tree in six files, so that proof does not transfer.
- **Current host, 2026-10-20.** The active host is Hermes `v0.22.2+2606.gbd3cbe9`. A disposable-profile structural smoke ran against the preview content: `plugins doctor --ci` (manifest parsing, import and registration passed; 0 tools, 5 hooks), install of the exact candidate commit with `--no-enable`, `plugins show` (present, not enabled) and `plugins capabilities` (both capabilities not granted); the profile was then deleted. The sanitized receipt is in [EVIDENCE.md](../EVIDENCE.md) and tracked as P0.5 in [ACCEPTANCE.md](ACCEPTANCE.md). This proves structural compatibility only. No native review ran on this host, and none is authorized for the preview.
- **Overall G0: NOT PASSED.** Working route access, historical build smoke and fixture behavior do not waive pending real hook/facade and native backend/owner-isolation checks. No released Hermes/Desktop version is certified supported, and this preview makes no supported-version claim. The preview is untagged source and may be structurally checked only on the disposable-profile path above.

## Sources and provenance

Source links support research claims, not proof of installation or live execution:

- [OMH Advisor-As-Reviewer contract](https://github.com/rlaope/oh-my-hermes/blob/7cd0d0232661ecb7f317f556d619ac44b6db9c83/docs/ORCHESTRATION_PATTERNS.md#L96-L127).
- [OMH external-advice workflow](https://github.com/rlaope/oh-my-hermes/blob/7cd0d0232661ecb7f317f556d619ac44b6db9c83/docs/WORKFLOWS.md#L5425-L5490).
- [OMH Jev declarations and limitations](https://github.com/rlaope/oh-my-hermes/blob/7cd0d0232661ecb7f317f556d619ac44b6db9c83/src/plugin_bundle/omh/jev_sidekick.py).
- [Hermes plugin authoring](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) and [public plugin LLM access](https://hermes-agent.nousresearch.com/docs/developer-guide/plugin-llm-access).
- [Public async structured completion](https://github.com/NousResearch/hermes-agent/blob/46d7718a52ff33accb15dc0501736fbdb6833cab/agent/plugin_llm.py#L475-L497) and [fallback ladder](https://github.com/NousResearch/hermes-agent/blob/46d7718a52ff33accb15dc0501736fbdb6833cab/agent/auxiliary_client.py#L7858-L7948).
- [Desktop SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk) and [exact-session composer draft API](https://github.com/NousResearch/hermes-agent/blob/46d7718a52ff33accb15dc0501736fbdb6833cab/website/docs/developer-guide/desktop-plugin-sdk.md#L481-L546).
- [CLI plugin command rendering](https://github.com/NousResearch/hermes-agent/blob/46d7718a52ff33accb15dc0501736fbdb6833cab/cli.py#L1276-L1287).
- [Plugin lifecycle helpers](https://github.com/NousResearch/hermes-agent/blob/46d7718a52ff33accb15dc0501736fbdb6833cab/hermes_cli/plugins.py#L409-L428).

Preserve full applicable copyright/permission notices if any upstream code or prompts are copied. Reference-inspired behavior alone does not justify copying the earlier unverified implementation sketches.

The pinned sources above contain the research needed for implementation; earlier plans are not required. OMH is inspiration, not a runtime dependency or proof that its documented advisor lane is shipped end to end. Original omp is a behavior reference, not an inherited feature checklist. Preserve applicable upstream notices if code or prompts are actually copied.

# Hermes Advisor — acceptance and verification

Version: 0.1-spec.1-public-preview · Prepared: 2026-10-10 · Public derivative: 2026-10-20 · Status: **normative acceptance target; current-candidate native acceptance is 0/35**.

This file owns the G0–G4 procedures, the A01–A35 functional contracts and the reported verification state. It distinguishes three things that must never be conflated: the normative target, the historical evidence produced before the public export, and the actual state of the current candidate. At the current candidate **0 of 35** acceptance rows are proven natively; A03, A16 and A17 have no receipt at any revision. No G0–G4 gate is passed at the current candidate, and the public preview does not close them. Publication of this derivative does not amend any acceptance contract.

## Verification rules

[SPEC.md](SPEC.md) defines expected behavior. [HERMES_COMPATIBILITY.md](HERMES_COMPATIBILITY.md) records what the host can support. This file maps requirements to evidence and specifies the gates. Source review and document validation performed while drafting are not runtime gate execution.

For every executed gate/test record: requirement and test IDs; spec revision; plugin commit and dirty diff identifier; host/Desktop versions; approved lab profile identifier; exact command or reproducible UI steps; exit code; raw stdout/stderr; actual observations; evidence path; reviewer; and PASS/FAIL/NOT RUN/UNSUPPORTED with reason. Store sanitized evidence in the future plugin repository under `verification/<run-id>/`; never persist raw packets or credentials merely to prove a test.

UNSUPPORTED is acceptable only for an explicit fallback (for example draft insertion when Copy passes). It is not a way to pass a missing mandatory hook, public LLM API, profile isolation, native pane or required CLI. A gate cannot pass on an agent summary or screenshot alone. Inspect underlying results and required side-effect counts. Synthetic fixtures/fake transports are permitted for deterministic fault injection only; label them and never present them as live host/provider evidence. They do not replace G1's real review or G2's actual UI checks.

## Requirements traceability

| Requirement | Acceptance coverage | Gate |
|---|---|---|
| R01 plugin-only boundary | A23, A22; G0 API inventory and diff review | G0, G3 |
| R02 reviewer/routing | A01, A05, A06, A12 | G0, G1, G3 |
| R03 completed-answer trigger | A02–A04, A10, A13 | G0, G1, G3 |
| R04 captured evidence | A08, A09, A24, A27, A32 | G1, G3 |
| R05 validated result | A07, A08, A26 | G1, G3 |
| R06 scheduling/cache | A02, A09–A11, A14, A15, A24, A30, A32 | G1, G3 |
| R07 limits/failure | A06, A11–A14, A20, A25, A33, A34 | G1, G3 |
| R08 storage/isolation | A14, A15, A20, A27, A28, A30, A33, A35 | G0, G3 |
| R09 Desktop inbox | A04, A15–A17, A21, A22, A29 | G0, G2, G3 |
| R10 CLI | A01, A18–A21, A25, A29 | G0, G2, G3 |
| R11 manual handoff | A04, A15–A18, A29 | G2, G3 |
| R12 configuration/lifecycle | A13–A15, A19, A25, A28, A30–A34 | G1, G3 |
| R13 install/docs | A22, A23, A28; notices/install/uninstall review | G3 |
| R14 usefulness | A26, A29; frozen ten-case evaluation | G4 |
| R15 approval/change control | Gate authorization record, A23, spec/diff review | Every gate |

## Execution stages

**Execute G0 → G1 → G2 → G3 → G4 when implementation is authorized within the stated environment and spending scope.** That authorization covers this implementation plan; it is not a release approval. Gates test technical results; passing a gate advances to the next without another approval request. Resolve startup inputs as specified in DECISIONS.md. Ask only when a required input is genuinely unavailable or a scope/cost/access change is necessary.

Work only in the plugin repository, on an approved feature branch. Inspect existing repository instructions, files and dirty state first. Do not modify a Hermes checkout or production profile. Preserve existing `AGENTS.md`; maintain README, testing/compatibility docs and CHANGELOG. Review and verify before each commit/push. PRs stay open and reviewers are tagged before any separately approved merge. Local plugin directory/repository setup is part of the implementation kickoff. Publication, tagging, PyPI/catalog submission and production enablement are outside this implementation baseline and require explicit instruction.

Each gate records exact revision/runtime, command, exit code, raw stdout/stderr, observed result and limitations. Expected outputs are not evidence. Missing mandatory dependencies or silently skipped integration tests fail the gate. Synthetic failures must be labeled; they do not establish real provider behavior.

### G0 — Existing-API feasibility, no host workarounds

Inspect actual Hermes/Python/Desktop versions and public API behavior. Record supported versions in `specs/HERMES_COMPATIBILITY.md`; any user-facing compatibility summary must link to that canonical register. Recheck hook payloads, completion identity, auxiliary recursion behavior, model grants, structured result shape, worker cleanup, CLI command output and supported plugin-data scope.

Verify worker ownership, owner-scoped recovery, metadata-only session discovery, the hook-to-control/cache path and Desktop backend mounting, exact-session pane selection, profile-aware polling and composer insertion acknowledgment. Source inspection is not a UI test. Do not invent a host API or reuse the previous plan's proposed handles.

Permitted capability fallbacks:

- No scoped command shortcut: require explicit session ID.
- No composer insertion: Copy only, visibly documented.
- No socket: polling, which is already the baseline.
- Missing captured tool evidence: review the available packet with incomplete-coverage labeling.
- Missing reliable post-completion observation, public LLM access or profile isolation: stop and report unsupported; do not silently replace automatic review with another product.

**Exit:** a verified capability matrix and the resolved isolated plugin-only environment. No host modification is part of this gate or any later gate.

### G1 — One reviewer and actual non-interference

Implement capture, bounds, scoped storage, local validation, worker lifecycle and public completion invocation before expanding the UI. Establish process/session limits and no-model behavior before any live request.

Run deterministic unit/host-contract tests first. In the approved lab, generate a real main answer and review it through Hermes-managed access. Capture sanitized route attribution, packet coverage, returned findings or honest failure and available usage.

Measure the main-agent transcript and main-model request count before/after review publication. They must not change because the reviewer completed. Reviewer API calls themselves are expected; do not confuse them with new main-agent turns.

**Exit:** real review output stored and retrievable; no recursive review, automatic model-context insertion or autonomous main turn.

### G2 — Desktop inbox, CLI retrieval and manual handoff

Implement the native pane, supported plugin backend, polling, exact-session selection and listed CLI commands. Test real installation paths for both plugin halves. Display findings/errors after the main answer while idle.

Implement selecting notes, Copy and ratings, plus Add to draft where G0 verified the composer API. Verify existing draft text survives; exact-session insertion returns an acknowledgment; no call to submit/injection occurs. A missing/unmounted session or changed connection fails visibly without affecting another composer. On versions without that API, show Copy-only handoff and mark insertion unsupported, never passed.

**Exit:** feedback is visible in actual Desktop and CLI; the user retains the final Send action. A screenshot alone does not prove insertion or delivery—inspect the draft/API result and unchanged main-agent request count.

### G3 — Reliability, data bounds and installation

Test the acceptance matrix below, including malformed results, late completion, coalescing, replay, config replacement, reconnect and profile isolation. Tests must cover actual public-host contracts in addition to isolated unit logic.

Create a clean unified plugin package with backend, Desktop ESM asset, manifest, docs and required notices. Prefer verified directory/git installation for v0.1. A wheel is not a required release artifact; do not claim wheel-based Desktop discovery without a separate clean-install test.

Verification contract in the future plugin repo:

- `python -m pytest tests/unit -q`
- `python -m pytest tests/integration -q --strict-markers` in the pinned Hermes test environment
- The plugin's configured Python lint/type checks and Desktop tests/build checks
- Actual discovery/enablement in an authorized clean lab install using commands verified at G0
- `git diff --check` before committing

Bind `python` to the verified project interpreter; do not rely on a stale environment or another installation's incidental import path. Test-helper imports must work with the chosen pytest import mode. CI must fail if required host contracts are skipped or unavailable. Update CHANGELOG and compatibility/install/uninstall instructions with observed limitations.

**Exit:** reproducible clean installation and mandatory checks passing with raw output inspected. No production profile was enabled.

### G4 — Usefulness evaluation and release review

Before running, freeze ten evaluation cases: five with independently verifiable defects, three correct controls and two cases with deliberately incomplete evidence. Include code correctness, unsupported completion claims and overlooked constraints. Define expected evidence-based observations without forcing a particular phrasing or pretending a model verdict is guaranteed.

Run the same case set against the configured reviewer. If the user authorizes a second model, use the same packets for comparison. Testing both Sol/GLM role directions is optional evaluation expansion, not a blocker for this smaller first release. Never silently substitute unavailable models.

Record actual notes, supported/unsupported assertions, false positives, missed defects, evidence references, latency, available usage/cost, served route and Useful/Not useful ratings. Preserve all cases rather than selecting successful examples. Deterministic product tests and model usefulness results are separate reports.

**Exit:** all mandatory functional acceptance tests pass, and an honest evaluation report is available for the user's release decision. Do not claim the model is reliable solely because transport tests pass. Do not tune acceptance expectations after seeing results to claim success.

Prepare a plugin PR with exact supported versions, verification evidence and limitations. Keep it open. No host PR, production installation, public release or merge is implied by passing G4.

## Functional acceptance scenarios

| ID | Scenario | Required observable result |
|---|---|---|
| A01 | Install/enable without route | No requests; actionable configuration state |
| A02 | Completed main answer | Completion deduplicated; admitted work gets one review, otherwise explicit skipped/paused status explains coalescing or limits |
| A03 | Interrupted/failed answer or auxiliary completion | No automatic recursive/invalid review |
| A04 | Successful review while main agent idle | Notes visible; zero new main-agent turns or transcript changes |
| A05 | Requested/served routes differ | Host result accepted under chosen policy; route change visible |
| A06 | Missing usage/attribution | Unknown/uncertain label, no invented zero spend or served identity |
| A07 | Invalid schema/severity/evidence reference | Validation error; no fabricated or partially trusted finding |
| A08 | Long packet or absent tool evidence | Explicit coverage limits; no claims of full inspection |
| A09 | Changed evidence after character 4,000 or earlier in packet | Full packet digest differs; valid new evidence not silently suppressed |
| A10 | Rapid consecutive answers | One active/latest pending; skipped turns identified |
| A11 | Deadline/cancellation not confirmed | Timed-out state, late output suppressed, no unbounded replacement calls |
| A12 | Quota/auth/network failure | Main agent remains usable; records retained; no plugin retry storm |
| A13 | Disable/unload/session-applied change during review | Affected session’s pending work cancelled and old result invalidated; other sessions and counters retained |
| A14 | Process crash/restart/reconnect | Completed notes survive; incomplete attempts labeled; no paid auto-replay |
| A15 | Two profiles/sessions/connections | No cross-scope note, request-context or draft access |
| A16 | Add to draft with existing text | On supported composer versions: exact-session append, original draft preserved, nothing sent; otherwise insertion explicitly unsupported and Copy tested |
| A17 | Missing composer or connection switch | No other-session insertion; Copy available; truthful failure state |
| A18 | CLI notes/copy | Safe command output, no model-history injection |
| A19 | Cancel/save/conflicting config edits | Cancel inert; atomic versioned save or explicit conflict |
| A20 | Session allowance exhausted | Pause new admission at the applied allowance (default 20); already admitted final call may publish; reset requires explicit operator action and does not resume |
| A21 | Terminal controls/unsafe Markdown | Safe text rendering, no executed control sequences/HTML |
| A22 | Clean package install | Backend and Desktop asset discovered through existing supported APIs |
| A23 | Host source/config unrelated to plugin | No Hermes code/generated-file changes or hidden routing changes |

Review now/retry tests additionally cover available snapshot, completed-review deduplication, expiry, eviction and restart. An unavailable snapshot produces no request and identifies why.

Manual user submission is outside the non-interference assertion: after the user presses Send, normal Hermes execution may legitimately proceed. Tests must distinguish that action from publication and draft preparation.

### Additional scenarios made explicit by specification review

The existing A01–A23 IDs retain their meaning. A24–A29 expose cross-cutting requirements that previously had no dedicated row.

| ID | Scenario | Required observable result |
|---|---|---|
| A24 | Review now with valid, duplicate, expired, evicted and restarted snapshot | Eligible snapshot uses original capture; active/completed equivalent deduplicates; failure retry is explicit; expiry/eviction/restart yields a reason and zero calls; 16-entry cache and applied serialized packet bound (default/max 32 KiB) verified |
| A25 | Allowance reservations, reset, deadlines and restart | Cancelled/skipped-before-call attempts consume zero; call that throws consumes one; concurrent reservations never exceed limit; no enable/restart/prune reset; explicit confirmed reset alone does not resume; expired queued attempts never call |
| A26 | Every result outcome and boundary | Findings/no_findings/insufficient_evidence display distinctly from error; unknown keys, wrong types, more than the applied max_findings, invalid/duplicate refs and over-limit text rejected; correct empty-array rules; no partial-result salvage or hidden retry |
| A27 | Sensitive evidence and local persistence | Synthetic secret fields/patterns removed across request, answer, tools and errors; exposed reasoning excluded; no independent file/network/tool access by reviewer; persisted/logged data has no raw packet/provider reply; sensitive-note limitations disclosed |
| A28 | Storage, migrations and configuration across processes/sessions | Terminal writes atomic, note IDs stable, retention bounded and counters durable; supported migrations and interruption tested with backup before destructive change; applied snapshots isolated; only proven-dead owners’ recovered sessions default disabled; no private host DB access |
| A29 | All user actions and supported surfaces | CLI help/status/sessions/on/off/configure/review/notes/copy/reset and Desktop enable/configure/review/select/copy/draft/rate follow scope; ratings survive reload; backend-disabled state actionable; polling suspended hidden/unmounted; unsupported surface makes zero automatic requests |


### Regression scenarios for the six final-review fixes

| ID | Scenario | Required observable result |
|---|---|---|
| A30 | Two worker processes and recovery | While A owns a live review, start/restart B: A’s enablement, attempt and counter are untouched; simultaneous claims permit exactly one owner; slow live worker/PID reuse/ambiguous death cannot trigger takeover; proven-dead owner recovery affects only its sessions; controls reach the owner or fail explicitly |
| A31 | Profile revision versus session generation | Apply settings to A while B runs: B publishes under its original applied snapshot, A’s old work is invalidated; conflicting saves reject atomically; delayed actions recheck their own session generation/target; historical notes remain selectable |
| A32 | Discovery and disabled/paused state | On first completion while off, discover metadata without retaining/digesting content; configure/enable then capture a later answer; off/pause clears pending/latest packets; manual and automatic requests while off/paused make zero calls; explicit on after recovery awaits new evidence; unsupported/foreign IDs rejected |
| A33 | Storage fault injection | Locked/unwritable/full/corrupt store and failed transactions: reservation failure makes zero calls; saves/ratings/resets never falsely acknowledged; failed result commit publishes no findings, retains charged allowance and pauses; volatile errors visible, main agent usable; recovery never replays; successful draft append plus log failure is reported once without another append |
| A34 | Configuration schema and non-default values | Exercise every documented key/type/min/max, booleans, fractions, missing/extra route fields, unknown keys and invalid host route; reject invalid updates atomically; lower limits affect packet/schema/deadline/allowance; lowering allowance does not reset it; process constants cannot be changed per session |
| A35 | Unified retention and pruning | Mix more than 100 settled attempts across every terminal status; exactly newest 100 retained in defined order; active/unresolved transports protected; linked notes/ratings/actions pruned together; counters/config/replay protection survive; pruned selections fail truthfully |

### Measurement details

- For A04/A16/A18, independently observe main transcript content and main-model invocation counts around reviewer completion, command output and draft preparation. Do not equate zero UI animation with zero main-agent work. The subsequent user Send is a separate phase.
- For A15, include concurrent sessions in one profile and two authenticated profile connections, focus change during a request/action, backend requests with foreign session IDs, reconnect and revision changes. Verify request context, response delivery, storage and composer targeting, not just the pane label.
- For A11/A25, use a controlled delayed transport to exercise timeout behavior and then separately document any real host cancellation evidence. A simulated cancellation cannot prove provider billing stopped.
- For A09, alter included evidence beyond character 4,000 and in an earlier tool result, while keeping the final answer identical. Digest and eligibility must reflect the changed packet. Also test genuinely identical replay deduplication.
- For A27, use inert synthetic secrets and adversarial instructions inside tool output. Verify that neither becomes an instruction granting the reviewer tools, changing routes or causing main-agent handoff.
- For A28, verify database artifacts after process restart and pruning, not only in-memory counters. Exercise contention if multiple runtimes can open the same profile store.

## Evaluation and release criteria

Before paid evaluation, freeze the ten G4 cases, independent defect/evidence annotations, selected route and authorized budget. Record every case, including failures and incomplete coverage. Distinguish an unsupported accusation from a recommendation to verify something unavailable. Report counts and examples of true supported findings, false positives, misses, correct abstentions, latency, user ratings and unknown usage/cost. No numeric model-quality threshold was agreed; the user decides usefulness from this report. Functional failures cannot be waived by favorable model ratings.

## Historical evidence ledger (2026-10-12; historical and excluded)

This ledger records verification performed before the public export, on revisions earlier than the current candidate. It is retained as history only. The receipts, directories and repository paths it originally referenced are **not part of the public tree**, are not public links, and prove nothing about the preview commit or the current candidate. They must not be cited as public evidence.

At historical candidate `a6d0552`, **0 of the 35 acceptance rows were proven**; 32 carried partial or historical receipts pinned to earlier revisions, and A03, A16 and A17 had no receipt at any revision. The corresponding frozen ledger is excluded from the export.

| Gate | Historical status | Evidence class and limitation |
|---|---|---|
| G0 | RUN — host pin later stale | Capability work was executed against `66605471…`. After the 2026-10-12 host update that object was absent from the checkout, so the runner default was re-qualified to `590af66a…` (v0.21.7) after confirming it still exported all five `plugin.yaml` hooks and both `llm.*_override` capabilities. G0-era receipts remain historical, and no capability check was re-run against the re-qualified host. |
| G1 | PARTIAL — historical review receipt only | One completed native automatic-review receipt on candidate `7959ee2e…`, which differs from the later tree in six files; the proof does not transfer. |
| G2 | PARTIAL — offline green, live lanes open | Desktop/Vitest 94/94 green at `a6d0552`; live lanes remained unproven. |
| G3 | PARTIAL — offline suites green, acceptance rows unproven | Unit 116+17, privacy 61, evaluation 40, cli_feedback 38+113 all exit 0, and integration 55 passed + 7 subtests under `--strict-markers` with the host dependency set on `PYTHONPATH`; the same non-integration suites passed from a second checkout. 0/35 A-rows proven at current-candidate level. |
| G4 | EXECUTED & ADJUDICATED — not current-tree certification | 10/10 cases accounted with a zero-mismatch hash ledger; adjudication 4/5 defects, 1/3 control false positive, 2/2 honest abstentions. The run pinned the 2026-10-11 source state and does not certify the current tree. |

## Current verification state

- **Current candidate:** this public preview's content, bound by [EVIDENCE.md](../EVIDENCE.md) and `SOURCE-MANIFEST.sha256`; `053ae098813173acecda0e99b12527a191f3e8a6` is its frozen private source baseline, not the public preview revision. **0 of 35** acceptance rows are proven natively at the preview candidate. A03, A16 and A17 have no receipt at any revision.
- No G0–G4 gate is passed at the current candidate. The preview does not close them and must not be described as doing so.
- Unit suites, fixture transports, offline runs, deterministic Desktop builds and structural host smoke are **not** native acceptance evidence. They must never be promoted to an A-row or a gate pass, and a green fixture run does not prove a real reviewer, a real pane or a real install.
- A future status change requires a receipt recorded in the plugin repository's verification system: requirement/test IDs, spec revision, plugin revision and dirty state, host/Desktop versions, exact command or UI steps, exit code, raw output, observation, reviewer and evidence location. This public file may only summarize such a receipt.

## Public preview publication ledger (P0.1–P0.8)

These are publication-hygiene checks for the public source preview. They are separate from G0–G4 and A01–A35: passing a P0 check does not pass a gate or an acceptance row, and none of them is native review evidence. Statuses below are actual as of this document revision and are backed by the sanitized receipt summary in `EVIDENCE.md`; "prepared" is not "verified".

| ID | Check | Procedure | Current status |
|---|---|---|---|
| P0.1 | Orphan isolation | In the fresh-root staging repository: `git rev-list --all --count` equals 1; no inherited refs, tags or private-origin object database; author/committer is the approved public attribution identity. | PASS — single fresh-root commit, no inherited refs, approved public identity; checked on a fresh clone of the commit ([EVIDENCE.md](../EVIDENCE.md)) |
| P0.2 | Frozen originals untouched | Private archive remains clean at `053ae098813173acecda0e99b12527a191f3e8a6`; freeze hashes unchanged; export contains no private evidence, verification, research-note or `.omh/` directories. | PASS — archive checked clean at the baseline revision with its freeze hashes passing; frozen evaluation files 2/2 OK and no excluded trees in the export ([EVIDENCE.md](../EVIDENCE.md)) |
| P0.3 | Identifier scan | Independent scan of the export for local paths, personal names/emails, private profile names, private repository URLs, RFC1918 and loopback endpoints, binaries and `__pycache__`. The approved public handle/attribution and canonical GitHub noreply identity are the only exceptions. | PASS — independent scanner 0 blocking findings, export checker clean, Gitleaks history scan clean ([EVIDENCE.md](../EVIDENCE.md)) |
| P0.4 | Offline suites from clean export | From an isolated export and dependency set: unit, privacy, evaluation (if included) and cli_feedback suites exit 0 with actual counts and no skips. Counts are recorded as observed, never as expected. | PASS — observed counts from a fresh clone of the preview commit in [EVIDENCE.md](../EVIDENCE.md). The load-sensitive storage burst diagnostic failed once in an earlier candidate run and is disclosed in `KNOWN-ISSUES.md` |
| P0.5 | Current-host structural smoke | Disposable profile only: manifest declares author/license/homepage (`repository` may warn as an unknown field); `hermes plugins doctor --ci` reports the five hooks and two capabilities; `hermes plugins show` confirms the plugin is present and disabled (it does not print hook/capability counts); install uses the exact revision `--no-enable`; profile deleted with cleanup receipt. Structural only; not native review proof. | PASS (structural only) — Hermes 0.22.2 disposable profile, plugin left disabled, profile deleted ([EVIDENCE.md](../EVIDENCE.md)) |
| P0.6 | Desktop preview | Fresh Desktop suite green including the packaging test; pane mounts on the current host with zero page errors and zero model calls; Copy-only scope confirmed. Fixture results are not native packaged-UI proof. | PARTIAL — fresh Desktop suite (103 tests) and artifact checks pass; native pane mount on the current host NOT RUN. Desktop is published as experimental, Copy-only source; the CLI is the preview surface |
| P0.7 | Truth audit | Preview label present; no stable/verified/supported claims; 0/35 and A03/A16/A17 disclosed; native evidence labelled historical; host claim labelled structural; uninstall data-retention behaviour disclosed. | PASS — two independent truth audits of the preview text; all blocking findings corrected before publication (audit reports kept outside the public tree) |
| P0.8 | Packaging and legal | `git diff --check` clean; deterministic content manifest; license/attribution resolved to the approved public identity; third-party notices present; secret-scan clean; public CI file present. A CI result is recorded only after it actually runs. | PASS locally — diff check (frozen `evaluation/README.md` excluded; its Markdown line breaks are hash-frozen), deterministic manifest, notices, secret scan and CI file present; public CI result recorded only after it runs |

The binding publication plan is: one fresh-root sanitized export; the private archive untouched; frozen evaluation files included only verbatim when scanner-clean; no paid inference; no writes to the active profile; no shared gateway or dashboard restart. A failed or missing check blocks publication rather than being reworded as a pass.

# Hermes Advisor — implementation decisions and startup inputs

Version: 0.1-spec.1-public-preview · Prepared: 2026-10-10 · Public derivative: 2026-10-20 · Status: **normative decision register; LOCKED and BASELINE rows remain binding for the target**.

This file records the user constraints and the selected engineering baseline. Current verification state is owned by [ACCEPTANCE.md](ACCEPTANCE.md); at the current candidate 0/35 acceptance rows are proven natively. Adding the public-preview scope decisions below does not reopen, weaken or reinterpret any earlier LOCKED or BASELINE decision.

## Status vocabulary

LOCKED = user product constraint. BASELINE = fixed engineering instruction for this version. DEFERRED = outside v0.1. DO NOT BUILD = prohibited. LOCKED and BASELINE are both binding; their distinction records provenance rather than pending approval. Runtime capability verification is execution work, not an undecided design.

## Decision register

| ID | Status | Decision and reason | Reopen only when |
|---|---|---|---|
| D01 | LOCKED | No Hermes modifications; use existing supported public APIs. This keeps the plugin independently maintainable. | User explicitly changes this hard requirement; missing APIs alone are not permission |
| D02 | LOCKED | Feedback first, human decides when to pass advice to the main agent. Severity has no control authority. | Separate approved product design |
| D03 | LOCKED | Hermes-managed model access, credentials and existing fallback accepted; actual route may be unknown. | User explicitly chooses a different routing requirement and public support is verified |
| D04 | LOCKED | Automatic review after each successfully completed main answer once enabled, plus manual Review now. | User revises cadence; no silent switch to manual-only to mask missing hooks |
| D05 | LOCKED | Captured evidence only; no autonomous investigation or reviewer tools. | Separate approved scope change |
| D06 | BASELINE | Single reviewer, explicit route, default off. A small release measures advice usefulness before roster complexity. | User-approved spec revision |
| D07 | BASELINE | Native pane and CLI inbox; exact-session append when available, Copy otherwise. No transcript-card dependency. | Supported APIs and revised acceptance justify another UI |
| D08 | BASELINE | Bounded queue, memory-only packet cache and plugin-owned SQLite notes/counters. Avoid private host history reconstruction. | Evidence of a need with privacy/resource tests revised |
| D09 | BASELINE | Defaults: 32 KiB packet, 2,048 output tokens, four findings, 120s including queue, 20 calls/session, one active/session and two/process. These are engineering bounds, not user-confirmed optimum values. | Measurements and explicit spec amendment; never silent limit inflation |
| D10 | BASELINE | Exact local response contract, type/reference/text bounds and whole-response rejection. Needed for deterministic validation. | Schema version and acceptance changes reviewed together |
| D11 | BASELINE | Recover only proven-dead owners; only their sessions restart disabled, saved settings/counters survive and live owners remain untouched. No automatic paid recovery. | Explicit revised recovery contract |
| D12 | BASELINE | Durable allowance reservation before invocation; explicit confirmed reset does not resume. Avoid race-driven overspend and reset-by-restart. | Revised budget semantics and concurrency tests approved |
| D13 | BASELINE | Record owner tokens and session-applied generation checks, independent of saved profile revision; keep pending-cancellation slot occupied. Local timeout is not proof of remote cancellation. | Actual host behavior supports stronger guarantees |
| D14 | BASELINE | Use SPEC’s exact route/integer settings schema and ranges. Process concurrency and cache/retention constants are fixed, not per-session settings. Unknown keys rejected. | Documented new setting or changed maximum |
| D15 | BASELINE | No added telemetry; notes/ratings local, raw packets/replies not persisted; partial redaction limitations explicit. | Explicit data-handling change approved |
| D16 | BASELINE | Directory/git package first; CLI and native Desktop target, other surfaces unclaimed. | Actual clean-install/surface evidence and amended support matrix |
| D17 | BASELINE | Four authoritative specifications, G0–G4 sequence in ACCEPTANCE.md, one project CHANGELOG; older plans excluded from the handoff. | Document structure no longer supports traceability |
| D18 | BASELINE | Hermes executes this plan after the user kickoff in an isolated plugin repo and lab profile; passing technical gates advances without repeated approval. | Lab isolation or review evidence proves inadequate |
| D19 | DEFERRED | Multi-reviewer comparison, during-work review, investigation, strict routing, imports, automatic steering/continuation, exact spend enforcement. | Separate approved design and supported API proof |
| D20 | DO NOT BUILD | Host patches/private handles/monkeypatches, automatic submit/injection, hidden retries, copied unverified prototype code, invented evidence or success. | Prohibitions cannot be bypassed by implementation convenience |
| D21 | BASELINE | Metadata-only discovery while disabled/paused; packet capture and manual requests require enabled state; explicit recovery awaits a new answer. | Privacy/activation contract revised with acceptance tests |
| D22 | BASELINE | Storage failures fail closed for inference/publication, show truthful errors and never claim failed mutations succeeded. | Revised failure contract verified |
| D23 | BASELINE | Retain exactly the newest 100 settled terminal attempts of all statuses; protect unresolved work and preserve counters/replay metadata. | Retention contract and pruning tests revised |
| D24 | BASELINE | The public derivative publishes this four-file set as the normative target plus explicit preview deviations. Publication does not approve a release, close a gate, or promote preview behavior to target behavior. | A separate release decision with gate receipts |
| D25 | BASELINE | Preview implementation fix scope is B2, H4, H5, M3, M5, the HTTP 422→400 contract and worker lifecycle, each with a test. B1 (R10 command surface) is documented as a known deviation and not implemented in the preview window; H1, H2, H3, M1, M2, M4 and the low-severity findings are deferred to the named post-preview campaign in `KNOWN-ISSUES.md`. | Fix campaign complete and deviations removed from KNOWN-ISSUES with receipts |
| D26 | BASELINE | Preview publication hygiene: one fresh-root sanitized export; private archive untouched; frozen evaluation files only verbatim when scanner-clean; no paid inference, current-profile writes or shared-service restarts. Redaction before clipping, no raw packet persistence and no invented proof remain binding. | Publication policy explicitly revised |

D25's fixes are present at the preview commit with their regression tests; the run that proves them is summarized in `EVIDENCE.md`. API mismatch, including the preview's narrower CLI, is a verification finding and never permission to reopen unrelated decisions.

The LOCKED rows record the user's product constraints. BASELINE rows are the selected engineering solution, including all six final-review repairs. Implement them as written. An API mismatch is a verification finding, not permission to reopen unrelated decisions.

## Startup inputs and execution tasks

Resolve these during execution; do not send the user a questionnaire merely because the table exists. Use supplied kickoff values and existing non-secret environment context first. Batch only genuinely missing information into one concise request. A missing input blocks its dependent live action; continue independent plugin work.

| ID | Task | Resolution rule | Stage |
|---|---|---|---|
| Q01 | Resolve plugin repository and branch | Use the supplied plugin repo. Otherwise create a local `hermes-advisor` directory/repository in the kickoff workspace and a feature branch; inspect existing files/instructions first and preserve unrelated changes. | G0 setup |
| Q02 | Resolve isolated lab host/profile and Desktop build | Use the supplied lab. Otherwise inspect available runtimes and supported profile creation; set up a dedicated lab through public interfaces when included in kickoff scope. Ask only if the target/access cannot be determined safely. Never repurpose a production profile. | G0 setup |
| Q03 | Resolve reviewer route, grants and live-test budget | Reuse values explicitly provided in kickoff/configuration. Never inherit the main model or invent spending authority. If absent, request just these inputs before inference; proceed with non-inference work meanwhile. | Before live inference |
| Q04 | Verify installed APIs and ownership/storage topology | Execute the G0 matrix, record exact observed behavior, apply listed fallbacks and stop only dependent work when a mandatory capability fails. | G0 |
| Q05 | Verify composer append | Test acknowledged exact-session append; select the already specified Copy-only fallback if unavailable, without a design approval round. | G0/G2 |
| Q06 | Verify supported connection/surface scope | Exercise required CLI/Desktop/profile isolation; advertise remote/reconnect combinations only when their tests pass. | G0/G3 |
| Q07 | Apply engineering defaults | Use SPEC's exact schema, defaults, recovery and reset rules. There is no outstanding design decision here. | Implementation |
| Q08 | Construct the evaluation set | Implementer prepares and freezes ten cases with the specified 5/3/2 composition and independent evidence annotations before running. No separate case-selection approval is required within the authorized evaluation budget. | Before G4 |

This plan is authorized once within its stated environment and spending scope; do not repeat the approval request at each gate. User input remains necessary for unavailable access, unprovided spending authority, changes to fixed scope, or actions outside kickoff authorization. A verified technical blocker is reported with evidence rather than bypassed. Gate results and the final usefulness report remain factual: this baseline does not claim an untested API or model will succeed.

## Change procedure

Record date, affected decision/requirement IDs, reason, evidence, approval where needed and impacted acceptance cases. Update current normative files together. Preserve historical reasoning here without maintaining competing active specifications. Engineering refinements within approved contracts need documentation and verification; changes to behavior, hard constraints, scope or authorized live actions require renewed review.

## History

- 2026-10-20: Public derivative for the untagged source preview. Added authority layering, target-versus-preview scope, the P0.1–P0.8 publication ledger, current-candidate 0/35 status and D24–D26. Removed the copied implementation kickoff, personal paths and profile names, procurement narrative, private evidence references and stale current-candidate claims. No R/A contract, LOCKED decision or BASELINE decision was removed or weakened; historical receipts are marked historical and excluded.

- 2026-10-12: Status reconciliation only; no decision, requirement or constraint changed, and no MUST is waived. The offline suites, the Desktop suite, the G4 usefulness evaluation and an isolated install/uninstall lifecycle now have receipts, and the evidence ledger in [ACCEPTANCE.md](ACCEPTANCE.md) replaces the blanket "NOT RUN" labels with source-bound states. The dated entries below keep their original wording as the record of those dates.

- 2026-10-10: Spec.1 establishes the execution baseline, removes recurring draft/design-review stops, turns open questions into startup tasks, and excludes older plans from the handoff. The user has requested ready-to-execute documents; implementation awaits their Hermes kickoff. G0–G4 and A01–A35 remain NOT RUN.

- 2026-10-10: Draft.2 resolves all six final-review findings: owner-scoped recovery; per-session generation isolation; metadata-only discovery and disabled/paused rules; storage failure behavior; exact configuration schema with fixed process limits; one retention policy. A30–A35 add explicit regression coverage. The user authorized these documentation repairs; implementation, runtime checks and paid inference remain unauthorized and NOT RUN. At that historical revision, engineering choices remained pending; Spec.1 above supersedes that status.

- 2026-10-10: Draft.1 extracts the approved-for-documentation feedback-first direction into four specifications. Preserves the existing G0–G4 sequence and A01–A23 IDs; adds traceability and A24–A29 coverage. Makes response schema, restart/reset semantics, allowance atomicity and state handling explicit as proposed engineering decisions. No implementation or runtime gates executed. Earlier documents retained unchanged.

# Changelog

All dates are UTC unless noted. This project is an untagged public source preview;
the version below is a pre-release identifier, not a stable release.

## 0.1.0a1 — public source preview (2026-10-20)

**Status:** untagged source preview. Not v0.1 stable, not a tagged release, not a
support commitment. CLI-first; Desktop experimental and Copy-only.

### Added

- Host entry point and registration for five hooks: `pre_llm_call`,
  `post_llm_call`, `on_session_end`, `subagent_start`, `subagent_stop`.
- `/advisor` command with `metadata`, `status`, `notes`, `enable`, `disable`,
  `review_now`, `configure`, `rate`, `reset_allowance`, and `feedback`.
- Pure core with bounded/redacted packet construction, coverage reporting,
  structured response validation, owner-scoped sessions, allowance accounting, and
  settled-attempt retention (100 per session).
- Profile-scoped SQLite store with WAL, per-session advisory locks, schema
  initialization, ratings, feedback actions, and bounded write serialization.
- Bounded daemon worker pool with two process slots, one transport per session,
  cancellation grace, and detached-loop accounting for unconfirmed transports.
- Authenticated dashboard router for metadata/status/notes/control, scoped to the
  runtime registered for the request's profile.
- Experimental Desktop pane generated as a single `desktop/plugin.js` from
  `desktop/pane.mjs` and `desktop/logic.mjs`; imports only host-provided React and
  `@hermes/plugin-sdk`. Copy is the only handoff.
- Public specifications, adapter contract, and this preview documentation set.

### Preview behavior deltas (target vs implemented)

These are the actual differences a reader must know. The target remains binding;
none of these entries amends it.

| Area | Target | Preview implementation | Tracking |
|---|---|---|---|
| CLI command surface | `sessions`, `on`, `off`, `status`, `notes`, `review`, `reset-allowance`, `copy`, `configure`, including single-key `configure ... set` | `metadata`, `status`, `notes`, `enable`, `disable`, `review_now`, `configure`, `rate`, `reset_allowance`, `feedback`; `--session` + `--json` | **B1** |
| CLI discovery | `/advisor sessions` | `/advisor metadata` returns discovered sessions | **B1** |
| CLI handoff | `copy` | `feedback --json` returns a copyable text block | **B1** |
| Desktop handoff | Copy **or** exact-session draft append | Copy only; no Add-to-draft | R09/R11 target deferred |
| Hook registration | Observation/lifecycle hooks only | Adds `pre_llm_call` as a start signal; callback returns `None` | **Low9** |
| Profile identity | Existing supported public API | Uses undocumented `profile_name_for_home` | **Low4** |
| Completion signal | Review on a successful completed main answer | Requires `failed` and `interrupted` to be exactly `False`; absent flags suppress review | **H1** |
| Reviewer choice | One explicit route, host-managed access | Same; no default or fallback | Implemented |
| Reviewer usage | Honest known/unknown accounting | Empty or zero-filled usage is reported as unknown | Fixed (**B2/Low8**) |
| Pending snapshots | Per-session, observable retention | Global 16-entry eviction | **M4** |
| Pairing coverage | Report why pairing was limited | Limitation names computed then discarded | **M2** |
| OS | Supported host platforms | Linux only; POSIX `fcntl` storage | Preview limit |
| Native acceptance | A01–A35 receipts at the release candidate | 0/35; A03/A16/A17 have no receipt at any revision | Not claimed |

### Fixed in this preview

These fixes are present in this preview's source, each with a regression test
that failed before the change and passes after it. The run that proves the
regressions at the published revision is recorded in [EVIDENCE.md](EVIDENCE.md);
a check not recorded there should be treated as not run.

| ID | Defect | Fix | Regression tests |
|---|---|---|---|
| **B2** | The host facade zero-fills counters it did not receive, and `_clean_usage` accepted an empty or all-zero mapping as known usage. | `advisor_host.py` drops zero-filled values; `_clean_usage` reports usage as known only with at least one positive measurement. A zero cost estimate is never shown as a price. | `tests/unit/test_preview_usage.py` |
| **H4** | `Service.close()` could release ownership and then return `False`, because it reported idle worker-thread exit rather than ownership. `Runtime.released` stayed false and re-registration was refused. | `close()` returns `True` once every transport has settled and the owner locks are released. It still returns `False` while a transport is unresolved. | `tests/unit/test_preview_service.py` (close and Runtime re-registration) |
| **H5** | Any review-worker exception, including a non-storage defect or a cancelled future, was latched as `storage_error` and disabled the session. | Only `StoreError` latches `storage_error`. Other worker failures stay visible as a volatile error, the attempt is finalized as `worker_error`, and healthy storage keeps admitting work. Cancellation is not reported as a failure. | `tests/unit/test_preview_service.py` (worker failure classification) |
| **M3** | `advisor_core/store.py` imported POSIX `fcntl` at module scope, so importing the plugin failed on Windows. | The import is guarded. Without `fcntl`, acquiring a session lock fails closed with an explicit `StoreError`; there is no emulated lock. Windows remains unsupported. | `tests/unit/test_preview_store.py` |
| **M5** | `capture()` built and digested the packet before checking local fail-closed admission. | A locally disabled session is refused before any content argument is inspected. | `tests/unit/test_preview_service.py` (content tripwire) |
| **HTTP control contract** | A missing or empty `expected_instance`, or a malformed, empty or non-object `/control` body, produced FastAPI's 422 instead of the documented 400. | Those requests return `400 invalid_control_shape`; a wrong instance still returns `409 backend_instance_conflict`. | `tests/cli_feedback/test_preview_api.py` |
| **Worker lifecycle** | Idle workers polled every 50 ms, and a shutdown requested from inside a worker could join itself or a peer. | Idle workers wait 0.75 s and are woken by explicit shutdown tokens. A shutdown requested from a worker never joins and reports that success is unconfirmed. | `tests/unit/test_preview_worker.py` |

### Deferred to PPH-1

B1, H1, H2, H3, M1, M2, M4, and Low1–Low7/Low9 are deferred to the named
post-preview campaign described in [KNOWN-ISSUES.md](KNOWN-ISSUES.md). Low8 is
fixed with B2. H2 and H3
are the evidence-limited races that can lose a paid review; the preview makes no
guarantee that a paid review is never lost.

### Verification

No pass counts, commit hashes, or native success claims are recorded here. The
authoritative current-run record is [EVIDENCE.md](EVIDENCE.md), with content digest
binding. At this candidate:

- **0 of 35** acceptance rows are proven natively.
- **A03, A16, A17** have no receipt at any revision.
- The only native review receipt is historical and does not transfer.
- Current-host install/doctor/show checks, if recorded, are structural only.
- Testing is Linux-only; Windows is not supported.
- `docs/CORE_API_FOR_ADAPTER.md` is preserved byte-for-byte; its "verified" label
  and test count are historical, not current-candidate proof.

### Changed from the earlier private development line

The following is retained as project history, not as evidence for this preview.
It describes earlier revisions that are not the public candidate.

- Earlier revisions added the pure core, packet redaction, owner-scoped storage,
  worker/runtime lifecycle, dashboard router, Desktop pane, and the ten-case
  synthetic usefulness fixtures.
- Earlier revisions tightened URL/userinfo redaction, disabled-session lock
  handling, failed-enable lock restoration, child-tracking bounds, and copy-text
  escaping.
- Earlier private publication and commit identifiers are intentionally omitted;
  they are not part of this repository's history and are not public references.

## 0.1.0-spec.1 — specification baseline (2026-10-10)

- Added the normative specification set: `specs/SPEC.md`,
  `specs/HERMES_COMPATIBILITY.md`, `specs/ACCEPTANCE.md`, and
  `specs/DECISIONS.md`.
- Defined R01–R15, A01–A35, G0–G4, the configuration envelope, privacy boundary,
  and the review/evidence pipeline.
- Recorded all gates as NOT RUN. No plugin code, installation, inference, or host
  configuration change was made by the specification work.

Earlier draft revisions (0.1-draft.1 and 0.1-draft.2) resolved review findings and
added owner-scoped recovery, generation checks, storage-failure semantics, and
A30–A35. They remain historical design records, not current verification.

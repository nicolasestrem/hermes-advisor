# Known issues

**Preview status:** `0.1.0a1`, untagged, CLI-first, Desktop experimental
Copy-only. The current candidate has **0 of 35** acceptance rows proven natively;
A03, A16, and A17 have no receipt at any revision. Historical native receipts do
not transfer to this candidate.

> **Evidence-limited races can still lose a paid review.**
> H2 and H3 can drop an in-flight or just-completed paid result when a session is
> disabled, reconfigured, or re-discovered by another owner. H1 can silently skip a
> review when the host omits expected completion flags, and M2/M4 can under-report
> coverage or evict the snapshot a review needs. **No guarantee is made that a paid
> review is never lost**, and no paid native review was run for this preview.
> Treat findings as best-effort and keep your own record of important reviews.

Line references are to the preview commit at the time this document was
written. They are diagnostic pointers, not proof of a fix. A finding is only
resolved when the code and its test are present at the preview revision and the
result is recorded in [EVIDENCE.md](EVIDENCE.md).

## Severity and disposition

| Level | Meaning in this preview |
|---|---|
| **B1 — blocker** | Prevents the preview from claiming the R10 CLI surface. Documented, not silently hidden. |
| **High** | Can lose a review, misattribute results, or break a core contract. Deferred only with disclosure. |
| **Medium** | Materially wrong behavior or coverage in realistic conditions. Deferred with disclosure. |
| **Low** | Localized correctness, diagnostics, or documentation defect. Deferred to the campaign. |
| **Integration** | Expected fix in this preview's bounded scope; not claimed fixed until observed. |

The named post-preview campaign is **PPH-1 (post-preview hardening, wave 1)**.
Each item below maps to a PPH-1 workstream. Fixes in the preview integration scope
are listed separately and are not part of PPH-1.

---

## B1 — Narrower CLI than R10

**Status:** open, documented deviation. Target R10 remains binding.

**Evidence:** `advisor_host.py:79-82` defines only `configure`, `enable`,
`disable`, `review_now`, `rate`, `reset_allowance`, and `feedback`.
`advisor_host.py:226-249` parses only `metadata`, `status`, `notes`, and those
control actions. The register call at `advisor_host.py:314-316` exposes one
`/advisor` command. There is no parser branch for `sessions`, `on`, `off`,
`review`, `copy`, `reset-allowance`, or single-key `configure ... set`.

**Impact:** users following the target R10 documentation will type commands that
do not exist. Session discovery relies on `/advisor metadata` instead of
`/advisor sessions`; lifecycle uses `enable`/`disable` instead of `on`/`off`; the
copy path is `feedback --json`, not `copy`. This is a release blocker for claiming
R10, but not a data-loss or privilege issue.

**Campaign:** **PPH-1A — CLI/R10 completion.** Implement the full explicit-session
surface against the target contract, with compatibility tests and a migration note
for preview spellings. Do not weaken R10.

---

## H1 — Missing completion flags silently suppress review

**Status:** open; deferred.

**Evidence:** `host_adapter.py:295-296` accepts `completed`, `failed`, and
`interrupted` from the host. At `host_adapter.py:306` the gate is
`completed is True and failed is False and interrupted is False`. If the host
omits `failed` or `interrupted` (values are `None`), the expression is not true,
the pending packet is popped, `metrics["discarded"]` increments, and no review is
queued. No user-visible error is produced.

**Impact:** on a host that reports only `completed=True`, Advisor appears enabled
but never reviews. The user may believe a paid review is running when nothing was
queued. This is an event-contract compatibility issue, not a privacy leak.

**Campaign:** **PPH-1B — Event adapter contracts.** Accept the documented host
completion shape, distinguish "flag absent" from "explicitly unsuccessful", and
surface a bounded diagnostic instead of silently discarding. Add a test for each
flag shape.

---

## H2 — Disable/re-discovery can orphan an in-flight paid result

**Status:** open; evidence-limited race. **Prominent because it can lose a paid
review.**

**Evidence:** `advisor_core/service.py:243-244` suspends the session's ownership
lock whenever durable state is not `enabled`. `service.py:508` suspends it again on
disable. A later `discover()` from another gateway can then take the suspended
lock and replace the owner token; the old owner's `_load_session` check at
`service.py:1799-1804` rejects the completed result, and `service.py:208-245`
reconciles the session to the new owner. The old transport's result has no durable
home.

**Impact:** a review that was already paid for can be discarded after a disable,
reconfigure, or second-gateway discovery. The result is not delivered and may not
be recoverable from the UI. This is a race, so it is not reproduced by every
disable; the absence of a failure in a smoke run is not proof of safety.

**Campaign:** **PPH-1C — Ownership and paid-result durability.** Define the
cancel-vs-detach rule for an in-flight transport, persist or quarantine a completed
result whose owner changed, and add a two-owner integration test with an injected
slow reviewer. Do not claim a no-loss guarantee before that receipt exists.

---

## H3 — Second live owner's rejection is swallowed during discovery

**Status:** open; evidence-limited race.

**Evidence:** `advisor_core/service.py:238` catches only `StoreError`. A live
second owner's `OwnerUnavailable` propagates out of `discover()`. The caller
`host_adapter.py:104-111` catches every exception, removes the active key, and
increments `metrics["errors"]`; nothing reaches status or the user. The first
owner is left running without a visible conflict.

**Impact:** the session can appear discovered but have no worker; the user gets no
review and no actionable error. Combined with H2, ownership can move without a
clear handoff.

**Campaign:** **PPH-1C — Ownership and paid-result durability.** Classify
ownership conflicts separately from storage failures, retry with bounded backoff,
and expose a stable reason through status/metadata. Test with two live stores on
one profile.

---

## M1 — Synchronous storage work on hook threads

**Status:** open; deferred.

**Evidence:** `host_adapter.py:241` calls `service.status()` synchronously inside
`post()`; `service.status()` performs SQLite reads at `service.py:1508-1516`.
`host_adapter.py:258-262` builds the packet synchronously, and the store's
connection path sets a bounded busy timeout (`store.py:258-261`, `store.py:273`).
All of this runs on the host hook thread.

**Impact:** a busy or slow filesystem, a competing writer, or a large packet can
stall the host's post-hook thread for up to the store deadline/busy timeout. The
UI may feel blocked; an in-flight review may miss its deadline. No content is sent
twice.

**Campaign:** **PPH-1D — Hook latency.** Move durable reads and packet
construction off the host callback, or bound them with a dedicated queue and an
explicit admission budget. Measure p95 hook latency under a locked store.

---

## M2 — Pairing limitations are computed but never reported

**Status:** open; deferred.

**Evidence:** `host_adapter.py:148-152` creates a `limitations` set and records
`history_unavailable`; lines 153-174 record `history_window_limited`,
`current_turn_boundary_unavailable`, and `current_turn_boundary_split`;
`tool_pairs_missing_result` is added at `host_adapter.py:223`; line 227 returns
only `pairs, omitted`. The limitations set is discarded. Coverage therefore
reports omitted pair counts but not why pairing was limited.

**Impact:** the reviewer can mistake a truncated history or missing turn boundary
for proof that no more evidence exists. A finding may be overconfident or an
abstention mislabeled. The packet's `coverage` block does not carry the limitation
names.

**Campaign:** **PPH-1B — Event adapter contracts.** Return limitation names with
the pair result, map them into packet coverage, and test each boundary case. Do not
invent evidence to fill a missing half-pair.

---

## M4 — Global 16-snapshot cap evicts another session's review

**Status:** open; deferred.

**Evidence:** `host_adapter.py:278-285` appends each pending snapshot and then
evicts the oldest entry whenever the global map exceeds 16. The map spans all
sessions in the adapter; eviction is not per-session and does not prefer expired
entries.

**Impact:** with more than 16 concurrently active sessions, a session's latest
snapshot can disappear before `review_now` or its own `end()` uses it. The user
sees "new answer required" or no review, even though the answer completed. This is
a capacity/retention bug, not data corruption.

**Campaign:** **PPH-1B — Event adapter contracts.** Bound pending snapshots
per session with an explicit global ceiling and an observable eviction metric;
never evict a snapshot that is the only eligible packet for an enabled session
without telling the user.

---

## Low-severity findings (nine)

### Low1 — Lowercased span indices can misalign redaction

**Evidence:** `advisor_core/packet.py:307` computes `low = text.lower()` and
`packet.py:327-328` splices redaction spans back into the original `text`.
`str.lower()` can change length (for example, U+0130), so a span found in `low`
may not match `text`.

**Impact:** a vendor token can be partially redacted, left intact, or the wrong
slice can be replaced. This is a privacy correctness bug on unusual Unicode input.
**Campaign:** PPH-1E — redaction and coverage.

### Low2 — Redaction is conflated with clipping in coverage

**Evidence:** `advisor_core/packet.py:605` marks text as changed when
`len(value) > _TEXT_CHARS or safe != value`; `packet.py:617-623` marks a mapping
as omitted when a secret key is removed. Those "omitted" flags feed
`request_clipped`/`answer_clipped`, so a complete-but-redacted request can be
reported as clipped.

**Impact:** the reviewer may treat redaction as missing evidence and abstain or
misjudge coverage. **Campaign:** PPH-1E — redaction and coverage.

### Low3 — Undocumented `local_fail_closed` pause reason

**Evidence:** `advisor_core/service.py:1526-1527` returns
`state="disabled", pause_reason="local_fail_closed"` when a session is locally
disabled but the durable row still says enabled. The documented pause-reason set
does not include `local_fail_closed`.

**Impact:** clients and users see an unexplained state string; the contract and
status diagnostics diverge. **Campaign:** PPH-1F — lifecycle/status contract.

### Low4 — Undocumented `profile_name_for_home` dependency

**Evidence:** `advisor_host.py:59-61` imports `hermes_constants` and calls
`profile_name_for_home(home)` to resolve the owning profile. This helper is not a
documented public plugin API.

**Impact:** a host refactor can make profile resolution fail closed, leaving the
plugin unavailable with no fallback. **Campaign:** PPH-1G — supported profile
identity.

### Low5 — `executescript` makes schema initialization non-atomic

**Evidence:** `advisor_core/store.py:315-318` begins an immediate transaction and
calls `_create_schema`; `store.py:346-348` uses `connection.executescript(...)`,
which issues an implicit commit. A failure part-way through schema creation can
leave a partially created schema while `user_version` is still 0.

**Impact:** a crash during first initialization can require manual recovery; a
later start may see `CREATE TABLE IF NOT EXISTS` objects but an incomplete schema.
**Campaign:** PPH-1F — lifecycle/status contract, with a crash-injection test.

### Low6 — Absent completion flags (same code path as H1)

**Evidence:** `host_adapter.py:306`.

**Impact:** this is the low-severity observation of the same gate as H1; it is not
a separate code change. Track it once under **H1** and keep this entry so the audit
inventory is complete. **Campaign:** PPH-1B — event adapter contracts.

### Low7 — CLI collapses `BackendRejected.code` to a class name

**Evidence:** `advisor_host.py:249-252` catches every exception and returns
`{"error": type(exc).__name__, "detail": "..."}`. A `BackendRejected` with a
stable code such as `invalid_control_params` or `no_model` is returned only as
`"BackendRejected"`.

**Impact:** users cannot distinguish a bad JSON shape from a missing route or an
invalid action without guessing; troubleshooting is slower. The generic detail is
deliberate to avoid leaking exception bodies. **Campaign:** PPH-1H — CLI
diagnostics.

### Low8 — Empty usage mapping was reported as known (same B2 defect)

**Evidence (historical, pre-B2):** at the revision this entry was filed against,
`_clean_usage` sat at `advisor_core/service.py:2043-2048`, and the call
`_clean_usage({}, None)` passed the mapping guard, built an empty `cleaned` dict,
and returned `True, {}`, so the caller treated usage as known when nothing was
reported. The helper now lives at `advisor_core/service.py:2062`; an empty or
all-zero mapping returns `False, None`, and usage is known only with at least
one positive measurement.

**Impact:** usage/cost attribution could claim knowledge it did not have. This is
the same defect as **B2** and is listed here for the audit inventory.
**Status:** fixed in this preview with B2: an empty or all-zero mapping is now
reported as unknown usage (see "Fixed in this preview" below).

### Low9 — `pre_llm_call` registration deviates from the R03 hook surface

**Evidence:** `advisor_host.py:309` registers `ctx.register_hook('pre_llm_call',
runtime.start)`. `host_adapter.py:93-111` uses it only as a start signal; the
callback returns `None` and does not transform the prompt. The normative capture
contract permits observation/lifecycle hooks only and prohibits prompt transforms.

**Impact:** the callback is not a prompt transform, but the registration itself
contradicts the required hook surface and may be rejected by a stricter host. It
is a documented deviation, **not** a decision waiver or permission to relax R03.
**Campaign:** PPH-1J — hook-surface conformance.

---

## Fixed in this preview (not PPH-1)

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

## Verification limitations that are not bugs

These are disclosed limits of the preview, not defects to be "fixed" by editing the
target:

- **0/35 native acceptance.** No A-row is proven at the current candidate.
  A03/A16/A17 have no receipt at any revision.
- **Historical native review only.** The one native automatic-review receipt is
  from an earlier revision and does not transfer.
- **Structural smoke only.** A current-host install/doctor/show check can prove
  presence, disabled state, five registered hooks, and two declared capabilities.
  It does not prove a real reviewer call, a packaged Desktop pane, or paid-review
  delivery. `hermes plugins show` does not print hook or capability counts; those
  are separate `doctor`/`capabilities` assertions.
- **Linux only.** Windows is not supported. There is no Windows storage backend;
  session locking needs POSIX `fcntl` and fails closed without it.
- **Load-sensitive storage diagnostic.** `tests/unit/test_storage_latch_diagnosis.py`
  runs 30 real-time bursts of concurrent writes and requires zero store failures
  under the store's fixed 1-second write deadline. In one pre-publication run on
  a heavily loaded host, one writer stayed inside a SQLite transaction for more
  than 4 s; six queued writers reached the deadline and the store failed closed
  with `StoreError`, as specified, so the test failed. That code path takes no
  other Python lock, and the stalled system call was not identified. The test
  passed in seven further runs (210 bursts), including four concurrent instances,
  and the failure was reproduced only by repeatedly suspending the test process. The test is kept
  unchanged in the default suite; on a busy machine it can fail without a code
  change. The deadline also counts time spent waiting for the in-process writer
  lock, so one slow writer can latch other sessions as `storage_error` until they
  are re-enabled. Changing that is a product decision, not a preview fix.
- **Desktop experimental.** Copy-only; no Add-to-draft. Fixture/DOM tests are not
  packaged-UI proof.
- **Private corpus excluded.** The private frozen usefulness corpus and raw
  evidence are not in the public tree; they are available on request subject to
  redaction and privacy review.
- **No paid inference in the preview.** No live reviewer request was made and no
  paid native review was run.

## Campaign summary

| Workstream | Scope | Issues |
|---|---|---|
| PPH-1A | CLI/R10 completion | B1 |
| PPH-1B | Event adapter contracts | H1, M2, M4, Low6 |
| PPH-1C | Ownership and paid-result durability | H2, H3 |
| PPH-1D | Hook latency | M1 |
| PPH-1E | Redaction and coverage | Low1, Low2 |
| PPH-1F | Lifecycle/status and schema atomicity | Low3, Low5 |
| PPH-1G | Supported profile identity | Low4 |
| PPH-1H | CLI diagnostics | Low7 |
| PPH-1I | Usage accounting (if B2 survives) | Low8 |
| PPH-1J | Hook-surface conformance | Low9 |

Exit criteria for PPH-1: each item has a failing test first, then a fix, then a
passing test at the preview/release candidate; H2/H3 additionally need a two-owner
integration receipt and a written no-loss/cancel policy. A fix is not done because
the code changed; it is done when the named receipt exists.

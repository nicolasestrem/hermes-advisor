# Public preview

This is an **untagged public source preview** of Hermes Advisor, version
`0.1.0a1`. It is not a v0.1 stable release, not a tagged release, and not a
statement that the v0.1 target is complete.

The preview exists so users and reviewers can inspect the implementation, run the
offline suites that are portable, and give feedback before a stable release. It
ships CLI-first. The Desktop pane is experimental and Copy-only.

> **Paid reviews are not guaranteed to be delivered.** H2 and H3 are
> evidence-limited races that can lose an in-flight or just-completed paid result
> when disable/configuration changes or a second owner re-discovers a session.
> H1 can silently produce no review when the host omits expected completion flags,
> and M2/M4 can under-report coverage or evict a snapshot. See
> [KNOWN-ISSUES.md](KNOWN-ISSUES.md). This preview makes **no guarantee that a
> paid review is never lost**, and it has no native current-candidate paid-review
> receipt.

## What is in the preview

- Plugin source: the host entry point, pure core, SQLite store, worker pool,
  dashboard API, and generated Desktop pane.
- CLI commands, as implemented (not the full target R10 surface).
- Offline Python suites and a portable Desktop harness. The clean-checkout
  commands and observed results are recorded in [EVIDENCE.md](EVIDENCE.md).
- Normative specification set under [`specs/`](specs/): target requirements,
  acceptance rows, decisions, and Hermes compatibility notes.
- The adapter contract in
  [`docs/CORE_API_FOR_ADAPTER.md`](docs/CORE_API_FOR_ADAPTER.md), preserved
  byte-for-byte. Its internal "verified" label and test count are historical and
  are not current-candidate verification.
- Ten synthetic frozen evaluation fixtures and their preparation README are
  included verbatim after hash verification and privacy scanning. The README's
  original freeze-register reference is historical; the same two public hashes
  are provided in [`tests/evaluation/FREEZE.sha256`](tests/evaluation/FREEZE.sha256).
  Its "G0–G4 NOT RUN" status line records the state at freeze time; G4 was later
  executed on the private baseline (see [specs/ACCEPTANCE.md](specs/ACCEPTANCE.md)).
  The private frozen corpus, adjudication artifacts, raw native receipts, and
  private evidence trees are **not** public. They are available on request where
  redaction and privacy review allow.

## What is not in the preview

- A stable or tagged release, an installable package on a registry, or a support
  commitment.
- The complete R10 CLI command surface.
- Desktop Add-to-draft.
- Windows support.
- Native current-candidate acceptance evidence, a real native review receipt, or
  proof of a packaged Desktop pane on this candidate.
- Any new paid inference, model procurement, or reviewer model/provider default.
- Private archive URLs, private evidence directories, raw receipts containing local
  paths, personal contact details, or host-specific paths.

## Target versus preview

The target specification remains binding. The preview differs in the following
ways; each is a documented deviation or known issue, not a relaxation.

| Area | Target | Preview implementation | Disposition |
|---|---|---|---|
| CLI (R10) | `sessions`, `on`, `off`, `status`, `notes`, `review`, `reset-allowance`, `copy`, `configure`, including single-key `configure ... set` | `metadata`, `status`, `notes`, `enable`, `disable`, `review_now`, `configure`, `rate`, `reset_allowance`, `feedback`, with `--session` and `--json` | **B1**; target stands |
| Desktop (R09/R11) | Native pane with Copy or exact-session draft append | Experimental pane; Copy only | Copy-only deviation; Add-to-draft unsupported |
| Hook surface (R03) | Observation/lifecycle hooks only | Registers `pre_llm_call`; callback returns `None` and does not transform the prompt | **Low9**; registration still deviates |
| Profile identity (R01) | Existing supported public APIs | Uses undocumented `profile_name_for_home` | **Low4**; unresolved |
| Completion signal | Review on a successful completed main answer | Requires `failed` and `interrupted` to be exactly `False`; missing flags mean no review | **H1**; deferred |
| Host/OS | Hermes-managed access on supported hosts | Structurally checked on Hermes `0.22.2` (Linux); no native reviewer run | Linux-only preview |
| Verification | A01–A35 proven by native receipts | 0/35 current-candidate; A03/A16/A17 no receipt at any revision | Not claimed |

## Verification status

The current candidate has **0 of 35** acceptance rows proven natively. No G0–G4
gate is passed at the current candidate. A03, A16, and A17 have no receipt at any
revision. The only native review receipt is historical, from an earlier revision,
and does not transfer.

Unit suites, fixture transports, offline runs, deterministic Desktop builds, and
structural host smoke are not native acceptance evidence. They are not promoted to
an A-row or a gate pass. Any current-host smoke recorded for this preview is
**structural only**: it can show that the plugin is present, disabled, and
registers its hooks/capabilities, but not that a real reviewer call or packaged
Desktop pane worked.

The authoritative current-run record is [EVIDENCE.md](EVIDENCE.md), with content
digest binding. This page does not invent or restate check counts, commit hashes,
or pass/fail results. Where a check is not recorded there, treat it as not run.

## Fix scope in this preview

This preview fixes **B2**, **H4**, **H5**, **M3**, **M5**, the HTTP control
contract and the worker lifecycle findings; [KNOWN-ISSUES.md](KNOWN-ISSUES.md)
lists each defect, its fix and its regression tests. B1, H1, H2, H3, M1, M2, M4
and the nine low-severity findings other than Low8 (fixed with B2) are deferred
to the named post-preview campaign and are **not** fixed by this preview.

## Compatibility

- **Host:** the preview manifest declares `requires_hermes: ">=0.22.2"` and was
  prepared against Hermes Agent `0.22.2` (build `v0.22.2+2606.gbd3cbe9`) on Linux. That is a
  floor and a development baseline, not a native install/review proof for every
  newer host. Current-host checks, if recorded, are structural only.
- **OS:** Linux only. The store uses `fcntl` advisory locks. On Windows the plugin
  can import, but session locking fails closed with `StoreError`; no Windows
  storage backend is provided.
- **Desktop:** experimental. The pane is a generated single-file ESM entry using
  host-provided React and `@hermes/plugin-sdk`. It is not a packaged-UI proof.
- **Reviewer:** any provider/model route the host can serve, selected explicitly by
  the user. No default, fallback, procurement, or cost guarantee.

## Data and uninstall

Attempts, findings, ratings, feedback actions, and configuration are stored in the
active profile's plugin-data directory. Uninstalling the plugin removes its code
but **does not delete local data**. If you intend to remove data, back it up and
inspect the exact directory first; deletion is permanent. Do not recursively delete
a home or profile directory. See
[docs/INSTALL.public.md](docs/INSTALL.public.md#uninstall-and-local-data).

## How to use this preview

1. Read [README.md](README.md) and [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
2. Install disabled at an exact commit and inspect the manifest with the commands
   in [docs/INSTALL.public.md](docs/INSTALL.public.md).
3. Use the CLI surface in [docs/CLI.public.md](docs/CLI.public.md). Do not assume
   target command spellings exist.
4. Treat the Desktop pane as experimental and Copy-only.
5. Report reproducible problems through [SUPPORT.md](SUPPORT.md), and security
   issues through [SECURITY.md](SECURITY.md).

## Preview exit criteria

A stable release would require, at minimum: the target CLI and Desktop scope
implemented or explicitly re-scoped through the decision process; the known
blockers/highs resolved with native receipts; A01–A35 re-proven at the release
candidate; A03/A16/A17 receipts created; clean supported install/uninstall evidence;
and a fresh verification record bound to the release content. This preview does
none of those and must not be described as if it did.

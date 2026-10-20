# Hermes Advisor

**Public source preview — untagged, not a v0.1 release.** CLI-first. Desktop is
experimental and Copy-only.

Hermes Advisor is a Hermes Agent plugin for evidence-linked review of a completed
assistant answer. After you explicitly configure and enable it for a session, it
captures the completed answer and paired tool evidence, asks the reviewer route
you selected for a bounded structured review, and stores findings you can inspect,
rate, and copy. It does not edit your answer, send anything, or inject review text
into the model context.

> **A paid review can still be lost.** The preview has evidence-limited races
> around session disable/re-discovery and in-flight transports (H2/H3), plus
> event-shape and retention limits (H1/M2/M4). Do not treat a review as guaranteed
> delivery or a billing record. See [KNOWN-ISSUES.md](KNOWN-ISSUES.md).

## Status

| Item | Preview state |
|---|---|
| Release | `0.1.0a1`, source only, **untagged**; not v0.1 stable |
| Primary surface | Hermes CLI (`/advisor`) |
| Desktop | Experimental, **Copy-only**; no Add-to-draft |
| Host baseline | Hermes Agent `0.22.2` (build `v0.22.2+2606.gbd3cbe9`), Linux; current-host install smoke only if recorded in EVIDENCE |
| Native acceptance | **0 of 35** current-candidate rows; A03, A16, A17 have no receipt at any revision |
| Paid native review | None for this preview |
| Supported OS | Linux only; Windows is not supported |

This preview claims no stable, verified, supported, or generally-available status.
The target specification is broader than the preview implementation; the
differences are documented deviations, not a relaxation of the target. Read
[PREVIEW.md](PREVIEW.md) before relying on it.

## What it does

- **Opt-in per session.** Discovery is metadata-only. Nothing is captured while a
  session is disabled or paused.
- **Explicit reviewer route.** You choose the provider and model. There is no
  reviewer model or provider default, and no fallback route.
- **Captured evidence only.** The reviewer receives a bounded packet built from
  the completed answer and paired tool calls/results, not your whole workspace.
- **Bounded, structured output.** Findings are validated locally, must cite packet
  evidence refs, and are stored with their coverage information.
- **User-controlled handoff.** Review text is returned for inspection and copying.
  Nothing is inserted, sent, or submitted automatically.
- **Local retention.** Attempts and findings live in the plugin data directory.
  The most recent 100 settled attempts per session are retained.
- **No telemetry.** The plugin does not send analytics or usage data anywhere
  except the reviewer call you configure.

## What it does not do

- It does **not** implement the full R10 CLI surface. The preview has a narrower,
  differently spelled command set; see [B1](KNOWN-ISSUES.md#b1--narrower-cli-than-r10).
- It does **not** implement Add-to-draft. Desktop is Copy-only.
- It does **not** work on Windows. The storage layer requires POSIX advisory locks.
- It does **not** provide native current-candidate acceptance evidence, a stable
  release, or a guarantee that every paid review is delivered.
- It does **not** ship the private frozen usefulness corpus.

## Install

Requirements: Linux, Hermes Agent `0.22.2` or newer, and an already-working CLI
session. The repository is source-only at this point; there is no tag or packaged
release.

Install disabled at an exact commit, inspect what was installed, then enable it:

```bash
hermes plugins install nicolasestrem/hermes-advisor \
  --ref <40_CHARACTER_COMMIT_SHA> --no-enable

hermes plugins show hermes-advisor
hermes plugins doctor --ci hermes-advisor
hermes plugins capabilities hermes-advisor
hermes plugins enable hermes-advisor
```

`hermes plugins show` reports status, source, key, isolation, emits, and listens.
It does **not** print hook or capability counts. `doctor` is the structural
registration check (the preview expects 0 tools and 5 hooks), and `capabilities`
shows the two declared `llm.*_override` grants against what you have granted.
Those are separate observations; none is a native review proof.

See [docs/INSTALL.public.md](docs/INSTALL.public.md) for the full procedure,
configuration examples, and uninstall/data-retention details.

## First session

1. Complete one normal answer so the plugin can discover the session from the
   host session context. The discovery answer itself is not reviewable later.
2. Find the session ID:
   `/advisor metadata`
3. Configure a complete settings envelope and reviewer route:
   `/advisor --session SESSION_ID configure --json '{"settings":{"packet_bytes":32768,"output_tokens":2048,"max_findings":4,"deadline_seconds":120,"session_allowance":20,"route":{"provider":"PROVIDER","model":"MODEL"}},"expected_revision":0}'`
4. Enable it for that session:
   `/advisor --session SESSION_ID enable`
5. On a later completed answer, inspect the result:
   `/advisor --session SESSION_ID status`
   `/advisor --session SESSION_ID notes`
   `/advisor --session SESSION_ID feedback --json '{"note_ids":["NOTE_ID"]}'`

Replace `PROVIDER`, `MODEL`, and IDs with your own values. The route must be a
provider/model your Hermes host can actually serve. Provider/model availability,
credentials, and cost are host concerns; the plugin does not choose or procure a
model.

## CLI surface (actual preview)

| Command | Behavior |
|---|---|
| `/advisor` or `/advisor help` | Help summary; never an implicit toggle |
| `/advisor metadata` | Instance, profile, and discovered session metadata |
| `/advisor --session ID status` | Session state, route/settings, budget, latest attempt, availability |
| `/advisor --session ID notes` | Stored findings as JSON |
| `/advisor --session ID enable` | Enable the session (requires a configured route) |
| `/advisor --session ID disable` | Disable the session and cancel local work |
| `/advisor --session ID review_now` | Queue a review of the latest eligible packet |
| `/advisor --session ID configure --json JSON` | Whole-configuration update with an explicit revision |
| `/advisor --session ID rate --json JSON` | Save `useful`, `not_useful`, or `not_rated` |
| `/advisor --session ID reset_allowance --json '{"confirm":true}'` | Confirmed allowance reset |
| `/advisor --session ID feedback --json '{"note_ids":[...]}'` | Return a Copy-only feedback block |

The full target surface (`sessions`, `on`, `off`, `review`, `copy`,
`reset-allowance`, and single-key `configure ... set`) is **not implemented** in
this preview. See [docs/CLI.public.md](docs/CLI.public.md) for parser details,
JSON quoting, exact parameter keys, and error behavior.

## Desktop

The Desktop pane is included as **experimental, Copy-only source**, not a
natively proven preview surface. Its implementation provides controls for status,
atomic settings updates, reviews, ratings, finding selection and clipboard copy.
It never inserts text into the composer, submits a message, or steers another
session. Add-to-draft remains a target requirement and is unsupported here.

Desktop code is a generated single-file `desktop/plugin.js` built from
`desktop/pane.mjs` and `desktop/logic.mjs`. The pane relies on host-provided
React and `@hermes/plugin-sdk`; no npm code is bundled.

## Privacy, cost, and retention

- Packet redaction and clipping are **best effort**. A secret pattern the
  sanitizer does not recognize can still reach the configured reviewer. Do not
  enable Advisor on sessions whose captured content you cannot send to that
  provider.
- A review can consume paid inference on the route you configure. The preview has
  no route default and no automatic fallback.
- Usage and cost fields can be unknown; a successful transport is not a
  model-quality claim.
- Plugin data stays under the host's plugin-data directory for the active
  profile. Uninstalling the plugin does **not** delete that data. See
  [SECURITY.md](SECURITY.md) and [docs/INSTALL.public.md](docs/INSTALL.public.md#uninstall-and-local-data).

## Verification

The authoritative record of what was actually run for this preview is
[EVIDENCE.md](EVIDENCE.md), with content digest binding. This README does not
restate check results, and no offline, fixture, Desktop-build, or structural-smoke
result is promoted to native acceptance.

Known limits, stated plainly:

- **0 of 35** acceptance rows are proven natively at the current candidate.
- **A03, A16, and A17** have no receipt at any revision.
- The only native review receipt is **historical**, from an earlier revision, and
  proves nothing about this candidate.
- Any current-host install/doctor/show smoke is **structural only**. It does not
  prove a real reviewer call, a packaged Desktop pane, or paid-review delivery.
- Testing is **Linux-only**. Windows is not supported. The storage layer needs
  POSIX `fcntl` locking; without it, the plugin imports but session locking fails
  closed with an explicit storage error. There is no Windows implementation.
- `docs/CORE_API_FOR_ADAPTER.md` is retained byte-for-byte as the adapter contract.
  Its "verified"/test-count label refers to an earlier revision and is not
  current-candidate evidence.

The target and acceptance contracts are in [specs/SPEC.md](specs/SPEC.md),
[specs/ACCEPTANCE.md](specs/ACCEPTANCE.md),
[specs/HERMES_COMPATIBILITY.md](specs/HERMES_COMPATIBILITY.md), and
[specs/DECISIONS.md](specs/DECISIONS.md).

## Documentation

- [PREVIEW.md](PREVIEW.md) — scope, verification limits, what is in and out.
- [docs/INSTALL.public.md](docs/INSTALL.public.md) — install, configure, uninstall,
  local data.
- [docs/CLI.public.md](docs/CLI.public.md) — actual parser, JSON quoting, actions.
- [docs/DESIGN.public.md](docs/DESIGN.public.md) — architecture, trust boundaries,
  storage, failure behavior.
- [docs/CORE_API_FOR_ADAPTER.md](docs/CORE_API_FOR_ADAPTER.md) — pure-core adapter
  contract (preserved).
- [KNOWN-ISSUES.md](KNOWN-ISSUES.md) — blockers, highs, mediums, lows, campaign.
- [SECURITY.md](SECURITY.md) — reporting and security model.
- [SUPPORT.md](SUPPORT.md) — where to ask, what to include.
- [CHANGELOG.md](CHANGELOG.md) — behavior deltas and preview history.
- [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) — dependencies and licenses.

## License

MIT. Copyright (c) 2026 nicolasestrem. See [LICENSE](LICENSE).

Repository: <https://github.com/nicolasestrem/hermes-advisor>

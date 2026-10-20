# Support

This is an untagged public source preview. Support is **best effort**, with no
SLA, no paid support, and no guarantee of a response time or fix.

## Where to ask

- **Bugs and usage questions:** open an issue at
  <https://github.com/nicolasestrem/hermes-advisor/issues>.
- **Security vulnerabilities:** do **not** open a public issue; follow
  [SECURITY.md](SECURITY.md).
- **Specification disagreement:** reference the relevant requirement/acceptance
  ID and the file in [`specs/`](specs/). The target contract is not amended by a
  support thread.

## Supported preview scope

| Surface | Support |
|---|---|
| Hermes CLI `/advisor` on Linux, Hermes `0.22.2` | Best effort |
| Current-host install/doctor/show structural checks | Best effort |
| Desktop pane | Experimental, Copy-only; best effort |
| Windows | **Unsupported** — no code path; do not file Windows port requests as regressions |
| Stable release / package registry | Not available |
| Paid reviewer/model procurement | Not provided |
| Private frozen corpus / raw native evidence | Not public; request with a privacy/redaction plan |

## What to include

Please provide enough to reproduce without exposing private data:

1. Preview commit or the exact installed source revision.
2. `hermes --version` output and Linux distribution/Python version.
3. Whether the failure is CLI, Desktop, install/metadata, or reviewer transport.
4. The exact command (redact session IDs if needed) and the sanitized error/status
   JSON.
5. The relevant `status` fields: `state`, `pause_reason`,
   `review_now_unavailable_reason`, `applied_revision`, `applied_generation`,
   `configuration_error`, `storage_error`.
6. Whether `hermes plugins doctor --ci hermes-advisor` passes, and what
   `hermes plugins capabilities hermes-advisor` shows. Do not claim `show` printed
   hook/capability counts; it does not.
7. For Desktop: host/Desktop version and whether the pane mounted; do not paste
   renderer console dumps containing private paths.

**Do not include:** credentials, tokens, full captured packets, private source
code, personal email addresses, private repository URLs, or unredacted screenshots.
A synthetic minimal reproduction is always preferred.

## Common answers

- **No session appears:** discovery is metadata-only and needs one ordinary
  completed answer from the supported surface. Use `/advisor metadata` (not the
  target-only `/advisor sessions`) to see what was discovered. The discovery answer
  is not reviewable later.
- **`enable` says a route is required:** configure a complete settings envelope
  with an explicit `route` first. There is no default reviewer model/provider.
- **`notes` is empty:** the review may not have been captured (H1), the snapshot may
  have expired/been evicted (M4), or the review may have been discarded by an
  ownership race (H2/H3). Check `status` and [KNOWN-ISSUES.md](KNOWN-ISSUES.md).
- **Desktop copy does nothing:** Desktop is experimental and Copy-only; clipboard
  permission is host-controlled. Use `feedback` on the CLI if the pane is not
  usable.
- **Windows install fails:** expected. Windows is not supported.
- **Uninstall left data behind:** expected. See
  [docs/INSTALL.public.md](docs/INSTALL.public.md#uninstall-and-local-data).

## What is not support

- Requests to bypass the target specification, the known-issue disclosures, or the
  no-guarantee statement.
- Requests for a reviewer model/provider recommendation, procurement, or spending
  authorization.
- Reports that a fixture/offline/structural check did not prove native acceptance.
  Those checks are explicitly not native proof.
- Debugging a modified fork without a minimal reproduction against the preview
  source.

## Status and verification

The authoritative record of what was actually run is [EVIDENCE.md](EVIDENCE.md).
This preview has **0/35** current-candidate native acceptance rows; A03, A16, and
A17 have no receipt at any revision. Historical native review receipts do not
transfer. Current-host smoke is structural only. Do not ask support to convert a
green offline or structural check into a native acceptance claim.

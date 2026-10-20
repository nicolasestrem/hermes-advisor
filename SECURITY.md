# Security policy

## Supported versions

This is an **untagged public source preview** (`0.1.0a1`). There is no stable
release line and no long-term security support commitment. Security reports are
accepted for the preview source and for the current host compatibility floor
(Hermes Agent `0.22.2`, Linux).

| Version | Supported |
|---|---|
| `0.1.0a1` preview source | Best effort |
| Any earlier/private revision | No |
| Windows | No — unsupported platform |
| Hermes Agent itself | Report upstream to the Hermes project |

## Reporting a vulnerability

**Do not open a public issue for a vulnerability.** Use GitHub's private
vulnerability reporting flow for the repository:

<https://github.com/nicolasestrem/hermes-advisor/security/advisories/new>

If that flow is unavailable, open a minimal public issue asking for a private
channel and include no vulnerability details. Do not send secrets, captured packet
content, credentials, tokens, private code, or personal data in the first contact.

A useful report includes:

- affected preview revision/commit and host version;
- Linux distribution and Python version;
- a minimal reproduction with synthetic content;
- the security impact and the trust boundary crossed;
- whether the reviewer route was called and whether any content left the host;
- any suggested fix, if you have one.

There is no bug bounty and no SLA. Reports are handled on a best-effort basis.
Please allow time for a fix before public disclosure; coordinated disclosure is
appreciated. Credit is given if you want it, using a public handle you provide.

## Security model

Hermes Advisor is a plugin, not a sandbox. Its security properties depend on the
host's plugin isolation and on the reviewer route you configure.

- **Opt-in.** A session is discovered from host metadata, but review is disabled
  until you configure a route and enable that session.
- **Capabilities are consent, not confinement.** The manifest declares
  `llm.provider_override` and `llm.model_override`. These are audited host
  capabilities; they do not sandbox plugin code, filesystem access, or network
  access.
- **Captured evidence only.** The plugin sends the reviewer a bounded packet built
  from the completed answer and paired tool calls/results. It does not send your
  whole workspace, shell history, or unrelated sessions.
- **Redaction is best effort.** Recognized credential patterns are removed, but an
  unrecognized secret can still reach the configured reviewer. Do not enable
  Advisor on content you are unwilling to send to that provider.
- **Prompt injection is treated as data.** Reviewer instructions say to treat
  captured text as evidence, never instructions, and to avoid tool use, agent
  steering, or action execution. This reduces risk; it does not make hostile
  captured content safe.
- **No automatic action.** Findings are returned for inspection and copying. The
  plugin does not edit the answer, insert into the composer, submit a message, or
  steer another session.
- **Local storage.** Attempts, findings, ratings, feedback actions, and
  configuration are stored in the active profile's plugin-data directory. The
  store uses WAL mode and POSIX advisory locks with owner tokens. Uninstalling the
  plugin does not delete this data.
- **No analytics.** The plugin does not include telemetry or analytics. Network
  activity is the reviewer call you configure, plus any host behavior outside the
  plugin's control.

## Known security-relevant limitations

These are disclosed in [KNOWN-ISSUES.md](KNOWN-ISSUES.md) and are not fixed by this
preview:

- **H2/H3** — ownership races can lose an in-flight or just-completed paid review.
  This is an availability/integrity issue, not a confidentiality issue.
- **H1** — absent completion flags can silently suppress review.
- **M1** — synchronous storage work on hook threads can stall the host UI.
- **M2** — pairing limitations are not reported, so coverage can mislead.
- **M4** — the global 16-snapshot cap can evict another session's review.
- **Low1/Low2** — Unicode lowercasing can misalign redaction spans; redaction and
  clipping are conflated in coverage.
- **Low4/Low9** — undocumented profile helper and a `pre_llm_call` registration
  that deviates from the target hook surface.
- **B1** — the CLI is narrower than R10; it is a scope deviation, not a privilege
  boundary.
- **Windows** — not supported; the POSIX `fcntl` storage import is a hard platform
  boundary.

No guarantee is made that a paid review is never lost. Keep independent records of
important reviews and do not use Advisor as a billing, audit, or safety system.

## Data handling and deletion

Data lives under the host's plugin-data directory for the active profile. Paths
are host- and profile-specific; use the host's plugin data location rather than a
guessed global path. Before deleting anything:

1. stop using the plugin and uninstall/disable it;
2. back up the profile if you may need the records;
3. inspect the exact plugin-data directory and confirm it is the Advisor directory
   for the intended profile;
4. delete only that directory, never a home or profile directory.

Uninstalling the plugin alone does **not** remove the data. See
[docs/INSTALL.public.md](docs/INSTALL.public.md#uninstall-and-local-data).

## Out of scope

- Vulnerabilities in Hermes Agent, the host plugin loader, the Desktop runtime, or
  the configured model provider. Report those upstream.
- Model output quality or prompt-injection outcomes that do not cross a security
  boundary. These are product/quality issues; see [SUPPORT.md](SUPPORT.md).
- Attacks that require an already-compromised host account or plugin code
  execution.
- Social engineering, denial of service from an intentionally hostile local
  filesystem, or physical access.

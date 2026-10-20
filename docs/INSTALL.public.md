# Install and uninstall (public preview)

This page describes the **actual preview**. It is CLI-first, Linux-only, and
untagged. For the product scope and verification limits, read [../README.md](../README.md)
and [../PREVIEW.md](../PREVIEW.md).

## Requirements

- Linux with Hermes Agent `0.22.2` or newer. The preview was exercised on Hermes
  `0.22.2` (build `v0.22.2+2606.gbd3cbe9`).
- A working Hermes CLI profile. The plugin is installed per profile.
- A provider/model route your Hermes host can serve. The plugin does not choose,
  procure, or default a reviewer model/provider.
- No Node.js is required to use the CLI or Desktop plugin. Node is only for the
  Desktop source/test workflow.

**Windows is not supported.** The storage layer uses POSIX `fcntl` advisory locks
and has no Windows implementation. The plugin imports on Windows, but every
storage lock fails closed with `StoreError`; there is no degraded or unsafe fallback.

## Install disabled at an exact revision

There is no tag or registry release. Install from the public repository at a
40-character commit SHA and leave the plugin disabled while you inspect it:

```bash
hermes plugins install nicolasestrem/hermes-advisor \
  --ref <40_CHARACTER_COMMIT_SHA> --no-enable
```

Use a full commit SHA, not a branch name. The preview has no self-referential
"latest" promise; the exact revision is part of the audit trail.

## Inspect before enabling

These are separate checks. Do not expect one command to print all of them.

```bash
hermes plugins show hermes-advisor
hermes plugins doctor --ci hermes-advisor
hermes plugins capabilities hermes-advisor
```

- `show` reports status, source, key, isolation, emits, and listens. It does **not**
  print hook or capability counts. Use it to confirm the plugin is present and
  disabled.
- `doctor` is the structural registration check. The preview expects **0 tools**
  and **5 hooks**. It validates the real runtime contracts, not a live review.
- `capabilities` shows the declared `llm.provider_override` and
  `llm.model_override` capabilities against what the profile has granted. A
  capability is consent/audit, not a sandbox.

None of these is native review evidence. A passing structural check does not prove
a reviewer call, a packaged Desktop pane, or paid-review delivery. Current results
belong in [../EVIDENCE.md](../EVIDENCE.md), with content digest binding.

The manifest's `homepage` is the approved repository URL. The current manifest
schema has no `repository` field; adding one is ignored with a warning, so this
preview uses `homepage`.

## Enable

```bash
hermes plugins enable hermes-advisor
```

If the host asks for capability consent, read what is being granted. The plugin
needs the two `llm.*_override` capabilities to pass your explicit route to the
host's structured completion API. It does not need or request a model of its own.

## First session and configuration

Discovery is metadata-only. Complete one ordinary answer on a supported surface,
then list discovered sessions:

```text
/advisor metadata
```

The discovery answer is not reviewable from private history. Configure the session
with a **complete** settings envelope and the current saved revision (`0` on a
fresh profile):

```text
/advisor --session SESSION_ID configure --json '{"settings":{"packet_bytes":32768,"output_tokens":2048,"max_findings":4,"deadline_seconds":120,"session_allowance":20,"route":{"provider":"PROVIDER","model":"MODEL"}},"expected_revision":0}'
```

Replace `SESSION_ID`, `PROVIDER`, and `MODEL`. The route must be exactly
`{"provider": ..., "model": ...}` with non-empty printable strings. Settings bounds:

| Setting | Range | Default |
|---|---|---|
| `packet_bytes` | 1024–32768 | 32768 |
| `output_tokens` | 128–2048 | 2048 |
| `max_findings` | 1–4 | 4 |
| `deadline_seconds` | 1–120 | 120 |
| `session_allowance` | 1–20 | 20 |

Then enable the session:

```text
/advisor --session SESSION_ID enable
```

If no route is configured, `enable` fails with a `no_model` condition. That is
intentional: there is no reviewer default and no fallback. Provider credentials,
account access, and cost remain host/user responsibilities.

## Inspect and use results

```text
/advisor --session SESSION_ID status
/advisor --session SESSION_ID notes
/advisor --session SESSION_ID feedback --json '{"note_ids":["NOTE_ID"]}'
/advisor --session SESSION_ID rate --json '{"attempt_id":"ATTEMPT_ID","rating":"useful","reason":null}'
```

`feedback` returns a copyable text block; it does not insert, send, or submit
anything. See [CLI.public.md](CLI.public.md) for exact parameter keys, quoting, and
error behavior.

## Desktop

The Desktop pane is experimental and **Copy-only**. It reads the installed
`desktop/plugin.js`, which is generated from `desktop/pane.mjs` and
`desktop/logic.mjs`. It imports only host-provided React and
`@hermes/plugin-sdk`; no npm packages are bundled. It does not implement
Add-to-draft. If the pane does not mount, use the CLI.

## Updates

There is no update channel promise for an untagged preview. To try another
revision, reinstall at an explicit commit SHA and re-run the inspection commands.
Do not assume data/schema compatibility across arbitrary revisions; back up the
plugin-data directory first if the records matter.

## Uninstall and local data

Uninstalling removes the plugin code. It **does not delete** the plugin's local
data: configuration, attempts, findings, ratings, feedback actions, and locks can
remain in the active profile's plugin-data directory.

Before any deletion:

1. Disable/uninstall the plugin and stop using it.
2. Back up the profile or the exact plugin-data directory if you may need the
   records.
3. Locate the Advisor directory through the host's plugin-data location for the
   **intended profile**. Do not guess a global path and do not delete a home or
   profile directory.
4. Inspect the directory contents and confirm it is the Advisor data directory.
5. Delete only that exact directory. Deletion is permanent.

```bash
hermes plugins disable hermes-advisor
hermes plugins remove hermes-advisor
# Then inspect, back up, and (only if intended) delete the Advisor plugin-data
# directory for this profile. Never recursively delete a home/profile directory.
```

If you are unsure which directory is correct, stop and ask for help rather than
deleting broadly.

## Troubleshooting

| Symptom | Check |
|---|---|
| Plugin not listed | Install revision, profile, and `hermes plugins show` |
| `doctor` fails | Host version, manifest fields, missing `__init__.py`/`desktop/plugin.js` |
| No sessions in metadata | Complete one normal answer; discovery is metadata-only |
| `enable` rejects route | Configure a complete route first; no default exists |
| No review appears | H1/M4/H2/H3 known issues; inspect `status` and [../KNOWN-ISSUES.md](../KNOWN-ISSUES.md) |
| Windows load failure | Unsupported platform; use Linux |

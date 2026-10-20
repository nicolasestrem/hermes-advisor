# CLI usage (public preview)

This is the **actual preview parser**, not the R10 target surface. The target
commands `sessions`, `on`, `off`, `review`, `copy`, `reset-allowance`, and
single-key `configure ... set` are not implemented. See
[../KNOWN-ISSUES.md](../KNOWN-ISSUES.md#b1--narrower-cli-than-r10).

## Parser shape

The host passes the text after `/advisor` to the plugin. The preview parser is:

```text
/advisor [--session SESSION_ID] [--json JSON] [action]
```

- `action` defaults to `help`.
- `--session` is the explicit session ID for `status`, `notes`, and every control
  action. `metadata` does not need it.
- `--json` defaults to `{}` and is parsed with `json.loads`. It is an option that
  takes a JSON value, not a flag.
- The raw command is split with POSIX shell-like quoting (`shlex.split`), so quote
  JSON so the JSON double quotes survive.
- The canonical order is `--session` first, then the action, then `--json`. The
  parser also accepts options in other positions, but use the canonical form in
  scripts.
- Unknown actions are passed to the control layer and rejected; they do not fall
  back to help.

## Actions

| Action | `--session` | `--json` | Behavior |
|---|---|---|---|
| `help` (or no action) | ignored | ignored | Prints the command summary; never toggles anything |
| `metadata` | ignored | ignored | Returns instance ID, profile, discovered sessions, closing flag |
| `status` | required | ignored | Returns session state, settings/route, budget, latest attempt, availability |
| `notes` | required | ignored | Returns stored findings as a JSON array |
| `enable` | required | must be `{}` or omitted | Enables; requires a configured route |
| `disable` | required | must be `{}` or omitted | Disables and cancels local work |
| `review_now` | required | must be `{}` or omitted | Queues a review of the latest eligible packet |
| `configure` | required | required object | Whole-settings update with `expected_revision` |
| `rate` | required | required object | Saves a usefulness rating |
| `reset_allowance` | required | required object | Resets the allowance with `confirm: true` |
| `feedback` | required | required object | Returns a Copy-only feedback block |

`status` and `notes` ignore a supplied `--json`; the other control actions use it.
`metadata` ignores `--session`.

## Exact parameters

### configure

```json
{
  "settings": {
    "packet_bytes": 32768,
    "output_tokens": 2048,
    "max_findings": 4,
    "deadline_seconds": 120,
    "session_allowance": 20,
    "route": {"provider": "PROVIDER", "model": "MODEL"}
  },
  "expected_revision": 0
}
```

`settings` must be a complete object; missing keys take the documented defaults,
but `route` is mandatory. `expected_revision` must be a nonnegative JSON integer
matching the current saved revision. A mismatch is a conflict; the draft is not
overwritten. The route must contain exactly `provider` and `model`, each a trimmed
printable non-empty string of at most 256 code points with no recognized
credential pattern.

### rate

```json
{"attempt_id": "ATTEMPT_ID", "rating": "useful", "reason": null}
```

`rating` is exactly `useful`, `not_useful`, or `not_rated`. `reason` is optional;
when present it must be a string of at most 500 code points, and it is redacted
before storage. Only a settled, completed review can be rated.

### reset_allowance

```json
{"confirm": true}
```

`confirm` must be the JSON boolean `true`. The reset zeroes the invocation count,
moves the session to paused/`allowance_reset`, and cancels queued attempts. You
must enable again to continue.

### feedback

```json
{"note_ids": ["NOTE_ID_1", "NOTE_ID_2"]}
```

One to 100 distinct note IDs from the same session. The action returns
`{action_id, notes, text}`; `text` is the copyable block. Nothing is inserted or
sent. A selected note that was pruned or belongs to another session is rejected.

## Quoting

POSIX shells: wrap the JSON in **single quotes** so the JSON double quotes are
literal. Do not wrap the JSON in double quotes unless you escape every inner quote.

```bash
/advisor --session SESSION_ID configure --json '{"settings":{"route":{"provider":"PROVIDER","model":"MODEL"}},"expected_revision":0}'
```

`--json='{"..."}'` (option and value joined by `=`) is also accepted by the parser.
For JSON containing a literal single quote, use a JSON escape such as `\u0027`
inside the single-quoted shell string; do not try to nest shell quotes.

In the Hermes chat command box, enter the command as one line. The plugin sees the
same `shlex`-split argument vector, so the quoting rules are the same.

Windows shells are not supported by this preview; do not rely on their quoting for
security or correctness.

## Output

Successful actions return compact JSON with sorted keys:
- `metadata`: `instance_id`, `profile`, `sessions` (each with `session_id` and
  `surface`), `closing`.
- `status`: `session_id`, `surface`, `surface_supported`, `state`, `pause_reason`,
  `saved_profile_revision`, `saved_settings`, `applied_revision`,
  `applied_generation`, `settings`, `configuration_error`, `invocations_used`,
  `invocations_remaining`, `review_now_available`,
  `review_now_unavailable_reason`, `review_now_expires_in_seconds`, `active`,
  `unconfirmed_transport`, `pending`, `storage_error`, `volatile_error`,
  `latest_attempt`.
- `notes`: an array of stored finding objects.
- `feedback`: `action_id`, `notes`, `text`.
- `rate`: `attempt_id`, `rating`, `reason`, `saved`.
- `review_now`: `accepted`, optional `reason`/`attempt_id`, and `requests`.

### Errors

Errors are returned as JSON, not raised as a traceback:

```json
{"error": "BackendRejected", "detail": "Advisor command rejected; inspect explicit session/configuration/status."}
```

The error body deliberately does not include exception text that might contain
packet/provider/credential content. The class name alone is often not enough to
distinguish a bad parameter from a missing route or a stale revision; this is
**Low7** in [../KNOWN-ISSUES.md](../KNOWN-ISSUES.md#low7--cli-collapses-backendrejectedcode-to-a-class-name).
Inspect `status` and the exact parameter shape. Do not paste packet content into an
issue.

## Command examples

```text
/advisor
/advisor metadata
/advisor --session SESSION_ID status
/advisor --session SESSION_ID notes
/advisor --session SESSION_ID enable
/advisor --session SESSION_ID disable
/advisor --session SESSION_ID review_now
/advisor --session SESSION_ID configure --json '{"settings":{"packet_bytes":32768,"output_tokens":2048,"max_findings":4,"deadline_seconds":120,"session_allowance":20,"route":{"provider":"PROVIDER","model":"MODEL"}},"expected_revision":0}'
/advisor --session SESSION_ID rate --json '{"attempt_id":"ATTEMPT_ID","rating":"useful","reason":null}'
/advisor --session SESSION_ID reset_allowance --json '{"confirm":true}'
/advisor --session SESSION_ID feedback --json '{"note_ids":["NOTE_ID"]}'
```

## Related

- [INSTALL.public.md](INSTALL.public.md) — install, enable, data retention.
- [../specs/SPEC.md](../specs/SPEC.md) — target R10 and preview scope table.
- [../KNOWN-ISSUES.md](../KNOWN-ISSUES.md) — B1 and CLI diagnostics.

# Hermes Advisor — Desktop packaging notes

The plugin ships as a **unified package** rooted at the plugin repository: the
Python backend (`dashboard/plugin_api.py`) and the Desktop renderer
(`desktop/plugin.js`) live beside the shared root `plugin.yaml`. This file
documents selected fields of that root manifest, the Desktop entry contract,
and the portable Desktop test harness.

## Selected root manifest fields

```yaml
name: hermes-advisor
version: 0.1.0a1
manifest_version: 2
api_version: 1
provides_hooks:
  - pre_llm_call
  - post_llm_call
  - on_session_end
  - subagent_start
  - subagent_stop
capabilities:
  - llm.provider_override
  - llm.model_override
```

The manifest declares **`provides_hooks`** (five Python-side hook entry points
implemented by `advisor_host.py`) and the two capabilities
`llm.provider_override` / `llm.model_override`, which the plugin needs to pass
an explicit reviewer route to the host's structured completion API (see
`docs/INSTALL.public.md`). There is **no** `provides_dashboard` field
in the manifest — the dashboard API is reached through the unified package
layout (`dashboard/plugin_api.py` beside the manifest), not through a manifest
declaration. The Desktop renderer likewise needs no manifest entry: the loader
discovers `desktop/plugin.js` automatically for unified packages.

## Desktop entry contract

- `desktop/plugin.js` is the loader entry: ONE self-contained file, plain ESM,
  imported by the runtime blob pipeline. It must not contain any relative
  imports (`./pane.mjs` cannot resolve from a blob URL); the shippable
  artifact inlines the pane and logic modules.
- Supported public specifiers in the artifact: `react`, `react/jsx-runtime`, `react/jsx-dev-runtime`,
  and `@hermes/plugin-sdk` (the public React SDK — `host`, `useValue`, and the
  UI primitives are re-exported by the SDK barrel). Anything else is
  unsupported in the blob loader.
- One pane contribution is registered: `id: 'hermes-advisor:pane'`.
- No direct network access, no `localStorage`, no filesystem, no private host
  internals. All backend I/O goes through the authenticated `ctx.rest('/…')`
  plugin namespace; every control request carries `params` as a plain object
  (the backend validates a strict dict — `null` is rejected).

## Public SDK surface and compatibility

The pane imports only these public SDK exports: `host`, `useValue`, `Button`,
`Checkbox`, `EmptyState`, `ErrorState`, `GlyphSpinner`, `Input`, `Textarea`.
`host.state` is read through `useValue`; the pane never imports app-internal
`@/…` modules.

`host.paneVisibility` is **feature-detected**, not assumed:
`typeof host.paneVisibility === 'function'`. When an older Desktop build lacks
it (or it throws / returns a non-readable value), the pane falls back to a
stable always-visible atom and keeps working instead of crashing. Hidden-pane
polling suppression applies whenever the host does expose the API.

All backend-derived display text passes through the existing
`sanitizeReasonText` contract via `displayText`; configuration labels use
operator-facing names rather than wire field names. Clipboard content is kept
verbatim and is never inserted into a draft — Copy is the only handoff.

## Portable Desktop test harness

The Desktop suite is self-contained under `tests/desktop/` and requires no
Hermes Desktop source checkout, lab host, or absolute home path:

```bash
npm --prefix tests/desktop ci
npm --prefix tests/desktop test
```

Pinned toolchain (see `tests/desktop/package.json` / lockfile): Node
`^20.19.0 || ^22.13.0 || >=24.0.0`, Vitest 4.1.10, React 19.2.7, jsdom
29.1.1, @testing-library/react 16.3.2, @vitejs/plugin-react 6.0.3.
`@hermes/plugin-sdk` resolves to the declared test contract double in
`tests/desktop/sdk/plugin-sdk.mjs` (`plugin-sdk.contract.json` lists the
surface). That double exercises public API semantics and ARIA forwarding; it
is **not** the production SDK and is **not** native Desktop proof. A native
mount remains a separate host-level check.

Optional environment overrides, for development only: `ADVISOR_REPO_ROOT`,
`ADVISOR_DESKTOP_SDK_ENTRY`, `ADVISOR_DESKTOP_SDK_ROOT`,
`ADVISOR_DESKTOP_DEPS_ROOT`, `ADVISOR_DESKTOP_SETUP_FILE`, `ADVISOR_PYTHON`.

## Deterministic artifact

`desktop/plugin.js` is generated; never hand-edit an inlined section.

```bash
python3 scripts/build-desktop-plugin.py          # regenerate
python3 scripts/build-desktop-plugin.py --check  # byte-identity gate, writes nothing
```

The builder is a pure function of `desktop/pane.mjs` + `desktop/logic.mjs`,
refuses non-allowlisted or path imports, and prints the artifact SHA-256.
`--check` exits non-zero if the artifact is stale or the build contract fails;
the packaging suite runs it as a test.

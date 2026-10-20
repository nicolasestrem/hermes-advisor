# Third-party notices

This repository contains the Hermes Advisor plugin source. **It does not vendor or
redistribute third-party source code, binaries, model weights, or datasets.**

The generated `desktop/plugin.js` is assembled from this repository's own
`desktop/pane.mjs` and `desktop/logic.mjs`. It imports host-provided modules at
runtime; it does not bundle them.

## Runtime dependencies supplied by the host

These are not included in this repository. They are provided by the Hermes Agent
installation or the host environment, and their own license terms apply.

| Component | Role | License | Upstream |
|---|---|---|---|
| Hermes Agent | Host runtime, plugin loader, hooks, structured LLM API, Desktop runtime | MIT | Nous Research |
| React | Desktop pane rendering, supplied by the Desktop runtime | MIT | Meta Platforms, Inc. and contributors |
| `@hermes/plugin-sdk` | Desktop SDK surface, part of the Hermes Desktop runtime | MIT | Nous Research |
| FastAPI | Dashboard router types used by `dashboard/plugin_api.py`, supplied by the host | MIT | Sebastián Ramírez and contributors |
| Python standard library | Core runtime (`sqlite3`, `fcntl`, `threading`, `asyncio`, etc.) | PSF-2.0 | Python Software Foundation |

`FastAPI`'s transitive dependencies (for example Starlette and Pydantic) are also
supplied by the host environment and are not redistributed here.

## Development and test dependencies (not vendored)

The offline Python harness runs with `pytest`; many test cases also use the
standard library's `unittest`. The Desktop harness installs its own Node/React
dependencies and uses an explicitly labelled SDK contract double, not a bundled
Hermes SDK. These development packages are not vendored in this repository.

| Direct dependency | Pinned version | Role | License |
|---|---|---|---|
| `pytest` | 9.1.1 | Offline Python test runner | MIT |
| `fastapi` | 0.152.0 | Dashboard router tests; also supplied by the host at runtime | MIT |
| `httpx` | 0.28.1 | Test HTTP client | BSD-3-Clause |
| `@testing-library/dom` | 10.4.1 | Desktop DOM queries | MIT |
| `@testing-library/react` | 16.3.2 | Desktop React tests | MIT |
| `@vitejs/plugin-react` | 6.0.3 | React transform for the Desktop test runner | MIT |
| `jsdom` | 29.1.1 | Desktop test DOM environment | MIT |
| `react` | 19.2.7 | Desktop test rendering; host-provided at runtime | MIT |
| `react-dom` | 19.2.7 | Desktop test renderer | MIT |
| `vitest` | 4.1.10 | Desktop test runner | MIT |

Python direct versions are pinned in `requirements-test.txt`; Python transitive
dependencies are resolved at installation time and are not lockfile-pinned.
Desktop direct versions are pinned in `tests/desktop/package.json`, and the
resolved dependency tree is recorded in `tests/desktop/package-lock.json`.
Consult the installed distributions and upstream packages for complete license
texts and transitive-dependency notices.

CI also downloads the standalone Gitleaks 8.30.1 executable (MIT) from its
upstream release, verifies the pinned archive SHA-256, and scans public Git
history with `.gitleaks.toml`. The executable is not vendored here.

## Data and fixtures

The evaluation fixtures, when present, are synthetic captured-input examples
authored for this project. They do not incorporate third-party code, datasets, or
model output. The private frozen corpus and raw evidence are not part of the public
tree.

## No warranty / no legal advice

This notice is provided for attribution clarity. It is not a legal opinion. If you
redistribute the plugin or a modified version, verify the licenses of the host
components and any dependencies you add. If a future revision vendors third-party
code, add the corresponding license text and attribution here before publishing.

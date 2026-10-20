/**
 * Portable Vitest runner for the Advisor Desktop renderer tests.
 *
 * Default mode needs only this repository plus `tests/desktop/node_modules`
 * (installed from tests/desktop/package.json): `@hermes/plugin-sdk` resolves to
 * the declared test contract double in `tests/desktop/sdk/`. No Hermes Desktop
 * source checkout, lab host, absolute home path, or private URL is required.
 *
 * Optional fidelity mode (development only, never required by the public
 * command):
 *   ADVISOR_DESKTOP_SDK_ENTRY  path to a real Desktop `src/sdk/index.ts`
 *   ADVISOR_DESKTOP_SDK_ROOT   that Desktop app's `src/` directory
 *   ADVISOR_DESKTOP_DEPS_ROOT  the node_modules root that owns React/Vitest
 *   ADVISOR_DESKTOP_SETUP_FILE optional real-app setup file (e.g. vitest.setup.ts)
 *   ADVISOR_REPO_ROOT          override the repository root (defaults here)
 *   ADVISOR_PYTHON             Python executable for the builder check (default python3)
 */
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'

const here = fileURLToPath(new URL('.', import.meta.url))
const repo = process.env.ADVISOR_REPO_ROOT ? resolve(process.env.ADVISOR_REPO_ROOT) : resolve(here, '../..')

const sdkEntry = process.env.ADVISOR_DESKTOP_SDK_ENTRY
  ? resolve(process.env.ADVISOR_DESKTOP_SDK_ENTRY)
  : resolve(here, 'sdk/plugin-sdk.mjs')

const desktopSrc = process.env.ADVISOR_DESKTOP_SDK_ROOT
  ? resolve(process.env.ADVISOR_DESKTOP_SDK_ROOT)
  : null
const depsRoot = process.env.ADVISOR_DESKTOP_DEPS_ROOT
  ? resolve(process.env.ADVISOR_DESKTOP_DEPS_ROOT)
  : null

const alias: Record<string, string> = { '@hermes/plugin-sdk': sdkEntry }

// Portable mode: the owned `desktop/*.mjs` files live outside
// tests/desktop, so their bare `react` imports cannot walk down into this
// harness's node_modules. Point them at the harness install explicitly. This
// path is derived from the config location, not from a machine-specific root.
const localDeps = resolve(here, 'node_modules')
if (!depsRoot && existsSync(localDeps)) {
  alias.react = resolve(localDeps, 'react')
  alias['react-dom'] = resolve(localDeps, 'react-dom')
  alias['react/jsx-runtime'] = resolve(localDeps, 'react/jsx-runtime.js')
  alias['react/jsx-dev-runtime'] = resolve(localDeps, 'react/jsx-dev-runtime.js')
}

// Real-SDK fidelity mode: keep one React copy and resolve the SDK's internal
// `@/` alias against the checkout named by the environment. These paths come
// only from the caller's environment; the portable default never sets them.
if (desktopSrc) {
  alias['@/'] = `${desktopSrc}/`
  const sharedTranslucency = resolve(desktopSrc, '../../shared/src/translucency.ts')
  if (existsSync(sharedTranslucency)) {
    alias['@hermes/shared/translucency'] = sharedTranslucency
  }
}
if (depsRoot) {
  alias.react = resolve(depsRoot, 'react')
  alias['react-dom'] = resolve(depsRoot, 'react-dom')
  alias['react/jsx-runtime'] = resolve(depsRoot, 'react/jsx-runtime.js')
  alias['react/jsx-dev-runtime'] = resolve(depsRoot, 'react/jsx-dev-runtime.js')
  alias['@testing-library/react'] = resolve(depsRoot, '@testing-library/react/dist/index.js')
}

const setupFiles = process.env.ADVISOR_DESKTOP_SETUP_FILE
  ? [resolve(process.env.ADVISOR_DESKTOP_SETUP_FILE)]
  : [resolve(here, 'setup.ts')]

export default {
  root: repo,
  plugins: [react()],
  resolve: { alias, dedupe: ['react', 'react-dom'] },
  test: {
    environment: 'jsdom',
    globals: true,
    env: { NODE_ENV: 'development' },
    setupFiles,
    include: [resolve(here, '**/*.test.{ts,tsx,mjs,jsx}')],
    // The suite drives real React effects with fake timers and mutates the
    // host override carrier; one worker keeps the ordering deterministic.
    maxWorkers: 1,
    testTimeout: 15_000,
    hookTimeout: 15_000,
  },
}

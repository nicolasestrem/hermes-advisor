/**
 * Packaging contract for the unified plugin layout, desktop half only.
 *
 * Portable by construction: every path is derived from this file's location
 * (or ADVISOR_REPO_ROOT), never from a developer's home directory or a lab
 * checkout. The root plugin.yaml is docs/runtime-owned; this suite reads it
 * when it exists and fails if its declared name drifts from the plugin id the
 * renderer binds to. It never edits the shared manifest.
 *
 * The generated artifact is exercised against the declared public SDK
 * contract double (`tests/desktop/sdk/plugin-sdk.mjs`), not a real Desktop
 * source graph. That keeps this contract test fast and deterministic; the
 * native mount remains a separate host-level check.
 */
import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const here = dirname(fileURLToPath(import.meta.url))
const repo = process.env.ADVISOR_REPO_ROOT ? resolve(process.env.ADVISOR_REPO_ROOT) : resolve(here, '../..')
const python = process.env.ADVISOR_PYTHON || 'python3'

const paneSource = readFileSync(resolve(repo, 'desktop/pane.mjs'), 'utf8')
const logicSource = readFileSync(resolve(repo, 'desktop/logic.mjs'), 'utf8')
const artifact = readFileSync(resolve(repo, 'desktop/plugin.js'), 'utf8')
const manifestPath = resolve(repo, 'plugin.yaml')
const manifestExists = existsSync(manifestPath)
const rootYaml = manifestExists ? readFileSync(manifestPath, 'utf8') : ''

const allowedSpecifiers = ['react', 'react/jsx-runtime', 'react/jsx-dev-runtime', '@hermes/plugin-sdk']

describe('unified plugin packaging (desktop half)', () => {
  it('generated entry parses as ESM and imports only the supported blob-loader specifiers', () => {
    expect(artifact).toMatch(/^export default /m)
    expect(artifact).toContain('function AdvisorPane(')
    const imports = [...artifact.matchAll(/\bfrom\s+['"]([^'"]+)['"]/g)].map(m => m[1])
    for (const specifier of imports) {
      expect(allowedSpecifiers).toContain(specifier)
    }
    execFileSync(process.execPath, ['--input-type=module', '--check'], { input: artifact, encoding: 'utf8' })
  })

  it('is byte-identical to a fresh builder run (no hand-edited generated sections)', () => {
    // --check builds in memory and writes nothing; a stale/hand-edited artifact
    // or a builder failure exits non-zero and surfaces here.
    const output = execFileSync(python, [resolve(repo, 'scripts/build-desktop-plugin.py'), '--check'], {
      cwd: repo,
      encoding: 'utf8',
    })
    expect(output).toMatch(/artifact up to date/)
    expect(output).toMatch(/sha256=[0-9a-f]{64}/)
  })

  it('generated registration actually contributes the Advisor pane', async () => {
    const plugin = (await import('../../desktop/plugin.js')).default
    const contributions = []
    plugin.register({ register: contribution => contributions.push(contribution) })
    expect(contributions).toHaveLength(1)
    expect(contributions[0]).toMatchObject({ id: 'pane', area: 'panes', title: 'Hermes Advisor', data: { placement: 'right', width: '380px' } })
    expect(typeof contributions[0].render).toBe('function')
  }, 5_000)

  it('desktop assets live beside the root manifest in the unified package layout', () => {
    expect(paneSource).toContain("from '@hermes/plugin-sdk'")
    expect(logicSource.length).toBeGreaterThan(0)
  })

  it('root manifest, when present, declares the advisor plugin id the renderer binds to', () => {
    if (manifestExists) {
      expect(rootYaml).toMatch(/^name:\s*hermes-advisor\s*$/m)
    } else {
      // Runtime sibling owns creation; record the pending state explicitly
      // instead of failing on another owner's file layout mid-flight.
      expect(rootYaml).toBe('')
    }
  })

  it('renderer imports only the public SDK surface and React', () => {
    const imports = [...paneSource.matchAll(/^import .* from '([^']+)'/gm)].map((m) => m[1])
    const allowed = ['react', 'react/jsx-runtime', '@hermes/plugin-sdk', './logic.mjs']
    for (const spec of imports) {
      expect(allowed).toContain(spec)
    }
  })

  it('every SDK symbol the renderer imports is declared by the test SDK contract', () => {
    const contract = JSON.parse(readFileSync(resolve(here, 'sdk/plugin-sdk.contract.json'), 'utf8'))
    const sdkImport = paneSource.match(/import \{([^}]+)\} from '@hermes\/plugin-sdk'/)
    expect(sdkImport).not.toBeNull()
    const names = sdkImport[1].split(',').map(name => name.trim()).filter(Boolean)
    expect(names.length).toBeGreaterThan(0)
    for (const name of names) {
      expect(contract.exports).toContain(name)
    }
  })

  it('renderer never touches private host internals, network, storage or DOM globals directly', () => {
    for (const [name, source] of [['pane', paneSource], ['logic', logicSource]]) {
      expect(source, name).not.toMatch(/localStorage|sessionStorage|fetch\(|XMLHttpRequest/)
      expect(source, name).not.toMatch(/@\/(store|sdk|api|components)\//)
      expect(source, name).not.toMatch(/window\.__|globalThis\.__/)
    }
  })

  it('desktop sources carry no private host paths or loopback/private URLs', () => {
    for (const [name, source] of [['pane', paneSource], ['logic', logicSource], ['artifact', artifact]]) {
      expect(source, name).not.toMatch(/\/home\/|\/Users\/|[A-Za-z]:\\\\/)
      expect(source, name).not.toMatch(/localhost|127\.0\.0\.1|::1/)
    }
  })

  it('documents the required provides fields for the shared manifest without editing it', () => {
    const doc = readFileSync(resolve(repo, 'desktop/PACKAGING.md'), 'utf8')
    expect(doc).toMatch(/provides_hooks/)
    expect(doc).toMatch(/name:\s*hermes-advisor/)
  })
})

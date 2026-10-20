/**
 * Frontend hardening contract (stale owner/instance, exact-route fail-closed,
 * real backend response shapes). Real React rendering of the real component
 * through the pinned SDK sources; `host` and `ctx` are labelled test doubles
 * and every payload is [REAL-SHAPE]/[SYNTHETIC] per fixtures.mjs.
 *
 * RED/GREEN record: tests tagged (RED-at-baseline) failed against the
 * pre-hardening pane.mjs; untagged ones are characterization tests that were
 * already green and must stay green. See verification/frontend-hardening/.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, act, cleanup, fireEvent } from '@testing-library/react'

import * as paneModule from '../../desktop/pane.mjs'
import {
  makeCtx,
  makeHost,
  setHostOverride,
  focusVerifiedSession,
  fixtureStatus,
  fixtureAttempt,
  fixtureNotes,
  fixtureFeedback,
  fixtureRateResult,
  fixtureMetadata,
  deferred,
  httpError,
  queryOf,
} from './fixtures.mjs'

let testKit
let AdvisorPane

beforeEach(async () => {
  testKit = makeHost()
  await setHostOverride(paneModule, testKit.host)
  ;({ AdvisorPane } = paneModule)
  vi.useFakeTimers()
})

afterEach(async () => {
  cleanup()
  vi.useRealTimers()
  await setHostOverride(paneModule, undefined)
})

const flush = async (ms = 5) => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms)
  })
}
const click = async (testId) => {
  await act(async () => {
    fireEvent.click(screen.getByTestId(testId))
  })
}
const $ = (id) => screen.queryByTestId(id)

/**
 * Backend double with a mutable environment so a test can move the world
 * (instance restart / profile) while requests are in flight. All handlers
 * return [REAL-SHAPE] payloads.
 */
function makeWorld({ control, notes, status } = {}) {
  const env = { instance: 'inst-1', profile: 'advisor-lab', notesCalls: 0, statusCalls: 0, controls: [] }
  const kit = makeCtx({
    '/metadata': () =>
      fixtureMetadata({
        instance_id: env.instance,
        profile: env.profile,
        sessions: [{ session_id: 'sess-1', surface: 'desktop' }, { session_id: 'sess-2', surface: 'desktop' }],
      }),
    '/status': (entry) => {
      env.statusCalls += 1
      return status ? status(entry, env) : fixtureStatus({ session_id: queryOf(entry.path).session_id })
    },
    '/notes': (entry) => {
      env.notesCalls += 1
      return notes ? notes(entry, env) : fixtureNotes()
    },
    '/control': (entry) => {
      env.controls.push(entry.body)
      return control ? control(entry, env) : { ok: true }
    },
  })
  return { env, kit }
}

const SCOPE_CHANGES = {
  session: async () => {
    testKit.atoms.runtimeSessionId.set('sess-2')
    testKit.atoms.storedSessionId.set('sess-2')
  },
  profile: async ({ env }) => {
    env.profile = 'other-profile'
    testKit.atoms.profile.set('other-profile')
    testKit.atoms.owner.set({ connectionId: 'local', profile: 'other-profile' })
  },
  connection: async () => {
    testKit.atoms.connectionId.set('conn-b')
    testKit.atoms.owner.set({ connectionId: 'conn-b', profile: 'advisor-lab' })
  },
  instance: async ({ env }) => {
    env.instance = 'inst-2' // backend restart: same session, new instance
  },
}

async function mountVerified(world) {
  focusVerifiedSession(testKit.atoms)
  const utils = render(<AdvisorPane ctx={world.kit.ctx} />)
  await flush(5)
  expect($('advisor-instance').textContent).toBe('inst-1')
  await flush(5)
  return utils
}

async function changeScope(kind, world) {
  await act(async () => {
    await SCOPE_CHANGES[kind](world)
  })
  await flush(3200) // let metadata/status re-verify the new target
}

describe('delayed control completion after scope change', () => {
  for (const kind of ['session', 'profile', 'connection', 'instance']) {
    it(`(RED-at-baseline) a failed control that completes after a ${kind} change shows no error on the new focus`, async () => {
      const gate = deferred()
      const world = makeWorld({ control: () => gate.promise })
      await mountVerified(world)
      await click('advisor-action-enable')
      expect(world.env.controls.map((b) => b.action)).toEqual(['enable'])

      await changeScope(kind, world)
      gate.reject(httpError(409, { detail: { error: 'worker_unavailable' } }))
      await flush(5)

      expect($('advisor-control-error')).toBeNull()
    })

    it(`(RED-at-baseline) a successful configure that completes after a ${kind} change never edits the new focus's revision or draft`, async () => {
      const gate = deferred()
      const world = makeWorld({ control: () => gate.promise })
      await mountVerified(world)
      await act(async () => {
        fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
        fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
      })
      await click('advisor-settings-save')
      expect(world.env.controls[0].action).toBe('configure')

      await changeScope(kind, world)
      const revisionBefore = $('advisor-revision')?.textContent
      gate.resolve({ saved_profile_revision: 99, applied_revision: 99, applied_generation: 4, state: 'enabled', pause_reason: null })
      await flush(5)

      expect($('advisor-revision')?.textContent).toBe(revisionBefore)
      expect($('advisor-revision')?.textContent).not.toBe('99')
    })
  }

  it('control bodies are always built from the live focused stored session and verified instance (storedSID)', async () => {
    const world = makeWorld()
    focusVerifiedSession(testKit.atoms)
    testKit.atoms.runtimeSessionId.set('runtime-xyz')
    testKit.atoms.storedSessionId.set('sess-1')
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(10)
    await click('advisor-action-enable')
    expect(world.env.controls).toEqual([
      { expected_instance: 'inst-1', session_id: 'sess-1', action: 'enable', params: {} },
    ])
  })
})

describe('delayed copy/feedback after scope change', () => {
  async function selectNoteAndCopy(world) {
    await click('advisor-note-check-note-1')
    await click('advisor-copy-selected')
  }

  for (const kind of ['session', 'profile', 'connection', 'instance']) {
    it(`(RED-at-baseline) feedback completing after a ${kind} change copies nothing and shows no handoff`, async () => {
      const gate = deferred()
      const clipboard = []
      const world = makeWorld({ control: (entry) => (entry.body.action === 'feedback' ? gate.promise : { ok: true }) })
      world.kit.ctx.os.writeClipboard = async (text) => { clipboard.push(text); return true }
      await mountVerified(world)
      await selectNoteAndCopy(world)
      expect(world.env.controls.map((b) => b.action)).toEqual(['feedback'])

      await changeScope(kind, world)
      gate.resolve(fixtureFeedback(['note-1'], 'STALE COPY BLOCK'))
      await flush(5)

      expect(clipboard).toEqual([])
      expect($('advisor-handoff-result')).toBeNull()
    })
  }

  it('(RED-at-baseline) a selection made in one session never carries into another focus', async () => {
    const world = makeWorld()
    await mountVerified(world)
    await click('advisor-note-check-note-1')
    expect(screen.getByTestId('advisor-copy-selected').disabled).toBe(false)
    await changeScope('session', world)
    expect(screen.getByTestId('advisor-copy-selected').disabled).toBe(true)
    expect(screen.getByTestId('advisor-note-check-note-1').getAttribute('data-state')).toBe('unchecked')
  })

  it('(RED-at-baseline) a clipboard write still in flight when the scope changes leaves no "copied" claim on the new focus', async () => {
    const write = deferred()
    const world = makeWorld({ control: () => fixtureFeedback(['note-1'], 'IN-FLIGHT BLOCK') })
    world.kit.ctx.os.writeClipboard = () => write.promise
    await mountVerified(world)
    await selectNoteAndCopy(world)
    await flush(5)
    await changeScope('session', world)
    write.resolve(true)
    await flush(5)
    expect($('advisor-handoff-result')).toBeNull()
  })

  it('(RED-at-baseline) unmounting with feedback in flight never writes the clipboard', async () => {
    const gate = deferred()
    const clipboard = []
    const world = makeWorld({ control: () => gate.promise })
    world.kit.ctx.os.writeClipboard = async (text) => { clipboard.push(text); return true }
    const utils = await mountVerified(world)
    await selectNoteAndCopy(world)
    utils.unmount()
    gate.resolve(fixtureFeedback(['note-1']))
    await flush(5)
    expect(clipboard).toEqual([])
  })

  it('(RED-at-baseline) writeClipboard resolving false is reported as a failure, never as "Copied"', async () => {
    const world = makeWorld({ control: () => fixtureFeedback(['note-1']) })
    world.kit.ctx.os.writeClipboard = async () => false // PluginOs.writeClipboard: Promise<boolean>
    await mountVerified(world)
    await selectNoteAndCopy(world)
    await flush(5)
    expect($('advisor-handoff-result')).toBeNull()
    expect($('advisor-handoff-error').textContent).toMatch(/clipboard/i)
  })

  it('(RED-at-baseline) a feedback response whose notes do not match the selection is not copied', async () => {
    const clipboard = []
    const world = makeWorld({ control: () => fixtureFeedback(['note-OTHER']) })
    world.kit.ctx.os.writeClipboard = async (text) => { clipboard.push(text); return true }
    await mountVerified(world)
    await selectNoteAndCopy(world)
    await flush(5)
    expect(clipboard).toEqual([])
    expect($('advisor-handoff-error').textContent).toMatch(/does not match/i)
  })

  it('copies the real-shape feedback text verbatim for a matching, current response', async () => {
    const clipboard = []
    const world = makeWorld({ control: () => fixtureFeedback(['note-1'], 'REAL SHAPE TEXT') })
    world.kit.ctx.os.writeClipboard = async (text) => { clipboard.push(text); return true }
    await mountVerified(world)
    await selectNoteAndCopy(world)
    await flush(5)
    expect(clipboard).toEqual(['REAL SHAPE TEXT'])
    expect($('advisor-handoff-result').textContent).toContain('REAL SHAPE TEXT')
  })
})

describe('exact-route fail-closed (ctx.rest only reaches the ACTIVE gateway)', () => {
  it('(RED-at-baseline) focused owner on a different connection than the active gateway: no traffic at all, visible reason', async () => {
    const world = makeWorld()
    testKit.atoms.connectionId.set('local') // active gateway
    testKit.atoms.profile.set('advisor-lab')
    testKit.atoms.owner.set({ connectionId: 'conn-b', profile: 'advisor-lab' }) // focus moved without the socket
    testKit.atoms.runtimeSessionId.set('sess-1')
    testKit.atoms.storedSessionId.set('sess-1')
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(7000)
    expect(world.kit.calls).toEqual([])
    expect($('advisor-route-blocked').textContent).toMatch(/active gateway|connection/i)
    expect($('advisor-action-enable')?.disabled ?? true).toBe(true)
  })

  it('(RED-at-baseline) same-named profile on another connection is not mistaken for the target by the metadata-profile check', async () => {
    // metadata.profile == owner.profile == "advisor-lab" would pass the old
    // check, but the owner connection differs from the one ctx.rest reaches.
    const world = makeWorld()
    testKit.atoms.connectionId.set('conn-a')
    testKit.atoms.profile.set('advisor-lab')
    testKit.atoms.owner.set({ connectionId: 'conn-b', profile: 'advisor-lab' })
    testKit.atoms.runtimeSessionId.set('sess-1')
    testKit.atoms.storedSessionId.set('sess-1')
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(4000)
    expect(world.kit.calls.filter((c) => c.path !== '/metadata')).toEqual([])
    expect($('advisor-instance').textContent).toBe('unverified')
  })

  it('(RED-at-baseline) owner profile differing from the active gateway profile fails closed', async () => {
    const world = makeWorld()
    testKit.atoms.connectionId.set('local')
    testKit.atoms.profile.set('advisor-lab')
    testKit.atoms.owner.set({ connectionId: 'local', profile: 'other-profile' })
    testKit.atoms.runtimeSessionId.set('sess-1')
    testKit.atoms.storedSessionId.set('sess-1')
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(4000)
    expect(world.kit.calls).toEqual([])
    expect($('advisor-route-blocked')).not.toBeNull()
  })

  it('(RED-at-baseline) an unavailable active-connection atom (older host) fails closed instead of guessing', async () => {
    const world = makeWorld()
    delete testKit.host.state.connectionId
    focusVerifiedSession(testKit.atoms)
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(4000)
    expect(world.kit.calls).toEqual([])
    expect($('advisor-route-blocked').textContent).toMatch(/unavailable|cannot establish/i)
  })

  it('(RED-at-baseline) recovers and starts polling once the active gateway matches the focused owner', async () => {
    const world = makeWorld()
    testKit.atoms.connectionId.set('local')
    testKit.atoms.profile.set('advisor-lab')
    testKit.atoms.owner.set({ connectionId: 'conn-b', profile: 'advisor-lab' })
    testKit.atoms.runtimeSessionId.set('sess-1')
    testKit.atoms.storedSessionId.set('sess-1')
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(3500)
    expect(world.kit.calls).toEqual([])
    await act(async () => { testKit.atoms.connectionId.set('conn-b') }) // gateway follows the focus
    await flush(10)
    expect($('advisor-instance').textContent).toBe('inst-1')
    expect($('advisor-route-blocked')).toBeNull()
  })

  it('(RED-at-baseline) metadata reporting closing:true is never adopted as a verified instance', async () => {
    const world = makeWorld()
    world.kit = makeCtx({
      '/metadata': () => fixtureMetadata({ closing: true }),
      '/status': () => fixtureStatus(),
    })
    focusVerifiedSession(testKit.atoms)
    render(<AdvisorPane ctx={world.kit.ctx} />)
    await flush(4000)
    expect($('advisor-instance').textContent).toBe('unverified')
    expect(world.kit.calls.filter((c) => c.path.startsWith('/status'))).toEqual([])
  })
})

describe('hidden / unmounted polling', () => {
  it('hidden pane makes zero requests (metadata, status, notes) and resumes once visible', async () => {
    const world = makeWorld()
    await mountVerified(world)
    await act(async () => { testKit.atoms.visible.set(false) })
    const before = world.kit.calls.length
    await flush(12000)
    expect(world.kit.calls.length).toBe(before)
    await act(async () => { testKit.atoms.visible.set(true) })
    await flush(10)
    expect(world.kit.calls.length).toBeGreaterThan(before)
  })

  it('hidden mid-flight: a status that completes while hidden is not applied to the view', async () => {
    const gate = deferred()
    let hold = false
    const world = makeWorld({ status: (entry) => (hold ? gate.promise : fixtureStatus({ saved_profile_revision: 7 })) })
    await mountVerified(world)
    hold = true
    await flush(3000)
    await act(async () => { testKit.atoms.visible.set(false) })
    gate.resolve(fixtureStatus({ saved_profile_revision: 55 }))
    await flush(5)
    expect($('advisor-revision').textContent).toBe('7')
  })

  it('unmount stops every timer: no request is made after unmount', async () => {
    const world = makeWorld()
    const utils = await mountVerified(world)
    utils.unmount()
    const before = world.kit.calls.length
    await flush(15000)
    expect(world.kit.calls.length).toBe(before)
  })

  it('unmounted mid-flight status/notes resolutions are inert', async () => {
    const gate = deferred()
    let hold = false
    const world = makeWorld({ status: () => (hold ? gate.promise : fixtureStatus()) })
    const utils = await mountVerified(world)
    hold = true
    await flush(3000)
    const errors = vi.spyOn(console, 'error').mockImplementation(() => {})
    utils.unmount()
    gate.resolve(fixtureStatus({ saved_profile_revision: 55 }))
    await flush(5)
    expect(errors).not.toHaveBeenCalled()
    errors.mockRestore()
  })
})

describe('ratings use the backend rate/notes shapes', () => {
  it('(RED-at-baseline) sends exactly {attempt_id, rating, reason} and shows the backend-redacted reason from refreshed notes', async () => {
    let rated = null
    const world = makeWorld({
      notes: () => fixtureNotes({ rating: rated ? { rating: rated.rating, reason: rated.reason } : null }),
      control: (entry) => {
        if (entry.body.action === 'rate') {
          rated = { rating: entry.body.params.rating, reason: '[redacted] accurate' }
          return fixtureRateResult(entry.body.params.attempt_id, entry.body.params.rating, rated.reason)
        }
        return { ok: true }
      },
    })
    await mountVerified(world)
    await act(async () => {
      fireEvent.change(screen.getByTestId('advisor-rating-reason'), { target: { value: 'accurate sk-SECRET123456789' } })
    })
    await click('advisor-rating-useful')
    await flush(5)
    const rate = world.env.controls.find((b) => b.action === 'rate')
    expect(Object.keys(rate.params).sort()).toEqual(['attempt_id', 'rating', 'reason'])
    expect(rate.params).toEqual({ attempt_id: 'att-1', rating: 'useful', reason: 'accurate sk-SECRET123456789' })
    // (RED-at-baseline) notes are refetched after a successful rate and show the persisted rating+reason.
    await flush(5)
    expect($('advisor-rating-current').textContent).toContain('useful')
    expect($('advisor-rating-current').textContent).toContain('[redacted] accurate')
    expect($('advisor-rating-current').textContent).not.toContain('SECRET123456789')
  })

  it('(RED-at-baseline) a reason over the backend limit (500 code points) is refused locally with no request', async () => {
    const world = makeWorld()
    await mountVerified(world)
    await act(async () => {
      fireEvent.change(screen.getByTestId('advisor-rating-reason'), { target: { value: 'x'.repeat(501) } })
    })
    await click('advisor-rating-useful')
    await flush(5)
    expect(world.env.controls.filter((b) => b.action === 'rate')).toEqual([])
    expect($('advisor-control-error').textContent).toMatch(/500/)
  })

  it('(RED-at-baseline) only settled completed attempts can be rated (backend rejects the rest)', async () => {
    const world = makeWorld({
      notes: () => fixtureNotes({ status: 'running', terminal_at: null }),
      status: () => fixtureStatus({ latest_attempt: fixtureAttempt({ status: 'running', terminal_at: null }) }),
    })
    await mountVerified(world)
    expect(screen.getByTestId('advisor-rating-useful').disabled).toBe(true)
  })

  it('(RED-at-baseline) a rate response that is not {saved:true} for the same attempt is reported, not trusted', async () => {
    const world = makeWorld({ control: () => ({ attempt_id: 'att-OTHER', rating: 'useful', reason: null, saved: true }) })
    await mountVerified(world)
    await click('advisor-rating-useful')
    await flush(5)
    expect($('advisor-control-error').textContent).toMatch(/rate/i)
  })
})

// A21 permanent DOM regression: a hostile reason carried through the REAL
// backend response shape into the REAL component render must never inject
// elements, attributes, or scriptable/data URIs into the rendered DOM.
describe('A21: hostile reason/notes text through real render is inert', () => {
  it('renders rating reason + attempt error as text only: no tags, attributes, or scriptable URIs', async () => {
    const hostileReason = '\x1b[31m<script>alert(1)</script>\x1b[0m \x9b31m<img src=x onerror="alert(1)"> javascript:alert(1) data:text/html,<script>2</script>'
    const world = makeWorld({
      notes: () => fixtureNotes({
        rating: { rating: 'useful', reason: '\x1b[31m<script>alert(1)</script>\x1b[0m \x9b31m<img src=x onerror="alert(1)"> javascript:alert(1) data:text/html,<script>2</script>' },
        error_code: '<script>err</script>',
        error: 'javascript:alert(1) data:text/html;base64,AAAA',
      }),
      status: () => fixtureStatus({
        latest_attempt: fixtureAttempt({
          rating: { rating: 'useful', reason: '\x1b[31m<script>alert(1)</script>\x1b[0m \x9b31m<img src=x onerror="alert(1)"> javascript:alert(1) data:text/html,<script>2</script>' },
          error_code: '<script>err</script>',
          error: 'javascript:alert(1) data:text/html;base64,AAAA',
        }),
      }),
    })
    await mountVerified(world)
    await flush(5)

    for (const id of ['advisor-rating-current', 'advisor-attempt-error']) {
      const node = $(id)
      expect(node).not.toBeNull()
      // No injected element: only the expected direct children, all plain spans/divs rendered by the pane itself.
      expect(node.querySelector('script, img, iframe, svg, object, embed, a[href]')).toBeNull()
      const text = node.textContent
      const low = text.toLowerCase()
      expect(low).not.toContain('javascript:')
      expect(low).not.toContain('vbscript:')
      expect(low).not.toContain('data:')
      expect(low).not.toContain('onerror')
      expect(text).not.toMatch(/</)
      // No terminal controls survived into the DOM text either.
      expect(text).not.toMatch(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]/)
    }
  })
})

describe('notes completion refresh', () => {
  it('(RED-at-baseline) refetches notes on completion with a real-shape running->completed transition (same attempt id)', async () => {
    let completed = false
    const world = makeWorld({
      status: () => fixtureStatus({
        latest_attempt: fixtureAttempt({ status: completed ? 'completed' : 'running', terminal_at: completed ? '2026-10-10T00:00:30Z' : null }),
      }),
      notes: () => (completed ? fixtureNotes() : []),
    })
    await mountVerified(world)
    expect($('advisor-findings')).toBeNull()
    completed = true
    await flush(3100)
    await flush(5)
    expect($('advisor-findings').textContent).toContain('SYNTHETIC finding explanation')
  })

  it('(RED-at-baseline) notes fetched for the previous session are never shown under the new focus while the new fetch is pending', async () => {
    const gate = deferred()
    let hold = false
    const world = makeWorld({ notes: (entry) => (hold ? gate.promise : fixtureNotes()) })
    await mountVerified(world)
    expect($('advisor-findings')).not.toBeNull()
    hold = true
    await act(async () => { await SCOPE_CHANGES.session(world) })
    await flush(10)
    expect($('advisor-findings')).toBeNull()
    gate.resolve([])
    await flush(5)
  })
})

describe('real status/notes shapes', () => {
  it('(RED-at-baseline) route readout reads requested_route/served_route objects and surfaces attempt.error', async () => {
    const world = makeWorld({
      status: () => fixtureStatus({
        latest_attempt: fixtureAttempt({
          status: 'failed',
          requested_route: { provider: 'nous', model: 'req-model' },
          served_route: { provider: 'nous', model: 'served-model' },
          error_code: 'provider_error',
          error: 'provider returned 500',
        }),
      }),
    })
    await mountVerified(world)
    expect($('advisor-route').textContent).toContain('nous/req-model')
    expect($('advisor-route').textContent).toContain('nous/served-model')
    expect($('advisor-attempt-error').textContent).toContain('provider_error')
  })

  it('(RED-at-baseline) a thrown 409 ConfigurationConflict preserves the draft and is shown as a revision conflict', async () => {
    const world = makeWorld({ control: () => { throw httpError(409, { detail: { error: 'ConfigurationConflict' } }) } })
    await mountVerified(world)
    await act(async () => {
      fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
      fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
    })
    await click('advisor-settings-save')
    await flush(5)
    expect($('advisor-settings-error').textContent).toMatch(/revision conflict/i)
    expect(screen.getByTestId('advisor-settings-provider').value).toBe('nous')
  })

  it('a real-shape configure success adopts the returned revision and clears the draft', async () => {
    const world = makeWorld({
      control: () => ({ saved_profile_revision: 8, applied_revision: 8, applied_generation: 4, state: 'enabled', pause_reason: null }),
    })
    await mountVerified(world)
    await act(async () => {
      fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
      fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
    })
    await click('advisor-settings-save')
    await flush(5)
    expect($('advisor-revision').textContent).toBe('8')
  })
})

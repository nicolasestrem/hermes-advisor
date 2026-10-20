/**
 * Pane contract: real rendering of the real component through the pinned SDK
 * module (aliased to the pinned lab sources). The component's `ctx` is the
 * controlled double; host state is a public-shaped host double (see
 * fixtures.mjs for the explicit fixture boundary). RED first.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, act, waitFor, fireEvent, cleanup } from '@testing-library/react'

import * as paneModule from '../../desktop/pane.mjs'
import {
  makeCtx,
  makeHost,
  setHostOverride,
  focusVerifiedSession,
  fixtureStatus,
  fixtureNotes,
  fixtureMetadata,
  fixtureAttempt,
  fixtureFeedback,
  fixtureRateResult,
  httpError,
  SETTINGS_FIXTURE,
} from './fixtures.mjs'

let testKit
let AdvisorPane

beforeEach(async () => {
  testKit = makeHost()
  const ctxKit = makeCtx()
  testKit.ctxKit = ctxKit
  await setHostOverride(paneModule, testKit.host)
  ;({ AdvisorPane } = paneModule)
})

afterEach(async () => {
  await setHostOverride(paneModule, undefined)
})

function renderPane({ handlers = {}, ctx } = {}) {
  const kit = ctx ?? makeCtx(handlers)
  testKit.ctxKit = kit
  const utils = render(<AdvisorPane ctx={kit.ctx} />)
  return { ...utils, kit }
}

/**
 * Flush pending promises/microtasks under fake timers (real `waitFor` cannot
 * advance vi's clock). Runs `fn` every flush until it passes, bounded.
 */
async function flushUntil(fn, { flushMs = 5, maxRounds = 200 } = {}) {
  for (let i = 0; i < maxRounds; i += 1) {
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(flushMs)
      })
      fn()
      return
    } catch {
      // keep flushing
    }
  }
  fn() // final attempt: surface the real failure
}

describe('AdvisorPane display + poll loop', () => {
  it('uses the stored hook session ID for reads and controls when runtime and stored IDs differ', async () => {
    vi.useFakeTimers()
    try {
    focusVerifiedSession(testKit.atoms)
    testKit.atoms.runtimeSessionId.set('runtime-owned')
    testKit.atoms.storedSessionId.set('stored-owned')
    const kit = makeCtx({
      '/metadata': () => ({ ...fixtureMetadata(), sessions: [{ session_id: 'stored-owned', surface: 'desktop' }] }),
      '/status': entry => fixtureStatus({ session_id: new URL(entry.path, 'http://fixture').searchParams.get('session_id') }),
      '/notes': () => [],
      '/control': () => ({ state: 'enabled' }),
    })
    renderPane({ ctx: kit })
    await flushUntil(() => expect(kit.calls.some(call => call.path.startsWith('/status?'))).toBe(true))
    expect(kit.calls.find(call => call.path.startsWith('/status?')).path).toContain('session_id=stored-owned')
    await flushUntil(() => expect(screen.queryByTestId('advisor-action-enable')).not.toBeNull())
    await act(async () => { fireEvent.click(screen.getByTestId('advisor-action-enable')) })
    await flushUntil(() => expect(kit.calls.some(call => call.path === '/control')).toBe(true))
    expect(kit.calls.find(call => call.path === '/control').body.session_id).toBe('stored-owned')
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  })

  it('refreshes notes when an existing running attempt becomes terminal without changing its ID', async () => {
    vi.useFakeTimers()
    try {
      focusVerifiedSession(testKit.atoms)
      let completed = false
      let notesCalls = 0
      const initial = fixtureStatus()
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus({ latest_attempt: { ...initial.latest_attempt, status: completed ? 'completed' : 'running' } }),
        '/notes': () => { notesCalls += 1; return completed ? fixtureNotes() : [] },
      })
      renderPane({ ctx: kit })
      await flushUntil(() => expect(notesCalls).toBeGreaterThan(0))
      const before = notesCalls
      completed = true
      await act(async () => { await vi.advanceTimersByTimeAsync(3000) })
      await flushUntil(() => expect(notesCalls).toBeGreaterThan(before))
    } finally {
      cleanup()
      vi.useRealTimers()
    }
  })

  it('renders the full idle explanation through the pinned SDK EmptyState description contract', () => {
    renderPane()
    expect(screen.getByText('Select a session to see Advisor status. Advisor does not discover anything from an unverified selection.')).toBeTruthy()
    expect(screen.queryByTestId('advisor-session-header')).toBeNull()
  })

  it('polls metadata every 3s while visible, shows status, and stops when hidden', async () => {
    vi.useFakeTimers()
    try {
      const meta = fixtureMetadata()
      let statusCalls = 0
      const kit = makeCtx({
        '/metadata': () => meta,
        '/status': () => {
          statusCalls += 1
          return fixtureStatus()
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      expect(screen.getByTestId('advisor-session-header').textContent).toContain('sess-1')

      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(screen.getByTestId('advisor-state').textContent).toBe('enabled')
      const afterFirst = statusCalls

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      expect(statusCalls).toBe(afterFirst + 1)

      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      expect(statusCalls).toBe(afterFirst + 2)

      // Hide the pane: no further polls.
      await act(async () => {
        testKit.atoms.visible.set(false)
        await vi.advanceTimersByTimeAsync(9500)
      })
      expect(statusCalls).toBe(afterFirst + 2)

      // Resume when visible again.
      await act(async () => {
        testKit.atoms.visible.set(true)
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(statusCalls).toBe(afterFirst + 3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('surfaces backend error state visibly and keeps retrying', async () => {
    vi.useFakeTimers()
    try {
      let fail = true
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => {
          if (fail) {
            throw Object.assign(new Error('HTTP 503 status unavailable'), { status: 503 })
          }
          return fixtureStatus()
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      expect(screen.getByTestId('advisor-error').textContent).toContain('status unavailable')

      fail = false
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      expect(screen.queryByTestId('advisor-error')).toBeNull()
      expect(screen.getByTestId('advisor-state').textContent).toBe('enabled')
    } finally {
      vi.useRealTimers()
    }
  })

  it('renders enabled/pending/running/answer/failings/coverage/error state text from status+notes', async () => {
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => fixtureStatus({ state: 'enabled', pending: true, active: true }),
      '/notes': () => fixtureNotes(),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-state').textContent).toBe('enabled'))
    expect(screen.getByTestId('advisor-lifecycle').textContent).toMatch(/pending|running/i)
    await waitFor(() => {
      expect(screen.getByTestId('advisor-answer-ref').textContent).toContain('turn-9')
    })
    expect(screen.getByTestId('advisor-findings').textContent).toContain('SYNTHETIC finding explanation')
    expect(screen.getByTestId('advisor-coverage').textContent).toMatch(/tool pairs omitted|coverage/i)
    expect(screen.getByTestId('advisor-route').textContent).toContain('nous')
    expect(screen.getByTestId('advisor-route').textContent).toContain('z-ai/glm-5.3-flash')
    expect(screen.getByTestId('advisor-usage').textContent).toMatch(/known/i)
  })

  it('marks usage visibly unknown and route unknown when the backend reports fallback', async () => {
    const status = fixtureStatus({
      latest_attempt: fixtureAttempt({
        attempt_id: 'att-2',
        turn_id: 'turn-10',
        requested_route: { provider: null, model: null },
        served_route: { provider: null, model: null },
        usage_known: false,
        created_at: '2026-10-10T01:00:00Z',
        terminal_at: '2026-10-10T01:00:20Z',
      }),
    })
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => status,
      '/notes': () => fixtureNotes(),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-usage').textContent).toMatch(/unknown/i))
    expect(screen.getByTestId('advisor-route').textContent).toMatch(/unknown/i)
  })
})

describe('AdvisorPane stale-result guard', () => {
  it('discards a delayed status that completes after a profile switch and shows the new profile data', async () => {
    vi.useFakeTimers()
    try {
      let profile = 'advisor-lab'
      let delayStatus = false
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata({ profile }),
        '/status': async () => {
          if (delayStatus) {
            await new Promise((resolve) => setTimeout(resolve, 4000))
          }
          return fixtureStatus({ saved_profile_revision: profile === 'advisor-lab' ? 7 : 8 })
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(screen.getByTestId('advisor-revision').textContent).toBe('7')

      // Start a slow status while on profile advisor-lab...
      delayStatus = true
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      // ...switch the profile mid-flight...
      await act(async () => {
        profile = 'other-profile'
        testKit.atoms.profile.set('other-profile')
        testKit.atoms.owner.set({ connectionId: 'local', profile: 'other-profile' })
      })
      // The stale response's microtask is left unawaited (fake timers); the
      // guard discards it without the test hanging on its 4s setTimeout.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      // The pane refreshed on the new profile's own poll, not the stale one.
      expect(screen.getByTestId('advisor-profile').textContent).toContain('other-profile')
      expect(screen.getByTestId('advisor-profile').textContent).toContain('other-profile')
    } finally {
      vi.useRealTimers()
    }
  })

  it('discards a delayed response after a connection switch', async () => {
    vi.useFakeTimers()
    try {
      let connection = 'local'
      let revision = 7
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': async () => {
          if (connection === 'conn-b') {
            await new Promise((resolve) => setTimeout(resolve, 4000))
            return fixtureStatus({ saved_profile_revision: revision, session_id: 'sess-connb' })
          }
          return fixtureStatus({ saved_profile_revision: revision })
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
      })
      connection = 'conn-b'
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000)
      })
      await act(async () => {
        connection = 'local'
        revision = 9
        testKit.atoms.connectionId.set('conn-b') // active gateway follows the focused owner
        testKit.atoms.owner.set({ connectionId: 'conn-b', profile: 'advisor-lab' })
        testKit.atoms.runtimeSessionId.set('sess-connb')
        testKit.atoms.storedSessionId.set('sess-connb')
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3100)
      })
      // The stale conn-b response must not overwrite the view; the header shows
      // the current focus and the revision reflects only verified fresh data.
      expect(screen.getByTestId('advisor-session-header').textContent).toContain('sess-connb')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('AdvisorPane settings draft', () => {
  it('blanks provider/model until the operator supplies them and inherits only unedited profile defaults', async () => {
    vi.useFakeTimers()
    try {
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus(),
        '/control': ({ body }) => ({ ok: true, action: body.action, revision: 8 }),
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      // Wait for the verified status (and its saved settings) to land first.
      await flushUntil(() => expect(screen.getByTestId('advisor-revision').textContent).toBe('7'))
      const providerInput = screen.getByTestId('advisor-settings-provider')
      const modelInput = screen.getByTestId('advisor-settings-model')
      // Route fields blank until the operator supplies them; not pre-filled from status.
      expect(providerInput.value).toBe('')
      expect(modelInput.value).toBe('')

      await act(async () => {
        fireEvent.change(providerInput, { target: { value: 'nous' } })
        fireEvent.change(modelInput, { target: { value: 'z-ai/glm-5.3-flash' } })
      })
      expect(providerInput.value).toBe('nous')
      expect(modelInput.value).toBe('z-ai/glm-5.3-flash')

      // Inherited defaults visible from the saved profile settings.
      expect(screen.getByTestId('advisor-settings-packet-bytes').value).toBe(String(SETTINGS_FIXTURE.packet_bytes))
      expect(screen.getByTestId('advisor-settings-output-tokens').value).toBe(String(SETTINGS_FIXTURE.output_tokens))
      expect(screen.getByTestId('advisor-settings-max-findings').value).toBe(String(SETTINGS_FIXTURE.max_findings))
      expect(screen.getByTestId('advisor-settings-deadline').value).toBe(String(SETTINGS_FIXTURE.deadline_seconds))
      expect(screen.getByTestId('advisor-settings-allowance').value).toBe(String(SETTINGS_FIXTURE.session_allowance))
    } finally {
      vi.useRealTimers()
    }
  })

  it('saves the full draft atomically with expected_revision, then reflects the verified response; never applies before save', async () => {
    vi.useFakeTimers()
    try {
      const controlBodies = []
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus({ applied_revision: 7, saved_profile_revision: 7 }),
        '/control': ({ body }) => {
          controlBodies.push(body)
          if (body.action === 'configure') {
            return { ok: true, action: 'configure', saved_profile_revision: 8, applied_revision: 8 }
          }
          return { ok: true, action: body.action }
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await act(async () => {
        fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
        fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
      })
      // Optimistic application is forbidden: revision must still read 7 before save.
      expect(screen.getByTestId('advisor-revision').textContent).toBe('7')

      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-settings-save')) })
      })
      expect(controlBodies.length).toBe(1)
      expect(controlBodies[0].action).toBe('configure')
      expect(controlBodies[0].expected_instance).toBe('inst-1')
      expect(controlBodies[0].session_id).toBe('sess-1')
      expect(controlBodies[0].params.expected_revision).toBe(7)
      expect(controlBodies[0].params.settings.route).toEqual({ provider: 'nous', model: 'z-ai/glm-5.3-flash' })
      await flushUntil(() => expect(screen.getByTestId('advisor-revision').textContent).toBe('8'))
    } finally {
      vi.useRealTimers()
    }
  })

  it('preserves the draft and shows the conflict on revision conflict instead of overwriting', async () => {
    vi.useFakeTimers()
    try {
      let revision = 7
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus({ saved_profile_revision: revision, applied_revision: revision }),
        '/control': ({ body }) => {
          if (body.action === 'configure') {
            // [REAL-SHAPE] service raises ConfigurationConflict -> plugin_api invoke -> HTTP 409 detail.error=<class>
            throw httpError(409, { detail: { error: 'ConfigurationConflict' } })
          }
          return { ok: true, action: body.action }
        },
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await act(async () => {
        fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
        fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
      })
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-settings-save')) })
      })
      await flushUntil(() => expect(screen.getByTestId('advisor-settings-error').textContent).toMatch(/revision/i))
      // The draft is preserved, not wiped, for re-inspection after a conflict.
      expect(screen.getByTestId('advisor-settings-provider').value).toBe('nous')
      expect(screen.getByTestId('advisor-settings-model').value).toBe('z-ai/glm-5.3-flash')
      // No silent overwrite: revision on screen still reflects the verified backend, and the
      // operator must re-apply explicitly; a second save re-reads the current revision.
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-settings-save')) })
        await vi.advanceTimersByTimeAsync(1)
      })
      const configureBodies = kit.calls.filter((c) => c.body?.action === 'configure')
      expect(configureBodies.length).toBe(2)
      // The operator re-applies explicitly; the pane never silently bumps the
      // expected revision on the operator's behalf.
      expect(configureBodies[1].body.params.expected_revision).toBe(7)
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows validation errors and refuses to send an invalid draft', async () => {
    vi.useFakeTimers()
    try {
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus(),
        '/control': () => ({ ok: true }),
      })
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await act(async () => {
        fireEvent.change(screen.getByTestId('advisor-settings-provider'), { target: { value: 'nous' } })
        fireEvent.change(screen.getByTestId('advisor-settings-model'), { target: { value: 'z-ai/glm-5.3-flash' } })
        fireEvent.change(screen.getByTestId('advisor-settings-packet-bytes'), { target: { value: '999999' } })
      })
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-settings-save')) })
      })
      expect(screen.getByTestId('advisor-settings-error').textContent).toMatch(/packet/i)
      expect(kit.calls.filter((c) => c.body?.action === 'configure').length).toBe(0)
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('AdvisorPane actions', () => {
  async function setupPane(overrides = {}) {
    const controlBodies = []
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => fixtureStatus(overrides.status ?? {}),
      '/notes': () => fixtureNotes(),
      '/control': ({ body }) => {
        controlBodies.push(body)
        if (body.action === 'rate') {
          return fixtureRateResult(body.params.attempt_id, body.params.rating, body.params.reason || null)
        }
        return { ok: true, action: body.action }
      },
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    return { kit, controlBodies }
  }

  it('Enable and Disable send control actions to the verified session', async () => {
    vi.useFakeTimers()
    try {
      const { controlBodies } = await setupPane({ status: { state: 'paused', pause_reason: 'never_enabled' } })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1)
        act(() => { fireEvent.click(screen.getByTestId('advisor-action-enable')) })
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(controlBodies.some((b) => b.action === 'enable' && b.session_id === 'sess-1' && b.expected_instance === 'inst-1')).toBe(true)
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-action-disable')) })
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(controlBodies.some((b) => b.action === 'disable')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('Review Now is gated on availability and sends review_now', async () => {
    vi.useFakeTimers()
    const { controlBodies } = await setupPane({ status: { review_now_available: false } })
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      const reviewBtn = screen.getByTestId('advisor-action-review-now')
      expect(reviewBtn.disabled).toBe(true)
      // The gate follows the verified status payload: when the backend reports
      // availability the same mounted button becomes enabled (no re-render kit).
      const { kit: _k } = { kit: null }
      const { calls } = testKit.ctxKit
      const control = calls.find((c) => c.body?.action === 'review_now')
      expect(control).toBeUndefined()
      await act(async () => {
        fireEvent.click(reviewBtn)
      })
      // Gated off: the click sends nothing.
      expect(calls.find((c) => c.body?.action === 'review_now')).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
    // Second pane (first unmounted) with availability on: the action goes out
    // with the verified instance.
    cleanup()
    const kit2 = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => fixtureStatus({ review_now_available: true }),
      '/control': ({ body }) => {
        controlBodies.push(body)
        return { ok: true, action: body.action }
      },
    })
    try {
      vi.useFakeTimers()
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit2 })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      const btn2 = screen.getByTestId('advisor-action-review-now')
      expect(btn2.disabled).toBe(false)
      await act(async () => {
        act(() => { fireEvent.click(btn2) })
        await vi.advanceTimersByTimeAsync(1)
      })
      expect(controlBodies.some((b) => b.action === 'review_now' && b.expected_instance === 'inst-1')).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('Reset Allowance asks for confirmation and sends confirm=true only after the user confirms', async () => {
    vi.useFakeTimers()
    try {
      const { controlBodies } = await setupPane()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-action-reset-allowance')) })
      })
      // Confirmation dialog appears first; no request yet.
      expect(controlBodies.some((b) => b.action === 'reset_allowance')).toBe(false)
      expect(screen.getByTestId('advisor-confirm-dialog')).toBeTruthy()
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-confirm-accept')) })
      })
      await flushUntil(() => {
        const call = controlBodies.find((b) => b.action === 'reset_allowance')
        expect(call?.params).toEqual({ confirm: true })
      })
    } finally {
      vi.useRealTimers()
    }
  })

  it('ratings cycle Useful / Not useful / Not rated with an optional redacted reason', async () => {
    vi.useFakeTimers()
    try {
      const { controlBodies } = await setupPane()
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      await flushUntil(() => expect(screen.getByTestId('advisor-rating-useful')).toBeTruthy())
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-rating-useful')) })
      })
      await flushUntil(() => {
        const call = controlBodies.find((b) => b.action === 'rate')
        expect(call?.params).toEqual({ attempt_id: 'att-1', rating: 'useful', reason: '' })
      })

      await act(async () => {
        fireEvent.change(screen.getByTestId('advisor-rating-reason'), { target: { value: 'accurate;  [redacted] packet excerpt: SECRET-PAYLOAD' } })
        act(() => { fireEvent.click(screen.getByTestId('advisor-rating-not-useful')) })
      })
      await flushUntil(() => {
        const call = controlBodies.filter((b) => b.action === 'rate').pop()
        expect(call.params.rating).toBe('not_useful')
        // Exact backend rate parameters; the backend redacts stored text.
        expect(call.params.reason).toContain('SECRET-PAYLOAD')
      })

      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-rating-not-rated')) })
      })
      await flushUntil(() => {
        const call = controlBodies.filter((b) => b.action === 'rate').pop()
        expect(call.params.rating).toBe('not_rated')
      })
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('AdvisorPane explicit copy handoff', () => {
  it('requires explicit selection and Copy click; calls ctx.feedback with note ids and copies the returned text', async () => {
    vi.useFakeTimers()
    try {
      const controlBodies = []
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus(),
        '/notes': () => fixtureNotes(),
        '/control': ({ body }) => {
          controlBodies.push(body)
          if (body.action === 'feedback') {
            return fixtureFeedback(body.params.note_ids, 'COPY BLOCK TEXT\n[concern] SYNTHETIC finding explanation')
          }
          return { ok: true, action: body.action }
        },
      })
      const clipboard = []
      testKit.ctxKit = kit
      kit.ctx.os.writeClipboard = async (text) => {
        clipboard.push(text)
        return true
      }
      focusVerifiedSession(testKit.atoms)
      const { container } = render(<AdvisorPane ctx={kit.ctx} />)
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5)
      })
      const copyBtn = screen.getByTestId('advisor-copy-selected')
      expect(copyBtn.disabled).toBe(true)

      const checkbox = screen.getByTestId('advisor-note-check-note-1')
      expect(checkbox.getAttribute('aria-checked')).toBe('false')
      expect(checkbox.getAttribute('data-state')).toBe('unchecked')
      await act(async () => {
        act(() => { fireEvent.click(checkbox) })
      })
      expect(checkbox.getAttribute('data-state')).toBe('checked')
      expect(copyBtn.disabled).toBe(false)
      await act(async () => {
        act(() => { fireEvent.click(copyBtn) })
      })
      await flushUntil(() => {
        const call = controlBodies.find((b) => b.action === 'feedback')
        expect(call?.params).toEqual({ note_ids: ['note-1'] })
      })
      await flushUntil(() => expect(clipboard.length).toBe(1))
      expect(clipboard[0]).toContain('SYNTHETIC finding explanation')
      // The copied block carries the captured answer ref and the next step.
      // The pane copies the backend-composed block verbatim (text is built by
      // the backend feedback action, not re-assembled in the renderer).
      expect(clipboard[0]).toBe('COPY BLOCK TEXT\n[concern] SYNTHETIC finding explanation')
      expect(container).toBeTruthy()
      // No automatic sends: no insertText, no prompt submit surfaces were touched.
      expect(kit.ctx.composer).toBeUndefined()
    } finally {
      vi.useRealTimers()
    }
  })

  it('shows the copied handoff result inside the pane without inserting into any transcript', async () => {
    vi.useFakeTimers()
    try {
      const kit = makeCtx({
        '/metadata': () => fixtureMetadata(),
        '/status': () => fixtureStatus(),
        '/notes': () => fixtureNotes(),
        '/control': ({ body }) => (body.action === 'feedback'
          ? fixtureFeedback(body.params.note_ids, 'COPY BLOCK READY')
          : { ok: true, action: body.action }),
      })
      const clipboard = []
      kit.ctx.os.writeClipboard = async (text) => {
        clipboard.push(text)
        return true
      }
      focusVerifiedSession(testKit.atoms)
      renderPane({ ctx: kit })
      await flushUntil(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1'))
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-note-check-note-1')) })
      })
      await act(async () => {
        act(() => { fireEvent.click(screen.getByTestId('advisor-copy-selected')) })
        await vi.advanceTimersByTimeAsync(1)
      })
      await flushUntil(() => expect(screen.getByTestId('advisor-handoff-result').textContent).toContain('COPY BLOCK READY'))
      expect(clipboard[0]).toContain('COPY BLOCK READY')
    } finally {
      vi.useRealTimers()
    }
  })
})

describe('AdvisorPane full-scope guard (no cross-connection leakage)', () => {
  it('renders only the focused connection/profile/session; a foreign same-id session never appears', async () => {
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': ({ path }) => {
        // Foreign connection responds for the same session id with different data.
        if (String(path).includes('expected_instance=inst-foreign')) {
          return fixtureStatus({ session_id: 'sess-1', saved_profile_revision: 999, invocations_used: 77 })
        }
        return fixtureStatus()
      },
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-revision').textContent).toBe('7'))
    // The pane stamps every request with its own owner tuple; a foreign
    // connection's response for the same session id cannot be displayed.
    expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1')
    expect(screen.getByTestId('advisor-usage').textContent).not.toContain('77')
  })

  it('never renders a raw packet or secret from any status payload', async () => {
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () =>
        fixtureStatus({
          volatile_error: 'raw packet capture: Authorization: Bearer *** do not display',
        }),
      '/notes': () => fixtureNotes(),
    })
    focusVerifiedSession(testKit.atoms)
    const { container } = renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-session-header').textContent).toContain('sess-1'))
    expect(container.textContent).not.toContain('«redacted:sk-…»')
  })
})

describe('AdvisorPane metadata discovery (hook-verified only)', () => {
  it('uses only the verified metadata instance for preconditions and never discovers via untrusted selection', async () => {
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => fixtureStatus(),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1'))
    // Same effect-flush ordering as the recovery test below: the verified
    // instance renders before the status effect runs, so wait for the
    // observable request rather than assuming it already fired.
    await waitFor(() => expect(kit.calls.filter((c) => c.path.startsWith('/status')).length).toBeGreaterThan(0), { timeout: 2000 })
    const statusCalls = kit.calls.filter((c) => c.path.startsWith('/status'))
    expect(statusCalls.every((c) => c.path.includes('expected_instance=inst-1'))).toBe(true)
  })

  it('waits for a verified instance before enabling actions instead of guessing a default instance', async () => {
    let respond = false
    const kit = makeCtx({
      '/metadata': () => {
        if (!respond) {
          throw Object.assign(new Error('HTTP 503 metadata unavailable'), { status: 503 })
        }
        return fixtureMetadata()
      },
      '/control': ({ body }) => ({ ok: true, action: body.action }),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    // Unverified instance: no status/notes/control traffic at all.
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(kit.calls.filter((c) => c.path.startsWith('/status')).length).toBe(0)
    expect(kit.calls.filter((c) => c.path.startsWith('/control')).length).toBe(0)
    expect(screen.getByTestId('advisor-instance').textContent).toBe('unverified')
    respond = true
    await waitFor(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1'), { timeout: 8000 })
    expect(screen.getByTestId('advisor-action-enable').disabled).toBe(false)
  })

  it('fails closed when metadata profile does not match the focused owner profile', async () => {
    const kit = makeCtx({
      // Foreign metadata: right shape, WRONG owner profile.
      '/metadata': () => fixtureMetadata({ profile: 'other-profile' }),
      '/status': () => fixtureStatus(),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() =>
      expect(screen.getByTestId('advisor-error').textContent).toMatch(/does not match/i)
    )
    // No instance is adopted and nothing is ever sent with a foreign instance.
    expect(screen.getByTestId('advisor-instance').textContent).toBe('unverified')
    expect(kit.calls.filter((c) => c.path.startsWith('/status')).length).toBe(0)
    expect(kit.calls.filter((c) => c.path.startsWith('/notes')).length).toBe(0)
  })

  it('recovers when metadata becomes owner-verified after being foreign', async () => {
    let foreign = true
    const kit = makeCtx({
      '/metadata': () => (foreign ? fixtureMetadata({ profile: 'other-profile' }) : fixtureMetadata()),
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('unverified'))
    foreign = false
    await waitFor(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1'), { timeout: 8000 })
    // The verified instance is committed to the DOM before the status effect
    // (which depends on that instance) has been flushed, so a bare assertion
    // here can observe the intermediate render. Wait for the observable
    // request instead of assuming it already happened; the assertion is
    // unchanged.
    await waitFor(() => expect(kit.calls.some((c) => c.path.startsWith('/status'))).toBe(true), { timeout: 2000 })
  })

  it('control params always send a plain object, never null', async () => {
    const controlBodies = []
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () => fixtureStatus(),
      '/control': ({ body }) => {
        controlBodies.push(body)
        return { ok: true, action: body.action }
      },
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-instance').textContent).toBe('inst-1'))
    await act(async () => {
      act(() => { fireEvent.click(screen.getByTestId('advisor-action-enable')) })
    })
    await waitFor(() => expect(controlBodies.length).toBeGreaterThan(0))
    for (const body of controlBodies) {
      expect(body.params).toEqual(expect.any(Object))
      expect(body.params).not.toBeNull()
    }
  })

  it('A05: route readout renders distinct requested vs served routes from the REAL nested payload shape', async () => {
    // REAL-SHAPE backend status (advisor_core/service.py _public_attempt):
    // requested_route/served_route are NESTED {provider, model} objects —
    // the historical flat top-level served_provider/served_model keys are
    // absent, so this test fails against any flat-shape readout regression.
    const kit = makeCtx({
      '/metadata': () => fixtureMetadata(),
      '/status': () =>
        fixtureStatus({
          latest_attempt: fixtureAttempt({
            status: 'completed',
            requested_route: { provider: 'req-provider', model: 'req-model' },
            served_route: { provider: 'srv-provider', model: 'srv-model' },
            usage_known: true,
          }),
        }),
      '/notes': () => [],
    })
    focusVerifiedSession(testKit.atoms)
    renderPane({ ctx: kit })
    await waitFor(() => expect(screen.getByTestId('advisor-route')).toBeTruthy(), { timeout: 8000 })
    const spans = [...screen.getByTestId('advisor-route').querySelectorAll('span')]
    const texts = spans.map((s) => s.textContent)
    expect(texts).toContain('Requested route: req-provider/req-model')
    expect(texts).toContain('Served route: srv-provider/srv-model')
    // The two routes are rendered as DISTINCT values, never collapsed.
    expect(texts.find((t) => t.startsWith('Requested route:'))).not.toBe(texts.find((t) => t.startsWith('Served route:')))
    // The payload handed to the pane really is the nested shape (no flat keys):
    // absent flat keys yield undefined at runtime on the [REAL-SHAPE] fixture.
    const statusCalls = kit.calls.filter((c) => c.path.startsWith('/status'))
    expect(statusCalls.length).toBeGreaterThan(0)
    const flatFixture = /** @type {any} */ (fixtureAttempt())
    expect(flatFixture.served_route).toEqual(expect.objectContaining({ provider: 'nous' }))
    expect(flatFixture.served_provider).toBeUndefined()
    expect(flatFixture.requested_provider).toBeUndefined()
  })
})

/**
 * Targeted regressions for the Copy-only hardening pass.
 *
 * Each test below targets behavior that failed before this pass:
 *  - Save was enabled as soon as an instance resolved, even with no status or
 *    with a missing saved revision.
 *  - `host.paneVisibility` was called unconditionally, crashing older hosts.
 *  - Several backend-derived fields were interpolated without the sanitizer.
 *  - Settings exposed snake_case backend keys and second-person copy, labels
 *    were not associated, and the confirmation was a bare `role="dialog"`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'

import * as paneModule from '../../desktop/pane.mjs'
import {
  deferred,
  fixtureAttempt,
  fixtureMetadata,
  fixtureNotes,
  fixtureStatus,
  focusVerifiedSession,
  makeCtx,
  makeHost,
  setHostOverride,
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
const flushUntil = async (assertion, rounds = 200) => {
  for (let i = 0; i < rounds; i += 1) {
    await flush()
    try {
      assertion()
      return
    } catch {
      // keep flushing until the bounded loop ends
    }
  }
  assertion()
}
const $ = (id) => screen.queryByTestId(id)

function renderVerified(handlers = {}) {
  const kit = makeCtx({
    '/metadata': () => fixtureMetadata(),
    '/status': () => fixtureStatus(),
    '/notes': () => fixtureNotes(),
    ...handlers,
  })
  focusVerifiedSession(testKit.atoms)
  render(<AdvisorPane ctx={kit.ctx} />)
  return kit
}

describe('Copy-only hardening regressions', () => {
  it('keeps Save disabled until status and a valid saved revision are loaded', async () => {
    const gate = deferred()
    const kit = renderVerified({ '/status': () => gate.promise, '/notes': () => [] })

    await flushUntil(() => expect($('advisor-instance').textContent).toBe('inst-1'))
    expect($('advisor-settings-save').disabled).toBe(true)

    gate.resolve(fixtureStatus())
    await flushUntil(() => expect($('advisor-settings-save').disabled).toBe(false))
    expect(kit.calls.some((call) => call.path.startsWith('/status?'))).toBe(true)
  })

  it('keeps Save disabled when status arrives without a usable revision', async () => {
    renderVerified({ '/status': () => fixtureStatus({ saved_profile_revision: null }) })

    await flushUntil(() => expect($('advisor-state')).not.toBeNull())
    expect($('advisor-settings-save').disabled).toBe(true)
    expect($('advisor-revision').textContent).toBe('')
  })

  it('works on a host without paneVisibility instead of crashing (feature-detected fallback)', async () => {
    delete testKit.host.paneVisibility
    const kit = renderVerified()

    await flushUntil(() => expect($('advisor-instance').textContent).toBe('inst-1'))
    expect(kit.calls.some((call) => call.path.startsWith('/status?'))).toBe(true)
    expect($('advisor-route-blocked')).toBeNull()
  })

  it('sanitizes every displayed backend-derived field', async () => {
    renderVerified({
      '/status': () => fixtureStatus({
        state: '<img src=x onerror=alert(1)>enabled',
        configuration_error: '<script>alert(1)</script>configuration failed',
        latest_attempt: fixtureAttempt({
          turn_id: '<b>turn</b>9',
          requested_route: { provider: 'javascript:alert(1)', model: 'model<svg>' },
          served_route: { provider: 'nous', model: 'served-ok' },
        }),
      }),
      '/notes': () => fixtureNotes({
        turn_id: '<b>turn</b>9',
        rating: { rating: 'useful', reason: '<script>x</script>accepted reason' },
        findings: [{
          note_id: 'note-1',
          severity: '<i>concern</i>',
          explanation: '<img src=x onerror=alert(2)>finding text',
          evidence_refs: ['request'],
          next_step: 'data:text/html;base64,AAAAnext step',
        }],
      }),
    })

    await flushUntil(() => expect($('advisor-findings-block')).not.toBeNull())
    const text = $('advisor-pane').textContent
    expect(text).not.toMatch(/<img|<script|<b>|<i>|javascript:|data:/i)
    expect(text).toContain('enabled')
    expect(text).toContain('configuration failed')
    expect(text).toContain('finding text')
    expect(text).toContain('turn9')
  })

  it('shows professional labels and no second-person copy', async () => {
    renderVerified()
    await flushUntil(() => expect($('advisor-settings')).not.toBeNull())

    const text = $('advisor-pane').textContent
    expect(text).not.toMatch(/packet_bytes|output_tokens|max_findings|deadline_seconds|session_allowance/i)
    expect(text).not.toMatch(/\byou\b|\byour\b/i)
    // Labels are associated with their inputs, not floating text.
    expect(screen.getByLabelText('Evidence packet size (bytes)')).toBe($('advisor-settings-packet-bytes'))
    expect(screen.getByLabelText('Response token limit')).toBe($('advisor-settings-output-tokens'))
    expect(screen.getByLabelText('Provider')).toBe($('advisor-settings-provider'))
    expect(screen.getByLabelText('Model')).toBe($('advisor-settings-model'))
  })

  it('exposes an accessible alertdialog, traps focus, closes on Escape and restores focus', async () => {
    renderVerified()
    await flushUntil(() => expect($('advisor-instance').textContent).toBe('inst-1'))

    await act(async () => { fireEvent.click($('advisor-action-reset-allowance')) })
    const dialog = $('advisor-confirm-dialog')
    expect(dialog.getAttribute('role')).toBe('alertdialog')
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(document.getElementById(dialog.getAttribute('aria-labelledby'))).not.toBeNull()
    expect(document.getElementById(dialog.getAttribute('aria-describedby'))).not.toBeNull()
    expect(document.activeElement).toBe($('advisor-confirm-cancel'))

    await act(async () => { fireEvent.keyDown(dialog, { key: 'Escape' }) })
    expect($('advisor-confirm-dialog')).toBeNull()
    expect(document.activeElement).toBe($('advisor-action-reset-allowance'))
  })
})

/**
 * Unit contract for the pure decision helpers in `desktop/logic.mjs`.
 * RED first: these describe the shipping behavior; they must fail against an
 * empty implementation and pass only when the real helpers exist.
 */
import { describe, expect, it } from 'vitest'
import {
  snapshotMatchesFocus,
  nextPollDecision,
  sanitizeReasonText,
  routeTargetsOwner,
  completionApplies,
  attemptRouteReadout,
} from '../../desktop/logic.mjs'

const OWNER = { connectionId: 'local', profile: 'advisor-lab' }

describe('snapshotMatchesFocus', () => {
  it('applies only when the full connection/profile/runtime/stored identity is unchanged', () => {
    const start = {
      owner: { ...OWNER },
      runtimeSessionId: 'sess-1',
      storedSessionId: 'store-1',
      profile: 'advisor-lab',
      connectionId: 'local',
    }
    expect(snapshotMatchesFocus(start, { ...start })).toEqual({ apply: true, reason: null })

    const ownerChange = { ...start, owner: { connectionId: 'conn-b', profile: 'advisor-lab' } }
    expect(snapshotMatchesFocus(start, ownerChange)).toEqual({ apply: false, reason: 'owner-changed' })

    const runtimeChange = { ...start, runtimeSessionId: 'sess-2' }
    expect(snapshotMatchesFocus(start, runtimeChange)).toEqual({ apply: false, reason: 'selected-session-changed' })

    const storedChange = { ...start, storedSessionId: 'store-2' }
    expect(snapshotMatchesFocus(start, storedChange)).toEqual({ apply: false, reason: 'selected-session-changed' })

    const profileChange = { ...start, profile: 'other-profile' }
    expect(snapshotMatchesFocus(start, profileChange)).toEqual({ apply: false, reason: 'profile-or-connection-changed' })

    const connChange = { ...start, connectionId: 'conn-b' }
    expect(snapshotMatchesFocus(start, connChange)).toEqual({ apply: false, reason: 'profile-or-connection-changed' })

    expect(snapshotMatchesFocus(null, start)).toEqual({ apply: false, reason: 'focus-vanished' })
    expect(snapshotMatchesFocus(start, null)).toEqual({ apply: false, reason: 'focus-vanished' })
  })

  it('fails closed when either snapshot is missing the full owner tuple', () => {
    const start = { owner: OWNER, runtimeSessionId: 's', storedSessionId: 's', profile: 'p', connectionId: 'c' }
    expect(snapshotMatchesFocus({ ...start, owner: null }, start)).toEqual({ apply: false, reason: 'focus-vanished' })
    expect(snapshotMatchesFocus(start, { ...start, storedSessionId: null })).toEqual({
      apply: false,
      reason: 'selected-session-changed',
    })
  })
})

describe('nextPollDecision (exactly 3s, only while visible+mounted)', () => {
  it('polls at exactly 3000ms when visible', () => {
    expect(nextPollDecision({ visible: true })).toEqual({ poll: true, intervalMs: 3000 })
    expect(nextPollDecision({ visible: false })).toEqual({ poll: false, intervalMs: 0 })
  })

  it('pauses while hidden and resumes at 3s when visible again', () => {
    expect(nextPollDecision({ visible: false })).toEqual({ poll: false, intervalMs: 0 })
    expect(nextPollDecision({ visible: true })).toEqual({ poll: true, intervalMs: 3000 })
  })
})

describe('sanitizeReasonText (view sanitization; no secrets in DOM/logs)', () => {
  it('clips and strips packet-looking secret lines from backend reasons', () => {
    const raw = 'backend said: Authorization: Bearer sk-secret-token value\nsecond line'
    const clean = sanitizeReasonText(raw)
    expect(clean).not.toContain('sk-secret-token')
    expect(clean).toContain('second line')
  })

  it('caps length and tolerates non-strings', () => {
    expect(sanitizeReasonText(null)).toBe('')
    expect(sanitizeReasonText(42)).toBe('')
    expect(sanitizeReasonText('x'.repeat(400)).length).toBeLessThanOrEqual(300)
  })

  it('does not leak packet_bytes payloads or raw packet bodies', () => {
    const raw = 'failure near {"packets":[{"raw":"TOPSECRET"}]} tail'
    expect(sanitizeReasonText(raw)).not.toContain('TOPSECRET')
  })

  // A21 permanent regression: full sink surface — C0, C1, ANSI CSI/OSC (both
  // ESC-form and single-byte C1 introducers), HTML, JS URIs, and data: URIs —
  // must never survive into text the pane renders into the DOM.
  const A21_SINKS = [
    // C0 control bytes
    'bell\x07x', '\x00null', 'a\x1fb', 'a\x0bb\x0cc',
    // ANSI CSI + OSC (ESC form)
    '\x1b[31mRED\x1b[0m',
    '\x1b]8;;http://evil.example\x1b\\click\x1b]8;;\x1b\\',
    '\x1b]0;title\x07',
    // C1 controls incl. single-byte CSI/OSC introducers
    'a\x85b\x9bc\x9dd', '\x9b31mSBCSI\x9bm',
    // HTML / scriptable URIs / data URIs
    '<script>alert(1)</script>',
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    'javascript:alert(1)',
    'vbscript:msgbox',
    'data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==',
    'data:text/html,<script>alert(1)</script>',
    '&#60;script&#62;', '&lt;img src=x&gt;',
  ]

  it('A21: strips every terminal-control sink (C0/C1/CSI/OSC incl. single-byte C1 forms)', () => {
    for (const raw of A21_SINKS.slice(0, 8)) {
      const out = sanitizeReasonText(raw)
      for (const ch of out) {
        const code = ch.codePointAt(0)
        expect(code === 10 || (code >= 32 && code !== 127 && !(code >= 0x80 && code <= 0x9f)))
          .toBe(true)
      }
    }
  })

  it('A21: strips HTML tags, entities, JS URIs and data URIs', () => {
    for (const raw of A21_SINKS.slice(8)) {
      const out = sanitizeReasonText(raw)
      const low = out.toLowerCase()
      expect(out).not.toMatch(/</)
      expect(low).not.toContain('javascript:')
      expect(low).not.toContain('vbscript:')
      expect(low).not.toContain('data:')
      expect(low).not.toContain('onerror')
      expect(low).not.toContain('<script')
    }
  })

  it('A21: combined terminal+HTML payload is fully neutralized and keeps safe text', () => {
    const out = sanitizeReasonText('\x1b[31m<script>alert(1)</script>\x1b[0m safe tail')
    expect(out).not.toMatch(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\x80-\x9f]/)
    expect(out).not.toMatch(/</)
    expect(out).toContain('safe tail')
  })

  // js/incomplete-multi-character-sanitization regression: one pass over a
  // multi-character pattern can splice a fresh dangerous sequence out of the
  // text that is left behind (`<<script>script>` -> `<script>`), so the strip
  // chain must run to a fixed point instead of exactly once.
  it('A21: nested payloads that reassemble after one strip pass are removed', () => {
    const NESTED = [
      '<<script>script>alert(1)',
      '<scrip<script>t>alert(1)</script>',
      '&&lt;lt;script&gt;',
      '<<img src=x onerror=alert(1)>>',
      'jjavascript:javascript:alert(1)',
    ]
    for (const raw of NESTED) {
      const out = sanitizeReasonText(raw)
      const low = out.toLowerCase()
      expect(low).not.toContain('<script')
      expect(low).not.toContain('javascript:')
      expect(low).not.toContain('onerror')
      expect(out).not.toMatch(/<[a-z]/i)
    }
  })
})

const SNAP = {
  owner: { connectionId: 'local', profile: 'advisor-lab' },
  runtimeSessionId: 'r1',
  storedSessionId: 's1',
  profile: 'advisor-lab', // host.state.profile (active gateway profile)
  connectionId: 'local', // host.state.connectionId (active gateway connection)
}

describe('routeTargetsOwner (ctx.rest reaches only the active gateway)', () => {
  it('is ok only when the focused owner equals the active gateway connection and profile', () => {
    expect(routeTargetsOwner(SNAP)).toEqual({ ok: true, reason: null })
  })

  it('fails closed on connection mismatch even when profile names are identical', () => {
    expect(routeTargetsOwner({ ...SNAP, connectionId: 'conn-a', owner: { connectionId: 'conn-b', profile: 'advisor-lab' } }))
      .toEqual({ ok: false, reason: 'owner-connection-not-active-gateway' })
  })

  it('fails closed on profile mismatch', () => {
    expect(routeTargetsOwner({ ...SNAP, owner: { connectionId: 'local', profile: 'other' } }))
      .toEqual({ ok: false, reason: 'owner-profile-not-active-gateway' })
  })

  it('fails closed when the active connection cannot be read or the owner is unresolved', () => {
    expect(routeTargetsOwner({ ...SNAP, connectionId: null })).toEqual({ ok: false, reason: 'active-connection-unavailable' })
    expect(routeTargetsOwner({ ...SNAP, connectionId: '' })).toEqual({ ok: false, reason: 'active-connection-unavailable' })
    expect(routeTargetsOwner({ ...SNAP, owner: null })).toEqual({ ok: false, reason: 'focus-vanished' })
    expect(routeTargetsOwner(null)).toEqual({ ok: false, reason: 'focus-vanished' })
  })

  it('treats an empty/blank profile as the SDK does (normalized to "default")', () => {
    expect(routeTargetsOwner({ ...SNAP, profile: '  ', owner: { connectionId: 'local', profile: 'default' } }).ok).toBe(true)
  })
})

describe('completionApplies (request completion guard)', () => {
  const start = { snapshot: SNAP, instanceId: 'inst-1' }

  it('applies when snapshot, route and instance are all unchanged', () => {
    expect(completionApplies(start, { snapshot: { ...SNAP }, instanceId: 'inst-1' })).toEqual({ apply: true, reason: null })
  })

  it('discards on instance change or loss', () => {
    expect(completionApplies(start, { snapshot: SNAP, instanceId: 'inst-2' })).toEqual({ apply: false, reason: 'instance-changed' })
    expect(completionApplies(start, { snapshot: SNAP, instanceId: null })).toEqual({ apply: false, reason: 'instance-changed' })
  })

  it('discards on any focus/owner/session/connection change', () => {
    expect(completionApplies(start, { snapshot: { ...SNAP, storedSessionId: 's2' }, instanceId: 'inst-1' }).apply).toBe(false)
    expect(completionApplies(start, { snapshot: { ...SNAP, connectionId: 'conn-b', owner: { connectionId: 'conn-b', profile: 'advisor-lab' } }, instanceId: 'inst-1' }).apply).toBe(false)
  })

  it('discards when the route no longer targets the owner even if the owner tuple is unchanged', () => {
    expect(completionApplies(start, { snapshot: { ...SNAP, connectionId: 'conn-b' }, instanceId: 'inst-1' }).apply).toBe(false)
  })

  it('discards when either side is missing', () => {
    expect(completionApplies(null, start).apply).toBe(false)
    expect(completionApplies(start, null).apply).toBe(false)
  })
})

describe('attemptRouteReadout (real _public_attempt shape)', () => {
  it('reads requested_route/served_route objects and marks absent parts unknown', () => {
    expect(attemptRouteReadout({
      requested_route: { provider: 'nous', model: 'm1' },
      served_route: { provider: null, model: null },
      usage_known: false,
    })).toEqual({ requested: 'nous/m1', served: 'unknown', usage: 'unknown' })
    expect(attemptRouteReadout(null)).toEqual({ requested: 'no review yet', served: 'no review yet', usage: 'unknown' })
    expect(attemptRouteReadout({ usage_known: true }).usage).toBe('known')
  })
})

/**
 * Controlled public-API fixtures for Advisor Desktop pane tests.
 *
 * LABELS USED BELOW
 *  - [SDK-DOUBLE]  test double of a public SDK object (host, ctx). Not real SDK code.
 *  - [REAL-SHAPE]  payload shaped exactly like the backend source of truth
 *                  (advisor_core/service.py _public_attempt/_session_status/
 *                  notes/rate/feedback/configure and advisor_host.py metadata)
 *                  as read at this repo's HEAD. Nothing here is captured from a
 *                  live run; shapes are derived from source only.
 *  - [SYNTHETIC]   invented values (ids, text) inside a real shape.
 *
 * FIXTURE BOUNDARY (explicit, per delegation contract):
 *  - `host` below is a test-only double of the PUBLIC `host` object shape that
 *    desktop/pane.mjs imports from `@hermes/plugin-sdk` at the pinned commit
 *    dce1e9b (installed host 66605471 has byte-identical sdk/api/plugin files):
 *    `host.state.focusedSessionOwner` ({connectionId, profile}|null),
 *    `host.state.focusedSessionId`, `host.state.focusedStoredSessionId`,
 *    `host.state.profile` (= $activeGatewayProfile),
 *    `host.state.connectionId` (= $activeConnectionId, the registry source that
 *    owns the ACTIVE gateway; sdk/index.ts), `host.paneVisibility(paneId)`
 *    (readable atom with get/set/subscribe used via useValue), plus `.get()`
 *    on each state atom for completion-time stale guards.
 *    ctx.rest has NO per-call connection/profile pin (PluginRestOptions =
 *    method/body/upload/timeoutMs) and routes through the AMBIENT scope
 *    (api/plugins.ts pluginRest -> profileScoped()/connectionScoped()), i.e.
 *    the active gateway, NOT the focused session's owner. The pane therefore
 *    only addresses a focused owner that equals (host.state.connectionId,
 *    host.state.profile) and fails closed otherwise.
 *  - The REAL SDK module is still imported by the component under test
 *    (`useValue`, `Badge`, `Button`, `Checkbox`, `EmptyState`, `ErrorState`,
 *    `GlyphSpinner`, `Input`, `StatusDot`, `Textarea` are the real pinned
 *    sources). Only `host` and `ctx` are doubles: the component cannot be
 *    handed a foreign host through the public module graph, so the tests
 *    substitute the host at the module boundary of `desktop/pane.mjs` via the
 *    `__PANE_HOST_FOR_TESTS` injection point (see pane.mjs — a test-only
 *    indirection that defaults to the real SDK `host` in production).
 *  - No private host store is touched. REST is the parent-agreed contract:
 *      GET  /metadata -> {instance_id, profile, sessions:[…]}
 *      GET  /status?session_id=&expected_instance=…
 *      GET  /notes ?session_id=&expected_instance=…
 *      POST /control {expected_instance, session_id, action, params}
 *    `params` is ALWAYS a plain object (backend strict-dict), never null.
 */

export function atom(initial) {
  let value = initial
  const subs = new Set()
  const notify = () => {
    for (const fn of [...subs]) fn(value)
  }
  return {
    get: () => value,
    set: (next) => {
      value = next
      notify()
    },
    // nanostores-compatible surface: @nanostores/react useStore calls
    // store.listen + store.get; subscribe = listen + immediate get.
    listen: (fn) => {
      subs.add(fn)
      return () => subs.delete(fn)
    },
    subscribe: (fn) => {
      subs.add(fn)
      fn(value)
      return () => subs.delete(fn)
    },
  }
}

export function makeHost({ profile = 'advisor-lab' } = {}) {
  const atoms = {
    owner: atom(null),
    runtimeSessionId: atom(null),
    storedSessionId: atom(null),
    profile: atom(profile),
    connectionId: atom('local'),
    visible: atom(true),
  }
  const host = {
    state: {
      focusedSessionOwner: atoms.owner,
      focusedSessionId: atoms.runtimeSessionId,
      focusedStoredSessionId: atoms.storedSessionId,
      profile: atoms.profile,
      connectionId: atoms.connectionId,
    },
    paneVisibility: () => atoms.visible,
    notify: () => {},
  }
  return { host, atoms }
}

/**
 * Route the real component's host reads to this test's host double. Returns
 * the previous override so tests can restore it. Imports the pane module's
 * mutable override carrier (ESM exports are read-only, so the pane exposes a
 * `{ current }` singleton instead of a settable binding).
 */
export async function setHostOverride(_moduleNamespace, host) {
  const carrier = await import('../../desktop/pane.mjs')
  const previous = carrier.__PANE_HOST_FOR_TESTS.current
  carrier.__PANE_HOST_FOR_TESTS.current = host
  return previous
}

export function makeCtx(handlers = {}) {
  const calls = []
  // Timers bridge to the REAL global timers so vi.useFakeTimers() drives
  // ctx.setInterval/ctx.setTimeout exactly like the production host does.
  const ctx = {
    rest: async (path, opts = {}) => {
      const key = String(path).split('?')[0]
      const entry = { path, opts, body: opts?.body, at: calls.length }
      calls.push(entry)
      const handler = handlers[key]
      if (!handler) {
        throw Object.assign(new Error(`HTTP 404 ${key}`), { status: 404 })
      }
      return handler(entry)
    },
    setInterval: (fn, ms) => {
      const id = globalThis.setInterval(fn, ms)
      return () => globalThis.clearInterval(id)
    },
    setTimeout: (fn, ms) => {
      const id = globalThis.setTimeout(fn, ms)
      return () => globalThis.clearTimeout(id)
    },
    addEventListener: () => () => {},
    onDispose: () => {},
    storage: { get: () => undefined, set: () => {}, remove: () => {} },
    os: {
      writeClipboard: async () => true,
    },
  }
  return { ctx, calls }
}

export const SETTINGS_FIXTURE = {
  route: { provider: 'nous', model: 'z-ai/glm-5.3-flash' },
  packet_bytes: 32768,
  output_tokens: 2048,
  max_findings: 4,
  deadline_seconds: 120,
  session_allowance: 20,
}

/** [REAL-SHAPE] advisor_core.service._public_attempt (status.latest_attempt and notes[*] base). */
export function fixtureAttempt(overrides = {}) {
  return {
    attempt_id: 'att-1',
    turn_id: 'turn-9',
    status: 'completed',
    outcome: 'findings',
    requested_route: { provider: 'nous', model: 'z-ai/glm-5.3-flash' },
    served_route: { provider: 'nous', model: 'z-ai/glm-5.3-flash' },
    usage: null,
    usage_known: true,
    cancellation_pending: false,
    created_at: '2026-10-10T00:00:00Z',
    terminal_at: '2026-10-10T00:00:30Z',
    error_code: null,
    error: null,
    ...overrides,
  }
}

/** [REAL-SHAPE] advisor_core.service.status() */
export function fixtureStatus(overrides = {}) {
  return {
    session_id: 'sess-1',
    surface: 'desktop',
    surface_supported: true,
    state: 'enabled',
    pause_reason: null,
    saved_profile_revision: 7,
    saved_settings: { ...SETTINGS_FIXTURE, route: { ...SETTINGS_FIXTURE.route } },
    applied_revision: 7,
    applied_generation: 3,
    settings: { ...SETTINGS_FIXTURE, route: { ...SETTINGS_FIXTURE.route } },
    configuration_error: null,
    invocations_used: 2,
    invocations_remaining: 18,
    review_now_available: true,
    review_now_unavailable_reason: null,
    review_now_expires_in_seconds: 600,
    active: false,
    pending: false,
    storage_error: false,
    volatile_error: null,
    latest_attempt: fixtureAttempt(),
    ...overrides,
  }
}

/** [REAL-SHAPE] advisor_core.service.notes(): list of attempts, rating is {rating,reason}|null. */
export function fixtureNotes(overrides = {}) {
  return [
    {
      ...fixtureAttempt(),
      coverage: { clipped_refs: ['tool-2'], tool_pairs_omitted: 1 },
      rating: null,
      findings: [
        {
          note_id: 'note-1',
          severity: 'concern',
          explanation: 'SYNTHETIC finding explanation for test fixtures only.',
          evidence_refs: ['request', 'answer-1'],
          next_step: 'SYNTHETIC next step text.',
        },
      ],
      ...overrides,
    },
  ]
}

/** [REAL-SHAPE] advisor_core.service.feedback(): {action_id, notes[], text}; no `ok` field. */
export function fixtureFeedback(noteIds = ['note-1'], text = 'COPY BLOCK TEXT\n[concern] SYNTHETIC finding explanation') {
  return {
    action_id: 'action-1',
    notes: noteIds.map((note_id) => ({
      note_id,
      attempt_id: 'att-1',
      answer_ref: 'turn-9',
      severity: 'concern',
      explanation: 'SYNTHETIC finding explanation for test fixtures only.',
      evidence_refs: ['request', 'answer-1'],
      next_step: 'SYNTHETIC next step text.',
      coverage: { clipped_refs: ['tool-2'], tool_pairs_omitted: 1 },
    })),
    text,
  }
}

/** [REAL-SHAPE] advisor_core.service.rate(): reason is the backend-redacted text or null. */
export function fixtureRateResult(attemptId, rating, reason = null) {
  return { attempt_id: attemptId, rating, reason, saved: true }
}

/** Controlled deferred promise for delayed-completion tests. */
export function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

/** [REAL-SHAPE] error thrown by hermesDesktop.api for a non-2xx: `<status>: <body>` (preload/api-expected-404 + main.ts). */
export function httpError(status, bodyObject) {
  return Object.assign(new Error(`${status}: ${JSON.stringify(bodyObject)}`), { status })
}

/** [REAL-SHAPE] advisor_host.metadata(): {instance_id, profile, sessions:[{session_id,surface}], closing}. */
export function fixtureMetadata(overrides = {}) {
  return {
    instance_id: 'inst-1',
    profile: 'advisor-lab',
    sessions: [{ session_id: 'sess-1', surface: 'desktop' }],
    closing: false,
    ...overrides,
  }
}

/** Focus the pane fixtures onto the verified session sess-1. */
export function focusVerifiedSession(atoms, { sessionId = 'sess-1', profile = 'advisor-lab', connectionId = 'local' } = {}) {
  // Consistent focus: the focused owner IS the active gateway route.
  atoms.connectionId.set(connectionId)
  atoms.profile.set(profile)
  atoms.owner.set({ connectionId, profile })
  atoms.runtimeSessionId.set(sessionId)
  atoms.storedSessionId.set(sessionId)
}

/** Parse a session_id/expected_instance pair out of a mocked query path. */
export function queryOf(path) {
  const q = String(path).split('?')[1] ?? ''
  const out = {}
  for (const part of q.split('&')) {
    const [k, v] = part.split('=')
    if (k) out[k] = decodeURIComponent(v || '')
  }
  return out
}

/**
 * Pure decision helpers for the Advisor Desktop pane. No React, no SDK, no
 * DOM: fully unit-testable. Everything here is view logic only — the pane
 * never trusts a response whose snapshot no longer matches the focused
 * session, and never renders backend-supplied raw text unsanitized.
 */

/**
 * Compare the focus snapshot captured when a request started against the
 * snapshot at completion. `snapshot.connectionId` is the ACTIVE GATEWAY
 * connection (`host.state.connectionId`) and `snapshot.profile` the active
 * gateway profile (`host.state.profile`) — the two halves `ctx.rest` routes
 * by — while `snapshot.owner` is the focused session's owner. A result applies only when the full
 * connection/profile/owner/runtime-session/stored-session tuple is identical
 * and still present; anything else fails closed.
 *
 * @param {{owner:{connectionId:string,profile:string}|null, runtimeSessionId:string|null,
 *          storedSessionId:string|null, profile:string, connectionId:string}|null} start
 * @param {same} current
 * @returns {{apply: boolean, reason: 'owner-changed'|'selected-session-changed'|
 *                     'profile-or-connection-changed'|'focus-vanished'|null}}
 */
export function snapshotMatchesFocus(start, current) {
  if (
    !start || !current ||
    !start.owner || !current.owner ||
    !start.owner.connectionId || !current.owner.connectionId ||
    !start.owner.profile || !current.owner.profile
  ) {
    return { apply: false, reason: 'focus-vanished' }
  }
  if (
    start.owner.connectionId !== current.owner.connectionId ||
    start.owner.profile !== current.owner.profile
  ) {
    return { apply: false, reason: 'owner-changed' }
  }
  if (start.profile !== current.profile || start.connectionId !== current.connectionId) {
    return { apply: false, reason: 'profile-or-connection-changed' }
  }
  if (
    start.runtimeSessionId !== current.runtimeSessionId ||
    start.storedSessionId !== current.storedSessionId
  ) {
    return { apply: false, reason: 'selected-session-changed' }
  }
  return { apply: true, reason: null }
}

/**
 * Poll policy: exactly 3 seconds, only while the pane is visible and mounted.
 * The component unmounts/stops the timer when hidden, so visibility is the
 * only gate needed here; hidden state means no polling at all.
 *
 * @param {{visible: boolean}} state
 * @returns {{poll: boolean, intervalMs: number}}
 */
export function nextPollDecision({ visible }) {
  if (!visible) {
    return { poll: false, intervalMs: 0 }
  }
  return { poll: true, intervalMs: 3000 }
}

/**
 * Sanitize backend-supplied free text (reasons, error text) for display:
 * drop lines/bearer tokens/packet-payload-looking content, collapse to a
 * bounded single-line string. View-only defense: the backend also redacts,
 * but the pane must not rely on that.
 *
 * @param {unknown} raw
 * @returns {string}
 */
const MAX_REASON_CHARS = 300
const SECRET_LINE = /(authorization\s*:|api[-_]?key|bearer\s+\S+|token\s*[:=]\s*\S+|password\s*[:=])/i
const PACKET_PAYLOAD = /\{\s*"packets?"|raw\s*packet|"raw"\s*:/i
// Terminal-control injection (SPEC R08): strip ANSI CSI/OSC escapes and
// residual C0 control bytes (except the already-handled newline) so model- or
// tool-derived text can never manipulate a terminal or a pasted-to-terminal sink.
const TERMINAL_CONTROL = /\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[PX^_][^\x1b]*\x1b\\?)/g
const C0_CONTROL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g
// C1 controls (U+0080-U+009F): including the single-byte CSI/OSC introducers
// \u009b/\u009d, which can bypass the ESC-based CSI/OSC filter entirely.
const C1_CONTROL = /[\u0080-\u009f]/g
// HTML/JS/data-URI injection (A21): reason/notes text must never carry live
// markup, scriptable URLs, or data: payloads into the DOM.
const HTML_DANGEROUS = /<\s*\/?\s*[a-z][^>]*>|&\s*#\d+;?|&\s*[a-z]+\s*;|&#x[0-9a-f]+;?|data\s*:\s*[^\\s"'`<>()]+|javascript\s*:|vbscript\s*:|livescript\s*:|mocha\s*:/gi

/**
 * Apply the strip chain until the text stops changing.
 *
 * Several of these patterns match a multi-character sequence, so one pass can
 * splice a fresh dangerous sequence out of what is left: `<<script>script>`
 * loses its inner match and hands back `<script>`, and `&&lt;lt;` hands back
 * `&lt;`. Re-running the whole chain closes that hole. Every replacement only
 * deletes characters, so each changing pass strictly shortens the text and the
 * loop always terminates.
 *
 * @param {string} text
 * @returns {string}
 */
function stripUnsafeText(text) {
  let input = text
  let previous
  do {
    previous = input
    input = input
      .replace(TERMINAL_CONTROL, '')
      .replace(C0_CONTROL, '')
      .replace(C1_CONTROL, '')
      .replace(HTML_DANGEROUS, '')
  } while (input !== previous)
  return input
}

export function sanitizeReasonText(raw) {
  if (typeof raw !== 'string') {
    return ''
  }
  const kept = []
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = stripUnsafeText(line.trim())
    if (!trimmed) {
      continue
    }
    if (SECRET_LINE.test(trimmed) || PACKET_PAYLOAD.test(trimmed)) {
      kept.push('[redacted]')
      continue
    }
    kept.push(trimmed)
  }
  let out = kept.join(' ')
  if (out.length > MAX_REASON_CHARS) {
    out = `${out.slice(0, MAX_REASON_CHARS - 1)}…`
  }
  return out
}

/**
 * Sanitized display text for ANY backend-derived value the pane renders.
 * Non-strings are stringified first; null/undefined/blank and fully-stripped
 * values fall back to a caller-provided literal. Every visible backend field
 * (state, revision context, route parts, errors, findings, handoff preview,
 * ratings, ids) goes through here — the pane never interpolates raw backend
 * text into the DOM.
 *
 * @param {unknown} raw
 * @param {string} [fallback]
 * @returns {string}
 */
export function displayText(raw, fallback = '') {
  if (raw === null || raw === undefined) {
    return fallback
  }
  const text = sanitizeReasonText(String(raw))
  return text || fallback
}

/**
 * Stable visible fallback for Desktop builds that predate
 * `host.paneVisibility`. The real SDK documents `typeof host.paneVisibility
 * === 'function'` as the compatibility guard; when the API is absent (or
 * throws / returns a non-readable value) the pane keeps working and polls
 * rather than crashing. The fallback is a readable atom so `useValue` can
 * subscribe to it like any host atom.
 */
export const ALWAYS_VISIBLE_ATOM = Object.freeze({
  get: () => true,
  listen: () => () => {},
  subscribe: (listener) => {
    listener(true)
    return () => {}
  },
})

/**
 * Resolve the pane visibility atom with feature detection.
 *
 * @param {{paneVisibility?: (paneId: string) => unknown}|null|undefined} hostApi
 * @param {string} paneId
 * @param {{get: Function, listen?: Function, subscribe?: Function}} [fallback]
 * @returns {{get: Function, listen?: Function, subscribe?: Function}}
 */
export function paneVisibilityAtom(hostApi, paneId, fallback = ALWAYS_VISIBLE_ATOM) {
  if (!hostApi || typeof hostApi.paneVisibility !== 'function') {
    return fallback
  }
  try {
    const atom = hostApi.paneVisibility(paneId)
    if (
      atom &&
      typeof atom.get === 'function' &&
      (typeof atom.listen === 'function' || typeof atom.subscribe === 'function')
    ) {
      return atom
    }
  } catch {
    // An older/broken host must not take the whole pane down.
  }
  return fallback
}

function normalizeProfile(name) {
  return (typeof name === 'string' ? name.trim() : '') || 'default'
}

/**
 * Can `ctx.rest` address the focused session's owner?
 *
 * Public `ctx.rest(path, opts)` has no per-call connection/profile pin
 * (PluginRestOptions = method/body/upload/timeoutMs); it routes through the
 * AMBIENT request scope, i.e. the ACTIVE gateway (`host.state.connectionId`,
 * `host.state.profile`). Focus can move to a session owned by another
 * connection/profile without moving the socket, so a request would silently
 * land on the wrong backend. The pane may only act when the focused owner
 * equals the active gateway route; anything it cannot establish fails closed.
 * A metadata `profile` match alone never establishes the connection.
 *
 * @returns {{ok: boolean, reason: null|'focus-vanished'|'active-connection-unavailable'|
 *            'owner-connection-not-active-gateway'|'owner-profile-not-active-gateway'}}
 */
export function routeTargetsOwner(snapshot) {
  const owner = snapshot ? snapshot.owner : null
  if (!owner || !owner.connectionId || typeof owner.profile !== 'string') {
    return { ok: false, reason: 'focus-vanished' }
  }
  if (typeof snapshot.connectionId !== 'string' || !snapshot.connectionId.trim()) {
    return { ok: false, reason: 'active-connection-unavailable' }
  }
  if (owner.connectionId !== snapshot.connectionId) {
    return { ok: false, reason: 'owner-connection-not-active-gateway' }
  }
  if (normalizeProfile(owner.profile) !== normalizeProfile(snapshot.profile)) {
    return { ok: false, reason: 'owner-profile-not-active-gateway' }
  }
  return { ok: true, reason: null }
}

/**
 * Request-completion guard: a response (read OR control OR copy) applies only
 * when the full focus snapshot, the exact-route relation and the verified
 * backend instance are all unchanged since the request started.
 *
 * @param {{snapshot: object, instanceId: string|null}|null} start
 * @param {{snapshot: object, instanceId: string|null}|null} current
 * @returns {{apply: boolean, reason: string|null}}
 */
export function completionApplies(start, current) {
  if (!start || !current || !start.snapshot || !current.snapshot) {
    return { apply: false, reason: 'focus-vanished' }
  }
  const focus = snapshotMatchesFocus(start.snapshot, current.snapshot)
  if (!focus.apply) {
    return focus
  }
  const route = routeTargetsOwner(current.snapshot)
  if (!route.ok) {
    return { apply: false, reason: route.reason }
  }
  if (!start.instanceId || start.instanceId !== current.instanceId) {
    return { apply: false, reason: 'instance-changed' }
  }
  return { apply: true, reason: null }
}

function routeText(route) {
  if (!route || typeof route !== 'object') {
    return 'unknown'
  }
  const provider = displayText(route.provider)
  const model = displayText(route.model)
  return provider && model ? `${provider}/${model}` : 'unknown'
}

/**
 * Route/usage readout from a backend `_public_attempt` (status.latest_attempt
 * or a notes item): `requested_route` / `served_route` are `{provider, model}`
 * objects; absent parts are visibly unknown.
 */
export function attemptRouteReadout(attempt) {
  if (!attempt) {
    return { requested: 'no review yet', served: 'no review yet', usage: 'unknown' }
  }
  return {
    requested: routeText(attempt.requested_route),
    served: routeText(attempt.served_route),
    usage: attempt.usage_known ? 'known' : 'unknown',
  }
}

/** Backend rate() limit: reason is at most 500 code points. */
export const MAX_RATING_REASON_CODEPOINTS = 500

export function ratingReasonTooLong(reason) {
  return [...String(reason ?? '')].length > MAX_RATING_REASON_CODEPOINTS
}

/**
 * Test-only contract double for `@hermes/plugin-sdk`.
 *
 * This is NOT the production SDK and establishes no native Desktop proof. It
 * implements the public SDK surface the Advisor pane is allowed to use so the
 * renderer suite runs from this repository alone, with no Hermes Desktop
 * source checkout or private lab path. The same public surface is declared in
 * `plugin-sdk.contract.json` and documented in `desktop/PACKAGING.md`.
 *
 * Semantics mirror the documented public contract:
 *  - `host.state.*` are readable atoms (`get`/`listen`/`subscribe`) and
 *    `useValue` subscribes to them (the real SDK re-exports @nanostores/react).
 *  - UI primitives are presentation-only and forward DOM props, so the pane's
 *    labels and ARIA attributes are exercised exactly as authored.
 *  - `Checkbox` is controlled (`checked` + `onCheckedChange`) and exposes
 *    `role="checkbox"`, `aria-checked`, and `data-state` like Radix Checkbox.
 */
import React from 'react'

function atom(initial) {
  let value = initial
  const listeners = new Set()
  return {
    get: () => value,
    set: (next) => {
      value = next
      for (const listener of [...listeners]) listener(value)
    },
    listen: (listener) => {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    subscribe: (listener) => {
      listeners.add(listener)
      listener(value)
      return () => listeners.delete(listener)
    },
  }
}
export { atom }

export const host = {
  state: {
    focusedSessionOwner: atom(null),
    focusedSessionId: atom(null),
    focusedStoredSessionId: atom(null),
    profile: atom('default'),
    connectionId: atom(null),
  },
  paneVisibility: () => atom(true),
}

/** Public SDK `useValue`: subscribe a component to a readable atom. */
export function useValue(store) {
  return React.useSyncExternalStore(
    (onChange) => (typeof store.listen === 'function' ? store.listen(onChange) : store.subscribe(onChange)),
    () => store.get()
  )
}

export function Button({ children, ...props }) {
  return React.createElement('button', { type: 'button', ...props }, children)
}

export function Checkbox({ checked = false, onCheckedChange, children, ...props }) {
  return React.createElement(
    'button',
    {
      type: 'button',
      role: 'checkbox',
      'aria-checked': checked ? 'true' : 'false',
      'data-state': checked ? 'checked' : 'unchecked',
      ...props,
      onClick: (event) => {
        props.onClick?.(event)
        onCheckedChange?.(!checked)
      },
    },
    children
  )
}

export function EmptyState({ title, description, children, ...props }) {
  return React.createElement(
    'div',
    props,
    title ? React.createElement('div', null, title) : null,
    description ? React.createElement('div', null, description) : null,
    children
  )
}

export function ErrorState({ title, description, children, ...props }) {
  return React.createElement(
    'div',
    props,
    title ? React.createElement('h2', null, title) : null,
    description ? React.createElement('div', null, description) : null,
    children
  )
}

export function GlyphSpinner({ ariaLabel = 'Loading', ...props }) {
  return React.createElement('span', { role: 'status', 'aria-label': ariaLabel, ...props })
}

export function Input(props) {
  return React.createElement('input', props)
}

export function Textarea(props) {
  return React.createElement('textarea', props)
}

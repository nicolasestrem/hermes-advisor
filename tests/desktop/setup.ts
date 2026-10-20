/** Portable test setup: cleanup between files, no app or lab dependencies. */
import { cleanup } from '@testing-library/react'
import { afterEach } from 'vitest'

afterEach(() => {
  cleanup()
})

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

// Cleanup after each test case
afterEach(() => {
  cleanup()
})

// Mock window.matchMedia for tests
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Mock ResizeObserver / IntersectionObserver.
//
// Real classes rather than `vi.fn(() => ({...}))`: floating-ui — which Radix's
// popper uses for every dropdown and popover — calls `new ResizeObserver(...)`,
// and a mock function is not a constructor, so any test that opens a menu died
// with "is not a constructor" before reaching its assertions. Also assigned on
// globalThis, since library code reaches for whichever is in scope.
class MockObserver {
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
  takeRecords = vi.fn(() => [])
  root = null
  rootMargin = ''
  thresholds: number[] = []
}

window.ResizeObserver = MockObserver as unknown as typeof ResizeObserver
globalThis.ResizeObserver = MockObserver as unknown as typeof ResizeObserver
window.IntersectionObserver = MockObserver as unknown as typeof IntersectionObserver
globalThis.IntersectionObserver = MockObserver as unknown as typeof IntersectionObserver

// Mock scrollTo
window.scrollTo = vi.fn()

// Mock clipboard API
Object.assign(navigator, {
  clipboard: {
    writeText: vi.fn().mockImplementation(() => Promise.resolve()),
    readText: vi.fn().mockImplementation(() => Promise.resolve('')),
  },
})

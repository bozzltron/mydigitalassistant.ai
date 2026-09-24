import { createRoot } from 'solid-js'
import '@solidjs/testing-library'
import { expect, vi } from 'vitest'
import * as matchers from '@testing-library/jest-dom/matchers'

expect.extend(matchers)

// Create a persistent root for the entire test run
// This ensures all module-level signals are created inside a root
let disposeTestRoot: () => void
createRoot((dispose) => {
  disposeTestRoot = dispose
})

// Make dispose function available globally for cleanup if needed
;(globalThis as any).__disposeTestRoot = disposeTestRoot

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
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

Object.defineProperty(window, 'SpeechRecognition', {
  writable: true,
  value: vi.fn().mockImplementation(() => ({
    continuous: true,
    interimResults: true,
    lang: 'en-US',
    start: vi.fn(),
    stop: vi.fn(),
    abort: vi.fn(),
    onresult: null,
    onerror: null,
    onend: null,
  })),
})

Object.defineProperty(window, 'webkitSpeechRecognition', {
  writable: true,
  value: window.SpeechRecognition,
})

// jsdom has no speechSynthesis; stub it like the other Web Speech APIs above.
const speechSynthesisMock = {
  getVoices: vi.fn(() => [
    { name: 'Default', lang: 'en-US', default: true, localService: true, url: '' },
  ]),
  speak: vi.fn(),
  cancel: vi.fn(),
  pause: vi.fn(),
  resume: vi.fn(),
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
  onvoiceschanged: null,
}
Object.defineProperty(window, 'speechSynthesis', {
  writable: true,
  value: speechSynthesisMock,
})

// Default fetch stub: component tests must not hit the real network.
// api.test.ts overrides global.fetch at module scope for its own cases.
const defaultFetchResponse = {
  ok: true,
  status: 200,
  statusText: 'OK',
  json: async () => ({}),
  text: async () => '',
} as Response
global.fetch = vi.fn(async () => defaultFetchResponse) as unknown as typeof fetch
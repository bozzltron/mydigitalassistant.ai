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
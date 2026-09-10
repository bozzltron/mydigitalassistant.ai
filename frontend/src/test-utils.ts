import { createRoot } from 'solid-js'

export function runWithRoot<T>(fn: () => T): T {
  let result: T
  createRoot(() => {
    result = fn()
  })
  return result!
}

export function createTestRoot() {
  const dispose = createRoot(() => {})
  return dispose
}
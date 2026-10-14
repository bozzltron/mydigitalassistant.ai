import { describe, expect, it, vi } from 'vitest'
import { debug } from './logger'

describe('debug', () => {
  it('actually logs in dev — it was a silent no-op', () => {
    // Regression: `isDev()` read `import.meta.env` through a variable, which Vite
    // does not inject the env object into — so `import.meta.env` was undefined in
    // this module and every `debug()` call in the app was a no-op. Reading the
    // literal `import.meta.env.DEV` is what makes Vite inject it.
    const spy = vi.spyOn(console, 'log').mockImplementation(() => {})
    debug('probe')
    expect(spy).toHaveBeenCalledWith('probe')
    spy.mockRestore()
  })
})

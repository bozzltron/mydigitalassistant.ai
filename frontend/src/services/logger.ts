/**
 * Dev-only diagnostics.
 *
 * The app is local-first and privacy-first: transcripts, memory payloads and
 * voice state must not land in a production browser console where anyone with
 * devtools can read them. Route every non-error log through here; `console.error`
 * and `console.warn` stay as-is for genuine failures.
 */

const isDev = (): boolean => {
  try {
    // Read `import.meta.env` *literally*. Vite injects the env object into a module
    // only where it sees that exact expression; going through a variable
    // (`const meta = import.meta; meta.env?.DEV`) is invisible to the injection, so
    // `import.meta.env` was `undefined` here and `isDev()` returned false — which
    // made every `debug()` call in the app a silent no-op in dev.
    return import.meta.env?.DEV === true
  } catch {
    return false
  }
}

export function debug(...args: unknown[]): void {
  if (isDev()) {
    console.log(...args)
  }
}

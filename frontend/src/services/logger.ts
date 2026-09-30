/**
 * Dev-only diagnostics.
 *
 * The app is local-first and privacy-first: transcripts, memory payloads and
 * voice state must not land in a production browser console where anyone with
 * devtools can read them. Route every non-error log through here; `console.error`
 * and `console.warn` stay as-is for genuine failures.
 */

type ViteImportMeta = ImportMeta & { env?: { DEV?: boolean; PROD?: boolean } }

const isDev = (): boolean => {
  try {
    const meta = import.meta as ViteImportMeta
    return meta.env?.DEV === true
  } catch {
    return false
  }
}

export function debug(...args: unknown[]): void {
  if (isDev()) {
    console.log(...args)
  }
}

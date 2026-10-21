/**
 * Prepare agent-authored HTML for display in a sandboxed iframe.
 *
 * The `sandbox` attribute is the real guarantee: without `allow-same-origin` the
 * document gets an opaque origin, so it cannot read the app's storage, call the
 * memory API, or reach `window.parent`. This function adds the other half — a
 * CSP that forbids network egress — because a sandbox still permits
 * `<img src="https://…">`, and a report that phones home breaks the project's
 * no-telemetry rule even when the file is otherwise harmless.
 *
 * The meta tag has to land in `<head>` (or before any body content), or the
 * parser may drop it — hence most-specific-first placement rather than a prefix.
 */
const CSP = [
  "default-src 'none'",
  "style-src 'unsafe-inline'",
  "script-src 'unsafe-inline'",
  'img-src data: blob:',
  'font-src data:',
].join('; ')

const CSP_META = `<meta http-equiv="Content-Security-Policy" content="${CSP}">`

export function sandboxedHtml(html: string): string {
  const head = /<head[^>]*>/i.exec(html)
  if (head) {
    const at = head.index + head[0].length
    return html.slice(0, at) + CSP_META + html.slice(at)
  }
  const doctype = /^\s*<!doctype[^>]*>/i.exec(html)
  if (doctype) {
    return doctype[0] + CSP_META + html.slice(doctype[0].length)
  }
  return CSP_META + html
}

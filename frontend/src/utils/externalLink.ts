/**
 * True for an absolute http(s) URL that points off this origin.
 *
 * Relative links, same-origin links, and non-http schemes (`mailto:`, `tel:`,
 * `#anchors`) return false — those are not "leaving the assistant" and do not
 * need a confirmation.
 */
export function isExternalHttpUrl(href: string | null | undefined): boolean {
  if (!href) return false
  try {
    const url = new URL(href, window.location.href)
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return false
    return url.origin !== window.location.origin
  } catch {
    return false
  }
}

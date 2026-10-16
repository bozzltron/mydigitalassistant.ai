/**
 * The one markdown renderer.
 *
 * `marked` is configured **here**, not at a component's import site. `marked.use` is
 * global state, so a config written inside `MessageContent.tsx` only applied when that
 * component happened to be loaded. One module, loaded once, removes that class of bug.
 *
 * Note what the `link` override can and cannot do: it sets `rel`, but **`target` does
 * not survive sanitizing** — DOMPurify drops it. That is fine, because opening a link
 * in a new tab is not the anchor's job here: `ExternalLinkGuard` is a document-level
 * capture listener that intercepts every off-origin anchor (markdown links come from
 * `innerHTML` and cannot carry a per-link handler) and opens it in a new tab *after
 * confirming*, which is the privacy behaviour the project wants. Do not add
 * `target="_blank"` here expecting it to work.
 *
 * Every caller sanitizes: the input is model output, a fetched document, or a stored
 * run summary.
 */
import DOMPurify from 'dompurify'
import { marked } from 'marked'

marked.use({
  renderer: {
    link(token) {
      const href = token.href
      const title = token.title ? ` title="${token.title}"` : ''
      return `<a href="${href}"${title} target="_blank" rel="noopener">${token.text}</a>`
    },
  },
})

/** Markdown as sanitized HTML, safe to hand to `innerHTML`. */
export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(parseMarkdown(text))
}

/**
 * Markdown as HTML **without** the sanitize pass — for a caller that annotates the
 * markup afterwards and sanitizes at the render site. If you are not doing that, use
 * `renderMarkdown`.
 */
export function parseMarkdown(text: string): string {
  return marked.parse(text) as string
}

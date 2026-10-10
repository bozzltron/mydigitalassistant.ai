import type { FileEntry } from '../types'

/**
 * Turn mentions of **known files** into links to the Files page
 * (`/files?file=<frame_id>`), so a filename the agent names is one click from
 * its contents and editor.
 *
 * Only files that exist are linked — a mention is matched against the loaded
 * file list, never by a filename-shaped pattern — so a name that is not a file
 * ("readme.md" you don't have) stays plain text and there are no dead links.
 */

export interface FileLink {
  id: string
  name: string
}

/** Names shorter than this are too generic to linkify ("a.txt", "log"). */
const MIN_NAME_LENGTH = 5

/** Never linkify inside a code block, an existing link, or a grounding tooltip. */
const SKIP_SELECTOR = 'pre, a, .confidence-tooltip'

/** A filename is a whole token: not glued to a word, dash, or underscore. */
const WORDISH = /[A-Za-z0-9_-]/

/** Build the link set from the file list: display name → frame id, deduped. */
export function fileLinksFrom(files: FileEntry[]): FileLink[] {
  const seen = new Set<string>()
  const out: FileLink[] = []
  for (const file of files) {
    const name = (file.file_name || file.name || '').trim()
    if (name.length < MIN_NAME_LENGTH) continue
    const key = name.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ id: file.id, name })
  }
  return out
}

/**
 * Wrap mentions of known files in already-sanitized `html` with an anchor to the
 * Files page. Returns the input unchanged when there is nothing to link (or
 * outside a DOM).
 */
export function linkifyFileMentions(html: string, links: FileLink[]): string {
  if (!html || links.length === 0 || typeof document === 'undefined') return html
  // Longest name first, so a shorter name cannot pre-empt a longer overlapping
  // one (the same ordering rule the grounding annotator uses).
  const usable = links
    .filter((link) => link.name.length >= MIN_NAME_LENGTH)
    .sort((a, b) => b.name.length - a.name.length)
  if (usable.length === 0) return html

  const root = document.createElement('div')
  root.innerHTML = html
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    if (node.parentElement?.closest(SKIP_SELECTOR)) continue
    textNodes.push(node)
  }
  for (const node of textNodes) linkifyNode(node, usable)
  return root.innerHTML
}

/** True when the match at [index, index+length) is a whole token. */
function isBounded(text: string, index: number, length: number): boolean {
  const before = index > 0 ? (text[index - 1] ?? '') : ''
  const after = index + length < text.length ? (text[index + length] ?? '') : ''
  return !WORDISH.test(before) && !WORDISH.test(after)
}

/** Split one text node around every known-filename mention it contains. */
function linkifyNode(node: Text, links: FileLink[]): void {
  let current: Text | null = node
  while (current) {
    const text: string = current.nodeValue ?? ''
    const lower = text.toLowerCase()
    let best: { index: number; length: number; link: FileLink } | null = null
    for (const link of links) {
      const needle = link.name.toLowerCase()
      let from = 0
      for (;;) {
        const index = lower.indexOf(needle, from)
        if (index === -1) break
        if (isBounded(text, index, link.name.length)) {
          if (
            !best ||
            index < best.index ||
            (index === best.index && link.name.length > best.length)
          ) {
            best = { index, length: link.name.length, link }
          }
          break
        }
        from = index + 1
      }
    }
    if (!best) break

    const before: string = text.slice(0, best.index)
    const matched: string = text.slice(best.index, best.index + best.length)
    const after: string = text.slice(best.index + best.length)
    const parent = current.parentNode
    if (!parent) break

    const anchor = document.createElement('a')
    anchor.className = 'file-mention'
    anchor.setAttribute('href', `/files?file=${best.link.id}`)
    anchor.setAttribute('title', `Open ${best.link.name} in Files`)
    anchor.textContent = matched

    if (before) parent.insertBefore(document.createTextNode(before), current)
    parent.insertBefore(anchor, current)
    const afterNode: Text | null = after ? document.createTextNode(after) : null
    if (afterNode) parent.insertBefore(afterNode, current)
    parent.removeChild(current)
    current = afterNode
  }
}

import type { ChatMessage, Slot } from '../types/chat'

/**
 * A learned value that the answer may rest on. The frontend annotates the
 * occurrence(s) of `value` in the answer with a dotted underline whose tooltip
 * shows which frame/slot it came from and how confident the memory is.
 */
export interface GroundingSlot {
  frame_name: string
  key: string
  value: string
  confidence?: number
}

/** Values shorter than this are too generic to annotate ("6", "no", "ai"). */
const MIN_VALUE_LENGTH = 3

/** Never annotate inside code, links, or an existing annotation. */
const SKIP_SELECTOR = 'code, pre, a, .confidence-tooltip'

/**
 * Slots learned this turn (conversation + search extraction), ready to annotate.
 * Deduped and ordered longest-value-first so a shorter value cannot pre-empt a
 * longer overlapping one.
 */
export function collectGroundingSlots(message: ChatMessage): GroundingSlot[] {
  const meta = message.meta
  const slots: Slot[] = [
    ...(meta?.extraction_summary?.slots ?? []),
    ...(meta?.search_extraction_summary?.slots ?? []),
  ]
  const seen = new Set<string>()
  const out: GroundingSlot[] = []
  for (const slot of slots) {
    const value = (slot.value ?? '').trim()
    if (value.length < MIN_VALUE_LENGTH) continue
    const dedupeKey = `${slot.frame_name}.${slot.key}.${value}`.toLowerCase()
    if (seen.has(dedupeKey)) continue
    seen.add(dedupeKey)
    out.push({
      frame_name: slot.frame_name,
      key: slot.key,
      value,
      confidence: slot.confidence,
    })
  }
  return out.sort((a, b) => b.value.length - a.value.length)
}

function tooltipFor(slot: GroundingSlot): string {
  const pct = Math.round((slot.confidence ?? 0) * 100)
  return `${slot.frame_name}.${slot.key} · ${pct}% confident`
}

/**
 * Wrap occurrences of grounded slot values in already-sanitized `html` with a
 * dotted-underline span carrying a confidence tooltip. Returns the input
 * unchanged when there is nothing to annotate (or outside a DOM).
 */
export function applyGrounding(html: string, slots: GroundingSlot[]): string {
  if (!html || slots.length === 0 || typeof document === 'undefined') return html
  const root = document.createElement('div')
  root.innerHTML = html
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const textNodes: Text[] = []
  while (walker.nextNode()) {
    const node = walker.currentNode as Text
    if (node.parentElement?.closest(SKIP_SELECTOR)) continue
    textNodes.push(node)
  }
  for (const node of textNodes) annotateNode(node, slots)
  return root.innerHTML
}

/** Split one text node around every grounded value it contains. */
function annotateNode(node: Text, slots: GroundingSlot[]): void {
  let current: Text | null = node
  while (current) {
    const text: string = current.nodeValue ?? ''
    const lower = text.toLowerCase()
    let best: { index: number; length: number; slot: GroundingSlot } | null = null
    for (const slot of slots) {
      const index = lower.indexOf(slot.value.toLowerCase())
      if (index === -1) continue
      if (!best || index < best.index || (index === best.index && slot.value.length > best.length)) {
        best = { index, length: slot.value.length, slot }
      }
    }
    if (!best) break

    const before: string = text.slice(0, best.index)
    const matched: string = text.slice(best.index, best.index + best.length)
    const after: string = text.slice(best.index + best.length)
    const parent = current.parentNode
    if (!parent) break

    const span = document.createElement('span')
    span.className = 'confidence-tooltip'
    span.setAttribute('data-confidence', tooltipFor(best.slot))
    span.textContent = matched

    if (before) parent.insertBefore(document.createTextNode(before), current)
    parent.insertBefore(span, current)
    const afterNode: Text | null = after ? document.createTextNode(after) : null
    if (afterNode) parent.insertBefore(afterNode, current)
    parent.removeChild(current)
    current = afterNode
  }
}

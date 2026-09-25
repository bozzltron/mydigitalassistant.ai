/**
 * Shared rendering helpers for the Brain Observatory graph components
 * (BrainGraph 2D + BrainGraphSigma 3D). Kept in one place so both views
 * render nodes/edges/tooltips identically.
 */
import type { BrainConflict, GraphNode } from '../../types'

export const TYPE_COLORS: Record<string, string> = {
  person: '#f78166',
  concept: '#d2a8ff',
  event: '#79c0ff',
  household: '#7ee787',
  entity: '#ffa657',
}

export function typeColor(type: string): string {
  return TYPE_COLORS[type] ?? '#ffa657'
}

export interface RelationGroup {
  re: RegExp
  color: string
  label: string
}

export const RELATION_GROUPS: RelationGroup[] = [
  { re: /^(is_a|instance_of|type_of|part_of|has|subclass_of)$/, color: '#79c0ff', label: 'taxonomy / structure' },
  { re: /(located_in|based_in|place|city|country)/, color: '#7ee787', label: 'spatial' },
  { re: /(founded|follows|inquir|member|works_for|created|produced|wrote|compared|participation)/, color: '#f78166', label: 'social / agency' },
  { re: /(source|citation|reference|forecast|compare|lists)/, color: '#d2a8ff', label: 'informational' },
  { re: /^related_to$|^associated/, color: '#4a5470', label: 'related (generic)' },
]
const FALLBACK_EDGE_COLOR = '#8b9bb8'

export function relationColor(relationType: string): string {
  for (const g of RELATION_GROUPS) {
    if (g.re.test(relationType)) return g.color
  }
  return FALLBACK_EDGE_COLOR
}

export function nodeRadius(d: { confidence?: number }): number {
  const base = 4
  const conf = d.confidence || 0.5
  return base + conf * 8
}

export function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16)
  const g = parseInt(hex.slice(3, 5), 16)
  const b = parseInt(hex.slice(5, 7), 16)
  return `rgba(${r},${g},${b},${alpha})`
}

/** Escape a string for safe interpolation into an HTML string. */
export function esc(value: string | null | undefined): string {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function slotLines(d: GraphNode): string {
  const slots = (d.slots || []).slice(0, 5)
  const lines = slots.map(s => `
    <div class="tooltip-slot">
      <span class="tooltip-slot-key">${esc(s.key)}:</span>
      <span class="tooltip-slot-val">${esc(s.value)}</span>
      ${s.confidence != null ? `<span class="tooltip-slot-conf" title="slot confidence">${Math.round(s.confidence * 100)}%</span>` : ''}
    </div>
  `).join('')
  return `${lines}${(d.slots?.length || 0) > 5 ? `<div style="color:var(--text-dim)">+${d.slots!.length - 5} more</div>` : ''}`
}

function conflictLines(conflicts: BrainConflict[]): string {
  return conflicts.map(c => `
    <div class="tooltip-conflict">
      <div class="tooltip-conflict-line">
        <span class="tooltip-conflict-key">${esc(c.slot_key)}</span>:
        <span class="tooltip-conflict-old">${esc(c.existing_value ?? '∅')}</span>
        →
        <span class="tooltip-conflict-new">${esc(c.new_value ?? '∅')}</span>
      </div>
      <div class="tooltip-conflict-actions">
        <button data-conflict-resolve data-conflict-id="${c.id}" data-conflict-value="${esc(c.existing_value ?? '')}">Keep ${esc(c.existing_value ?? 'old')}</button>
        <button class="use-new" data-conflict-resolve data-conflict-id="${c.id}" data-conflict-value="${esc(c.new_value ?? '')}">Use ${esc(c.new_value ?? 'new')}</button>
      </div>
    </div>
  `).join('')
}

export function tooltipHtml(d: GraphNode, conflictsByFrame: Record<number, BrainConflict[]>): string {
  const conf = Math.round((d.confidence || 0.5) * 100)
  const pri = Math.round((d.priority || 0.5) * 100)
  const frameConflicts = conflictsByFrame[d.id] || []

  return `
    <div class="tooltip-name" style="color:${typeColor(d.type)}">${esc(d.name)}</div>
    <div class="tooltip-type">${esc(d.type)}</div>
    <div class="tooltip-meta">
      confidence: ${conf}% &nbsp;|&nbsp; priority: ${pri}%
      ${d.essential ? '&nbsp;|&nbsp; <span style="color:var(--warning)">essential</span>' : ''}
      ${d.hasConflict ? '&nbsp;|&nbsp; <span style="color:var(--error)">conflict</span>' : ''}
    </div>
    ${d.slots?.length ? `<div class="tooltip-slots">${slotLines(d)}</div>` : ''}
    ${frameConflicts.length ? `<div class="tooltip-conflicts"><div class="tooltip-conflicts-title">Pending conflicts</div>${conflictLines(frameConflicts)}</div>` : ''}
  `
}
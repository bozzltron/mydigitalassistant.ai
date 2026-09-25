import { describe, it, expect } from 'vitest'
import {
  TYPE_COLORS,
  typeColor,
  relationColor,
  nodeRadius,
  hexToRgba,
  esc,
  tooltipHtml,
} from './brainLib'
import type { BrainConflict, GraphNode } from '../../types'

describe('typeColor', () => {
  it('maps known types and falls back for unknown ones', () => {
    expect(typeColor('person')).toBe(TYPE_COLORS.person)
    expect(typeColor('concept')).toBe(TYPE_COLORS.concept)
    expect(typeColor('unknown-type')).toBe('#ffa657')
  })
})

describe('relationColor', () => {
  it('groups relation types into edge color families', () => {
    expect(relationColor('is_a')).toBe('#79c0ff') // taxonomy / structure
    expect(relationColor('located_in')).toBe('#7ee787') // spatial
    expect(relationColor('works_for')).toBe('#f78166') // social / agency
    expect(relationColor('source')).toBe('#d2a8ff') // informational
    expect(relationColor('related_to')).toBe('#4a5470') // generic
    expect(relationColor('totally_unknown_relation')).toBe('#8b9bb8')
  })
})

describe('nodeRadius', () => {
  it('sizes nodes by confidence with a 4px base and 0.5 default', () => {
    expect(nodeRadius({})).toBe(8)
    expect(nodeRadius({ confidence: 0.5 })).toBe(8)
    expect(nodeRadius({ confidence: 0 })).toBe(8)
    expect(nodeRadius({ confidence: 0.9 })).toBe(11.2)
  })
})

describe('hexToRgba', () => {
  it('converts hex colors to rgba strings', () => {
    expect(hexToRgba('#79c0ff', 0.12)).toBe('rgba(121,192,255,0.12)')
    expect(hexToRgba('#000000', 1)).toBe('rgba(0,0,0,1)')
  })
})

describe('esc', () => {
  it('escapes HTML-significant characters', () => {
    expect(esc('<script>"x"&\'y\'')).toBe('&lt;script&gt;&quot;x&quot;&amp;&#39;y&#39;')
    expect(esc(null)).toBe('')
    expect(esc(undefined)).toBe('')
  })
})

function frame(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 1,
    name: 'Alice',
    type: 'person',
    confidence: 0.8,
    priority: 0.6,
    essential: 1,
    hasConflict: false,
    ...overrides,
  }
}

describe('tooltipHtml', () => {
  it('renders name, type, confidence and priority', () => {
    const html = tooltipHtml(frame(), {})
    expect(html).toContain('class="tooltip-name"')
    expect(html).toContain('Alice')
    expect(html).toContain('person')
    expect(html).toContain('confidence: 80%')
    expect(html).toContain('priority: 60%')
  })

  it('escapes the frame name before interpolation', () => {
    const html = tooltipHtml(frame({ name: '<img src=x onerror=alert(1)>' }), {})
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
    expect(html).not.toContain('<img src=x')
  })

  it('renders up to 5 slots and flags extras', () => {
    const slots = Array.from({ length: 7 }, (_, i) => ({
      id: i,
      frame_id: 1,
      key: `k${i}`,
      value: `v${i}`,
      confidence: 0.5 + i * 0.05,
      essential: 0,
      priority: 0.5,
      source_type: null,
      source_url: null,
      source_reliability: null,
      source_episode_id: null,
      updated_at: null,
      last_strengthened_at: null,
    }))
    const html = tooltipHtml(frame({ slots }), {})
    for (let i = 0; i < 5; i++) expect(html).toContain(`k${i}`)
    expect(html).not.toContain('k5')
    expect(html).toContain('+2 more')
  })

  it('escapes slot values inside the HTML', () => {
    const slots = [
      {
        id: 1,
        frame_id: 1,
        key: 'note',
        value: '<b>x</b>',
        confidence: 0.9,
        essential: 0,
        priority: 0.5,
        source_type: null,
        source_url: null,
        source_reliability: null,
        source_episode_id: null,
        updated_at: null,
        last_strengthened_at: null,
      },
    ]
    const html = tooltipHtml(frame({ slots }), {})
    expect(html).toContain('&lt;b&gt;x&lt;/b&gt;')
  })

  it('renders pending-conflict resolve actions with escaped values', () => {
    const conflict: BrainConflict = {
      id: 12,
      frame_id: 1,
      slot_key: 'nickname',
      existing_value: '<old>',
      new_value: "Al'ice",
      resolved_value: null,
      status: 'pending',
      created_at: null,
      resolved_at: null,
    }
    const html = tooltipHtml(frame({ hasConflict: true }), { 1: [conflict] })
    expect(html).toContain('data-conflict-resolve')
    expect(html).toContain(`data-conflict-id="12"`)
    expect(html).toContain('data-conflict-value="&lt;old&gt;"')
    expect(html).toContain('data-conflict-value="Al&#39;ice"')
  })
})
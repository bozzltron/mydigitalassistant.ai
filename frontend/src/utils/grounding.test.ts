import { describe, it, expect } from 'vitest'
import { collectGroundingSlots, applyGrounding } from './grounding'
import type { ChatMessage } from '../types/chat'

const msg = (meta: ChatMessage['meta']): ChatMessage => ({
  role: 'assistant',
  content: 'text',
  id: 'm',
  meta,
})

describe('collectGroundingSlots', () => {
  it('pulls values from both extraction summaries, dropping short values', () => {
    const slots = collectGroundingSlots(
      msg({
        extraction_summary: {
          slots_applied: 2,
          associations_created: 0,
          conflicts_created: 0,
          frame_ids: [1],
          slots: [
            { frame_name: 'machu_picchu', key: 'country', value: 'Peru', confidence: 0.9 },
            { frame_name: 'machu_picchu', key: 'height', value: 'no' },
          ],
        },
        search_extraction_summary: {
          slots_applied: 1,
          associations_created: 0,
          conflicts_created: 0,
          frame_ids: [2],
          slots: [{ frame_name: 'inca', key: 'era', value: '15th century', confidence: 0.6 }],
        },
      })
    )
    expect(slots.map((s) => s.value).sort()).toEqual(['15th century', 'Peru'])
    expect(slots.find((s) => s.value === 'Peru')?.confidence).toBe(0.9)
  })

  it('dedupes and orders longest value first', () => {
    const slots = collectGroundingSlots(
      msg({
        extraction_summary: {
          slots_applied: 3,
          associations_created: 0,
          conflicts_created: 0,
          frame_ids: [1],
          slots: [
            { frame_name: 'a', key: 'k', value: 'Peru' },
            { frame_name: 'a', key: 'k', value: 'Peru' },
            { frame_name: 'b', key: 'k', value: 'Machu Picchu' },
          ],
        },
      })
    )
    expect(slots.map((s) => s.value)).toEqual(['Machu Picchu', 'Peru'])
  })
})

describe('applyGrounding', () => {
  const slots = [{ frame_name: 'machu_picchu', key: 'country', value: 'Peru', confidence: 0.9 }]

  it('wraps matches in a dotted-underline span with a confidence tooltip', () => {
    const html = applyGrounding('<p>Machu Picchu sits in Peru.</p>', slots)
    expect(html).toContain('class="confidence-tooltip"')
    expect(html).toContain('data-confidence="machu_picchu.country · 90% confident"')
    expect(html).toContain('>Peru</span>')
  })

  it('annotates every occurrence', () => {
    const html = applyGrounding('<p>Peru and Peru again</p>', slots)
    expect(html.match(/confidence-tooltip/g)).toHaveLength(2)
  })

  it('does not annotate inside code, links, or existing annotations', () => {
    const html = applyGrounding(
      '<p><code>Peru</code> <a href="https://x/Peru">Peru</a></p>',
      slots
    )
    expect(html).not.toContain('confidence-tooltip')
  })

  it('returns the input unchanged when there is nothing to annotate', () => {
    expect(applyGrounding('<p>hello</p>', [])).toBe('<p>hello</p>')
    expect(applyGrounding('', slots)).toBe('')
  })
})

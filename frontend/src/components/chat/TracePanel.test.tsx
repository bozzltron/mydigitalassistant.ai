import { render, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import TracePanel from './TracePanel'
import type { MessageMeta } from '../../types/chat'

// The trace panel used to render literal `-` with no binding at all: it never
// showed the turn's task type, memory context, citations, or search. These pin
// that the panel (a) docks open/closed and (b) actually renders the last turn's
// meta, with an honest empty state before the first turn.

afterEach(cleanup)

describe('TracePanel', () => {
  it('docks open when visible and closed when not', () => {
    const open = render(() => (
      <TracePanel visible meta={undefined} onClose={() => {}} />
    ))
    expect(open.container.querySelector('#trace-panel')?.className).toContain('open')
    cleanup()

    const closed = render(() => (
      <TracePanel visible={false} meta={undefined} onClose={() => {}} />
    ))
    expect(closed.container.querySelector('#trace-panel')?.className).not.toContain('open')
  })

  it('shows an empty state before the first turn, never a bare dash', () => {
    const { container } = render(() => (
      <TracePanel visible meta={undefined} onClose={() => {}} />
    ))
    expect(container.textContent).toContain('No turn yet')
    expect(container.querySelector('#trace-task-type')).toBeNull()
  })

  it('renders the turn meta: task type, memory, citations, and search', () => {
    const meta: MessageMeta = {
      task_type: 'search',
      memory_context: '### guitar\nstrings = 6',
      citations: ['https://a.example', 'https://b.example'],
      search_info: { backend: 'brave', query: 'guitar strings' },
    }
    const { container } = render(() => (
      <TracePanel visible meta={meta} onClose={() => {}} />
    ))

    expect(container.querySelector('#trace-task-type')?.textContent).toBe('search')
    expect(container.querySelector('#trace-memory')?.textContent).toContain('strings = 6')
    expect(container.querySelector('#trace-citations')?.textContent).toContain('https://a.example')
    // The search section is shown only when the turn searched.
    const search = container.querySelector('#trace-search-info')
    expect(search?.textContent).toContain('brave')
    expect(search?.textContent).toContain('guitar strings')
  })

  it('hides the search section when the turn did not search', () => {
    const { container } = render(() => (
      <TracePanel visible meta={{ task_type: 'functional' }} onClose={() => {}} />
    ))
    expect(container.querySelector('#trace-search-section')).toBeNull()
  })
})

import { render } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import MessageList from './MessageList'
import { postChatMessageStream, setSessionId, messages } from '../../state/chat'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
  postFeedback: vi.fn(),
}))

vi.mock('../../services/status', () => ({
  startStatusPolling: vi.fn(),
  stopStatusPolling: vi.fn(),
  setStreamStage: vi.fn(),
}))

describe('MessageList streamed bubble', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-sl-1')
  })

  it('renders meta that arrives after finalize (search info + extraction summary)', async () => {
    // Regression test: the streamed bubble reconciles in place (For by="id");
    // late-arriving meta must still render the learned indicator and the
    // backend badge instead of being frozen at placeholder time.
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    let resolveStream: (v: unknown) => void
    const streamDone = new Promise(r => { resolveStream = r })
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        // The stream stays open until the server closes it (after finalize).
        return streamDone
      }
    )

    render(() => <MessageList messages={messages} />)

    const promise = postChatMessageStream('search this', 'session-sl-1')
    await Promise.resolve()

    onEvent!({ type: 'text_delta', delta: 'Here is ' })
    onEvent!({ type: 'finalize', answer: 'Here is the answer' })
    onEvent!({
      type: 'meta',
      task_type: 'search',
      extraction_summary: {
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [1],
        slots: [{ frame_name: 'topic', key: 'name', value: 'testing' }],
      },
      search_extraction_summary: {
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [2],
        slots: [{ frame_name: 'web', key: 'fact', value: 'found it' }],
      },
      search_info: { backend: 'brave', query: 'search this', engines: ['brave'] },
    })

    // The stream ends after the meta event rides in.
    resolveStream!({
      response: 'answer',
      task_type: 'search',
      session_id: 'session-sl-1',
    })

    const result = await promise
    expect(result.response).toBe('Here is the answer')

    // Content, learned indicator, and Brave badge all render without refresh.
    expect(document.querySelector('.msg-markdown')?.textContent).toContain('Here is the answer')
    expect(document.querySelector('.learned-indicator summary')?.textContent).toContain('Found from search')
    expect(document.querySelector('.badge-brave')?.textContent).toContain('Searched via Brave')
  })
})
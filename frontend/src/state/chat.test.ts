import { describe, it, expect, vi, beforeEach } from 'vitest'
import { messages, queue, setQueue, isTurnActive, setTurnActive, sessionId, setSessionId, currentTurnId, setCurrentTurnId, postChatMessage, postChatMessageStream, loadConversationMessages, initChat } from '../state/chat'
import * as api from '../services/api'
import * as status from '../services/status'

vi.mock('../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
}))
vi.mock('../services/status')

describe('chat state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setQueue([])
    setTurnActive(false)
    setSessionId(null)
    setCurrentTurnId(null)
    localStorage.clear()
    initChat()
  })

  describe('messages', () => {
    it('initializes empty', () => {
      expect(messages()).toEqual([])
    })

    it('derives messages from conversationMessages store when sessionId is set', () => {
      setSessionId('session-123')
      expect(messages()).toEqual([])
    })
  })

  describe('queue', () => {
    it('initializes empty', () => {
      expect(queue()).toEqual([])
    })

    it('updates via setQueue', () => {
      const newQueue = [{ id: '1', message: 'test', timestamp: Date.now() }]
      setQueue(newQueue)
      expect(queue()).toEqual(newQueue)
    })
  })

  describe('turn state', () => {
    it('isTurnActive starts false', () => {
      expect(isTurnActive()).toBe(false)
    })

    it('setTurnActive updates state', () => {
      setTurnActive(true)
      expect(isTurnActive()).toBe(true)
    })

    it('currentTurnId starts null', () => {
      expect(currentTurnId()).toBeNull()
    })

    it('setCurrentTurnId updates state', () => {
      setCurrentTurnId('turn-123')
      expect(currentTurnId()).toBe('turn-123')
    })
  })

  describe('sessionId', () => {
    it('starts null', () => {
      expect(sessionId()).toBeNull()
    })

    it('setSessionId updates state', () => {
      setSessionId('session-new')
      expect(sessionId()).toBe('session-new')
    })

    it('loads from localStorage via initChat', () => {
      localStorage.setItem('session_id', 'session-abc')
      initChat()
      expect(sessionId()).toBe('session-abc')
    })
  })

  describe('postChatMessage', () => {
    it('sends message and returns response', async () => {
      const mockResponse = {
        response: 'Hello there!',
        task_type: 'functional',
        session_id: 'session-123',
      }
      vi.mocked(api.postChat).mockResolvedValue(mockResponse)
      vi.mocked(status.startStatusPolling).mockImplementation(() => {})
      vi.mocked(status.stopStatusPolling).mockImplementation(() => {})

      const result = await postChatMessage('Hi')

      expect(api.postChat).toHaveBeenCalledWith('Hi', undefined, undefined, 'test-turn-id-123', undefined, undefined)
      expect(result).toEqual(mockResponse)
      expect(isTurnActive()).toBe(false)
      expect(currentTurnId()).toBeNull()
    })

    it('sets turn active during request', async () => {
      let resolvePolling: () => void
      const pollingPromise = new Promise(r => { resolvePolling = r })
      vi.mocked(api.postChat).mockImplementation(() => pollingPromise)
      vi.mocked(status.startStatusPolling).mockImplementation(() => {})
      vi.mocked(status.stopStatusPolling).mockImplementation(() => {})

      const promise = postChatMessage('Hi')

      expect(isTurnActive()).toBe(true)
      expect(currentTurnId()).not.toBeNull()

      resolvePolling!({ response: 'OK', task_type: 'functional', session_id: 's1' })
      await promise

      expect(isTurnActive()).toBe(false)
    })

    it('cleans up on error', async () => {
      vi.mocked(api.postChat).mockRejectedValue(new Error('Network error'))
      vi.mocked(status.startStatusPolling).mockImplementation(() => {})
      vi.mocked(status.stopStatusPolling).mockImplementation(() => {})

      await expect(postChatMessage('Hi')).rejects.toThrow('Network error')
      expect(isTurnActive()).toBe(false)
      expect(currentTurnId()).toBeNull()
      expect(status.stopStatusPolling).toHaveBeenCalled()
    })
  })

  describe('postChatMessageStream', () => {
    it('renders the streamed answer into the conversation bubbles', async () => {
      setSessionId('session-123')
      let onEvent: ((e: api.StreamEvent) => void) | undefined
      const streamPromise = new Promise<{ response: string; session_id: string }>(resolve => {
        resolve({ response: 'Hi there!', session_id: 'session-123' })
      })
      vi.mocked(api.postChatStream).mockImplementation(
        async (_message, _session, _files, _turn, _consent, _max, callback) => {
          onEvent = callback
          return streamPromise
        }
      )
      vi.mocked(status.startStatusPolling).mockImplementation(() => {})
      vi.mocked(status.stopStatusPolling).mockImplementation(() => {})

      const promise = postChatMessageStream('Hi', 'session-123')

      // Placeholder assistant bubble exists before any tokens arrive.
      await Promise.resolve()
      expect(messages()).toHaveLength(1)
      expect(messages()[0].role).toBe('assistant')
      expect(messages()[0].meta?.isStreaming).toBe(true)

      // Streamed tokens render incrementally.
      onEvent!({ type: 'text_delta', delta: 'Hi ' })
      expect(messages()[0].content).toBe('Hi ')
      onEvent!({ type: 'text_delta', delta: 'there!' })
      expect(messages()[0].content).toBe('Hi there!')

      // Finalize stores the full answer and clears the streaming flag.
      onEvent!({ type: 'finalize', answer: 'Hi there!' })
      expect(messages()[0].content).toBe('Hi there!')
      expect(messages()[0].meta?.isStreaming).toBe(false)

      const result = await promise
      expect(result.response).toBe('Hi there!')
      expect(isTurnActive()).toBe(false)
    })

    it('never leaves a dangling empty bubble after a non-ok stream', async () => {
      setSessionId('session-err-987')
      vi.mocked(api.postChatStream).mockRejectedValue(new Error('HTTP 500'))
      vi.mocked(status.startStatusPolling).mockImplementation(() => {})
      vi.mocked(status.stopStatusPolling).mockImplementation(() => {})

      await expect(postChatMessageStream('Hi', 'session-err-987')).rejects.toThrow('HTTP 500')

      // The stream failed before any token — the placeholder is replaced by an
      // error bubble, not left as empty/streaming state.
      expect(messages()).toHaveLength(1)
      expect(messages()[0].content).toBe('Error: Failed to send message')
    })
  })

  describe('loadConversationMessages', () => {
    it('loads messages from API and maps them with metadata', async () => {
      const mockMessages = [
        { role: 'user', content: 'Hello' },
        {
          role: 'assistant',
          content: 'Hi there!',
          task_type: 'functional',
          memory_context: 'some context',
          citations: ['cite1'],
          extraction_summary: { slots: [{ frame_name: 'test', key: 'key', value: 'val' }] },
          search_extraction_summary: { slots: [] },
          search_info: { backend: 'searxng', query: 'test', engines: ['bing'] },
          ogData: { 'https://example.com': { title: 'Test', image: 'img.jpg' } },
        },
      ]
      vi.mocked(api.getSessionMessages).mockResolvedValue(mockMessages)

      setSessionId('session-123')

      await loadConversationMessages('session-123', 1)

      expect(messages()).toHaveLength(2)
      expect(messages()[0]).toMatchObject({
        role: 'user',
        content: 'Hello',
        id: 'history-session-123-0',
      })
      expect(messages()[1]).toMatchObject({
        role: 'assistant',
        content: 'Hi there!',
        id: 'history-session-123-1',
        meta: {
          task_type: 'functional',
          memory_context: 'some context',
          citations: ['cite1'],
          extraction_summary: { slots: [{ frame_name: 'test', key: 'key', value: 'val' }] },
          search_extraction_summary: { slots: [] },
          search_info: { backend: 'searxng', query: 'test', engines: ['bing'] },
          ogData: { 'https://example.com': { title: 'Test', image: 'img.jpg' } },
        },
      })
    })

    it('clears messages on error', async () => {
      vi.mocked(api.getSessionMessages).mockRejectedValue(new Error('Failed'))
      setSessionId('session-123')

      await loadConversationMessages('session-123', 1)

      expect(messages()).toEqual([])
    })
  })
})
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { messages, setMessages, queue, setQueue, isTurnActive, setTurnActive, sessionId, setSessionId, currentTurnId, setCurrentTurnId, postChatMessage, loadConversationMessages } from '../state/chat'
import * as api from '../services/api'
import * as status from '../services/status'

vi.mock('../services/api', () => ({
  postChat: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
}))
vi.mock('../services/status')

describe('chat state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setMessages([])
    setQueue([])
    setTurnActive(false)
    setSessionId(null)
    setCurrentTurnId(null)
    localStorage.clear()
  })

  describe('messages', () => {
    it('initializes empty', () => {
      expect(messages()).toEqual([])
    })

    it('updates via setMessages', () => {
      const newMessages = [{ role: 'user', content: 'Hello', id: '1' }]
      setMessages(newMessages)
      expect(messages()).toEqual(newMessages)
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

    it('loads from localStorage on init', async () => {
      localStorage.setItem('session_id', 'session-abc')
      // Re-import to trigger initialization
      vi.resetModules()
      const { sessionId: newSessionId } = await import('../state/chat')
      expect(newSessionId()).toBe('session-abc')
    })

    it('setSessionId updates state', () => {
      setSessionId('session-new')
      expect(sessionId()).toBe('session-new')
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

      expect(api.postChat).toHaveBeenCalledWith('Hi', undefined, undefined, 'test-turn-id-123')
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

  describe('loadConversationMessages', () => {
    it('loads messages from API and maps them', async () => {
      const mockMessages = [
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Hi there!' },
      ]
      vi.mocked(api.getSessionMessages).mockResolvedValue(mockMessages)

      await loadConversationMessages('session-123', 1)

      expect(messages()).toHaveLength(2)
      expect(messages()[0]).toMatchObject({
        role: 'user',
        content: 'Hello',
        id: 'history-session-123-0',
      })
    })

    it('clears messages on error', async () => {
      vi.mocked(api.getSessionMessages).mockRejectedValue(new Error('Failed'))
      setMessages([{ role: 'user', content: 'old', id: '1' }])

      await loadConversationMessages('session-123', 1)

      expect(messages()).toEqual([])
    })
  })
})
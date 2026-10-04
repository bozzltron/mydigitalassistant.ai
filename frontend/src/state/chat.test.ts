import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { messages, isTurnActive, sessionId, setSessionId, currentTurnId, setCurrentTurnId, postChatMessageStream, loadConversationMessages, initChat, drainQueue, enqueueMessage, addMessageToConversation, reportsIdentityRename, ASSISTANT_NAME_CHANGED_EVENT } from '../state/chat'
import * as api from '../services/api'
import * as status from '../services/status'
import * as messageQueue from '../state/messageQueue'

vi.mock('../services/api', () => ({
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
  getUsers: vi.fn(),
}))
vi.mock('../services/status')

describe('chat state', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    setSessionId(null)
    setCurrentTurnId(null)
    localStorage.clear()
    // Clear messageQueue state
    messageQueue.clearQueue()
    messageQueue.setProcessing(false)
    messageQueue.setActiveConversation(null)
    initChat()
  })

  // Without this, a vi.spyOn from one test (e.g. the drainQueue test) stays
  // installed for every later test and silently no-ops the real module
  // function, hiding genuine behavior.
  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('messages', () => {
    it('initializes empty', () => {
      expect(messages()).toEqual([])
    })

    it('derives messages from conversationMessages store when sessionId is set', () => {
      setSessionId('session-123')
      expect(messages()).toEqual([])
    })

    it('does not render pending queue items in the transcript (regression: they rendered twice, and the transcript copy looked already-sent)', () => {
      // Distinct session id: conversationMessages is never cleared between
      // tests, so reusing an id used elsewhere leaks these messages forward.
      setSessionId('session-transcript-pending')
      addMessageToConversation('session-transcript-pending', { role: 'user', content: 'already sent', id: 'm1' })

      messageQueue.enqueue({ content: 'still waiting', source: 'voice', timestamp: 1 })

      const rendered = messages()
      expect(rendered.map((m) => m.content)).toEqual(['already sent'])
      // The queue panel is the single surface for pending messages.
      expect(messageQueue.getQueueLength()).toBe(1)
    })

    it('shows a drained message in the transcript exactly once, not also as queued', () => {
      setSessionId('session-transcript-drained')
      const id = messageQueue.enqueue({ content: 'processed', source: 'voice', timestamp: 1 })

      // What the drainer does after a successful send.
      addMessageToConversation('session-transcript-drained', { role: 'user', content: 'processed', id })
      messageQueue.dequeue(id)

      const rendered = messages()
      expect(rendered).toHaveLength(1)
      expect(rendered[0].content).toBe('processed')
    })

    it('ignores a re-commit of the same message id (regression: retry grew a duplicate turn)', () => {
      // The drainer commits a turn before the request goes out, so a failed send
      // can be retried with the same queued id. Without this guard the retry
      // appended a second copy of the user's own message.
      setSessionId('session-transcript-recommit')
      addMessageToConversation('session-transcript-recommit', { role: 'user', content: 'asked once', id: 'dup-1' })
      addMessageToConversation('session-transcript-recommit', { role: 'user', content: 'asked once', id: 'dup-1' })

      const rendered = messages()
      expect(rendered).toHaveLength(1)
      expect(rendered[0].content).toBe('asked once')
    })

    it('still appends distinct messages in order', () => {
      setSessionId('session-transcript-distinct')
      addMessageToConversation('session-transcript-distinct', { role: 'user', content: 'first', id: 'd-1' })
      addMessageToConversation('session-transcript-distinct', { role: 'assistant', content: 'reply', id: 'd-2' })
      addMessageToConversation('session-transcript-distinct', { role: 'user', content: 'second', id: 'd-3' })

      expect(messages().map((m) => m.content)).toEqual(['first', 'reply', 'second'])
    })
  })

  describe('turn state', () => {
    it('isTurnActive starts false', () => {
      setSessionId('session-123')
      expect(isTurnActive()).toBe(false)
    })

    it('isTurnActive is per-conversation', () => {
      setSessionId('session-a')
      expect(isTurnActive()).toBe(false)
      setSessionId('session-b')
      expect(isTurnActive()).toBe(false)
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
      vi.mocked(status.beginTurnStatus).mockImplementation(() => {})
      vi.mocked(status.endTurnStatus).mockImplementation(() => {})

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
      vi.mocked(status.beginTurnStatus).mockImplementation(() => {})
      vi.mocked(status.endTurnStatus).mockImplementation(() => {})

      await expect(postChatMessageStream('Hi', 'session-err-987')).rejects.toThrow('HTTP 500')

      // The stream failed before any token — the placeholder is replaced by an
      // error bubble, not left as empty/streaming state.
      expect(messages()).toHaveLength(1)
      expect(messages()[0].content).toBe('Error: Failed to send message')
    })
  })

  describe('reportsIdentityRename', () => {
    it('is true only when the summary carries identity_name.full_name', () => {
      expect(reportsIdentityRename(undefined)).toBe(false)
      expect(reportsIdentityRename({ slots_applied: 0, associations_created: 0, conflicts_created: 0, frame_ids: [], slots: [] })).toBe(false)
      expect(reportsIdentityRename({
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [1],
        slots: [{ frame_name: 'guitar', key: 'strings', value: '6' }],
      })).toBe(false)
      expect(reportsIdentityRename({
        slots_applied: 1,
        associations_created: 0,
        conflicts_created: 0,
        frame_ids: [1],
        slots: [{ frame_name: 'identity_name', key: 'full_name', value: 'Carl' }],
      })).toBe(true)
    })

    it('signals a name change over the stream so the UI can refetch', async () => {
      setSessionId('session-name-change')
      let onEvent: ((e: api.StreamEvent) => void) | undefined
      vi.mocked(api.postChatStream).mockImplementation(
        async (_message, _session, _files, _turn, _consent, _max, callback) => {
          onEvent = callback
          return { response: 'done', session_id: 'session-name-change' }
        }
      )
      vi.mocked(status.beginTurnStatus).mockImplementation(() => {})
      vi.mocked(status.endTurnStatus).mockImplementation(() => {})

      const heard: Event[] = []
      const listener = (e: Event) => heard.push(e)
      window.addEventListener(ASSISTANT_NAME_CHANGED_EVENT, listener)
      try {
        const promise = postChatMessageStream('call yourself Carl', 'session-name-change')
        await Promise.resolve()
        onEvent!({
          type: 'meta',
          extraction_summary: {
            slots_applied: 1,
            associations_created: 0,
            conflicts_created: 0,
            frame_ids: [1],
            slots: [{ frame_name: 'identity_name', key: 'full_name', value: 'Carl' }],
          },
        })
        await promise
        expect(heard).toHaveLength(1)
      } finally {
        window.removeEventListener(ASSISTANT_NAME_CHANGED_EVENT, listener)
      }
    })
  })

  describe('drainQueue', () => {
    it('sets active conversation and clears message queue', () => {
      // New behavior: drainQueue delegates to messageQueue
      setSessionId('session-queue-1')
      vi.spyOn(messageQueue, 'setActiveConversation')
      vi.spyOn(messageQueue, 'drainQueue').mockReturnValue([])

      enqueueMessage('Second question', 'session-queue-1')
      drainQueue('session-queue-1')

      expect(messageQueue.setActiveConversation).toHaveBeenCalledWith('session-queue-1')
      expect(messageQueue.drainQueue).toHaveBeenCalled()
    })
  })

  describe('queue durability across a turn', () => {
    it('does not destroy messages enqueued while a turn is streaming (regression: finally block called drainQueue, silently dropping them)', async () => {
      setSessionId('session-durability')
      vi.mocked(api.postChatStream).mockResolvedValue({
        response: 'done',
        session_id: 'session-durability',
      } as never)

      // Turn starts and streams.
      const turn = postChatMessageStream('first question', 'session-durability')

      // The user speaks/types while the agent is busy: the message is queued.
      messageQueue.enqueue({
        content: 'queued while busy',
        source: 'voice',
        timestamp: Date.now(),
      })
      expect(messageQueue.getQueueLength()).toBe(1)

      // Turn completes. The queued message must survive for the drainer.
      await turn

      expect(messageQueue.getQueueLength()).toBe(1)
      expect(messageQueue.getQueue()[0].content).toBe('queued while busy')
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

    it('preserves in-flight streaming placeholder and optimistic user message when history lands', async () => {
      // Regression test: a history load racing an in-flight turn used to
      // wholesale-replace the store, orphaning the streaming placeholder so
      // stream updates no-oped and the answer only appeared after refresh.
      setSessionId('session-race')

      addMessageToConversation('session-race', {
        role: 'assistant',
        content: '',
        id: 'streaming-turn-assistant',
        meta: { isStreaming: true },
      })
      addMessageToConversation('session-race', {
        role: 'user',
        content: 'Just sent this',
        id: '1727000000000',
      })

      // The server already persisted a copy of the optimistic user message.
      vi.mocked(api.getSessionMessages).mockResolvedValue([
        { role: 'user', content: 'Hello' },
        { role: 'assistant', content: 'Hi there!' },
        { role: 'user', content: 'Just sent this' },
      ] as never)

      await loadConversationMessages('session-race', 1)

      const msgs = messages()
      const contents = msgs.map(m => `${m.role}:${m.content}`)

      // History rows are present.
      expect(contents).toContain('user:Hello')
      expect(contents).toContain('assistant:Hi there!')

      // The persisted copy of the optimistic message is not duplicated.
      expect(contents.filter(c => c === 'user:Just sent this')).toHaveLength(1)

      // The streaming placeholder survives so the live stream keeps updating.
      expect(msgs.some(m => m.id === 'streaming-turn-assistant')).toBe(true)
    })
  })
})
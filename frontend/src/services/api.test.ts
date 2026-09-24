import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { api, postChat, postChatStream, getFrames, getAssociations, getSearchResults, listFiles, postFileUpload, deleteFile, getUserSessions, createNewConversation, updateConversationTitle, getSessionMessages, getAssistantName, getSettings, postFeedback, postCorrection, transcribeAudio, getOGPreview, createTurnId } from '../services/api'

const mockFetch = vi.fn()
global.fetch = mockFetch

describe('api service', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockFetch.mockReset()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  describe('api', () => {
    it('makes request with correct headers and credentials', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ data: 'test' }),
      })

      await api('/test')

      expect(mockFetch).toHaveBeenCalledWith('/test', {
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
      })
    })

    it('throws on non-ok response', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 400,
        json: () => Promise.resolve({ detail: 'Bad request' }),
      })

      await expect(api('/test')).rejects.toThrow('Bad request')
    })

    it('throws on non-ok response with no json body', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.reject(new Error('No body')),
      })

      await expect(api('/test')).rejects.toThrow('Unknown error')
    })
  })

  describe('postChat', () => {
    it('sends chat request with correct body', async () => {
      const mockResponse = {
        session_id: 'session-123',
        response: 'Hello!',
        task_type: 'functional',
      }
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResponse),
      })

      const result = await postChat('Hi there', 'session-123', [], 'turn-456')

      expect(mockFetch).toHaveBeenCalledWith('/chat', {
        method: 'POST',
        body: JSON.stringify({
          user_id: 1,
          message: 'Hi there',
          session_id: 'session-123',
          attached_files: [],
          turn_id: 'turn-456',
        }),
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
      expect(result).toEqual(mockResponse)
    })
  })

  describe('createTurnId', () => {
    it('generates UUID', () => {
      const id = createTurnId()
      expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i)
    })

    it('generates unique IDs', () => {
      const ids = new Set()
      for (let i = 0; i < 100; i++) {
        ids.add(createTurnId())
      }
      expect(ids.size).toBe(100)
    })
  })

  describe('getFrames', () => {
    it('fetches frames for user', async () => {
      const mockFrames = [{ id: 1, name: 'Test' }]
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockFrames),
      })

      const result = await getFrames(1)

      expect(mockFetch).toHaveBeenCalledWith('/memory/frames?user_id=1', expect.any(Object))
      expect(result).toEqual(mockFrames)
    })
  })

  describe('getAssociations', () => {
    it('fetches associations for frame', async () => {
      const mockAssociations = [{ id: 1, from_frame_id: 1, to_frame_id: 2 }]
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockAssociations),
      })

      const result = await getAssociations(1)

      expect(mockFetch).toHaveBeenCalledWith('/memory/frames/1/associations', expect.any(Object))
      expect(result).toEqual(mockAssociations)
    })
  })

  describe('getSearchResults', () => {
    it('searches with query and optional minRelevance', async () => {
      const mockResults = { results: [] }
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve(mockResults),
      })

      await getSearchResults('test query', 0.5)

      expect(mockFetch).toHaveBeenCalledWith('/search?q=test+query&min_relevance=0.5', expect.any(Object))
    })

    it('searches without minRelevance', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ results: [] }),
      })

      await getSearchResults('test query')

      expect(mockFetch).toHaveBeenCalledWith('/search?q=test+query', expect.any(Object))
    })
  })

  describe('listFiles', () => {
    it('lists files', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([{ id: '1', name: 'test.txt' }]),
      })

      const result = await listFiles()

      expect(mockFetch).toHaveBeenCalledWith('/files/list', expect.any(Object))
      expect(result).toEqual([{ id: '1', name: 'test.txt' }])
    })
  })

  describe('postFileUpload', () => {
    it('uploads file with FormData', async () => {
      const file = new File(['content'], 'test.txt', { type: 'text/plain' })
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ id: '1', name: 'test.txt' }),
      })

      const result = await postFileUpload(file)

      expect(mockFetch).toHaveBeenCalled()
      const call = mockFetch.mock.calls[0]
      expect(call[0]).toBe('/files/upload')
      expect(call[1].method).toBe('POST')
      expect(call[1].body).toBeInstanceOf(FormData)
      expect(result).toEqual({ id: '1', name: 'test.txt' })
    })
  })

  describe('deleteFile', () => {
    it('deletes file', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      })

      const result = await deleteFile('file-123')

      expect(mockFetch).toHaveBeenCalledWith('/files/file-123', {
        method: 'DELETE',
        credentials: 'include',
      })
      expect(result).toEqual({ success: true })
    })
  })

  describe('getUserSessions', () => {
    it('fetches user sessions', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([{ id: '1', title: 'Session 1' }]),
      })

      const result = await getUserSessions(1)

      expect(mockFetch).toHaveBeenCalledWith('/users/1/sessions', expect.any(Object))
      expect(result).toEqual([{ id: '1', title: 'Session 1' }])
    })
  })

  describe('createNewConversation', () => {
    it('creates new conversation', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ session_id: 'new-session', message: 'Created' }),
      })

      const result = await createNewConversation(1)

      expect(mockFetch).toHaveBeenCalledWith('/conversations/new?user_id=1', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
      expect(result).toEqual({ session_id: 'new-session', message: 'Created' })
    })
  })

  describe('updateConversationTitle', () => {
    it('updates conversation title', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ session_id: 'session-123', title: 'New Title' }),
      })

      const result = await updateConversationTitle('session-123', 1, 'New Title')

      expect(mockFetch).toHaveBeenCalledWith('/conversations/session-123/title', {
        method: 'PATCH',
        body: JSON.stringify({ user_id: 1, title: 'New Title' }),
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
      expect(result).toEqual({ session_id: 'session-123', title: 'New Title' })
    })
  })

  describe('getSessionMessages', () => {
    it('fetches session messages with limit', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve([{ role: 'user', content: 'Hi' }]),
      })

      const result = await getSessionMessages('session-123', 1, 25)

      expect(mockFetch).toHaveBeenCalledWith('/chat/session/session-123/messages?user_id=1&limit=25', expect.any(Object))
      expect(result).toEqual([{ role: 'user', content: 'Hi' }])
    })
  })

  describe('getAssistantName', () => {
    it('fetches assistant name', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ name: 'Assistant' }),
      })

      const result = await getAssistantName()

      expect(mockFetch).toHaveBeenCalledWith('/assistant/name', expect.any(Object))
      expect(result).toEqual({ name: 'Assistant' })
    })
  })

  describe('getSettings', () => {
    it('fetches settings', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ brave_enabled: true, brave_configured: true }),
      })

      const result = await getSettings()

      expect(mockFetch).toHaveBeenCalledWith('/settings', expect.any(Object))
      expect(result).toEqual({ brave_enabled: true, brave_configured: true })
    })
  })

  describe('postFeedback', () => {
    it('posts feedback', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      })

      await postFeedback('ep-1', 'msg-1', 'thumbs_up', 'Great!')

      expect(mockFetch).toHaveBeenCalledWith('/feedback', {
        method: 'POST',
        body: JSON.stringify({ episode_id: 'ep-1', message_id: 'msg-1', kind: 'thumbs_up', comment: 'Great!' }),
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
    })
  })

  describe('postCorrection', () => {
    it('posts correction', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      })

      await postCorrection('ep-1', 'msg-1', 'Correction text')

      expect(mockFetch).toHaveBeenCalledWith('/correction', {
        method: 'POST',
        body: JSON.stringify({ episode_id: 'ep-1', message_id: 'msg-1', correction_text: 'Correction text' }),
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
    })
  })

  describe('transcribeAudio', () => {
    it('transcribes audio blob', async () => {
      const blob = new Blob(['audio'], { type: 'audio/webm' })
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ text: 'Hello world' }),
      })

      const result = await transcribeAudio(blob)

      expect(mockFetch).toHaveBeenCalled()
      const call = mockFetch.mock.calls[0]
      expect(call[0]).toBe('/transcribe')
      expect(call[1].method).toBe('POST')
      expect(call[1].body).toBeInstanceOf(FormData)
      expect(result).toEqual({ text: 'Hello world' })
    })
  })

  describe('getOGPreview', () => {
    it('fetches OG preview', async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ title: 'Test', image: 'img.png' }),
      })

      const result = await getOGPreview('https://example.com')

      expect(mockFetch).toHaveBeenCalledWith('/og-preview?url=https%3A%2F%2Fexample.com', expect.any(Object))
      expect(result).toEqual({ title: 'Test', image: 'img.png' })
    })
  })

  describe('postChatStream', () => {
    function sseResponse(events: string[]): Response {
      const encoder = new TextEncoder()
      const stream = new ReadableStream({
        start(controller) {
          for (const ev of events) {
            controller.enqueue(encoder.encode(ev))
          }
          controller.close()
        },
      })
      return { ok: true, status: 200, body: stream } as unknown as Response
    }

    it('accumulates streamed text and returns without ReferenceError', async () => {
      // Regression test: the return object previously referenced undeclared
      // `finalSessionId`/`finalTaskType`/etc., throwing a ReferenceError the
      // moment the stream completed and erasing the good streamed answer.
      const events = [
        'data: {"type": "stage", "stage": "recall", "detail": "checking my memory"}\n\n',
        'data: {"type": "text_delta", "delta": "Hello"}\n\n',
        'data: {"type": "text_delta", "delta": " world"}\n\n',
        'data: {"type": "finalize", "answer": "Hello world"}\n\n',
      ]
      mockFetch.mockResolvedValue(sseResponse(events))

      const result = await postChatStream('Hi', 'session-123', [], 'turn-1')

      expect(result.response).toBe('Hello world')
      expect(result.session_id).toBe('session-123')
      // Fields the backend does not send over SSE stay undefined — they must
      // not throw.
      expect(result.task_type).toBeUndefined()
    })

    it('falls back to an empty session id when none is provided', async () => {
      mockFetch.mockResolvedValue(
        sseResponse(['data: {"type": "finalize", "answer": "ok"}\n\n']),
      )

      const result = await postChatStream('Hi')

      expect(result.session_id).toBe('')
      expect(result.response).toBe('ok')
    })

    it('emits stage and text events through onEvent in order', async () => {
      const events = [
        'data: {"type": "stage", "stage": "recall", "detail": "checking my memory"}\n\n',
        'data: {"type": "text_delta", "delta": "Hi"}\n\n',
        'data: {"type": "finalize", "answer": "Hi"}\n\n',
      ]
      mockFetch.mockResolvedValue(sseResponse(events))
      const seen: Array<{ type: string; stage?: string }> = []

      await postChatStream('Hi', 'session-123', [], 'turn-1', false, (event) => {
        seen.push(event)
      })

      expect(seen.map((e) => e.type)).toEqual(['stage', 'text_delta', 'finalize'])
      expect(seen[0].stage).toBe('recall')
    })

    it('throws a useful error when the stream returns non-ok', async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ detail: 'boom' }),
      })

      await expect(postChatStream('Hi')).rejects.toThrow('boom')
    })
  })
})
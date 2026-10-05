import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import ChatPage from './ChatPage'
import {
  setSessionId,
  setIsStreaming,
  setStreamingMessageId,
} from '../../state/chat'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  postChat: vi.fn(),
  postChatStream: vi.fn(),
  createTurnId: vi.fn(() => 'test-turn-id-123'),
  getSessionMessages: vi.fn(),
  postFeedback: vi.fn(),
  getUsers: vi.fn(),
  postFileUpload: vi.fn(),
}))

vi.mock('../../services/status', () => ({
  useTurnStatus: () => ({ turnStatus: () => null, isPolling: () => false }),
  beginTurnStatus: vi.fn(),
  endTurnStatus: vi.fn(),
  setStreamStage: vi.fn(),
  getStageLabel: (stage: string, detail?: string) => detail || stage,
}))

vi.mock('../../hooks/useVoiceRecording', () => ({
  useVoiceRecording: () => ({ startDictation: vi.fn(), endDictation: vi.fn() }),
}))

// ChatPage's TTS effect constructs an utterance when an assistant bubble lands;
// test-setup stubs speechSynthesis but not its constructor, so define it here.
class MockSpeechSynthesisUtterance {
  text: string
  voice: unknown = null
  rate = 1
  pitch = 1
  volume = 1
  onstart: (() => void) | null = null
  onend: (() => void) | null = null
  onerror: (() => void) | null = null
  constructor(text: string) {
    this.text = text
  }
}
Object.defineProperty(window, 'SpeechSynthesisUtterance', {
  writable: true,
  configurable: true,
  value: MockSpeechSynthesisUtterance,
})

describe('ChatPage chat wiring', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
    setSessionId('session-cp-1')
    setIsStreaming(false)
    setStreamingMessageId(null)
  })

  it('sends through the streaming endpoint and renders the finalize answer', () => {
    // Regression test: previously ChatPage destructured `sendMessage:
    // sendMessageStream`, which bound the NON-streaming postChat (POST /chat)
    // to the streaming slot. That path never adds the assistant reply to the
    // store, so answers only appeared after a page refresh.
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        return { response: 'Streamed reply', task_type: 'functional', session_id: 'session-cp-1' }
      }
    )

    render(() => <ChatPage conversation={null} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    // The streaming endpoint must be used, never the non-streaming one.
    expect(api.postChatStream).toHaveBeenCalled()
    expect(api.postChat).not.toHaveBeenCalled()

    // The bubbled answer renders without a refresh.
    onEvent!({ type: 'finalize', answer: 'Streamed reply' })
    const markdown = document.querySelector('.msg-assistant .msg-markdown')
    expect(markdown?.textContent).toContain('Streamed reply')
  })

  it('speaks the finalized assistant reply when read-aloud is on', async () => {
    let onEvent: ((e: api.StreamEvent) => void) | undefined
    vi.mocked(api.postChatStream).mockImplementation(
      async (_message, _session, _files, _turn, _consent, _maxness, callback) => {
        onEvent = callback
        return { response: 'Streamed reply', task_type: 'functional', session_id: 'session-cp-1' }
      }
    )

    render(() => <ChatPage conversation={null} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    await vi.waitFor(() => expect(onEvent).toBeDefined())
    onEvent!({ type: 'finalize', answer: 'Streamed reply' })

    await vi.waitFor(() =>
      expect(window.speechSynthesis.speak).toHaveBeenCalled()
    )
    const spoken = vi.mocked(window.speechSynthesis.speak).mock.calls.at(-1)![0] as unknown as {
      text: string
    }
    expect(spoken.text).toBe('Streamed reply')
  })

  it('sends the message even when an attachment fails to upload', async () => {
    // The input bar uploads bytes through the multipart endpoint and references
    // the stored file by frame. If that upload throws — the backend 400s an
    // unsupported type, or the request drops — the user's typed message must
    // still go: losing what they wrote because a file was rejected is the worse
    // failure. The failed attachment is dropped instead.
    vi.mocked(api.postChatStream).mockResolvedValue({
      response: 'ok',
      task_type: 'functional',
      session_id: 'session-cp-1',
    })
    vi.mocked(api.postFileUpload).mockRejectedValue(new Error('Unsupported file type'))

    render(() => <ChatPage conversation={null} />)

    const fileInput = screen.getByTestId('file-input') as HTMLInputElement
    const file = new File(['%PDF-1.4'], 'report.pdf', { type: 'application/pdf' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'read this please' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    // The upload was attempted, failed, and did not stop the message.
    await vi.waitFor(() => expect(api.postChatStream).toHaveBeenCalled())
    const [message, , attached] = vi.mocked(api.postChatStream).mock.calls[0]
    expect(message).toBe('read this please')
    expect(attached).toEqual([])
  })
})
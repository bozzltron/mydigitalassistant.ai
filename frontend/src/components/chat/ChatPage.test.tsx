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
})
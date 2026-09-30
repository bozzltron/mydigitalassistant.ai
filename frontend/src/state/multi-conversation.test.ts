import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createRoot } from 'solid-js'
import type { ChatMessage } from '../types'
import { TurnStatus } from '../services/status'

interface ChatModule {
  messages: () => ChatMessage[]
  isTurnActive: () => boolean
  sessionId: () => string | null
  setSessionId: (v: string | null) => void
  currentTurnId: () => string | null
  setCurrentTurnId: (v: string | null) => void
  loadConversationMessages: (sessionIdParam: string, userId: number) => Promise<void>
  addMessageToConversation: (sessionIdParam: string, message: ChatMessage) => void
  getConversationTurnId: (sessionIdParam: string) => string | undefined
  initChat: () => void
}

import type { SessionMessage } from '../services/api'

interface ApiModule {
  createTurnId: () => string
  getSessionMessages: (session_id: string, user_id: number, limit?: number) => Promise<SessionMessage[]>
}

interface StatusModule {
  useTurnStatus: (turnId?: () => string | undefined) => { turnStatus: () => TurnStatus | null; isPolling: () => boolean }
  beginTurnStatus: (turnId: string) => void
  endTurnStatus: (turnId?: string) => void
}

let chatModule: ChatModule
let apiModule: ApiModule
let statusModule: StatusModule

beforeEach(async () => {
  vi.resetModules()
  vi.mock('../services/api', () => ({
    createTurnId: vi.fn(() => 'test-turn-id-123'),
    getSessionMessages: vi.fn(),
    getUsers: vi.fn(),
  }))

  chatModule = await import('../state/chat')
  apiModule = await import('../services/api')
  statusModule = await import('../services/status')

  vi.mocked(apiModule.getSessionMessages).mockResolvedValue([])

  localStorage.clear()
  chatModule.setSessionId(null)
  chatModule.setCurrentTurnId(null)
  chatModule.initChat()
})

describe('multi-conversation isolation', () => {
  it('two conversations - send message to A, verify B unchanged', async () => {
    await chatModule.loadConversationMessages('session-A', 1)
    chatModule.setSessionId('session-A')
    chatModule.addMessageToConversation('session-A', { role: 'user', content: 'Hello A', id: 'msg-A-1' })

    await chatModule.loadConversationMessages('session-B', 1)
    chatModule.setSessionId('session-B')
    chatModule.addMessageToConversation('session-B', { role: 'user', content: 'Hello B', id: 'msg-B-1' })

    chatModule.setSessionId('session-A')
    expect(chatModule.messages()).toHaveLength(1)
    expect(chatModule.messages()[0].content).toBe('Hello A')

    chatModule.setSessionId('session-B')
    expect(chatModule.messages()).toHaveLength(1)
    expect(chatModule.messages()[0].content).toBe('Hello B')
  })

  it('load conversation, send message, switch back, verify history intact', async () => {
    await chatModule.loadConversationMessages('session-A', 1)
    chatModule.setSessionId('session-A')
    chatModule.addMessageToConversation('session-A', { role: 'user', content: 'First message', id: 'msg-1' })
    chatModule.addMessageToConversation('session-A', { role: 'assistant', content: 'First response', id: 'msg-2' })

    await chatModule.loadConversationMessages('session-B', 1)
    chatModule.setSessionId('session-B')
    chatModule.addMessageToConversation('session-B', { role: 'user', content: 'B message', id: 'msg-B' })

    chatModule.setSessionId('session-A')
    expect(chatModule.messages()).toHaveLength(2)
    expect(chatModule.messages()[0].content).toBe('First message')
    expect(chatModule.messages()[1].content).toBe('First response')
  })

  it('concurrent turn tracking - each conversation has own turnId', () => {
    chatModule.setSessionId('session-A')
    expect(chatModule.getConversationTurnId('session-A')).toBeUndefined()
    expect(chatModule.getConversationTurnId('session-B')).toBeUndefined()
  })

  it('sessionId isolation - messages derived per session', () => {
    chatModule.addMessageToConversation('session-A', { role: 'user', content: 'A1', id: 'a1' })
    chatModule.addMessageToConversation('session-A', { role: 'assistant', content: 'A2', id: 'a2' })
    chatModule.addMessageToConversation('session-B', { role: 'user', content: 'B1', id: 'b1' })

    chatModule.setSessionId('session-A')
    expect(chatModule.messages()).toHaveLength(2)
    expect(chatModule.messages().map(m => m.content)).toEqual(['A1', 'A2'])

    chatModule.setSessionId('session-B')
    expect(chatModule.messages()).toHaveLength(1)
    expect(chatModule.messages()[0].content).toBe('B1')

    chatModule.setSessionId(null)
    expect(chatModule.messages()).toHaveLength(0)
  })

  it('turn status isolation - each conversation tracks own status', () => {
    createRoot(() => {
      const turnIdA = 'turn-A'
      const turnIdB = 'turn-B'

      const { turnStatus: statusA } = statusModule.useTurnStatus(() => turnIdA)
      const { turnStatus: statusB } = statusModule.useTurnStatus(() => turnIdB)

      expect(statusA()).toBeNull()
      expect(statusB()).toBeNull()

      statusModule.beginTurnStatus(turnIdA)
      expect(statusA()).not.toBeNull()
      expect(statusA()?.stage).toBe('queued')

      expect(statusB()).toBeNull()

      statusModule.endTurnStatus(turnIdA)
      expect(statusA()).toBeNull()
    })
  })
})
import { createSignal, createMemo } from 'solid-js'
import { createStore } from 'solid-js/store'
import { postChat, createTurnId } from '../services/api'
import { startStatusPolling, stopStatusPolling } from '../services/status'
import { getSessionMessages } from '../services/api'
import type {
  ChatMessage,
  ExtractionSummary,
  SearchInfo,
  OgData,
  QueuedMessage,
  SessionMessage,
} from '../../types'

interface ChatState {
  conversationMessages: Map<string, ChatMessage[]>
  conversationTurnIds: Map<string, string>
}

const [chatState, setChatState] = createStore<ChatState>({
  conversationMessages: new Map(),
  conversationTurnIds: new Map(),
})

export const [sessionId, setSessionId] = createSignal<string | null>(null)
export const [queue, setQueue] = createSignal<QueuedMessage[]>([])
export const [isTurnActive, setTurnActive] = createSignal(false)
export const [currentTurnId, setCurrentTurnId] = createSignal<string | null>(null)

export const messages = createMemo(() => {
  const sid = sessionId()
  if (sid) {
    return chatState.conversationMessages.get(sid) || []
  }
  return []
})

export function initChat(): void {
  if (typeof localStorage !== 'undefined') {
    const saved = localStorage.getItem('session_id')
    if (saved) setSessionId(saved)
  }
}

export async function loadConversationMessages(sessionIdParam: string, userId: number): Promise<void> {
  try {
    const data = await getSessionMessages(sessionIdParam, userId, 50)
    const loadedMessages: ChatMessage[] = data.map((m: SessionMessage, index: number) => ({
      role: m.role,
      content: m.content,
      id: `history-${sessionIdParam}-${index}`,
      meta: {
        task_type: m.task_type,
        memory_context: m.memory_context,
        citations: m.citations,
        extraction_summary: m.extraction_summary,
        search_extraction_summary: m.search_extraction_summary,
        search_info: m.search_info,
        ogData: m.ogData,
      },
    }))
    setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
      const next = new Map(prev)
      next.set(sessionIdParam, loadedMessages)
      return next
    })
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
      const next = new Map(prev)
      next.set(sessionIdParam, [])
      return next
    })
  }
}

export async function postChatMessage(
  message: string,
  session_id?: string,
  attached_files?: File[]
): Promise<{
  response: string
  task_type?: string
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  session_id?: string
}> {
  const turnId = createTurnId()
  setCurrentTurnId(turnId)
  setTurnActive(true)

  if (session_id) {
    setChatState('conversationTurnIds', (prev: Map<string, string>) => {
      const next = new Map(prev)
      next.set(session_id, turnId)
      return next
    })
  }

  startStatusPolling(turnId)

  try {
    const result = await postChat(message, session_id, attached_files, turnId)
    return {
      response: result.response,
      task_type: result.task_type,
      extraction_summary: result.extraction_summary,
      search_extraction_summary: result.search_extraction_summary,
      search_info: result.search_info,
      session_id: result.session_id,
    }
  } catch (error) {
    console.error('Error sending message:', error)
    throw error
  } finally {
    stopStatusPolling(turnId)
    setCurrentTurnId(null)
    setTurnActive(false)

    if (session_id) {
      setChatState('conversationTurnIds', (prev: Map<string, string>) => {
        const next = new Map(prev)
        next.delete(session_id)
        return next
      })
    }
  }
}

export function addMessageToConversation(sessionIdParam: string, message: ChatMessage): void {
  setChatState('conversationMessages', (prev: Map<string, ChatMessage[]>) => {
    const next = new Map(prev)
    const existing = next.get(sessionIdParam) || []
    next.set(sessionIdParam, [...existing, message])
    return next
  })
}

export function getConversationTurnId(sessionIdParam: string): string | undefined {
  return chatState.conversationTurnIds.get(sessionIdParam)
}

// Create a memo for the current conversation's turnId that tracks the store
export function useConversationTurnId(sessionIdParam: string | null | undefined): string | undefined {
  return createMemo(() => {
    if (!sessionIdParam) return undefined
    return chatState.conversationTurnIds.get(sessionIdParam)
  })()
}

interface SessionMessage {
  role: string
  content: string
  task_type?: string
  memory_context?: string
  citations?: string[]
  extraction_summary?: ExtractionSummary
  search_extraction_summary?: ExtractionSummary
  search_info?: SearchInfo
  ogData?: Record<string, OgData>
}
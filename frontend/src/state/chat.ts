import { createSignal } from 'solid-js'
import { postChat, createTurnId } from '../services/api'
import { startStatusPolling, stopStatusPolling } from '../services/status'
import { getSessionMessages } from '../services/api'

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  id: string
  meta?: MessageMeta
}

export interface MessageMeta {
  task_type?: string
  memory_context?: string
  citations?: string[]
  extraction_summary?: any
  search_extraction_summary?: any
  search_info?: any
  ogData?: Record<string, any>
}

export interface QueuedMessage {
  id: string
  message: string
  timestamp: number
}

export const [messages, setMessages] = createSignal<ChatMessage[]>([])
export const [queue, setQueue] = createSignal<QueuedMessage[]>([])
export const [isTurnActive, setTurnActive] = createSignal(false)
export const [sessionId, setSessionId] = createSignal<string | null>(null)
export const [currentTurnId, setCurrentTurnId] = createSignal<string | null>(null)

// Load from localStorage on init
if (typeof localStorage !== 'undefined') {
  const saved = localStorage.getItem('session_id')
  if (saved) setSessionId(saved)
}

export async function loadConversationMessages(sessionId: string, userId: number): Promise<void> {
  try {
    const data = await getSessionMessages(sessionId, userId, 50)
    const loadedMessages: ChatMessage[] = data.map((m: any, index: number) => ({
      role: m.role,
      content: m.content,
      id: `history-${sessionId}-${index}`,
      meta: undefined,
    }))
    setMessages(loadedMessages)
  } catch (error) {
    console.error('Failed to load conversation messages:', error)
    setMessages([])
  }
}

export async function postChatMessage(
  message: string,
  session_id?: string,
  attached_files?: any[]
): Promise<{ 
  response: string; 
  task_type?: string;
  extraction_summary?: any;
  search_extraction_summary?: any;
  search_info?: any;
  session_id?: string;
}> {
  const turnId = createTurnId()
  setCurrentTurnId(turnId)
  setTurnActive(true)
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
    stopStatusPolling()
    setCurrentTurnId(null)
    setTurnActive(false)
  }
}
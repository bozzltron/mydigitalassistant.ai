import { createSignal } from 'solid-js'

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

// Load from localStorage on init
if (typeof localStorage !== 'undefined') {
  const saved = localStorage.getItem('session_id')
  if (saved) setSessionId(saved)
}

// Define postChat function
export async function postChat(message: string): Promise<{ response: string; task_type?: string }> {
  // This is a stub implementation - will be replaced with actual API call
  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        response: `This is a mock response to "${message}"`,
        task_type: 'default'
      })
    }, 1000)
  })
}
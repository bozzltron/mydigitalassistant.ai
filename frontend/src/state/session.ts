import { createSignal } from 'solid-js'
import { getUserSessions, createNewConversation, updateConversationTitle, getSessionMessages } from '../services/api'

// Session type definition
export interface Session {
  id: string
  title?: string
  createdAt: string
  updatedAt: string
  episode_count?: number
  last_message?: string
}

interface ApiSession {
  id: string
  title?: string
  last_message?: string
  created_at: string
  updated_at: string
  episode_count?: number
}

interface ApiMessage {
  role: string
  content: string
  timestamp: string
}

// Create signal for session state
export const [session, setSession] = createSignal<Session | null>(null)
export const [isSessionLoading, setIsSessionLoading] = createSignal(false)

// Fetch user sessions from API
export const fetchSessions = async (userId: number): Promise<Session[]> => {
  setIsSessionLoading(true)
  try {
    const data = await getUserSessions(userId)
    return data.map((s: ApiSession) => ({
      id: s.id,
      title: s.title || s.last_message || `Conversation ${s.episode_count}`,
      createdAt: s.created_at,
      updatedAt: s.updated_at,
      episode_count: s.episode_count,
      last_message: s.last_message,
    }))
  } catch (error) {
    console.error('Failed to fetch sessions:', error)
    return []
  } finally {
    setIsSessionLoading(false)
  }
}

// Fetch messages for a session from the API
export const fetchSessionMessages = async (sessionId: string, userId: number) => {
  try {
    const data = await getSessionMessages(sessionId, userId, 50)
    return data.map((m: ApiMessage) => ({
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
    }))
  } catch (error) {
    console.error('Failed to fetch session messages:', error)
    return []
  }
}

// Create a new conversation
export const createConversation = async (userId: number, title?: string) => {
  try {
    const result = await createNewConversation(userId)
    if (title && title.trim()) {
      await updateConversationTitle(result.session_id, userId, title.trim())
    }
    return result.session_id
  } catch (error) {
    console.error('Failed to create conversation:', error)
    throw error
  }
}

// Update conversation title
export const updateTitle = async (sessionId: string, userId: number, title: string) => {
  try {
    await updateConversationTitle(sessionId, userId, title)
  } catch (error) {
    console.error('Failed to update conversation title:', error)
    throw error
  }
}
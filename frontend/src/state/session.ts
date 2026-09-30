import { createSignal } from 'solid-js'
import { getUserSessions, createNewConversation, updateConversationTitle } from '../services/api'
import type { Session, SessionSummary } from '../types'

// The conversation shape the UI consumes is defined once in `src/types` and
// re-exported here so the existing `from '../state/session'` import sites stay
// valid without a second, drifting definition.
export type { Session }

/**
 * Display name for a conversation.
 *
 * The sessions endpoint returns a backend-computed label in `last_message`
 * (explicit title, else the first user message, else "Conversation N"); it does
 * not return a separate `title` field. This picks the explicit/derived label and
 * only falls back to a generic when even that is empty.
 */
export function sessionTitle(summary: Pick<SessionSummary, 'last_message' | 'episode_count'>): string {
  return summary.last_message?.trim() || `Conversation ${summary.episode_count}`
}

function toSession(summary: SessionSummary): Session {
  return {
    id: summary.id,
    title: sessionTitle(summary),
    episode_count: summary.episode_count,
    last_activity: summary.last_activity,
  }
}

/**
 * Most recently active first (ISO timestamps sort lexicographically). A brand
 * new conversation has no activity yet, so it sorts last rather than floating to
 * the top with a stale/absent value.
 */
export function sortByRecentActivity(sessions: Session[]): Session[] {
  return [...sessions].sort((a, b) => {
    const av = a.last_activity ?? ''
    const bv = b.last_activity ?? ''
    if (av === bv) return 0
    if (av === '') return 1
    if (bv === '') return -1
    return av < bv ? 1 : -1
  })
}

// Create signal for session state
export const [session, setSession] = createSignal<Session | null>(null)
export const [isSessionLoading, setIsSessionLoading] = createSignal(false)

// Fetch user sessions from API
export const fetchSessions = async (userId: number): Promise<Session[]> => {
  setIsSessionLoading(true)
  try {
    const summaries = await getUserSessions(userId)
    return sortByRecentActivity(summaries.map(toSession))
  } catch (error) {
    console.error('Failed to fetch sessions:', error)
    return []
  } finally {
    setIsSessionLoading(false)
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

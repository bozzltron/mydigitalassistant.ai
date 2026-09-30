import { createSignal, createEffect } from 'solid-js'
import { loadConversationMessages } from '../state/chat'
import { user } from '../state/user'
import type { Session } from '../state/session'
import { setSessionId } from '../state/chat'

export function useActiveConversation(conversations: () => Session[]) {
  const [activeConversation, setActiveConversation] = createSignal<Session | null>(null)

  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations().find(c => c.id === conversationId) || null
    setActiveConversation(conversation)
  }

  createEffect(() => {
    const conv = activeConversation()
    const u = user()
    if (conv && u) {
      loadConversationMessages(conv.id, u.id)
      setSessionId(conv.id)
    } else if (!conv) {
      setSessionId(null)
    }
  })

  const initializeFromSavedSession = (sessions: Session[]) => {
    const savedSessionId = localStorage.getItem('session_id')
    const u = user()

    if (savedSessionId && u) {
      const saved = sessions.find(c => c.id === savedSessionId)
      if (saved) {
        setActiveConversation(saved)
      } else if (sessions.length > 0) {
        setActiveConversation(sessions[0])
      }
    } else if (sessions.length > 0) {
      setActiveConversation(sessions[0])
    }
  }

  return {
    activeConversation,
    setActiveConversation,
    handleConversationChange,
    initializeFromSavedSession,
  }
}
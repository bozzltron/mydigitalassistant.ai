import { createSignal, createEffect, onMount, onCleanup } from 'solid-js'
import { loadConversationMessages } from '../state/chat'
import { user } from '../state/user'
import type { Session } from '../state/session'
import { setSessionId } from '../state/chat'

/**
 * A conversation the user chose to open, dispatched by the alerts panel when an
 * alert is resolved into it. A CustomEvent rather than a shared signal so the
 * panel does not need to import chat state: it announces a destination and this
 * hook — which already owns conversation switching — decides what to do with it.
 */
export const OPEN_CONVERSATION_EVENT = 'open-conversation'

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

  /**
   * Open a conversation by id, even one not in the current list.
   *
   * An alert can resolve into a session that has not been fetched yet — the panel
   * loaded it from the alert's own options endpoint. Setting the signal directly is
   * deliberate: it is the same state the effect above drives, so messages load and
   * `session_id` persists exactly as if the user had picked it from the sidebar.
   */
  const openConversationById = (sessionId: string) => {
    const known = conversations().find(c => c.id === sessionId)
    if (known) {
      setActiveConversation(known)
      return
    }
    // Not in the list yet: adopt a minimal entry so the effect still fires.
    setActiveConversation({
      id: sessionId,
      title: 'Alert',
      episode_count: 0,
      last_activity: new Date().toISOString(),
    })
  }

  const onOpenConversation = (event: Event) => {
    const detail = (event as CustomEvent<{ sessionId?: string }>).detail
    if (detail?.sessionId) {
      openConversationById(detail.sessionId)
    }
  }

  onMount(() => {
    window.addEventListener(OPEN_CONVERSATION_EVENT, onOpenConversation)
  })

  onCleanup(() => {
    window.removeEventListener(OPEN_CONVERSATION_EVENT, onOpenConversation)
  })

  const initializeFromSavedSession = (sessions: Session[]) => {
    const savedSessionId = localStorage.getItem('session_id')
    const u = user()
    const first = sessions[0]

    if (savedSessionId && u) {
      const saved = sessions.find(c => c.id === savedSessionId)
      if (saved) {
        setActiveConversation(saved)
      } else if (first) {
        setActiveConversation(first)
      }
    } else if (first) {
      setActiveConversation(first)
    }
  }

  return {
    activeConversation,
    setActiveConversation,
    handleConversationChange,
    initializeFromSavedSession,
  }
}
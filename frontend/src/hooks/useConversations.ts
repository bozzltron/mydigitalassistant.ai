import { createSignal, createEffect } from 'solid-js'
import { fetchSessions, createConversation } from '../state/session'
import { user } from '../state/user'
import { Session } from '../state/session'

export function useConversations() {
  const [conversations, setConversations] = createSignal<Session[]>([])
  const [isLoading, setIsLoading] = createSignal(false)

  const fetchSessionsFromAPI = async () => {
    const u = user()
    if (!u) return

    setIsLoading(true)
    try {
      const sessions = await fetchSessions(u.id)
      setConversations(sessions)
    } catch (error) {
      console.error('Failed to fetch sessions:', error)
    } finally {
      setIsLoading(false)
    }
  }

  const createNewConversation = async (title?: string) => {
    const u = user()
    if (!u) return null

    try {
      const sessionId = await createConversation(u.id, title?.trim())
      await fetchSessionsFromAPI()
      return sessionId
    } catch (error) {
      console.error('Failed to create conversation:', error)
      return null
    }
  }

  createEffect(() => {
    const u = user()
    if (u) {
      fetchSessionsFromAPI()
    }
  })

  return {
    conversations,
    isLoading,
    fetchSessionsFromAPI,
    createNewConversation,
  }
}
import { createSignal } from 'solid-js'

// Session type definition
export interface Session {
  id: string
  title?: string
  createdAt: string
  updatedAt: string
}

// Create signal for session state
export const [session, setSession] = createSignal<Session | null>(null)
export const [isSessionLoading, setIsSessionLoading] = createSignal(false)

// Mock function to simulate fetching session data
export const fetchSession = async (id: string) => {
  setIsSessionLoading(true)
  try {
    // This would normally be an API call
    await new Promise(resolve => setTimeout(resolve, 300))
    setSession({
      id,
      title: 'Test Session',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    })
  } catch (error) {
    console.error('Failed to fetch session:', error)
  } finally {
    setIsSessionLoading(false)
  }
}

// Fetch messages for a session from the API
export const fetchSessionMessages = async (sessionId: string, userId: number) => {
  try {
    const resp = await fetch(`/chat/session/${encodeURIComponent(sessionId)}/messages?user_id=${userId}&limit=50`, {
      credentials: 'include'
    })
    if (!resp.ok) return []
    const data = await resp.json()
    // Convert API response to message objects
    return (data.messages || []).map((m: any) => ({
      role: m.role,
      content: m.content
    }))
  } catch (error) {
    console.error('Failed to fetch session messages:', error)
    return []
  }
}
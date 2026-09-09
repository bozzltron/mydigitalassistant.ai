import { Meta } from '@solidjs/meta'
import { createEffect, createSignal } from 'solid-js'
import { fetchUser } from './state/user'
import { loadSettings } from './state/settings'
import { fetchSessionMessages, fetchSessions, createConversation } from './state/index'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
import SettingsPanel from './components/ui/SettingsPanel'
import { Session } from './state/session'
import { user } from './state/user'
import './App.css'

export default function App() {
  // Initialize on app start - fetch user and settings (runs once)
  createEffect(async () => {
    console.log('APP INIT: Starting initialization...')
    await fetchUser()
    console.log('APP INIT: fetchUser completed')
    loadSettings()
    console.log('APP INIT: loadSettings completed')
  })

  // Restore previous conversation from session_id in localStorage
  // Run once on mount using a flag to prevent re-triggering
  createEffect(() => {
    // Only run once by checking a flag stored in localStorage
    // or by checking if we've already initialized
    const alreadyInitialized = localStorage.getItem('app_initialize_complete')
    
    if (alreadyInitialized) return
    localStorage.setItem('app_initialize_complete', 'true')
    
    const savedSessionId = localStorage.getItem('session_id')
    const u = user()
    
    if (savedSessionId && u) {
      console.log('APP INIT: Restoring conversation, session_id =', savedSessionId)
      fetchSessionMessages(savedSessionId, u.id).then(messages => {
        console.log('APP INIT: Restored', messages.length, 'messages for session', savedSessionId)
      }).catch(error => {
        console.error('APP INIT: Failed to restore conversation:', error)
      })
    } else {
      if (!savedSessionId) console.log('APP INIT: No saved session_id in localStorage')
      if (!u) console.log('APP INIT: User not yet loaded')
    }
  })

  const [conversations, setConversations] = createSignal<Session[]>([])
  const [activeConversation, setActiveConversation] = createSignal<Session | null>(null)
  const [isConversationsLoading, setIsConversationsLoading] = createSignal(false)

  // Fetch sessions from API
  const fetchSessionsFromAPI = async () => {
    const u = user()
    if (!u) return
    
    setIsConversationsLoading(true)
    try {
      const sessions = await fetchSessions(u.id)
      setConversations(sessions)
      
      // Set first conversation as active by default, or restore from localStorage
      const savedSessionId = localStorage.getItem('session_id')
      if (savedSessionId) {
        const saved = sessions.find(c => c.id === savedSessionId)
        if (saved) {
          setActiveConversation(saved)
        } else if (sessions.length > 0) {
          setActiveConversation(sessions[0])
        }
      } else if (sessions.length > 0) {
        setActiveConversation(sessions[0])
      }
    } catch (error) {
      console.error('Failed to fetch sessions:', error)
    } finally {
      setIsConversationsLoading(false)
    }
  }

  // Load conversations on app start
  createEffect(() => {
    const u = user()
    if (u) {
      fetchSessionsFromAPI()
    }
  }, [user])

  // Handle conversation selection
  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations().find(c => c.id === conversationId) || null
    setActiveConversation(conversation)
  }

  // Handle creating a new conversation
  const handleNewConversation = async () => {
    const u = user()
    if (!u) return
    
    try {
      const sessionId = await createConversation(u.id)
      // Refresh sessions list
      await fetchSessionsFromAPI()
      // The new conversation will be selected automatically via the refresh
    } catch (error) {
      console.error('Failed to create new conversation:', error)
    }
  }

  return (
    <>
      <TopBar 
        conversations={conversations()} 
        activeConversation={activeConversation()}
        onConversationChange={handleConversationChange}
        onNewConversation={handleNewConversation}
        isLoading={isConversationsLoading()}
      />
      <ChatPage conversation={activeConversation()} />
      <SettingsPanel />
    </>
  )
}
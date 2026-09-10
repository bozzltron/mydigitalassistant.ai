import { createEffect, createSignal } from 'solid-js'
import { fetchUser } from './state/user'
import { loadSettings } from './state/settings'
import { fetchSessionMessages, fetchSessions, createConversation } from './state/index'
import { loadConversationMessages, setMessages } from './state/chat'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
import { Modal } from './components/ui/Modal'
import { Session } from './state/session'
import { user } from './state/user'
import './App.css'

export default function App() {
  const [showNewConvModal, setShowNewConvModal] = createSignal(false)
  const [newConvTitle, setNewConvTitle] = createSignal('')

  const handleNewConversationClick = () => {
    setNewConvTitle('')
    setShowNewConvModal(true)
  }

  const handleNewConversation = async () => {
    const u = user()
    if (!u) return
    try {
      const sessionId = await createConversation(u.id, newConvTitle().trim() || undefined)
      localStorage.setItem('session_id', sessionId)
      setShowNewConvModal(false)
      setNewConvTitle('')
      fetchSessionsFromAPI()
    } catch (error) {
      console.error('Failed to create conversation:', error)
    }
  }

  const handleModalClose = () => {
    setShowNewConvModal(false)
    setNewConvTitle('')
  }
  createEffect(async () => {
    console.log('APP INIT: Starting initialization...')
    await fetchUser()
    console.log('APP INIT: fetchUser completed')
    loadSettings()
    console.log('APP INIT: loadSettings completed')
  })

  createEffect(() => {
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

  const fetchSessionsFromAPI = async () => {
    const u = user()
    if (!u) return
    
    setIsConversationsLoading(true)
    try {
      const sessions = await fetchSessions(u.id)
      setConversations(sessions)
      
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

  createEffect(() => {
    const u = user()
    if (u) {
      fetchSessionsFromAPI()
    }
  })

  // Load conversation messages when active conversation changes
  createEffect(() => {
    const conv = activeConversation()
    const u = user()
    if (conv && u) {
      console.log('Loading messages for conversation:', conv.id)
      loadConversationMessages(conv.id, u.id)
      localStorage.setItem('session_id', conv.id)
    } else if (!conv) {
      // Clear messages when no conversation selected
      setMessages([])
    }
  })

  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations().find(c => c.id === conversationId) || null
    setActiveConversation(conversation)
  }

  const handleConversationCreated = () => {
    fetchSessionsFromAPI()
  }

return (
    <>
      <Modal
        isOpen={showNewConvModal()}
        onClose={handleModalClose}
        title="New Conversation"
        size="small"
      >
        <div class="modal-content">
          <input
            id="new-conv-title"
            type="text"
            placeholder="Conversation name (optional)"
            value={newConvTitle()}
            onInput={(e) => setNewConvTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleNewConversation() }}
          />
          <div class="modal-actions">
            <button
              class="btn-secondary"
              onClick={handleModalClose}
            >
              Cancel
            </button>
            <button
              class="btn-primary"
              onClick={handleNewConversation}
            >
              Create
            </button>
          </div>
        </div>
      </Modal>

      <div class="app-layout">
        <TopBar
          conversations={conversations()}
          activeConversation={activeConversation()}
          onConversationChange={handleConversationChange}
          onNewConversationClick={handleNewConversationClick}
          isLoading={isConversationsLoading()}
        />
        <ChatPage conversation={activeConversation()} />
      </div>
    </>
  )
}
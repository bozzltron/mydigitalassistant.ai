import { createEffect, createSignal } from 'solid-js'
import { fetchUser } from './state/user'
import { loadSettings } from './state/settings'
import { fetchSessionMessages, fetchSessions, createConversation } from './state/index'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
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

  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations().find(c => c.id === conversationId) || null
    setActiveConversation(conversation)
  }

  const handleConversationCreated = () => {
    fetchSessionsFromAPI()
  }

  return (
    <>
      {showNewConvModal() && (
        <div class="modal-overlay" onClick={() => setShowNewConvModal(false)}>
          <div class="modal" onClick={(e) => e.stopPropagation()}>
            <div class="modal-header">
              <h3>New Conversation</h3>
              <button 
                class="modal-close"
                onClick={() => { setShowNewConvModal(false); setNewConvTitle('') }}
                aria-label="Close modal"
              >
                ✕
              </button>
            </div>
            <div class="modal-content">
              <input
                id="new-conv-title"
                type="text"
                placeholder="Conversation name (optional)"
                value={newConvTitle()}
                onInput={(e) => setNewConvTitle(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleNewConversation() }}
              />
              <div style={{"display":"flex","gap":"0.5rem","justify-content":"flex-end","margin-top":"1rem"}}>
                <button 
                  class="voice-btn-small" 
                  onClick={() => { setShowNewConvModal(false); setNewConvTitle('') }}
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
          </div>
        </div>
      )}

      <TopBar 
        conversations={conversations()} 
        activeConversation={activeConversation()}
        onConversationChange={handleConversationChange}
        onNewConversationClick={handleNewConversationClick}
        isLoading={isConversationsLoading()}
      />
      <ChatPage conversation={activeConversation()} />
    </>
  )
}
import { Meta } from '@solidjs/meta'
import { createEffect, createSignal } from 'solid-js'
import { fetchUser } from './state/user'
import { loadSettings } from './state/settings'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
import TracePanel from './components/chat/TracePanel'
import SettingsPanel from './components/ui/SettingsPanel'
import { Session } from './state/session'
import './App.css'

export default function App() {
  // Initialize on app start
  createEffect(() => {
    fetchUser()
    loadSettings()
  })

  // Conversation state management
  const [conversations, setConversations] = createSignal<Session[]>([])
  const [activeConversation, setActiveConversation] = createSignal<Session | null>(null)
  const [isConversationsLoading, setIsConversationsLoading] = createSignal(false)

  // Mock function to fetch sessions (in a real implementation, this would be an API call)
  const fetchSessions = async () => {
    setIsConversationsLoading(true)
    try {
      // This would normally be an API call
      await new Promise(resolve => setTimeout(resolve, 500))
      
      setConversations([
        {
          id: '1',
          title: 'First Conversation',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        },
        {
          id: '2', 
          title: 'Another Conversation',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        },
        {
          id: '3',
          title: 'Yet Another Conversation',
          createdAt: new Date().toISOString(),
          updatedAt: new Date().toISOString()
        }
      ])
      
      // Set first conversation as active by default
      if (conversations().length > 0) {
        setActiveConversation(conversations()[0])
      }
    } catch (error) {
      console.error('Failed to fetch sessions:', error)
    } finally {
      setIsConversationsLoading(false)
    }
  }

  // Load conversations on app start
  createEffect(() => {
    fetchSessions()
  })

  // Handle conversation selection
  const handleConversationChange = (conversationId: string) => {
    const conversation = conversations().find(c => c.id === conversationId) || null
    setActiveConversation(conversation)
  }

  // Handle creating a new conversation
  const handleNewConversation = () => {
    const newConversation: Session = {
      id: Date.now().toString(),
      title: 'New Conversation',
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    }
    
    setConversations(prev => [newConversation, ...prev])
    setActiveConversation(newConversation)
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
      <TracePanel />
      <SettingsPanel />
    </>
  )
}
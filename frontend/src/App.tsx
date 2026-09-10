import { createSignal, createEffect } from 'solid-js'
import { useConversations } from './hooks/useConversations'
import { useActiveConversation } from './hooks/useActiveConversation'
import { useAppInit } from './hooks/useAppInit'
import { useChat } from './hooks/useChat'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
import { Modal } from './components/ui/Modal'
import './App.css'

export default function App() {
  const [showNewConvModal, setShowNewConvModal] = createSignal(false)
  const [newConvTitle, setNewConvTitle] = createSignal('')
  const [assistantName, setAssistantName] = createSignal<string>('Cognitive Assistant')

  const { conversations, isLoading, createNewConversation } = useConversations()
  const {
    activeConversation,
    handleConversationChange,
    initializeFromSavedSession,
  } = useActiveConversation(conversations)
  const { initialized } = useAppInit([assistantName, setAssistantName])
  const { sendMessage } = useChat()

  const handleNewConversationClick = () => {
    setNewConvTitle('')
    setShowNewConvModal(true)
  }

  const handleNewConversation = async () => {
    const sessionId = await createNewConversation(newConvTitle().trim() || undefined)
    if (sessionId) {
      localStorage.setItem('session_id', sessionId)
      setShowNewConvModal(false)
      setNewConvTitle('')
    }
  }

  const handleModalClose = () => {
    setShowNewConvModal(false)
    setNewConvTitle('')
  }

  createEffect(() => {
    if (initialized()) {
      initializeFromSavedSession(conversations())
    }
  })

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
          isLoading={isLoading()}
          assistantName={assistantName()}
        />
        <ChatPage conversation={activeConversation()} sendMessage={sendMessage} />
      </div>
    </>
  )
}
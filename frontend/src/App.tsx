import { createSignal, createEffect, onMount, onCleanup } from 'solid-js'
import { useConversations } from './hooks/useConversations'
import { useActiveConversation } from './hooks/useActiveConversation'
import { useAppInit } from './hooks/useAppInit'
import { getAssistantName } from './services/api'
import { ASSISTANT_NAME_CHANGED_EVENT } from './state/chat'
import ChatPage from './components/chat/ChatPage'
import TopBar from './components/ui/TopBar'
import { Modal } from './components/ui/Modal'

export default function App() {
  const [showNewConvModal, setShowNewConvModal] = createSignal(false)
  const [newConvTitle, setNewConvTitle] = createSignal('')
  const [assistantName, setAssistantName] = createSignal<string>('Cognitive Assistant')

  const { conversations, isLoading, createNewConversation, fetchSessionsFromAPI } = useConversations()
  const {
    activeConversation,
    handleConversationChange,
    setActiveConversation,
    initializeFromSavedSession,
  } = useActiveConversation(conversations)
  const { initialized } = useAppInit([assistantName, setAssistantName])

  // The name lives in memory, so a rename during a conversation must be
  // re-read rather than waiting for a page reload. chat state emits this event
  // when a turn's extraction reports identity_name.full_name changed.
  onMount(() => {
    const refreshName = async () => {
      try {
        const result = await getAssistantName()
        if (result.name) setAssistantName(result.name)
      } catch (error) {
        console.error('Failed to refresh assistant name:', error)
      }
    }
    window.addEventListener(ASSISTANT_NAME_CHANGED_EVENT, refreshName)
    onCleanup(() => window.removeEventListener(ASSISTANT_NAME_CHANGED_EVENT, refreshName))
  })

  const handleNewConversationClick = () => {
    setNewConvTitle('')
    setShowNewConvModal(true)
  }

  const handleNewConversation = async () => {
    const title = newConvTitle().trim()
    const sessionId = await createNewConversation(title || undefined)
    if (sessionId) {
      localStorage.setItem('session_id', sessionId)
      // Select the conversation we just created, falling back to a placeholder
      // if the refreshed list has not caught up yet, so the new chat is active.
      const created = conversations().find((c) => c.id === sessionId) ?? {
        id: sessionId,
        title: title || 'New Conversation',
        episode_count: 0,
        last_activity: null,
        created_at: new Date().toISOString(),
      }
      setActiveConversation(created)
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
          onRefreshConversations={fetchSessionsFromAPI}
        />
        <ChatPage conversation={activeConversation()} />
      </div>
    </>
  )
}
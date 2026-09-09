

import { createEffect } from 'solid-js'
import { Session } from '../../state/session'

interface TopBarProps {
  conversations: Session[]
  activeConversation: Session | null
  onConversationChange: (conversationId: string) => void
  onNewConversation: () => void
  isLoading: boolean
}

export default function TopBar(props: TopBarProps) {
  // Update select dropdown when conversations change
  createEffect(() => {
    const selectElement = document.getElementById('conversation-select') as HTMLSelectElement | null
    if (selectElement && props.conversations) {
      // Clear existing options except the first one
      while (selectElement.options.length > 1) {
        selectElement.remove(1)
      }
      
      // Add new conversation options
      props.conversations.forEach(conversation => {
        const option = document.createElement('option')
        option.value = conversation.id
        option.text = conversation.title || 'Conversation'
        selectElement.add(option)
      })
      
      // Set active conversation
      if (props.activeConversation) {
        selectElement.value = props.activeConversation.id
      } else {
        selectElement.value = ''
      }
    }
  })

  const handleConversationSelect = (e: Event) => {
    const selectElement = e.target as HTMLSelectElement
    const selectedId = selectElement.value
    if (selectedId) {
      props.onConversationChange(selectedId)
    }
  }

  const handleNewConversation = () => {
    props.onNewConversation()
  }

  return (
    <>
        <header>
        <h1 id="agent-name">Cognitive Assistant</h1>
        <div class="conversation-switcher" style="white-space: nowrap;">
        <select 
          id="conversation-select" 
          class="select" 
          style="font-size:0.8rem;padding:0.2rem 0.4rem;"
          onChange={handleConversationSelect}
        >
            <option value="">-- New Conversation --</option>
        </select>
        <button 
          id="new-conversation-btn" 
          class="voice-btn-small" 
          style="padding:0.2rem 0.4rem;font-size:0.75rem;margin-left:0.3rem;"
          onClick={handleNewConversation}
        >New</button>
        </div>
        <div class="header-right">
            <span class="user-badge" id="user-badge">Loading...</span>
            <span class="voice-status-bar" id="voice-status-bar">
            <span class="voice-dot" id="voice-status-dot"></span>
            <span id="voice-status-text">Listening</span>
            <button class="voice-btn-small" id="voice-stop-inline">Stop</button>
            <button class="voice-btn-small danger" id="voice-cancel-inline">Cancel</button>
            </span>
            <button id="voice-mode-btn" class="voice-btn" title="Voice conversation mode">
            Voice
            </button>
            <button id="stop-speaking-btn" class="voice-btn" title="Stop speaking" style="display:none;">
            Stop
            </button>
            <button class="settings-btn" id="settings-toggle" title="Settings">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            </button>
            <a href="/brain-ui" class="nav-link" title="Brain Observatory" style="color:var(--text-dim);text-decoration:none;font-size:0.8rem;padding:0.35rem 0.75rem;border:1px solid var(--border);border-radius:6px;">Brain</a>
        </div>
        </header>
    </>
  )
}


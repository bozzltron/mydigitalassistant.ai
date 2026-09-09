import { createEffect, createSignal } from 'solid-js'
import { Session } from '../../state/session'
import { createConversation } from '../../state/index'
import { user } from '../../state/user'

interface TopBarProps {
  conversations: Session[]
  activeConversation: Session | null
  onConversationChange: (conversationId: string) => void
  isLoading: boolean
}

export default function TopBar(props: TopBarProps) {
  const [showSettings, setShowSettings] = createSignal(false)
  const [showNewConvModal, setShowNewConvModal] = createSignal(false)
  const [newConvTitle, setNewConvTitle] = createSignal('')
  
  const u = user()

  createEffect(() => {
    const selectElement = document.getElementById('conversation-select') as HTMLSelectElement | null
    if (selectElement && props.conversations) {
      while (selectElement.options.length > 1) {
        selectElement.remove(1)
      }
      
      props.conversations.forEach(conversation => {
        const option = document.createElement('option')
        option.value = conversation.id
        option.text = conversation.title || 'Conversation'
        selectElement.add(option)
      })
      
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

  const handleNewConversation = async () => {
    if (!u) return
    try {
      const sessionId = await createConversation(u.id, newConvTitle().trim() || undefined)
      localStorage.setItem('session_id', sessionId)
      setShowNewConvModal(false)
      setNewConvTitle('')
      // The App component will refresh the conversations list
    } catch (error) {
      console.error('Failed to create conversation:', error)
    }
  }

  const openNewConvModal = () => {
    setNewConvTitle('')
    setShowNewConvModal(true)
    // Focus the input after modal opens
    setTimeout(() => {
      const input = document.getElementById('new-conv-title') as HTMLInputElement
      input?.focus()
    }, 0)
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
            disabled={props.isLoading}
          >
            <option value="">-- New Conversation --</option>
          </select>
          <button 
            id="new-conversation-btn" 
            class="voice-btn-small" 
            style="padding:0.2rem 0.4rem;font-size:0.75rem;margin-left:0.3rem;"
            onClick={openNewConvModal}
          >New</button>
        </div>
        <div class="header-right">
          <span class="user-badge" id="user-badge">{u?.name || 'Loading...'}</span>
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
          <button class="settings-btn" id="settings-toggle" title="Settings" onClick={() => setShowSettings(true)}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09A1.65 1.65 0 0 0 9 9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
          </button>
          <a href="/brain-ui" class="nav-link" title="Brain Observatory" style="color:var(--text-dim);text-decoration:none;font-size:0.8rem;padding:0.35rem 0.75rem;border:1px solid var(--border);border-radius:6px;">Brain</a>
        </div>
      </header>

      {showNewConvModal && (
        <div class="modal-overlay" onClick={() => setShowNewConvModal(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', 
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000
        }}>
          <div class="modal" onClick={(e) => e.stopPropagation()} style={{
            background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '12px', 
            padding: '1.5rem', minWidth: '320px', maxWidth: '90vw'
          }}>
            <h3 style="margin: 0 0 1rem;">New Conversation</h3>
            <input
              id="new-conv-title"
              type="text"
              placeholder="Conversation name (optional)"
              value={newConvTitle()}
              onInput={(e) => setNewConvTitle(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleNewConversation() }}
              style={{
                width: '100%', background: 'var(--bg)', border: '1px solid var(--border)',
                borderRadius: '6px', color: 'var(--text)', padding: '0.6rem 0.75rem',
                fontSize: '0.95rem', marginBottom: '1rem', boxSizing: 'border-box'
              }}
            />
            <div style="display: flex; gap: 0.5rem; justify-content: flex-end;">
              <button 
                class="voice-btn-small" 
                onClick={() => { setShowNewConvModal(false); setNewConvTitle('') }}
                style="padding:0.4rem 0.8rem;"
              >
                Cancel
              </button>
              <button 
                class="btn-primary" 
                onClick={handleNewConversation}
                style="padding:0.4rem 0.8rem;"
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {showSettings && (
        <div id="settings-panel" class="settings-panel open" style={{
          position: 'fixed', top: 0, right: 0, width: '300px', height: '100vh',
          background: 'var(--surface)', borderLeft: '1px solid var(--border)',
          zIndex: 1000, padding: '1rem', overflowY: 'auto',
          transition: 'right 0.25s ease'
        }}>
          <div class="settings-title" style="display: flex; justify-content: space-between; align-items: center; marginBottom: 1rem;">
            Settings
            <button class="settings-close" onClick={() => setShowSettings(false)}>&times;</button>
          </div>

          <div class="settings-section">
            <label>Voice settings</label>
            <div class="toggle-row" style="display: flex; justify-content: space-between; align-items: center; marginBottom: 0.75rem;">
              <span class="toggle-label">Read responses aloud</span>
              <input type="checkbox" id="tts-enabled" checked />
            </div>
            <div class="settings-section">
              <label for="voice-select">Voice</label>
              <select id="voice-select" style="width: 100%; background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 0.4rem; borderRadius: 5px; fontSize: 0.85rem;">
                <option value="">Loading voices...</option>
              </select>
            </div>
            <div class="settings-section" style="marginTop: 0.5rem;">
              <label for="voice-speed">Speed</label>
              <input type="range" id="voice-speed" min="0.5" max="2" step="0.1" value="1" style="accent-color: var(--accent);" />
              <span id="voice-speed-value">1.0x</span>
            </div>
            <div class="settings-section" style="marginTop: 0.5rem;">
              <label for="voice-pitch">Pitch</label>
              <input type="range" id="voice-pitch" min="0" max="2" step="0.1" value="1" style="accent-color: var(--accent);" />
              <span id="voice-pitch-value">1.0x</span>
            </div>
            <div class="settings-section" style="marginTop: 0.5rem;">
              <label for="voice-volume">Volume</label>
              <input type="range" id="voice-volume" min="0" max="1" step="0.1" value="1" style="accent-color: var(--accent);" />
              <span id="voice-volume-value">1.0</span>
            </div>
          </div>

          <div class="settings-section" style="marginTop: 1.25rem;">
            <label>Interface</label>
            <div class="toggle-row" style="display: flex; justify-content: space-between; align-items: center; marginBottom: 0.75rem;">
              <span class="toggle-label">Show trace panel</span>
              <input type="checkbox" id="trace-visible" checked />
            </div>
          </div>

          <div class="settings-section" id="search-settings-section" style={{ marginTop: '1.25rem', display: 'none' }}>
            <label>Search backend</label>
            <div class="toggle-row" style="display: flex; justify-content: space-between; align-items: center; marginBottom: 0.75rem;">
              <span class="toggle-label">Use Brave Search API</span>
              <input type="checkbox" id="brave-enabled" />
            </div>
            <p style="fontSize: '0.75rem', color: 'var(--text-dim)', marginTop: '0.25rem';">
              When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
            </p>
          </div>
        </div>
      )}
    </>
  )
}
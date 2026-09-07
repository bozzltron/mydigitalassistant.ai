import { createSignal, createEffect } from 'solid-js'
import { messages, setMessages, postChat } from '../../state/chat'
import MessageList from './MessageList'
import InputBar from './InputBar'
import TracePanel from './TracePanel'
import { fetchSession } from '../../state/session'

export default function ChatPage() {
  const [isSending, setIsSending] = createSignal(false)
  const [sessionTitle, setSessionTitle] = createSignal('New Session')
  const [showTrace, setShowTrace] = createSignal(true)
  const [isRecording, setIsRecording] = createSignal(false)
  const [showSettings, setShowSettings] = createSignal(false)
  
  // Load session on component mount
  createEffect(() => {
    fetchSession('current-session-id')
  })
  
  const handleSendMessage = async (message: string) => {
    if (!message.trim() || isSending()) return
    
    setIsSending(true)
    
    try {
      // Add user message immediately
      const userMessage = {
        role: 'user' as const,
        content: message,
        id: Date.now().toString()
      }
      
      setMessages(prev => [...prev, userMessage])
      
      // Call API to get assistant response
      const response = await postChat(message)
      
      // Add assistant message  
      const assistantMessage = {
        role: 'assistant' as const,
        content: response.response || 'No response received',
        id: Date.now().toString() + '-assistant',
        meta: {
          task_type: response.task_type
        }
      }
      
      setMessages(prev => [...prev, assistantMessage])
    } catch (error) {
      console.error('Error sending message:', error)
      const errorMessage = {
        role: 'assistant' as const,
        content: 'Error: Failed to send message',
        id: Date.now().toString() + '-error'
      }
      setMessages(prev => [...prev, errorMessage])
    } finally {
      setIsSending(false)
    }
  }

  return (
    <>
      {/* Header */}
      <header class="chat-header">
        <h1 id="agent-name">Cognitive Assistant</h1>
        <div class="header-right">
          <span class="user-badge" id="user-badge">Loading...</span>
          <button 
            class={`voice-btn ${isRecording() ? 'active' : ''}`}
            onClick={() => setIsRecording(!isRecording())}
            title="Voice conversation mode"
          >
            Voice
          </button>
          <button 
            class="settings-btn" 
            onClick={() => setShowSettings(true)}
            title="Settings"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="12" cy="12" r="3"/>
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
            </svg>
          </button>
        </div>
      </header>

      {/* Main Content */}
      <div id="main">
        <div id="chat-area">
          <div id="messages">
            <div class="welcome" id="welcome">
              <div class="welcome-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
              <h2>Ready to chat</h2>
              <p>Type a message below or click Voice to start hands-free.</p>
            </div>
          </div>

          <div id="input-row">
            <textarea id="msg-input" placeholder="Type a message..." />
            <div class="file-attach">
              <button class="file-attach-btn btn-icon" title="Attach file" id="file-attach-btn">
                <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M10 2C9.44772 2 9 2.44772 9 3V12H7V3C7 1.34315 8.34315 0 10 0C11.6569 0 13 1.34315 13 3V11C13 13.7614 10.7614 16 8 16C5.23858 16 3 13.7614 3 11V3.5H5V11C5 12.6569 6.34315 14 8 14C9.65685 14 11 12.6569 11 11V3C11 2.44772 10.5523 2 10 2Z"/>
                </svg>
              </button>
              <input type="file" id="file-input" multiple accept=".txt,.csv,.json,.xml,.html,.ics" style="position: absolute; left: -9999px; top: -9999px;" />
            </div>
            <div class="file-chips" id="file-chips-preview"></div>
            <button class="mic-btn btn-icon" title="Dictate into message box">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
                <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                <line x1="12" x2="12" y1="19" y2="22"/>
              </svg>
            </button>
            <button class="btn-primary" id="send-btn" onClick={() => handleSendMessage((document.getElementById('msg-input') as HTMLTextAreaElement)?.value || '')}>
              Send
            </button>
          </div>
        </div>

        {/* Trace Panel - hidden by default like in legacy */}
        <div id="trace-panel" class={showTrace() ? '' : 'hidden'}>
          <div class="trace-header">
            <span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align: middle; margin-right: 4px;"><line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/></svg>
              Trace
            </span>
            <button 
              class="trace-close" 
              onClick={() => setShowTrace(false)}
              id="trace-close"
            >&times;</button>
          </div>
          <div class="trace-content">
            <div class="trace-section">
              <div class="trace-label">Task type</div>
              <div class="trace-value" id="trace-task-type">-</div>
            </div>
            <div class="trace-section">
              <div class="trace-label">Memory context</div>
              <div class="trace-value" id="trace-memory">-</div>
            </div>
            <div class="trace-section">
              <div class="trace-label">Citations</div>
              <div class="trace-value" id="trace-citations">-</div>
            </div>
          </div>
        </div>
      </div>

      {/* Settings panel */}
      <div id="settings-panel" class={showSettings() ? 'open' : ''}>
        <div class="settings-title">
          Settings
          <button class="settings-close" onClick={() => setShowSettings(false)}>&times;</button>
        </div>

        <div class="settings-section">
          <label>Voice settings</label>
          <div class="toggle-row">
            <span class="toggle-label">Read responses aloud</span>
            <input type="checkbox" id="tts-enabled" />
          </div>
          <div class="settings-section">
            <label for="voice-select">Voice</label>
            <select id="voice-select">
              <option value="">Loading voices...</option>
            </select>
          </div>
          <div class="settings-section">
            <label for="voice-speed">Speed</label>
            <input type="range" id="voice-speed" min="0.5" max="2" step="0.1" value="1" />
            <span id="voice-speed-value">1.0x</span>
          </div>
          <div class="settings-section">
            <label for="voice-pitch">Pitch</label>
            <input type="range" id="voice-pitch" min="0" max="2" step="0.1" value="1" />
            <span id="voice-pitch-value">1.0x</span>
          </div>
          <div class="settings-section">
            <label for="voice-volume">Volume</label>
            <input type="range" id="voice-volume" min="0" max="1" step="0.1" value="1" />
            <span id="voice-volume-value">1.0</span>
          </div>
        </div>

        <div class="settings-section">
          <label>Interface</label>
          <div class="toggle-row">
            <span class="toggle-label">Show trace panel</span>
            <input type="checkbox" id="trace-visible" checked />
          </div>
        </div>
      </div>
    </>
  )
}
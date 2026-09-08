import { createSignal, createEffect } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import TracePanel from './TracePanel'
import VoiceControls from './VoiceControls'
import { messages, setMessages, postChat } from '../../state/chat'
import { Session } from '../../state/session'

interface ChatPageProps {
  conversation: Session | null
}

export default function ChatPage(props: ChatPageProps) {
  const [isSending, setIsSending] = createSignal(false)
  const [showTrace, setShowTrace] = createSignal(false)
  const [showSettings, setShowSettings] = createSignal(false)
  const [isRecording, setIsRecording] = createSignal(false)
  const [showVoiceOverlay, setShowVoiceOverlay] = createSignal(false)
  const [voiceModeActive, setVoiceModeActive] = createSignal(false)
  
  // Simple example message to make sure UI renders
  createEffect(() => {
    setMessages([{
      role: 'assistant',
      content: `Welcome to ${props.conversation?.title || 'Chat'}! How can I help you today?`,
      id: 'welcome'
    }])
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
      
      // Simulate API call by waiting a bit
      await new Promise(resolve => setTimeout(resolve, 500))
      
      // Add assistant response  
      const assistantMessage = {
        role: 'assistant' as const,
        content: `I received your message: "${message}"`,
        id: Date.now().toString() + '-assistant',
        meta: {
          task_type: 'functional'
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
    <div id="main">
      <div id="chat-area" class="chat-area">
        <div id="messages" class="message-container">
          <MessageList messages={messages()} />
          
          <div class="welcome-message" id="welcome">
            <div class="welcome-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
            <h2>{props.conversation?.title ? `Conversation: ${props.conversation.title}` : 'Ready to chat'}</h2>
            <p>Type a message below or click Voice to start hands-free.</p>
          </div>
        </div>

      {/* Input row should be outside chat-area at bottom */}
      <div id="input-row">
        <InputBar 
          onSend={handleSendMessage} 
          isSending={isSending()}
        />
        
        <VoiceControls 
          isOpen={showVoiceOverlay()} 
          onClose={() => setShowVoiceOverlay(false)}
          isRecording={isRecording()}
          onStartRecording={() => {
            setIsRecording(true)
            setShowVoiceOverlay(true)
          }}
          onStopRecording={() => {
            setIsRecording(false)
            setShowVoiceOverlay(false)
          }}
        />
      </div>
      </div>

      {/* Trace Panel - hidden by default like in legacy */}
      <div id="trace-panel" class={`trace-panel ${showTrace() ? '' : 'hidden'}`}>
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
          <div class="trace-section" id="trace-search-section" style="display:none">
            <div class="trace-label">Search</div>
            <div class="trace-value" id="trace-search-info">-</div>
          </div>
        </div>
      </div>

      {/* Settings panel */}
      <div id="settings-panel" class={`settings-panel ${showSettings() ? 'open' : ''}`}>
        <div class="settings-title">
          Settings
          <button class="settings-close" onClick={() => setShowSettings(false)}>&times;</button>
        </div>

        <div class="settings-section">
          <label>Voice settings</label>
          <div class="toggle-row">
            <span class="toggle-label">Read responses aloud</span>
            <input type="checkbox" id="tts-enabled" checked />
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
        
        <div class="settings-section" id="search-settings-section" style="display:none">
          <label>Search backend</label>
          <div class="toggle-row">
            <span class="toggle-label">Use Brave Search API</span>
            <input type="checkbox" id="brave-enabled" />
          </div>
          <p style="font-size:0.75rem;color:var(--text-dim);margin-top:0.25rem;">
            When enabled, queries are sent to Brave's servers. Only available when a Brave API key is configured.
          </p>
        </div>
      </div>

      {/* Voice recording overlay - this is rendered by VoiceControls now */}
    </div>
  )
}
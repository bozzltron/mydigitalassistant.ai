import { createSignal, createEffect, Show } from 'solid-js'
import MessageList from './MessageList'
import InputBar from './InputBar'
import VoiceControls from './VoiceControls'
import StatusIndicator from './StatusIndicator'
import { messages, setMessages, postChatMessage, sessionId, setSessionId, isTurnActive } from '../../state/chat'
import { Session } from '../../state/session'

interface ChatPageProps {
  conversation: Session | null
}

export default function ChatPage(props: ChatPageProps) {
  const [showTrace, setShowTrace] = createSignal(false)
  const [isRecording, setIsRecording] = createSignal(false)
  const [showVoiceOverlay, setShowVoiceOverlay] = createSignal(false)
  const [isDictating, setIsDictating] = createSignal(false)
  
  const isSending = isTurnActive

  const handleSendMessage = async (message: string) => {
    if (!message.trim() || isSending()) return
    
    try {
      const userMessage = {
        role: 'user' as const,
        content: message,
        id: Date.now().toString()
      }
      
      setMessages(prev => [...prev, userMessage])
      
      const currentSessionId = sessionId()
      const result = await postChatMessage(message, currentSessionId || undefined)
      
      if (result.session_id) {
        setSessionId(result.session_id)
        localStorage.setItem('session_id', result.session_id)
      }
      
      const assistantMessage = {
        role: 'assistant' as const,
        content: result.response,
        id: Date.now().toString() + '-assistant',
        meta: {
          task_type: result.task_type,
          extraction_summary: result.extraction_summary,
          search_extraction_summary: result.search_extraction_summary,
          search_info: result.search_info,
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
    }
  }

  const handleDictationStart = () => {
    setIsDictating(true)
    setShowVoiceOverlay(true)
  }

  const handleDictationStop = () => {
    setIsDictating(false)
    setShowVoiceOverlay(false)
  }

  return (
    <div id="main">
      <div id="chat-area" class="chat-area">
        <div id="messages">
          <MessageList messages={messages()} />
          
          <StatusIndicator />
          
          <Show when={messages().length === 0}>
            <div class="welcome-message" id="welcome">
              <div class="welcome-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg></div>
              <h2>{props.conversation?.title ? `Conversation: ${props.conversation.title}` : 'Ready to chat'}</h2>
              <p>Type a message below or click Voice to start hands-free.</p>
            </div>
          </Show>
        </div>

        <div id="input-row">
          <InputBar 
            onSend={handleSendMessage} 
            isSending={isSending()}
            onDictationStart={handleDictationStart}
            onDictationStop={handleDictationStop}
            isDictating={isDictating()}
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

      <div id="trace-panel" class={`trace-panel ${showTrace() ? '' : 'hidden'}`}>
        <div class="trace-header">
          <span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style={{"vertical-align":"middle","margin-right":"4px"}}><line x1="18" x2="18" y1="20" y2="10"/><line x1="12" x2="12" y1="20" y2="4"/><line x1="6" x2="6" y1="20" y2="14"/></svg>
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
          <div class="trace-section" id="trace-search-section" style={{"display":"none"}}>
            <div class="trace-label">Search</div>
            <div class="trace-value" id="trace-search-info">-</div>
          </div>
        </div>
      </div>
    </div>
  )
}
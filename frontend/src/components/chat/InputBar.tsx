import { createSignal } from 'solid-js'
import VoiceControls from './VoiceControls'
import { postChat } from '../../services/api'

interface InputBarProps {
  onSend: (message: string) => void
  isSending?: boolean
}

export default function InputBar(props: InputBarProps) {
  const [message, setMessage] = createSignal('')
  const [isFocused, setIsFocused] = createSignal(false)

  const handleSubmit = async (e: Event) => {
    e.preventDefault()
    if (message().trim() && !props.isSending) {
      props.onSend(message())
      setMessage('')
    }
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as any)
    }
  }

  return (
    <form class="input-bar" onSubmit={handleSubmit}>
      <div class="input-container">
        <textarea
          class="message-input"
          value={message()}
          onInput={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => setIsFocused(true)}
          onBlur={() => setIsFocused(false)}
          placeholder="Type your message..."
          rows={1}
        />
        
        <div class="input-actions">
          <VoiceControls />
          
          <button 
            type="submit" 
            class="send-button"
            disabled={!message().trim() || props.isSending}
          >
            {props.isSending ? 'Sending...' : 'Send'}
          </button>
        </div>
      </div>
      
      <div class="file-attachments">
        {/* File attachment area would go here */}
        <div class="attachment-placeholder">📎 Attach files</div>
      </div>
    </form>
  )
}
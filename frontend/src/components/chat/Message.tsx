import { Show } from 'solid-js'
import { MessageMeta } from '../../state/chat'

interface MessageProps {
  role: 'user' | 'assistant'
  content: string
  meta?: MessageMeta
  onReact?: (kind: 'positive' | 'negative' | 'correction', msgId: string) => void
}

export default function Message(props: MessageProps) {
  const isUser = props.role === 'user'
  
  return (
    <div class={`message ${isUser ? 'user-message' : 'assistant-message'}`}>
      <div class="message-content">
        <div class="message-text" innerHTML={props.content} />
        
        <Show when={props.meta}>
          <div class="message-meta">
            <div class="task-type">Task: {props.meta?.task_type || 'Unknown'}</div>
            {props.meta?.citations && props.meta.citations.length > 0 && (
              <div class="citations">
                <strong>Citations:</strong>{' '}
                {props.meta.citations.map((citation, i) => (
                  <span key={i} class="citation">
                    <a href={citation} target="_blank">{citation}</a>
                  </span>
                ))}
              </div>
            )}
          </div>
        </Show>
      </div>
      
      <div class="message-actions">
        <button 
          class="react-button" 
          aria-label="Positive reaction"
          onClick={() => props.onReact?.('positive', 'test-msg-id')}
        >
          👍
        </button>
        <button 
          class="react-button" 
          aria-label="Negative reaction"
          onClick={() => props.onReact?.('negative', 'test-msg-id')}
        >
          👎
        </button>
        <button 
          class="react-button" 
          aria-label="Correction"
          onClick={() => props.onReact?.('correction', 'test-msg-id')}
        >
          ✏️
        </button>
      </div>
    </div>
  )
}
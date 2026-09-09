import { For } from 'solid-js'
import Message from './Message'
import { ChatMessage } from '../../state/chat'
import { postFeedback, postCorrection } from '../../services/api'

interface MessageListProps {
  messages: ChatMessage[]
}

export default function MessageList(props: MessageListProps) {
  const handleReact = async (kind: 'positive' | 'negative' | 'correction', msgId: string) => {
    if (kind === 'correction') return
    try {
      await postFeedback(
        null,
        msgId,
        kind,
        null
      )
    } catch (e) {
      console.error('Failed to submit reaction:', e)
    }
  }

  const handleCopy = (text: string) => {
    const plain = text
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/[#*`_\[\]]/g, '')
      .replace(/\n+/g, ' ')
      .trim()
    navigator.clipboard.writeText(plain).then(() => {
      // Could show toast here
    }).catch(() => {})
  }

  const handleCorrect = async (msgId: string) => {
    // This will be handled by the Message component's correction panel
  }

  return (
    <div class="message-list">
      <For each={props.messages}>
        {(message) => (
          <Message 
            message={message}
            onReact={handleReact}
            onCopy={handleCopy}
            onCorrect={handleCorrect}
          />
        )}
      </For>
    </div>
  )
}
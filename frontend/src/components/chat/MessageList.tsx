import { For } from 'solid-js'
import Message from './Message'
import { ChatMessage } from '../../state/chat'
import { postFeedback } from '../../services/api'

export default function MessageList(props: { messages: () => ChatMessage[] }) {
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
      .replace(/[#*`_[\]]/g, '')
      .replace(/\n+/g, ' ')
      .trim()
    navigator.clipboard.writeText(plain).then(() => {
      // Could show toast here
    }).catch(() => {})
  }

  const handleCorrect = async () => {
    // This will be handled by the Message component's correction panel
  }

  return (
    <For each={props.messages()}>
      {(message) => (
        <Message 
          message={message}
          onReact={handleReact}
          onCopy={handleCopy}
          onCorrect={handleCorrect}
        />
      )}
    </For>
  )
}
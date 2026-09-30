import { For, Show } from 'solid-js'
import Message from './Message'
import { sessionId } from '../../state/chat'
import type { ChatMessage } from '../../types/chat'
import { postFeedback, postCorrection } from '../../services/api'

export default function MessageList(props: { messages: () => ChatMessage[] }) {
  const handleReact = async (kind: 'positive' | 'negative' | 'correction', msgId: string) => {
    if (kind === 'correction') return
    try {
      // The backend's first parameter is a *session id* despite its legacy name
      // (see MemoryStore.apply_positive_feedback). Passing null here made every
      // reaction a silent no-op: the handler returned 0 updated slots and still
      // reported {"status":"ok"}, so the confidence-reinforcement loop never ran
      // from the web UI.
      await postFeedback(
        sessionId(),
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

  const handleCorrect = async (msgId: string, text: string): Promise<boolean> => {
    try {
      // The endpoint takes the session id in its `episode_id` field (legacy
      // name); `message_id` scopes the audit record. Applying the correction is
      // the whole point of the panel — this used to be an empty function, so
      // every submitted correction was silently discarded.
      await postCorrection(sessionId(), msgId, text)
      return true
    } catch (e) {
      console.error('Failed to submit correction:', e)
      return false
    }
  }

  return (
    // Solid diffs <For> by item identity. The streaming message keeps a stable
    // id and is updated in place through the chat store, so the bubble
    // reconciles instead of being recreated on every token.
    <For each={props.messages()}>
      {(message) => (
        // Don't render empty streaming assistant messages (wait for first token)
        <Show when={!(message.role === 'assistant' && message.meta?.isStreaming && !message.content.trim())}>
          <Message 
            message={message}
            onReact={handleReact}
            onCopy={handleCopy}
            onCorrect={handleCorrect}
          />
        </Show>
      )}
    </For>
  )
}
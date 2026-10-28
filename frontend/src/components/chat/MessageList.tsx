import { For, Show } from 'solid-js'
import Message from './Message'
import { sessionId } from '../../state/chat'
import type { ChatMessage } from '../../types/chat'
import { postFeedback, postCorrection } from '../../services/api'

export default function MessageList(props: { messages: () => ChatMessage[] }) {
  // Ids this list has already accounted for. A plain Set, never a signal: it is
  // read and written only when <For> creates a row, so it can neither re-run on
  // an unrelated store update nor make the row's class reactive. A reactive
  // marker is exactly the bug this avoids — it would re-apply while a later
  // token streams into an old message and replay the slide.
  const seen = new Set<string>()

  // Seed from the rows already in the store at this mount. The transcript is
  // keyed on the session (see ChatPage), so opening or switching a conversation
  // remounts this list with the thread already populated; those rows were read
  // before and must not enter again. The snapshot is the mechanism, not an
  // oversight: a tracked read here would just re-run nothing.
  // eslint-disable-next-line solid/reactivity
  for (const message of props.messages()) seen.add(message.id)

  /** True the first time this list sees a row, and never for a restored one. */
  const isArriving = (id: string): boolean => {
    const known = seen.has(id)
    seen.add(id)
    if (known) return false
    // `history-*` rows are restored by loadConversationMessages and can land
    // after this mount (the fetch resolves late), so they are suppressed by id
    // as well: the seed alone would let a late response animate.
    return !id.startsWith('history-')
  }

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
      {(message) => {
        // Decided once, here, when <For> creates the row. Reading it inside the
        // JSX would put the decision back inside a reactive expression.
        const arriving = isArriving(message.id)
        return (
          // Don't render empty streaming assistant messages (wait for first token)
          <Show when={!(message.role === 'assistant' && message.meta?.isStreaming && !message.content.trim())}>
            <Message
              message={message}
              arriving={arriving}
              onReact={handleReact}
              onCopy={handleCopy}
              onCorrect={handleCorrect}
            />
          </Show>
        )
      }}
    </For>
  )
}
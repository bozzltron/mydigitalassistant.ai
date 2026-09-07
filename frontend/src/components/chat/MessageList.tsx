import { For } from 'solid-js'
import Message from './Message'
import { ChatMessage } from '../../state/chat'

interface MessageListProps {
  messages: ChatMessage[]
  onReact?: (kind: 'positive' | 'negative' | 'correction', msgId: string) => void
}

export default function MessageList(props: MessageListProps) {
  return (
    <div class="message-list">
      <For each={props.messages}>
        {(message) => (
          <Message 
            role={message.role}
            content={message.content}
            meta={message.meta}
            onReact={props.onReact}
          />
        )}
      </For>
    </div>
  )
}
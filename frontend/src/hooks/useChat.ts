import { postChatMessage, addMessageToConversation, sessionId, messages, isTurnActive, currentTurnId } from '../state/chat'
import { ChatMessage } from '../state/chat'

export function useChat() {
  const sendMessage = async (
    message: string,
    session_id?: string,
    attached_files?: File[]
  ) => {
    return postChatMessage(message, session_id, attached_files)
  }

  const addMessage = (sessionIdParam: string, message: ChatMessage) => {
    addMessageToConversation(sessionIdParam, message)
  }

  return {
    sendMessage,
    addMessage,
    messages,
    sessionId,
    isTurnActive,
    currentTurnId,
  }
}
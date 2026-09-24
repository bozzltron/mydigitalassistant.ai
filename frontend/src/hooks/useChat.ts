import { postChatMessage, postChatMessageStream, addMessageToConversation, sessionId, messages, isTurnActive, currentTurnId, isStreaming, streamingMessageId } from '../state/chat'
import { ChatMessage } from '../state/chat'
import type { AttachedFile } from '../../types'

export function useChat() {
  const sendMessage = async (
    message: string,
    session_id?: string,
    attached_files?: AttachedFile[],
    max_intelligence?: boolean
  ) => {
    return postChatMessage(message, session_id, attached_files, max_intelligence)
  }

  const sendMessageStream = async (
    message: string,
    session_id?: string,
    attached_files?: AttachedFile[],
    search_consent?: boolean,
    max_intelligence?: boolean
  ) => {
    return postChatMessageStream(message, session_id, attached_files, search_consent, max_intelligence)
  }

  const addMessage = (sessionIdParam: string, message: ChatMessage) => {
    addMessageToConversation(sessionIdParam, message)
  }

  return {
    sendMessage,
    sendMessageStream,
    addMessage,
    messages,
    sessionId,
    isTurnActive,
    currentTurnId,
    isStreaming,
    streamingMessageId,
  }
}
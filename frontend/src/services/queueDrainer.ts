import {
  getQueue,
  dequeue,
  isProcessing,
  setProcessing,
  setDrainBlocked,
  isDrainBlocked,
  getActiveConversationId,
  combineQueuedMessages,
} from '../state/messageQueue';
import { postChatMessageStream, addMessageToConversation, setSessionId, sessionId } from '../state/chat';
import type { ChatMessage, SearchInfo } from '../types';

/**
 * Search consent is raised by the backend mid-stream, but the UI modal lives in
 * ChatPage. The drainer owns the send, so it hands the result back through this
 * hook instead of reaching into component state.
 */
type SearchConsentHandler = (info: { searchInfo: SearchInfo; content: string }) => void;

let searchConsentHandler: SearchConsentHandler | null = null;

export function onSearchConsentRequired(fn: SearchConsentHandler): () => void {
  searchConsentHandler = fn;
  return () => {
    if (searchConsentHandler === fn) searchConsentHandler = null;
  };
}

/**
 * QueueDrainer - Processes the message queue for the active conversation.
 * Drains ALL queued messages as a single chat turn.
 */
export async function drainQueueIfReady(): Promise<void> {
  const messages = getQueue();
  if (messages.length === 0) return;

  // Already processing? Let the current drain handle it
  if (isProcessing()) return;

  // A previous attempt failed against this backend; stand down until the user
  // does something new, which reads as intent to try again.
  if (isDrainBlocked()) return;

  // Prefer the queue's active conversation, but fall back to the session that
  // is actually on screen. activeConversationId is hand-maintained state that
  // only the text path and the postChat* finally blocks used to update, so
  // treating it as the sole gate stranded anything enqueued by voice.
  const conversationId = getActiveConversationId() ?? sessionId();
  if (!conversationId) {
    console.warn('[queueDrainer] No active conversation, skipping drain');
    return;
  }

  await processQueue(conversationId, messages);
}

/**
 * Process the entire queue as a single turn.
 */
async function processQueue(conversationId: string, messages: ReturnType<typeof getQueue>): Promise<void> {
  setProcessing(true);

  try {
    // Combine all queued messages into single prompt
    const combinedPrompt = combineQueuedMessages(messages);

    // Attached files from all messages (voice messages won't have files)
    const attachedFiles = messages.flatMap((m) => m.attachedFiles || []);

    // Flags come from the queued messages themselves, so a consent retry or a
    // mid-queue max_intelligence toggle survives the trip through the queue.
    const searchConsent = messages.some((m) => m.searchConsent === true);
    const maxIntelligence = messages.some((m) => m.maxIntelligence === true);

    // A message is either queued or in the transcript, never both, so hand these
    // over to the transcript as the request goes out. Two things fall out of that.
    //
    // The commit has to happen before the stream starts, because
    // postChatMessageStream appends the reply as it arrives -- committing
    // afterwards ordered the transcript [.., reply, prompt] and put the reply
    // above the message it was answering.
    //
    // And dequeuing at the same moment is what keeps the queue panel showing
    // only what is genuinely still on deck. Dequeuing after the reply arrived
    // left every message rendered twice for the whole duration of the request.
    // History holds the individual utterances, not the combined prompt, so the
    // transcript matches what the user actually said.
    messages.forEach((m) => {
      if (!m.skipHistory) {
        addMessageToConversation(conversationId, {
          role: 'user',
          content: m.content,
          id: m.id,
          meta: { isQueued: false, source: m.source } as ChatMessage['meta'],
        });
      }
      dequeue(m.id);
    });

    const result = await postChatMessageStream(
      combinedPrompt,
      conversationId,
      attachedFiles,
      searchConsent,
      maxIntelligence
    );

    // Only adopt the backend's session_id when it matches what we sent. A raw
    // UUID the backend minted for a request that lacked a session_id would
    // otherwise orphan the conversation history.
    if (result?.session_id && result.session_id === conversationId) {
      setSessionId(result.session_id);
      if (typeof localStorage !== 'undefined') {
        localStorage.setItem('session_id', result.session_id);
      }
    }

    // A search-consent prompt must not be followed by the rest of the queue:
    // the agent is blocked on the user answering the question.
    if (result?.task_type === 'search_consent_required' && result.search_info) {
      console.warn('[queueDrainer] Search consent required, holding remaining messages');
      searchConsentHandler?.({
        searchInfo: result.search_info,
        content: messages[messages.length - 1]?.content ?? combinedPrompt,
      });
      return;
    }

    // Check if new messages arrived during processing
    const remainingMessages = getQueue();
    if (remainingMessages.length > 0) {
      // Recursively drain remaining
      await processQueue(conversationId, remainingMessages);
    }

  } catch (error) {
    console.error('[queueDrainer] Error processing queue:', error);
    // The messages have already moved to the transcript, so there is nothing
    // left to retry and the queue is empty. Stand the auto-drain effect down
    // anyway: without it, any message enqueued while the failed request was in
    // flight would be retried in a hot loop against the backend.
    setDrainBlocked(true);
  } finally {
    setProcessing(false);
  }
}

/**
 * Explicit drain request from an entrypoint (user typed, spoke, or answered a
 * consent prompt). This counts as fresh intent, so it clears a prior failure.
 */
export async function triggerDrain(): Promise<void> {
  setDrainBlocked(false);
  await drainQueueIfReady();
}

/**
 * Force drain current queue (used for testing or manual flush).
 */
export async function forceDrain(): Promise<void> {
  const messages = getQueue();
  if (messages.length === 0) return;

  const conversationId = getActiveConversationId();
  if (!conversationId) return;

  await processQueue(conversationId, messages);
}

/**
 * Get combined prompt for current queue (for UI preview).
 */
export function getCombinedPrompt(): string {
  return combineQueuedMessages(getQueue());
}
import {
  getQueue,
  isProcessing,
  setProcessing,
  getActiveConversationId,
  combineQueuedMessages,
} from '../state/messageQueue';
import { sendMessageStream } from '../state/chat';

/**
 * QueueDrainer - Processes the message queue for the active conversation.
 * Drains ALL queued messages as a single chat turn.
 */
export async function drainQueueIfReady(): Promise<void> {
  const messages = getQueue();
  if (messages.length === 0) return;

  // Already processing? Let the current drain handle it
  if (isProcessing()) return;

  const conversationId = getActiveConversationId();
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

    // Send as single stream
    await sendMessageStream(
      combinedPrompt,
      conversationId,
      attachedFiles,
      false, // search_consent - will be handled by backend if needed
      false  // max_intelligence
    );

    // Clear processed messages from queue
    // Note: drainQueue() clears all. Messages that arrived during processing
    // will be handled by the auto-re-drain below.

    // Check if new messages arrived during processing
    const remainingMessages = getQueue();
    if (remainingMessages.length > 0) {
      // Recursively drain remaining
      await processQueue(conversationId, remainingMessages);
    }

  } catch (error) {
    console.error('[queueDrainer] Error processing queue:', error);
    // On error, keep messages in queue for retry
    // Don't clear the queue
  } finally {
    setProcessing(false);
  }
}

/**
 * Trigger drain from outside (e.g., after chat turn completes).
 * Called by ChatPage when streaming completes.
 */
export async function triggerDrain(): Promise<void> {
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
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// Mock ../state/chat with ONLY the real export names. The drainer must use
// postChatMessageStream (the actual export). A typo like `sendMessageStream`
// resolves to undefined here and surfaces as a TypeError instead of silently
// wedging the queue at runtime.
const postChatMessageStream = vi.fn(async () => ({ response: 'ok' }));
const addMessageToConversation = vi.fn();
const setSessionId = vi.fn();
let sessionIdValue: string | null = null;
vi.mock('../state/chat', () => ({
  postChatMessageStream: (...args: unknown[]) => postChatMessageStream(...(args as [])),
  addMessageToConversation: (...args: unknown[]) => addMessageToConversation(...(args as [])),
  setSessionId: (...args: unknown[]) => setSessionId(...(args as [])),
  sessionId: () => sessionIdValue,
}));

import {
  enqueue,
  clearQueue,
  isProcessing,
  setProcessing,
  isDrainBlocked,
  setActiveConversation,
  getQueue,
} from '../state/messageQueue';
import { drainQueueIfReady, triggerDrain, forceDrain, onSearchConsentRequired } from './queueDrainer';

describe('queueDrainer', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // clearAllMocks does not reset implementations, so a mockRejectedValue from
    // one test would otherwise leak into the next.
    postChatMessageStream.mockResolvedValue({ response: 'ok' });
    addMessageToConversation.mockClear();
    setSessionId.mockClear();
    sessionIdValue = null;
    clearQueue();
    setProcessing(false);
    setActiveConversation(null);
  });

  afterEach(() => {
    clearQueue();
    setProcessing(false);
    setActiveConversation(null);
  });

  it('sends the queued message as a chat turn using the real postChatMessageStream export', async () => {
    setActiveConversation('conv-1');
    enqueue({ content: 'what is the weather', source: 'voice', timestamp: Date.now() });

    await triggerDrain();

    expect(postChatMessageStream).toHaveBeenCalledTimes(1);
    expect(postChatMessageStream.mock.calls[0][0]).toBe('what is the weather');
    expect(postChatMessageStream.mock.calls[0][1]).toBe('conv-1');
  });

  it('does not throw when draining a populated queue (regression: undefined drain fn wedged processing=true)', async () => {
    setActiveConversation('conv-1');
    enqueue({ content: 'hello there', source: 'voice', timestamp: Date.now() });

    await expect(drainQueueIfReady()).resolves.not.toThrow();
    // processing must be released so later utterances can still drain
    expect(isProcessing()).toBe(false);
  });

  it('releases the processing flag when the send fails', async () => {
    postChatMessageStream.mockRejectedValueOnce(new Error('network down'));
    setActiveConversation('conv-1');
    enqueue({ content: 'hello', source: 'voice', timestamp: Date.now() });

    await drainQueueIfReady();

    expect(isProcessing()).toBe(false);
  });

  it('collapses multiple queued messages into a single turn', async () => {
    setActiveConversation('conv-1');
    enqueue({ content: 'first', source: 'voice', timestamp: Date.now() });
    enqueue({ content: 'second', source: 'voice', timestamp: Date.now() });

    await triggerDrain();

    expect(postChatMessageStream).toHaveBeenCalledTimes(1);
    const prompt = postChatMessageStream.mock.calls[0][0] as string;
    expect(prompt).toContain('first');
    expect(prompt).toContain('second');
  });

  it('skips draining only when there is no active conversation AND no session', async () => {
    enqueue({ content: 'orphaned', source: 'voice', timestamp: Date.now() });

    await drainQueueIfReady();

    expect(postChatMessageStream).not.toHaveBeenCalled();
    expect(isProcessing()).toBe(false);
    // message is kept for a later conversation rather than dropped
    expect(getQueue()).toHaveLength(1);
  });

  it('drains using the on-screen session when the queue has no active conversation (regression: voice enqueued without setActiveConversation, so nothing was ever sent)', async () => {
    // Reproduces the reported failure: the conversation-voice hook enqueues and
    // calls triggerDrain without ever setting activeConversationId.
    sessionIdValue = 'session-from-screen';
    enqueue({ content: 'catch me up on classic literature', source: 'voice', timestamp: 1 });

    await triggerDrain();

    expect(postChatMessageStream).toHaveBeenCalledTimes(1);
    expect(postChatMessageStream.mock.calls[0][1]).toBe('session-from-screen');
  });

  it('prefers the queue active conversation over the on-screen session', async () => {
    sessionIdValue = 'session-from-screen';
    setActiveConversation('session-from-queue');
    enqueue({ content: 'hello', source: 'text', timestamp: 1 });

    await triggerDrain();

    expect(postChatMessageStream.mock.calls[0][1]).toBe('session-from-queue');
  });

  it('forceDrain sends a single queued message', async () => {
    setActiveConversation('conv-2');
    enqueue({ content: 'manual flush', source: 'text', timestamp: Date.now() });

    await forceDrain();

    expect(postChatMessageStream).toHaveBeenCalledTimes(1);
    expect(postChatMessageStream.mock.calls[0][1]).toBe('conv-2');
  });

  describe('conversation history', () => {
    it('commits each sent message to history as a user message', async () => {
      setActiveConversation('conv-h');
      enqueue({ content: 'remember this', source: 'voice', timestamp: 1 });

      await triggerDrain();

      expect(addMessageToConversation).toHaveBeenCalledTimes(1);
      const [convId, msg] = addMessageToConversation.mock.calls[0] as [string, { role: string; content: string }];
      expect(convId).toBe('conv-h');
      expect(msg.role).toBe('user');
      expect(msg.content).toBe('remember this');
    });

    it('commits each message separately, not the combined prompt', async () => {
      setActiveConversation('conv-h2');
      enqueue({ content: 'first thing', source: 'text', timestamp: 1 });
      enqueue({ content: 'second thing', source: 'text', timestamp: 2 });

      await triggerDrain();

      const contents = addMessageToConversation.mock.calls.map(
        (c) => (c[1] as { content: string }).content
      );
      expect(contents).toEqual(['first thing', 'second thing']);
    });

    it('does not duplicate a consent retry that is already in history', async () => {
      setActiveConversation('conv-h3');
      enqueue({ content: 'weather in paris', source: 'text', timestamp: 1, searchConsent: true, skipHistory: true });

      await triggerDrain();

      expect(addMessageToConversation).not.toHaveBeenCalled();
    });

    it('commits the user turn BEFORE the stream starts, so the reply cannot render above it (regression)', async () => {
      // postChatMessageStream appends the assistant reply to the transcript as it
      // arrives, so committing the user's own turn afterwards ordered the
      // transcript [.., reply, prompt] and stacked the reply on top of the
      // message it was answering. A refresh hid it because the server returns
      // the pair in the right order.
      const order: string[] = [];
      addMessageToConversation.mockImplementation(() => {
        order.push('commit-user');
      });
      postChatMessageStream.mockImplementation(async () => {
        order.push('stream-reply');
        return { response: 'ok' };
      });
      setActiveConversation('conv-order');
      enqueue({ content: 'ordered please', source: 'text', timestamp: 1 });

      await triggerDrain();

      expect(order).toEqual(['commit-user', 'stream-reply']);
    });

    it('moves the message out of the queue as the request goes out (regression: rendered in both the panel and the transcript)', async () => {
      const order: string[] = [];
      setActiveConversation('conv-handoff');
      const id = enqueue({ content: 'only once', source: 'text', timestamp: 1 });
      postChatMessageStream.mockImplementation(async () => {
        // Mid-flight is exactly when the message used to be on screen twice.
        order.push(`queue-during-send:${getQueue().length}`);
        return { response: 'ok' };
      });

      await drainQueueIfReady();

      expect(order).toEqual(['queue-during-send:0']);
      expect(getQueue()).toHaveLength(0);
      // and the transcript carries it instead
      expect(addMessageToConversation).toHaveBeenCalledWith(
        'conv-handoff',
        expect.objectContaining({ id, content: 'only once' })
      );
    });

    it('leaves nothing to retry when the send fails, since the turn is already in the transcript', async () => {
      postChatMessageStream.mockRejectedValueOnce(new Error('boom'));
      setActiveConversation('conv-h4');
      enqueue({ content: 'will fail', source: 'text', timestamp: 1 });

      await drainQueueIfReady();

      // The user keeps what they said, and the queue does not hold a copy that
      // would resend on the next utterance.
      expect(addMessageToConversation).toHaveBeenCalledTimes(1);
      expect(getQueue()).toHaveLength(0);
    });

    it('stands the auto-drain down after a failure so it cannot hot-loop (regression)', async () => {
      postChatMessageStream.mockRejectedValue(new Error('backend down'));
      setActiveConversation('conv-loop');
      enqueue({ content: 'retry me', source: 'text', timestamp: 1 });

      await drainQueueIfReady();
      expect(isDrainBlocked()).toBe(true);

      // Subsequent automatic attempts are refused, even though the agent is idle
      // and the queue is still populated.
      await drainQueueIfReady();
      await drainQueueIfReady();
      expect(postChatMessageStream).toHaveBeenCalledTimes(1);
    });

    it('an explicit triggerDrain clears the block and sends the next thing enqueued', async () => {
      // The failed message left the queue when the request went out, so clearing
      // the block does not resurrect it -- it just resumes sending from the next
      // message. Resending on an explicit trigger would put the user's own turn
      // in the transcript twice.
      postChatMessageStream.mockRejectedValueOnce(new Error('transient'));
      setActiveConversation('conv-retry');
      enqueue({ content: 'lost to failure', source: 'text', timestamp: 1 });

      await drainQueueIfReady();
      expect(isDrainBlocked()).toBe(true);

      enqueue({ content: 'next thing', source: 'text', timestamp: 2 });
      await triggerDrain();

      expect(isDrainBlocked()).toBe(false);
      expect(postChatMessageStream).toHaveBeenCalledTimes(2);
      expect(postChatMessageStream.mock.calls[1][0]).toBe('next thing');
    });

    it('enqueueing a new message clears the block', async () => {
      postChatMessageStream.mockRejectedValueOnce(new Error('transient'));
      setActiveConversation('conv-new');
      enqueue({ content: 'first', source: 'text', timestamp: 1 });
      await drainQueueIfReady();
      expect(isDrainBlocked()).toBe(true);

      enqueue({ content: 'second', source: 'text', timestamp: 2 });
      expect(isDrainBlocked()).toBe(false);
    });
  });

  describe('flags pass-through', () => {
    it('forwards searchConsent from the queued message', async () => {
      setActiveConversation('conv-f');
      enqueue({ content: 'consented', source: 'text', timestamp: 1, searchConsent: true });

      await triggerDrain();

      expect(postChatMessageStream.mock.calls[0][3]).toBe(true);
    });

    it('forwards maxIntelligence from the queued message', async () => {
      setActiveConversation('conv-f');
      enqueue({ content: 'think hard', source: 'text', timestamp: 1, maxIntelligence: true });

      await triggerDrain();

      expect(postChatMessageStream.mock.calls[0][4]).toBe(true);
    });
  });

  describe('search consent', () => {
    it('notifies the UI with the search info', async () => {
      const handler = vi.fn();
      const off = onSearchConsentRequired(handler);
      postChatMessageStream.mockResolvedValueOnce({
        response: '',
        task_type: 'search_consent_required',
        search_info: { query: 'private thing' },
      } as never);

      setActiveConversation('conv-c');
      enqueue({ content: 'look this up', source: 'text', timestamp: 1 });

      await triggerDrain();

      expect(handler).toHaveBeenCalledTimes(1);
      expect(handler.mock.calls[0][0].searchInfo).toEqual({ query: 'private thing' });
      expect(handler.mock.calls[0][0].content).toBe('look this up');
      off();
    });

    it('does not drain messages that arrive while consent is pending', async () => {
      const handler = vi.fn();
      const off = onSearchConsentRequired(handler);
      // A follow-up arrives mid-send, while the agent is blocked on consent.
      postChatMessageStream.mockImplementationOnce(async () => {
        enqueue({ content: 'typed during consent', source: 'text', timestamp: 2 });
        return {
          response: '',
          task_type: 'search_consent_required',
          search_info: { query: 'private thing' },
        } as never;
      });

      setActiveConversation('conv-c2');
      enqueue({ content: 'look this up', source: 'text', timestamp: 1 });

      await triggerDrain();

      expect(postChatMessageStream).toHaveBeenCalledTimes(1);
      expect(handler).toHaveBeenCalledTimes(1);
      // the mid-send message survives for after the user answers
      expect(getQueue()).toHaveLength(1);
      expect(getQueue()[0].content).toBe('typed during consent');
      off();
    });
  });
});

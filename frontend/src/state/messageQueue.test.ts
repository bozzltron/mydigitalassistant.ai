import { describe, it, expect, vi, beforeEach, afterEach, beforeAll } from 'vitest';
import {
  enqueue,
  dequeue,
  clearQueue,
  drainQueue,
  isProcessing,
  setProcessing,
  getQueue,
  getQueueLength,
  setActiveConversation,
  getActiveConversationId,
  combineQueuedMessages,
  loadFromStorage,
  initQueue,
} from './messageQueue';
import type { AttachedFile } from '../types';

// Mock localStorage
const mockLocalStorage = {
  store: {} as Record<string, string>,
  getItem: vi.fn((key: string) => mockLocalStorage.store[key] || null),
  setItem: vi.fn((key: string, value: string) => { mockLocalStorage.store[key] = value; }),
  removeItem: vi.fn((key: string) => { delete mockLocalStorage.store[key]; }),
  clear: vi.fn(() => { mockLocalStorage.store = {}; }),
};

Object.defineProperty(global, 'localStorage', {
  value: mockLocalStorage,
  writable: true,
});

describe('messageQueue', () => {
  beforeAll(() => {
    initQueue();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockLocalStorage.store = {};
    clearQueue();
    setProcessing(false);
    setActiveConversation(null);
  });

  afterEach(() => {
    clearQueue();
    setProcessing(false);
    setActiveConversation(null);
  });

  describe('enqueue', () => {
    it('adds a message to the queue', () => {
      const id = enqueue({ content: 'Hello', source: 'text', timestamp: Date.now() });
      expect(id).toBeTruthy();
      expect(getQueueLength()).toBe(1);
      expect(getQueue()[0].content).toBe('Hello');
    });

    it('generates unique IDs', () => {
      const id1 = enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      const id2 = enqueue({ content: 'Msg 2', source: 'voice', timestamp: Date.now() });
      expect(id1).not.toBe(id2);
    });

    it('includes attached files', () => {
      const files: AttachedFile[] = [{ name: 'test.txt', ext: 'txt', preview: 'preview', text: 'content' }];
      enqueue({ content: 'With files', source: 'text', timestamp: Date.now(), attachedFiles: files });
      expect(getQueue()[0].attachedFiles).toEqual(files);
    });

    it('persists to localStorage', () => {
      enqueue({ content: 'Persisted', source: 'text', timestamp: Date.now() });
      expect(mockLocalStorage.setItem).toHaveBeenCalledWith('messageQueue', expect.any(String));
    });

    it('enforces MAX_QUEUE_SIZE (50)', () => {
      for (let i = 0; i < 55; i++) {
        enqueue({ content: `Msg ${i}`, source: 'text', timestamp: Date.now() + i });
      }
      expect(getQueueLength()).toBe(50);
      // Should keep the last 50
      expect(getQueue()[0].content).toBe('Msg 5');
      expect(getQueue()[49].content).toBe('Msg 54');
    });
  });

  describe('dequeue', () => {
    it('removes a specific message by ID', () => {
      const id1 = enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      const id2 = enqueue({ content: 'Msg 2', source: 'text', timestamp: Date.now() });
      const id3 = enqueue({ content: 'Msg 3', source: 'text', timestamp: Date.now() });

      dequeue(id2);
      expect(getQueueLength()).toBe(2);
      expect(getQueue().map(m => m.id)).not.toContain(id2);
      expect(getQueue().map(m => m.content)).toEqual(['Msg 1', 'Msg 3']);
    });

    it('handles non-existent ID gracefully', () => {
      enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      dequeue('non-existent-id');
      expect(getQueueLength()).toBe(1);
    });
  });

  describe('clearQueue', () => {
    it('removes all messages', () => {
      enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      enqueue({ content: 'Msg 2', source: 'text', timestamp: Date.now() });
      clearQueue();
      expect(getQueueLength()).toBe(0);
      expect(getQueue()).toEqual([]);
    });

    it('persists empty queue', () => {
      enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      clearQueue();
      expect(mockLocalStorage.setItem).toHaveBeenCalledWith('messageQueue', '[]');
    });
  });

  describe('drainQueue', () => {
    it('returns all messages and clears queue', () => {
      enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      enqueue({ content: 'Msg 2', source: 'voice', timestamp: Date.now() });

      const drained = drainQueue();
      expect(drained).toHaveLength(2);
      expect(drained[0].content).toBe('Msg 1');
      expect(drained[1].content).toBe('Msg 2');
      expect(getQueueLength()).toBe(0);
    });

    it('returns empty array if queue is empty', () => {
      expect(drainQueue()).toEqual([]);
    });
  });

  describe('processing state', () => {
    it('tracks processing state', () => {
      expect(isProcessing()).toBe(false);
      setProcessing(true);
      expect(isProcessing()).toBe(true);
      setProcessing(false);
      expect(isProcessing()).toBe(false);
    });
  });

  describe('active conversation', () => {
    it('tracks active conversation ID', () => {
      expect(getActiveConversationId()).toBeNull();
      setActiveConversation('conv-123');
      expect(getActiveConversationId()).toBe('conv-123');
    });

    it('clears queue and processing when set to null', () => {
      enqueue({ content: 'Msg 1', source: 'text', timestamp: Date.now() });
      setProcessing(true);
      setActiveConversation('conv-123');

      setActiveConversation(null);
      expect(getQueueLength()).toBe(0);
      expect(isProcessing()).toBe(false);
      expect(getActiveConversationId()).toBeNull();
    });
  });

  describe('combineQueuedMessages', () => {
    it('returns single message as-is', () => {
      const messages = [{ id: '1', content: 'Hello', source: 'text' as const, timestamp: 1 }];
      expect(combineQueuedMessages(messages)).toBe('Hello');
    });

    it('joins multiple messages with separator', () => {
      const messages = [
        { id: '1', content: 'Hello', source: 'text' as const, timestamp: 1 },
        { id: '2', content: 'World', source: 'voice' as const, timestamp: 2 },
        { id: '3', content: '!', source: 'text' as const, timestamp: 3 },
      ];
      const combined = combineQueuedMessages(messages);
      expect(combined).toContain('The user sent 3 messages while you were processing. Address each:');
      expect(combined).toContain('Hello');
      expect(combined).toContain('---');
      expect(combined).toContain('World');
      expect(combined).toContain('!');
    });

    it('returns empty string for empty array', () => {
      expect(combineQueuedMessages([])).toBe('');
    });
  });

  describe('persistence', () => {
    it('loads from localStorage on init', () => {
      const savedQueue = [
        { id: '1', content: 'Saved', source: 'text', timestamp: 1000 },
      ];
      mockLocalStorage.store.messageQueue = JSON.stringify(savedQueue);

      const loaded = loadFromStorage();
      expect(loaded).toEqual(savedQueue);
    });

    it('ignores corrupted localStorage data', () => {
      mockLocalStorage.store.messageQueue = 'not valid json';
      const loaded = loadFromStorage();
      expect(loaded).toEqual([]);
    });

    it('ignores invalid structure in localStorage', () => {
      mockLocalStorage.store.messageQueue = JSON.stringify([{ not: 'valid' }]);
      const loaded = loadFromStorage();
      expect(loaded).toEqual([]);
    });
  });
});
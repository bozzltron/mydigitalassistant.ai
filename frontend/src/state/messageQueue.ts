import { createStore } from 'solid-js/store';
import type { AttachedFile } from '../types';

// ============================================================================
// Types
// ============================================================================

export interface QueuedMessage {
  id: string;
  content: string;
  source: 'voice' | 'text';
  timestamp: number;
  attachedFiles?: AttachedFile[];
  /** Carried through to the drainer so a consent retry keeps its flag. */
  searchConsent?: boolean;
  /** Snapshot of the user's toggle at enqueue time. */
  maxIntelligence?: boolean;
  /**
   * A consent retry re-sends a message that is already in history, so the
   * drainer must not append it to the conversation a second time.
   */
  skipHistory?: boolean;
}

interface QueueState {
  queue: QueuedMessage[];
  processing: boolean;
  activeConversationId: string | null;
  /**
   * Set when a drain attempt failed. The auto-drain effect must not retry a
   * failing queue in a hot loop; it is cleared when the user enqueues something
   * new (new intent) or explicitly nudges the drainer.
   */
  drainBlocked: boolean;
}

const STORAGE_KEY = 'messageQueue';
const MAX_QUEUE_SIZE = 10;

// ============================================================================
// Lazy Store Initialization
// ============================================================================

let _queueState: QueueState | null = null;
let _setQueueState: ((patch: Partial<QueueState> | ((prev: QueueState) => Partial<QueueState>)) => void) | null = null;

function getStore(): [QueueState, (patch: Partial<QueueState> | ((prev: QueueState) => Partial<QueueState>)) => void] {
  if (!_queueState || !_setQueueState) {
    const [state, setState] = createStore<QueueState>({
      queue: [],
      processing: false,
      activeConversationId: null,
      drainBlocked: false,
    });
    // eslint-disable-next-line solid/reactivity -- state assigned to module-level ref
    _queueState = state;
    _setQueueState = setState;
  }
  return [_queueState, _setQueueState!];
}

// Export reactive accessors
export function queueState(): QueueState {
  return getStore()[0];
}

export function setQueueState(patch: Partial<QueueState> | ((prev: QueueState) => Partial<QueueState>)): void {
  const [, setState] = getStore();
  // setState accepts Partial<QueueState> or a function that receives QueueState and returns Partial<QueueState>
  setState(patch as Parameters<typeof setState>[0]);
}

// ============================================================================
// Persistence
// ============================================================================

export function loadFromStorage(): QueuedMessage[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return [];
    const parsed = JSON.parse(stored);
    if (!Array.isArray(parsed)) return [];
    // Validate structure
    return parsed.filter(
      (m): m is QueuedMessage =>
        typeof m === 'object' &&
        m !== null &&
        typeof m.id === 'string' &&
        typeof m.content === 'string' &&
        (m.source === 'voice' || m.source === 'text') &&
        typeof m.timestamp === 'number'
    );
  } catch {
    return [];
  }
}

function saveToStorage(queue: QueuedMessage[]): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(queue));
  } catch {
    // Ignore quota errors
  }
}

// Initialize from localStorage on module load - called lazily
let _initialized = false;

export function initQueue(): void {
  if (_initialized || typeof window === 'undefined') return;
  _initialized = true;
  const saved = loadFromStorage();
  if (saved.length > 0) {
    setQueueState({ queue: saved });
  }
}

// ============================================================================
// Pure Actions (testable, no side effects beyond store)
// ============================================================================

function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function enqueue(message: Omit<QueuedMessage, 'id'>): string {
  const id = generateId();
  const newMessage: QueuedMessage = { ...message, id };

  setQueueState((prev) => {
    const next = [...prev.queue, newMessage];
    // Enforce max size (FIFO eviction)
    if (next.length > MAX_QUEUE_SIZE) {
      return { queue: next.slice(-MAX_QUEUE_SIZE), drainBlocked: false };
    }
    // A new message is fresh user intent, so unblock a previously failed drain.
    return { queue: next, drainBlocked: false };
  });

  // Persist the post-update queue. Do NOT append newMessage again here:
  // setQueueState above already applied synchronously, so re-appending
  // persisted every message twice and duplicated it on reload.
  saveToStorage(queueState().queue);

  return id;
}

export function dequeue(messageId: string): void {
  setQueueState((prev) => ({
    queue: prev.queue.filter((m) => m.id !== messageId),
  }));
  saveToStorage(queueState().queue.filter((m) => m.id !== messageId));
}

export function clearQueue(): void {
  setQueueState({ queue: [] });
  saveToStorage([]);
}

export function drainQueue(): QueuedMessage[] {
  const messages = [...queueState().queue];
  setQueueState({ queue: [] });
  saveToStorage([]);
  return messages;
}

export function isProcessing(): boolean {
  return queueState().processing;
}

/** True when a drain failed and auto-drain should stand down until new input. */
export function isDrainBlocked(): boolean {
  return queueState().drainBlocked;
}

export function setDrainBlocked(value: boolean): void {
  setQueueState({ drainBlocked: value });
}

export function setProcessing(value: boolean): void {
  setQueueState({ processing: value });
}

export function getQueue(): QueuedMessage[] {
  return [...queueState().queue];
}

export function getQueueLength(): number {
  return queueState().queue.length;
}

export function setActiveConversation(conversationId: string | null): void {
  setQueueState({ activeConversationId: conversationId });
  if (conversationId === null) {
    // Clear queue when no active conversation
    clearQueue();
    setQueueState({ processing: false });
  }
}

export function getActiveConversationId(): string | null {
  return queueState().activeConversationId;
}

// ============================================================================
// Reactive Helpers (for UI)
// ============================================================================

// These are getter functions for components to use in createMemo/createEffect
export const queueLength = () => queueState().queue.length;
export const isQueueProcessing = () => queueState().processing;

// ============================================================================
// Utility: Combine queued messages for LLM
// ============================================================================

export function combineQueuedMessages(messages: QueuedMessage[]): string {
  if (messages.length === 0) return '';
  if (messages.length === 1) return messages[0].content;

  const separator = '\n\n---\n\n';
  const combined = messages.map((m) => m.content).join(separator);
  return `The user sent ${messages.length} messages while you were processing. Address each:\n\n${combined}`;
}
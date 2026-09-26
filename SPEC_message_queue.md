# Message Queue System — Design Spec (MVP: Single Conversation)

> ## ⚠️ STATUS: HISTORICAL — the code has moved on
>
> This is the original design spec, kept for the reasoning behind the queue. **It is
> not a description of the current code.** Where the two disagree, the code wins.
>
> Known drift, as of 2026-09-25:
>
> | This spec says | Reality |
> |---|---|
> | `TranscriptionPipeline` in `src/services/transcriptionPipeline.ts` (Phase 1, §B) | **Deleted.** 0 callers. The hook enqueues directly. |
> | `drainQueue()` dequeues **after** the send (§C, `messages.forEach(m => dequeue(m.id))`) | **Reversed.** Dequeue happens *with* the history commit, as the request goes out. Dequeuing after left every message rendered twice for the whole request. |
> | `combineQueuedMessages()` wraps N messages in one prompt | Still true, and now a known problem: the backend logs the user episode as `request.message` (`orchestrator.py`), so the *wrapper* is what gets stored as episodic memory. See the note below. |
> | `QueueState` has 3 fields | 4. `drainBlocked` was added (a debounce, not loop protection). |
> | `export const [queueState, setQueueState] = createStore(...)` | A lazy module-level singleton with an `eslint-disable solid/reactivity`. |
> | "Max queue size: limit to 50?" (Open Question 2) | 10, with **silent** FIFO eviction — no warning to the user. |
> | All Phase 1–5 checkboxes unchecked | The work is done. |
>
> **The current flow is documented in `docs/CONVERSATION_MODE_FLOW.md`** and enforced
> by `queueDrainer.test.ts`. Update this file or delete it — do not leave unchecked
> boxes next to shipped code.

## Overview

Redesign the conversation flow to support a robust message queue for the **active conversation** that handles simultaneous voice transcription and chat processing.

## Requirements (MVP)

### Core Flow
1. **Voice mode ON** → all user speech transcribed automatically
2. **First transcription** → starts processing immediately
3. **Subsequent transcriptions** (while processing) → queue up
4. **Typed messages** (while processing) → queue up
5. **Each queued message** → deletable by user
6. **Agent ready** → grabs ALL queued messages for active conversation, processes as single turn

### Non-Goals (Future)
- Cross-conversation queues (user switches conversations, queues persist)
- Voice mode persists across conversation switches
- Sidebar queue badges for inactive conversations

### Concurrency
- **Transcription** and **chat processing** must run simultaneously
- Voice mode continues listening/transcribing while agent thinks
- No blocking: transcription pipeline independent from chat pipeline

---

## Architecture (MVP: Single Conversation)

### 1. Dual Pipeline Design

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER INPUT                                │
│  ┌─────────────────┐         ┌─────────────────┐               │
│  │   VOICE MODE    │         │  TEXT INPUT     │               │
│  │  (continuous)   │         │  (manual send)  │               │
│  └────────┬────────┘         └────────┬────────┘               │
│           │                           │                         │
│           ▼                           ▼                         │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              TRANSCRIPTION PIPELINE                      │   │
│  │  • Audio capture → Whisper → text                        │   │
│  │  • Runs INDEPENDENTLY of chat processing                 │   │
│  │  • Outputs: transcribed text                             │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              MESSAGE QUEUE (active conversation)         │   │
│  │  • Queue<TranscribedMessage | TypedMessage>              │   │
│  │  • Each entry: { id, content, source: 'voice'|'text',   │   │
│  │       timestamp, deletable: true, attachedFiles? }       │   │
│  │  • Persisted to localStorage (survives refresh)          │   │
│  └────────────────────────┬────────────────────────────────┘   │
│                           │                                      │
│                           ▼                                      │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              CHAT PROCESSING PIPELINE                    │   │
│  │  • One active turn (global)                              │   │
│  │  • Drains ENTIRE queue as ONE turn                       │   │
│  │  • Streams response via SSE                              │   │
│  │  • On complete: marks turn done, triggers next drain     │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**Note:** Queue is tied to active conversation. Switching conversations clears queue (or pauses voice mode).

### 2. Key Components (MVP)

#### A. `MessageQueue` (shared state module)
```typescript
// src/state/messageQueue.ts
interface QueuedMessage {
  id: string;
  content: string;
  source: 'voice' | 'text';
  timestamp: number;
  attachedFiles?: AttachedFile[];
}

interface QueueState {
  queue: QueuedMessage[];        // Single queue for active conversation
  processing: boolean;           // Is chat processing?
  activeConversationId: string | null;
}

export const [queueState, setQueueState] = createStore<QueueState>({
  queue: [],
  processing: false,
  activeConversationId: null,
});

// Actions
export function enqueue(message: Omit<QueuedMessage, 'id'>): string;
export function dequeue(messageId: string): void;
export function drainQueue(): QueuedMessage[];
export function isProcessing(): boolean;
export function setProcessing(value: boolean): void;
export function getQueue(): QueuedMessage[];
export function setActiveConversation(conversationId: string | null): void;
export function clearQueue(): void;
```

#### B. `TranscriptionPipeline` (independent)
```typescript
// src/services/transcriptionPipeline.ts
class TranscriptionPipeline {
  private isRunning = false;
  private currentConversationId: string | null = null;
  
  async start(conversationId: string): Promise<void>;
  async stop(): Promise<void>;
  // Internally uses useVoiceRecording but outputs to MessageQueue
  // NOT to handleSendMessage directly
}
```

#### C. `QueueDrainer` (single conversation)
```typescript
// src/services/queueDrainer.ts
async function drainQueue(): Promise<void> {
  const messages = getQueue();
  if (messages.length === 0) return;
  
  setProcessing(true);
  
  // Combine all queued messages into single prompt
  const combinedPrompt = combineQueuedMessages(messages);
  
  // Single chat turn with combined context
  const conversationId = queueState.activeConversationId;
  if (!conversationId) { setProcessing(false); return; }
  
  const result = await sendMessageStream(combinedPrompt, conversationId, ...);
  
  // Clear processed messages from queue
  messages.forEach(m => dequeue(m.id));
  
  setProcessing(false);
  
  // Auto-drain if more arrived during processing
  if (getQueue().length > 0) {
    drainQueue();
  }
}

function combineQueuedMessages(messages: QueuedMessage[]): string {
  if (messages.length === 1) return messages[0].content;
  const separator = '\n\n---\n\n';
  const combined = messages.map(m => m.content).join(separator);
  return `The user sent ${messages.length} messages while you were processing. Address each:\n\n${combined}`;
}
```

---

## Data Flow (MVP)

### Voice Mode ON
1. User enables voice mode for active conversation
2. `TranscriptionPipeline.start()` begins listening
3. Each transcription → `enqueue({ content: text, source: 'voice' })`
4. If `!isProcessing()` → `drainQueue()`
5. If `isProcessing()` → message stays in queue, UI shows queued badge

### Text Input During Processing
1. User types in input box, hits Enter
2. `handleSendMessage` checks `isProcessing()`
3. If processing → `enqueue({ content: text, source: 'text', attachedFiles })`
4. If not processing → normal immediate send (existing behavior)

### Conversation Switch
1. User switches conversation
2. `setActiveConversation(newId)` → clears queue, stops voice mode
3. New conversation starts fresh

### Queue UI
- Active conversation shows queued message count badge
- Click badge → expands to show queued messages with delete buttons
- Delete removes single message from queue
- "Clear queue" button removes all

---

## Concurrency Model (MVP)

| Scenario | Transcription | Chat Processing | Queue |
|----------|---------------|-----------------|-------|
| Voice ON, idle | ✅ Running | ❌ Idle | Drains immediately |
| Voice ON, processing | ✅ Running | ✅ Running | Buffers |
| Voice OFF, idle | ❌ Stopped | ❌ Idle | N/A |
| Voice OFF, processing | ❌ Stopped | ✅ Running | Buffers text only |

**Key invariant:** Transcription pipeline runs independently. Chat processing runs independently. They only meet at the `MessageQueue`.

---

## Implementation Plan (aligned with AGENTS.md)

### Phase 1: Core Queue Infrastructure
- [ ] `src/state/messageQueue.ts` — shared queue state + persistence (pure functions, testable)
- [ ] `src/services/transcriptionPipeline.ts` — decoupled from chat (single responsibility)
- [ ] `src/services/queueDrainer.ts` — drain logic (async, non-blocking)
- [ ] Unit tests for queue operations (every module must have tests)

### Phase 2: Voice Mode Integration
- [ ] Refactor `useVoiceRecording` to output to `MessageQueue` instead of `handleSendMessage`
- [ ] `TranscriptionPipeline` manages voice mode lifecycle
- [ ] **Regression test**: Voice recording flow (silence detection → stopRecording → transcription → queue)

### Phase 3: Chat Integration
- [ ] Modify `handleSendMessage` to check queue + enqueue if processing
- [ ] `postChatMessageStream` accepts pre-combined prompt (or we combine in drainer)
- [ ] Auto-drain on turn completion
- [ ] **No templated responses**: Let model handle combined prompt naturally

### Phase 4: UI
- [ ] Queue badge for active conversation (CSS modules, design tokens from variables.css)
- [ ] Queue panel (expandable) showing pending messages with delete (SVG icons only)
- [ ] Visual indicator: "Processing X messages..." during drain (transitions 150-300ms)

### Phase 5: Persistence & Polish
- [ ] localStorage persistence for queue
- [ ] Restore queue on app init
- [ ] Edge case handling (network failure during drain, etc.)

---

## AGENTS.md Compliance Checklist

| Principle | Application |
|-----------|-------------|
| **Clean ship** | New modules only; verify zero callers before removing old `conversationQueues` |
| **Speed-first UI** | Queue operations < 10ms; visual feedback immediate; drain async |
| **No templated responses** | Model receives combined prompt, generates natural response |
| **Lean on model** | No hardcoded "I'll handle your queued messages" — model decides |
| **Stability: no regressions** | Regression tests for voice flow, queue drain |
| **Safety & Privacy** | All transcription local (Whisper); no external calls without consent |
| **Clean Code** | Shared utilities in `src/shared/`; pure functions; ES modules |

---

## Open Questions (MVP)

1. **Combined prompt format**: Option A (separator) recommended — see analysis below

2. **Max queue size**: Limit to 50 messages to prevent memory issues?

3. **Voice mode on conversation switch**: Stop voice mode and clear queue? (Simplest for MVP)

4. **Backend changes needed?** 
   - Current `/chat/stream` handles single message
   - Drainer combines queue → single prompt → single backend call
   - No backend changes needed

5. **Transcription failures**: Retry? Queue failed transcriptions with error flag?

6. **Queue persistence key**: `messageQueue_${conversationId}` or single `messageQueue`?

---

## Migration Notes

### Breaking Changes
- `useVoiceRecording.onTranscription` callback signature changes
- `handleSendMessage` no longer called directly from voice
- Queue state replaces `conversationQueues` in `chat.ts`

### Backward Compatibility
- Non-voice conversations work identically
- Single message send (no queue) unchanged
- Existing `conversationQueues` Map → migrate to `MessageQueue` (single queue)

---

## Testing Strategy

### Unit Tests
- `messageQueue.ts`: enqueue, dequeue, drain, clear, persistence
- `transcriptionPipeline.ts`: start/stop, transcription → enqueue
- `queueDrainer.ts`: drain logic, auto-re-drain, error handling, combine prompt

### Integration Tests
- Voice ON → speak → immediate processing
- Voice ON → speak during processing → queues → auto-drains after
- Text send during processing → queues
- Delete queued message → removed from drain
- Page refresh → queue restored
- Conversation switch → queue cleared, voice stopped

### E2E Tests
- Full voice conversation with multiple queued utterances

---

## Estimated Effort (MVP)

| Phase | Files | Est. Days |
|-------|-------|-----------|
| 1. Core Queue | 3 new + 1 test | 1-2 |
| 2. Voice Integration | 2 modified + 1 new | 1-2 |
| 3. Chat Integration | 2 modified | 1 |
| 4. UI | 2-3 new/modified | 1-2 |
| 5. Persistence/Polish | 2 modified | 0.5-1 |
| **Total** | ~10 files | **4-7 days** |

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Transcription + processing race conditions | Medium | High | Strict separation via MessageQueue; no shared mutable state |
| Queue memory growth | Low | Medium | Max queue size (e.g., 50), persist to localStorage |
| Voice mode stuck ON | Medium | High | Explicit stop on conversation delete, tab close, logout |
| Combined prompt too long | Medium | Medium | Truncate/summarize old queued messages; token count check |
| Backend timeout on large combined prompt | Low | High | Chunk large queues into multiple turns; stream each |
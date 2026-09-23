# Phase 5: Frontend SSE Consumer (SolidJS)

## Overview
Integrate the backend `/chat/stream` SSE endpoint into the SolidJS frontend, replacing the current blocking `/chat` fetch with real-time streaming.

## Backend Events (SSE Format)

The backend `/chat/stream` endpoint yields these event types:
```javascript
// Text token streaming
{ type: 'text_delta', delta: 'token text' }

// Tool call requested by model
{ type: 'tool_call', tool_calls: [{ name: 'read_file', arguments: {...} }] }

// Tool execution result
{ type: 'tool_result', tool_name: 'read_file', success: true, data: {...}, error: '' }

// Completion
{ type: 'finalize', answer: 'Full response', reasoning_trace: '...' }

// Error
{ type: 'error', error: 'Error message' }
```

## Current Frontend Architecture (to be modified)

| Component | Current | Target |
|-----------|---------|--------|
| `api.ts` | `postChat()` → `fetch('/chat')` | Add `postChatStream()` → `EventSource('/chat/stream')` |
| `chat.ts` | `postChatMessage()` waits for full response | Incremental message building via streaming |
| `chat.ts` state | `messages` = complete messages | Support `isStreaming` + partial content |
| `ChatPage.tsx` | `handleSendMessage()` awaits full response | Stream tokens into assistant message |
| `Message.tsx` | Renders complete messages | Support streaming state + tool indicators |
| `status.ts` | Polls `/chat/status/{turnId}` | Replace with SSE events (or keep as fallback) |

## SolidJS Best Practices (from AGENTS.md)

### CSS/Styling
- **No inline styles** - Use CSS modules (`.module.css`) + design tokens from `variables.css`
- **States via classes** - `.is-streaming`, `.is-tool-calling`, `.is-complete`
- **Shared base classes** in `components.css` - extend rather than redefine

### Reactivity
- **Signals for streaming state** - `createSignal` for `isStreaming`, `streamingContent`
- **Stores for message collections** - `createStore` for conversation messages
- **Memos for derived state** - `createMemo` for filtered/transformed data
- **No `useRef`** - Use callback refs: `<div ref={(el) => { myEl = el }} />`
- **onMount/onCleanup** for subscriptions (EventSource)

### Component Structure
- **ES Modules** - Native `<script type="module">` (Vite handles this)
- **CSS Modules** - Co-located `Component.module.css` per component
- **Shared utilities first** - Check `shared/` before duplicating logic

## Implementation Plan

### 5.1: API Layer - SSE Client
**File**: `frontend/src/services/api.ts` (additions)

```typescript
// SSE event types
export interface StreamEvent {
  type: 'text_delta' | 'tool_call' | 'tool_result' | 'finalize' | 'error';
  delta?: string;
  tool_calls?: Array<{ name: string; arguments: Record<string, unknown> }>;
  tool_name?: string;
  success?: boolean;
  data?: Record<string, unknown>;
  error?: string;
  answer?: string;
  reasoning_trace?: string;
}

// Streaming chat function
export function postChatStream(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
  turn_id?: string,
  search_consent?: boolean,
  onEvent: (event: StreamEvent) => void,
  onError: (error: Error) => void,
  onClose: () => void
): () => void; // Returns cleanup function
```

**Implementation - POST + ReadableStream (Required for SSE with body)**:
```typescript
export function postChatStream(
  message: string,
  session_id?: string,
  attached_files?: AttachedFile[],
  turn_id?: string,
  search_consent?: boolean,
  onEvent: (event: StreamEvent) => void,
  onError: (error: Error) => void,
  onClose: () => void
): () => void {
  const requestBody = {
    user_id: 1,
    message,
    session_id,
    attached_files,
    turn_id,
    search_consent,
  };

  const controller = new AbortController();
  const decoder = new TextDecoder();
  let buffer = '';

  fetch(`${BASE_URL}/chat/stream`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestBody),
    credentials: 'include',
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(error.detail || `HTTP ${response.status}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error('No response body');

      try {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          
          // Parse SSE format: data: {...}\n\n
          const lines = buffer.split('\n\n');
          buffer = lines.pop() || ''; // Keep incomplete line in buffer
          
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const event = JSON.parse(line.slice(6)) as StreamEvent;
                onEvent(event);
              } catch (e) {
                console.warn('Failed to parse SSE event:', line, e);
              }
            }
          }
        }
      } finally {
        reader.releaseLock();
        onClose();
      }
    })
    .catch((error) => {
      if (error.name !== 'AbortError') {
        onError(error);
      }
      onClose();
    });

  // Return cleanup function
  return () => controller.abort();
}
```

**Key Implementation Details**:
- Use `fetch` + `ReadableStream` (not `EventSource`) because we need POST with JSON body
- Buffer handling for partial chunks across reads
- SSE format: each event is `data: {json}\n\n` (double newline separated)
- AbortController for cleanup on unmount/cancel
- Credentials: 'include' for session cookies

### 5.1.1: Retry Logic (Optional Enhancement)
```typescript
export function postChatStreamWithRetry(
  ...args: Parameters<typeof postChatStream>
): () => void {
  const maxRetries = 3;
  let attempt = 0;
  let cleanup: (() => void) | null = null;

  const attemptStream = () => {
    cleanup = postChatStream(
      ...args.slice(0, -2),
      args[args.length - 2], // onError
      (error: Error) => {
        if (attempt < maxRetries && isRetryableError(error)) {
          attempt++;
          console.log(`Stream retry ${attempt}/${maxRetries}:`, error.message);
          setTimeout(attemptStream, 1000 * attempt);
        } else {
          args[args.length - 2](error); // Final error
        }
      },
      () => { /* onClose */ }
    );
  };

  attemptStream();
  return () => cleanup?.();
}

function isRetryableError(error: Error): boolean {
  return error.message.includes('network') || 
         error.message.includes('timeout') ||
         error.message.includes('503') ||
         error.message.includes('504');
}
```

### 5.2: State - Streaming Message Building
**File**: `frontend/src/state/chat.ts`

**New types**:
```typescript
interface StreamingMessage extends ChatMessage {
  isStreaming: boolean;
  toolCalls?: Array<{ name: string; arguments: unknown }>;
  currentTool?: string;
}
```

**Changes to `chat.ts`**:
- Add `streamingMessage` signal for current assistant message being built
- `postChatMessage()` → async generator or callback-based streaming
- `appendToken(delta: string)` - append to streaming message content
- `setToolCall(toolCalls)` - show tool spinner
- `setToolResult(toolName, result)` - update tool status
- `finalizeMessage(answer, meta)` - complete streaming message, add to history

### 5.3: ChatPage - Streaming Integration
**File**: `frontend/src/components/chat/ChatPage.tsx`

**Changes**:
- Replace `handleSendMessage` blocking call with streaming version
- Create assistant message with `isStreaming: true` immediately
- Handle SSE events:
  - `text_delta` → `appendToken(delta)`
  - `tool_call` → `setToolCall(toolCalls)`
  - `tool_result` → `setToolResult(toolName, result)`
  - `finalize` → `finalizeMessage(answer, meta)`
  - `error` → show error, clean up
- Remove status polling (SSE provides stages) or keep as fallback

### 5.4: Message Component - Streaming UI
**File**: `frontend/src/components/chat/Message.tsx` + `Message.module.css`

**New CSS classes** (in `Message.module.css`):
```css
.msg-streaming { /* pulsing cursor, partial opacity */ }
.msg-tool-calling { /* spinner overlay */ }
.msg-complete { /* final state */ }
```

**Changes to `Message.tsx`**:
- Accept `isStreaming` prop from message meta
- Show streaming cursor while `isStreaming`
- Show tool call indicator when `toolCalls` present
- Smooth height transition for auto-expanding content (CSS transition)

### 5.5: Status Indicator - SSE Stage Events
**File**: `frontend/src/components/chat/StatusIndicator.tsx`

**Changes**:
- Accept stage events from SSE (`routing`, `recall`, `searching`, `reasoning`, `responding`)
- Replace polling with direct SSE event handling
- Keep polling as fallback for non-streaming requests

## File Changes Summary

| File | Change Type | Description |
|------|-------------|-------------|
| `api.ts` | Add | `postChatStream()` with EventSource/fetch streaming |
| `chat.ts` | Modify | Streaming message state, incremental building |
| `ChatPage.tsx` | Modify | Streaming `handleSendMessage`, event handlers |
| `Message.tsx` | Modify | Streaming state rendering, tool indicators |
| `Message.module.css` | Add | Streaming/tool/complete state styles |
| `StatusIndicator.tsx` | Modify | Consume SSE stage events |
| `types/chat.ts` | Add | `StreamEvent`, `StreamingMessage` types |

## Design Principle Alignment

| Principle | Application |
|-----------|-------------|
| **Model-first correction** | Streaming errors → fallback to `/chat` endpoint |
| **No templated responses** | Model tokens stream directly, no hardcoded templates |
| **Lean on model flexibility** | Model determines tool usage, frontend just renders |
| **Clean ship** | Reuse existing `StatusIndicator`, `MessageContent` components |
| **Stability: no regressions** | Keep `/chat` endpoint as fallback; add regression test for streaming |
| **Speed-first UI** | First token <100ms perceived; CSS transitions 150-300ms |

## Regression Tests Required

1. **Streaming happy path**: Send message → receive tokens → finalize → message in history
2. **Tool call streaming**: `tool_call` → spinner → `tool_result` → continue streaming
3. **Error handling**: SSE error → show error message → fallback to `/chat`
4. **Connection loss**: Network error mid-stream → retry or fallback
5. **Search consent**: SSE `finalize` with `task_type: search_consent_required` → modal
6. **Queue integration**: Queued messages wait for current stream to complete

## Dependencies
- None (native `EventSource` / `fetch` + `ReadableStream`)
- Zod schemas already in `api.ts` for validation

## Testing Strategy
- Unit: `api.ts` streaming parser, `chat.ts` message building
- Integration: `ChatPage` streaming flow (mock EventSource)
- E2E: Full conversation with streaming (Playwright)

## 5.6: Accessibility Requirements

### ARIA Live Regions
- Streaming content container: `aria-live="polite" aria-atomic="false"` for token streaming
- Tool call status: `aria-live="assertive"` for tool start/complete announcements
- Error messages: `role="alert"` for immediate screen reader notification

### Keyboard Navigation
- Streaming messages must be navigable (not removed from tab order while streaming)
- `Esc` key cancels active stream (calls cleanup function)
- Tool call indicators: focusable with descriptive labels

### Screen Reader Support
```tsx
// Streaming message example
<div class="msg msg-assistant msg-streaming" aria-live="polite" aria-busy="true">
  <div class="content">{streamingContent}</div>
  <span class="sr-only">Assistant is responding...</span>
</div>

// Tool call indicator
<div class="tool-indicator" aria-live="assertive" aria-label={`Calling tool: ${toolName}`}>
  <Spinner /> <span>{toolName}</span>
</div>
```

### Reduced Motion
- Respect `prefers-reduced-motion` for streaming cursor pulse
- Disable spinner animation when reduced motion preferred

## 5.7: CSS Design Tokens (from `variables.css`)

### Streaming States
```css
/* Message.module.css */
.msg-streaming {
  opacity: var(--opacity-streaming, 0.85);
}
.msg-streaming::after {
  content: '';
  display: inline-block;
  width: 1ch;
  height: 1.2em;
  background: currentColor;
  animation: pulse var(--duration-pulse, 1s) infinite;
  vertical-align: text-bottom;
  margin-left: 2px;
}

.msg-tool-calling {
  position: relative;
}
.msg-tool-calling::before {
  content: '';
  position: absolute;
  inset: 0;
  background: var(--color-surface-overlay, rgba(0,0,0,0.05));
  border-radius: var(--radius-md);
  z-index: 1;
}

.msg-complete {
  opacity: 1;
}
.msg-complete::after {
  display: none;
}

/* Spinner using design tokens */
.spinner {
  width: var(--size-spinner, 16px);
  height: var(--size-spinner, 16px);
  border: 2px solid var(--color-border);
  border-top-color: var(--color-primary);
  border-radius: 50%;
  animation: spin var(--duration-spin, 0.8s) linear infinite;
}

@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}
@keyframes spin { to { transform: rotate(360deg); } }

/* Reduced motion */
@media (prefers-reduced-motion: reduce) {
  .msg-streaming::after { animation: none; opacity: 1; }
  .spinner { animation: none; }
}
```

### Design Tokens to Use (from `variables.css`)
```css
/* Colors */
--color-primary: #3b82f6;
--color-surface-overlay: rgba(0, 0, 0, 0.05);
--color-border: #e5e7eb;
--color-text-muted: #6b7280;

/* Spacing */
--space-xs: 4px;
--space-sm: 8px;
--space-md: 16px;

/* Radius */
--radius-sm: 4px;
--radius-md: 8px;

/* Transitions */
--transition-fast: 150ms ease;
--transition-normal: 250ms ease;

/* Durations */
--duration-pulse: 1s;
--duration-spin: 0.8s;
```

## 5.8: Integration with Existing Systems

### Voice Mode Integration
- Voice transcription → `handleSendMessage()` → streaming response
- TTS: Wait for `finalize` event before speaking (don't interrupt streaming)
- Voice mode state (`voice.status`) checked before starting stream

### Queue System (`queue.ts`)
- Queued messages wait for `isTurnActive` → false (stream complete)
- If stream errors, queued messages retry via existing `drainQueue()`
- Turn ID propagation: each stream gets unique `turn_id` for status tracking

### Status Polling Migration
```typescript
// In status.ts - add SSE stage event support
export interface StreamStageEvent {
  type: 'stage';
  stage: 'routing' | 'recall' | 'learning' | 'searching' | 'reasoning' | 'responding';
  detail: string;
}

// status.ts - useTurnStatus hook enhancement
export function useTurnStatus(turnId?: () => string | undefined) {
  // Existing polling logic...
  
  // New: SSE stage handler
  const handleStageEvent = (event: StreamStageEvent) => {
    entry.status[1]({
      stage: event.stage,
      detail: event.detail,
      elapsed_s: 0, // Not available via SSE
      done: false,
    });
  };
  
  return { turnStatus, isPolling, handleStageEvent };
}
```

### Search Consent Modal
- Backend sends `finalize` with `task_type: "search_consent_required"`
- Frontend shows existing `Modal` component (no new UI needed)
- User clicks "Proceed" → resends with `search_consent: true`

## 5.9: Complete Type Definitions

**File**: `frontend/src/types/chat.ts` (additions)

```typescript
// SSE Event Types
export interface StreamEvent {
  type: 'text_delta' | 'tool_call' | 'tool_result' | 'finalize' | 'error';
  delta?: string;
  tool_calls?: Array<{ name: string; arguments: Record<string, unknown> }>;
  tool_name?: string;
  success?: boolean;
  data?: Record<string, unknown>;
  error?: string;
  answer?: string;
  reasoning_trace?: string;
}

export interface StreamStageEvent {
  type: 'stage';
  stage: 'routing' | 'recall' | 'learning' | 'searching' | 'reasoning' | 'responding';
  detail: string;
}

// Extended ChatMessage for streaming
export interface StreamingMessage extends ChatMessage {
  isStreaming: boolean;
  toolCalls?: Array<{ name: string; arguments: unknown; status: 'pending' | 'running' | 'complete' | 'error' }>;
  currentTool?: string;
  streamError?: string;
}

// API function types
export interface PostChatStreamOptions {
  message: string;
  session_id?: string;
  attached_files?: AttachedFile[];
  turn_id?: string;
  search_consent?: boolean;
  onEvent: (event: StreamEvent) => void;
  onStageEvent?: (event: StreamStageEvent) => void;
  onError: (error: Error) => void;
  onClose: () => void;
}
```

## 5.10: Performance Considerations

### Memory Management
- Buffer streaming content in signal, not DOM (avoid re-renders per token)
- Batch token updates: debounce `appendToken` at ~16ms (1 frame) for high-volume streams
- Limit message history: keep last 50 messages in store, paginate older

### Network
- Reuse single fetch connection per conversation turn
- Backend keeps connection open; frontend aborts on unmount
- Compression: ensure backend sends `Content-Encoding: gzip`

### Rendering Optimization
```typescript
// In chat.ts - batched token appending
let tokenBuffer = '';
let flushTimeout: number;

function appendToken(delta: string) {
  tokenBuffer += delta;
  if (flushTimeout) clearTimeout(flushTimeout);
  flushTimeout = setTimeout(() => {
    streamingMessage.content += tokenBuffer;
    tokenBuffer = '';
  }, 16); // ~60fps max
}
```

### Large Response Handling
- Virtualized message list for long conversations (future)
- Streaming doesn't increase memory vs full response (same final size)

## 5.11: Migration Strategy (Polling → SSE)

### Phase 5A: Parallel Implementation
1. Add `postChatStream()` alongside existing `postChat()`
2. Add streaming state to `chat.ts` without removing polling
3. Feature flag: `settings.streamingEnabled` (default true)

### Phase 5B: Gradual Rollout
```typescript
// In ChatPage.tsx
const useStreaming = settings.streamingEnabled && !isVoiceMode;

const handleSend = useStreaming ? handleSendStreaming : handleSendBlocking;
```

### Phase 5C: Deprecate Polling
- Remove `/chat/status/{turnId}` polling after validation
- Keep polling code for 1 release as fallback
- Delete `status.ts` polling logic after stability confirmed

### Fallback Logic
```typescript
// In api.ts - automatic fallback
export async function postChatWithFallback(...args) {
  if (settings.streamingEnabled) {
    try {
      return await postChatStream(...args);
    } catch (error) {
      console.warn('Streaming failed, falling back to /chat:', error);
    }
  }
  return postChat(...args); // Blocking fallback
}
```

## 5.12: SolidJS Patterns for Streaming

### Callback Refs for Streaming Container
```tsx
// Message.tsx - auto-scroll to bottom during streaming
<div 
  ref={(el) => { streamingContainer = el; }}
  class={`msg-content ${isStreaming() ? 'msg-streaming' : ''}`}
  aria-live="polite"
>
  {content()}
</div>

// Auto-scroll effect
createEffect(() => {
  if (isStreaming() && streamingContainer) {
    streamingContainer.scrollTop = streamingContainer.scrollHeight;
  }
});
```

### Resource Management with onCleanup
```tsx
// ChatPage.tsx
onMount(() => {
  let cleanup: () => void;
  
  const startStream = async () => {
    cleanup = postChatStream(
      message,
      sessionId,
      attachedFiles,
      turnId,
      searchConsent,
      handleEvent,
      handleError,
      handleClose
    );
  };
  
  onCleanup(() => cleanup?.());
});
```

### Derived State with createMemo
```typescript
// chat.ts - streaming message for current turn
export const streamingMessage = createMemo(() => {
  const msg = chatState.currentStreamingMessage;
  if (!msg) return null;
  return {
    ...msg,
    displayContent: msg.content + (msg.isStreaming ? '▊' : ''),
  };
});
```

## 5.13: File Structure & Naming

### New Files
```
frontend/src/
├── services/
│   ├── api.ts              # ← add postChatStream()
│   └── status.ts           # ← add StreamStageEvent handling
├── state/
│   ├── chat.ts             # ← add streamingMessage signal
│   └── chat.test.ts        # ← add streaming tests
├── components/
│   ├── chat/
│   │   ├── Message.tsx     # ← streaming states
│   │   ├── Message.module.css  # ← NEW: streaming styles
│   │   ├── ChatPage.tsx    # ← streaming integration
│   │   └── StatusIndicator.tsx # ← SSE stage events
│   └── ui/
│       └── Spinner.tsx     # ← NEW: reusable spinner (if needed)
└── types/
    └── chat.ts             # ← StreamEvent, StreamingMessage types
```

### CSS Module Convention
```css
/* Message.module.css */
.msg { /* base */ }
.msg-user { /* user message */ }
.msg-assistant { /* assistant message */ }
.msg-streaming { /* streaming state */ }
.msg-tool-calling { /* tool active */ }
.msg-complete { /* finished */ }
.msg-error { /* stream error */ }

.content { /* message text */ }
.tool-badge { /* tool name label */ }
.spinner { /* loading spinner */ }
.streaming-cursor { /* pulsing cursor */ }
```

## 5.14: Validation Checklist

### Pre-Implementation
- [ ] Backend `/chat/stream` endpoint tested with curl
- [ ] SSE event format matches specification
- [ ] Fallback `/chat` endpoint works identically

### During Implementation
- [ ] TypeScript compiles without errors
- [ ] ESLint passes (no new warnings)
- [ ] CSS modules follow naming convention
- [ ] Design tokens used (no hardcoded values)

### Post-Implementation
- [ ] All 6 regression tests pass
- [ ] Streaming works with voice mode
- [ ] Search consent modal triggers correctly
- [ ] Queue drains after stream completes
- [ ] Error fallback to `/chat` works
- [ ] No memory leaks (cleanup called)
- [ ] Reduced motion respected
- [ ] ARIA attributes present

### Performance Benchmarks
- [ ] First token paint < 100ms (perceived)
- [ ] 60fps scrolling during streaming
- [ ] No layout shift during token append
- [ ] Memory stable over 10+ message conversation
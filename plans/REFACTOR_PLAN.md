# Frontend Refactor Plan

Based on code review findings (2026-09-10). All items must pass lint, typecheck, and tests before merge.

---

## Testing Strategy

**Principles** (apply to all phases):

1. **No third-party/library tests** — Don't test SolidJS, vitest, Zod, fetch, browser APIs. Test our logic that uses them.
2. **Test critical business logic** — Conversation isolation, message routing, state transitions, correction pipeline, search extraction/merge.
3. **Test what's reasonable** — Skip: audio/voice (Web Audio API, MediaRecorder, browser permissions), file upload (FormData, drag-drop), complex animations. These are integration/e2e concerns.
4. **Logical path coverage** — Each test must exercise a distinct code path (happy, error, edge). No duplicate "happy path" tests with different data.
5. **Variants = different logic paths** — Parameterized tests only when each variant hits a different branch. Not for data permutations.

**What to test (priority order):**
- **State machines we wrote**: `voice.ts` status transitions, `chat.ts` turn lifecycle
- **Pure functions we wrote**: `getStageLabel`, `formatBytes`, `copyMessage` stripping, `markdown` link rewriting
- **Store/selectors we wrote**: `conversationMessages` per-session isolation, `messages` derivation
- **Async flows we orchestrate**: `postChatMessage` → API → status polling → cleanup (happy + error + abort)
- **Component logic we wrote**: `InputBar` submit/key handling, `Message` reaction/correction handlers
- **Integration**: Conversation switch preserves history + status (Phase 4)

**What NOT to test (not our business logic):**
- Browser APIs: Web Audio, MediaRecorder, FileReader, fetch, localStorage, crypto.randomUUID
- Library behavior: SolidJS reactivity, marked markdown parsing, Zod validation
- Visual/CSS: animations, transitions, layout, theming
- Complex browser interactions: drag-drop, permissions, audio context
- **E2E (Playwright/Cypress)** — Slow, flaky, duplicate unit coverage. Our CI runs unit tests in <2s; E2E adds minutes.

---

## Current Test Audit (2026-09-10)

| File | Tests | Keep | Remove/Refactor | Reason |
|------|-------|------|-----------------|--------|
| `voice.test.ts` | 8 | 8 | — | State machine logic we wrote ✓ |
| `chat.test.ts` | 16 | 10 | 6 | `loads from localStorage on init` (tests module side effect), `setMessages` direct calls (tests signal, not our logic) |
| `api.test.ts` | 23 | 0 | 23 | Tests `api()` wrapper + fetch mocking — tests library/HTTP, not our logic |
| `TracePanel.test.tsx` | 10 | 4 | 6 | Tests rendering variants (data permutations), not logic paths |
| `VoiceControls.test.tsx` | 5 | 0 | 5 | Tests MediaRecorder/permissions — browser API |
| `InputBar.test.tsx` | 13 | 8 | 5 | Tests `handleKeyDown`/`handleSubmit` logic ✓; rest test refs/rendering |
| `TopBar.test.tsx` | 10 | 3 | 7 | Tests dropdown rendering (DOM), not conversation switching logic |

**Target**: ~35 focused tests (down from 85), each covering a distinct logic path we own.

---

## Phase 1: Critical Fixes (Week 1)

### 1.1 Eliminate module-level side effects in `chat.ts`
- [ ] Move `localStorage` initialization into `initChat()` function
- [ ] Move `createEffect` (sessionId → messages sync) into `initChat()`
- [ ] Call `initChat()` from `App.tsx` during app initialization
- [ ] Remove `if (typeof localStorage !== 'undefined')` block from module scope
- [ ] Verify tests no longer show "computations created outside createRoot" warnings

**Files**: `chat.ts`, `App.tsx`

### 1.2 Fix parameter shadowing pattern
- [ ] Audit all exported signal getters (`sessionId`, `messages`, `isTurnActive`, `currentTurnId`, `queue`)
- [ ] Rename function parameters to avoid collisions (e.g., `sessionIdParam`, `turnIdParam`)
- [ ] Add ESLint rule `no-shadow` for imported signals

**Files**: `chat.ts`, `status.ts`

---

## Phase 2: State Architecture (Week 1-2)

### 2.1 Replace Map signals with `createStore`
- [ ] Replace `conversationMessages: Map<string, ChatMessage[]>` with store
- [ ] Replace `conversationTurnIds: Map<string, string>` with store
- [ ] Use store selectors for derived state (e.g., `messages` becomes pure derived)
- [ ] Remove exported `setMessages` — messages become read-only derived
- [ ] Update all call sites (`loadConversationMessages`, `addMessageToConversation`, `postChatMessage`)

**Files**: `chat.ts`, `ChatPage.tsx`, `App.tsx`

### 2.2 Add memory leak prevention to status polling
- [ ] Implement `clearTurnStatus(turnId)` to delete from `turnStatusMap`
- [ ] Call cleanup in `stopStatusPolling` after interval cleared
- [ ] Add `onCleanup` in components using `useTurnStatus`
- [ ] Consider `WeakMap` for automatic GC (if keys are objects)

**Files**: `status.ts`, `StatusIndicator.tsx`, `ChatPage.tsx`

---

## Phase 3: Component Architecture (Week 2)

### 3.1 Extract App logic into composable hooks
- [ ] `useConversations()` — fetch, cache, loading state
- [ ] `useActiveConversation()` — current selection, switching logic
- [ ] `useAppInit()` — user fetch, settings, assistant name, session restore
- [ ] `useChat(conversationId?)` — message sending, status polling per conversation
- [ ] Each hook in own file under `src/hooks/` or `src/state/`

**Files**: `App.tsx` → split into hooks, new hook files

### 3.2 Make `messages` a pure derived signal
- [ ] Remove `export const [messages, setMessages] = createSignal(...)`
- [ ] Create `messages` as `createMemo(() => conversationMessagesStore.get(sessionId()) || [])`
- [ ] Update `ChatPage.tsx` to use derived messages
- [ ] Remove `setMessages` from test imports

**Files**: `chat.ts`, `ChatPage.tsx`, `chat.test.ts`

---

## Phase 4: Test Reliability (Week 2)

### 4.1 Wrap all tests in `createRoot`
- [ ] Create test utility: `renderWithRoot(component)` 
- [ ] Apply to all test files (`chat.test.ts`, `TopBar.test.ts`, etc.)
- [ ] Verify no "computations created outside createRoot" warnings

### 4.2 Add integration tests for multi-conversation isolation
- [ ] Test: Two conversations, send message to A, verify B unchanged
- [ ] Test: Switch conversation mid-stream, verify status persists per conversation
- [ ] Test: Load conversation, send message, switch back, verify history intact
- [ ] Test: Concurrent polling for two conversations doesn't cross-talk

### 4.3 Remove module-load hacks from tests
- [ ] Remove `vi.resetModules()` + dynamic import in `chat.test.ts:76-78`
- [ ] Use `createRoot` + explicit init instead

**Files**: `chat.test.ts`, `vitest.setup.ts` (new), test utilities

---

## Phase 5: Type Safety & Lint (Week 2-3)

### 5.1 Type all `any[]` returns in `api.ts`
- [ ] `getFrames` → `Frame[]`
- [ ] `getAssociations` → `Association[]`
- [ ] `getSearchResults` → `SearchResult`
- [ ] `listFiles` → `FileEntry[]`
- [ ] `getUserSessions` → `Session[]`
- [ ] `getSessionMessages` → `SessionMessage[]`

### 5.2 Fix Solid reactivity warnings
- [ ] `Button.tsx`: inline `props.variant` in JSX or use `createMemo`
- [ ] `Toast.tsx`: inline `props.duration` in effect
- [ ] `Message.tsx`: ensure `props` accessed only in JSX/event handlers

### 5.3 Enable stricter ESLint rules
- [ ] Add `no-shadow` for signal imports
- [ ] Add `solid/no-destructure` (already erroring on Button/Toast)

---

## Phase 6: Cleanup (Week 3)

### 6.1 Remove dead code from `status.ts`
- [ ] Delete unused exports: `getTurnStatus`, `isTurnPolling`, `useActiveTurnStatus`

### 6.2 Remove unused exports from `chat.ts`
- [ ] `conversationTurnIds` (only used internally)
- [ ] `currentTurnId` (only used internally)
- [ ] `queue` / `setQueue` (verify unused)

### 6.3 Consolidate types
- [ ] Move shared interfaces (`ChatMessage`, `MessageMeta`, `ExtractionSummary`, etc.) to `src/types/chat.ts`
- [ ] Import from types barrel file

---

## Acceptance Criteria

| Check | Command | Must Pass |
|-------|---------|-----------|
| Lint | `npm run lint` | 0 errors, 0 warnings |
| Typecheck | `npm run typecheck` | 0 errors |
| Unit tests | `npm run test -- --run` | ~35 focused tests passing (down from 85) |
| Build | `npm run build` | Success |
| No test warnings | — | No "computations created outside createRoot" |

---

## Rollback Plan

Each phase is independently mergeable. If Phase 2+3 introduce regressions:
1. Revert to Phase 1 baseline (stable, working)
2. Debug in isolation
3. Re-apply incrementally

---

## Notes

- **Priority**: Phase 1 unblocks reliable testing. Phase 2 fixes root cause of shadowing bugs.
- **Testing**: New integration tests in Phase 4 are the regression guard for conversation isolation.
- **Design principle**: "Clean ship" — every exported function must have a caller. Dead code = liability.
# Frontend Testing Strategy (SolidJS + Vitest)

A living document. Mirrors the backend philosophy: test behavior, not frameworks. High signal, low bloat.

## 1. Philosophy
- **Test user-visible behavior**, not implementation details. If a test only verifies a signal value changed, delete it. Test what the user sees/does.
- **Mock external boundaries only** — API calls, browser APIs (SpeechRecognition, localStorage, fetch). Test stores, components, and logic for real.
- **Integration > unit** for critical flows. One test covering "voice → transcribe → send" outweighs 10 unit tests on individual pieces.
- **No snapshot tests.** They catch noise, not regressions.
- **No coverage targets.** Test by judgment. If a module feels undertested, write a meaningful test.

## 2. Tooling
- **vitest** — runner, native Vite integration, TypeScript support
- **@solidjs/testing-library** — official utilities (React Testing Library equivalent)
- **jsdom** — environment for DOM APIs
- **No Playwright, Cypress, or E2E frameworks** — intentionally excluded per project decision

## 3. What to Test (In Scope)

### State Stores (Highest ROI)
Pure signals/functions with observable behavior:
- `src/state/chat.ts` — message queue, turn lifecycle, session persistence, correction handling
- `src/state/voice.ts` — recording state machine, silence detection, transcription flow
- `src/state/session.ts` — session CRUD, history, branching
- `src/state/settings.ts` — validation, persistence, migration

### Service Layer (API Boundaries)
- `src/services/api.ts` — request/response shapes, error handling, retry logic, Zod validation
- `src/services/status.ts` — WebSocket/polling behavior, reconnection

### Component Behavior (Not Rendering)
Test **what users do and see**, not DOM structure:
- `InputBar` — sends on Enter, Shift+Enter=newline, disables during streaming, file chips, dictation toggle
- `VoiceControls` — start/stop recording, handles missing callbacks
- `TracePanel` — expand/collapse, shows task type, memory, citations, search info
- `MessageList` — auto-scroll, markdown rendering, correction chips
- `Message` — renders role, content, meta (citations, extraction summary)

### Critical User Flows (Integration-ish)
Single tests covering multi-component interactions:
- "User types → presses Enter → message appears → streaming response renders → correction chip appears"
- "Voice button pressed → recording starts → silence stops → transcription fills input → send"
- "Settings changed → persisted → reflected in UI"

## 4. What to Skip (Out of Scope)
- ❌ Framework internals (createSignal, createEffect, onMount)
- ❌ CSS/classes/style assertions
- ❌ Trivial props-pass-through components (Button, Icon, Modal shell)
- ❌ Mock verification ("when I mock fetch to return X, does my function return X?")
- ❌ Snapshot tests
- ❌ String pass-through without conditional logic

## 5. Test File Structure
```
src/
├── state/
│   ├── chat.ts
│   ├── chat.test.ts           # ← test file mirrors source
│   ├── voice.ts
│   ├── voice.test.ts
│   └── ...
├── services/
│   ├── api.ts
│   ├── api.test.ts
│   └── ...
├── components/
│   ├── chat/
│   │   ├── InputBar.tsx
│   │   ├── InputBar.test.tsx
│   │   ├── VoiceControls.tsx
│   │   ├── VoiceControls.test.tsx
│   │   ├── TracePanel.tsx
│   │   ├── TracePanel.test.tsx
│   │   └── ...
│   └── ...
```

Test names describe **behavior**, not implementation:
- ✅ `test_sends_message_on_enter_key`
- ✅ `test_disables_send_during_streaming`
- ✅ `test_transitions_listening_to_processing_on_silence`
- ❌ `test_setMessages_called`
- ❌ `test_component_renders`

## 6. Mocking Policy
- **API calls always mocked** at the `fetch` boundary. Patch `global.fetch` in tests.
- **Browser APIs mocked** in `test-setup.ts`: `SpeechRecognition`, `matchMedia`, `localStorage`
- **No mocking internal signals/stores** — test the real reactive graph
- **Time is real** unless testing time-dependent logic (use `vi.useFakeTimers()` locally)
- **Async is real** — vitest runs real event loop

## 7. Running Tests
```bash
# In frontend directory
npm run test        # Run once (CI mode)
npm run test:watch  # Watch mode for development
```

## 8. Adding New Tests — Checklist
Before adding a test, ask:
1. Does this test verify behavior the user can observe?
2. If this test fails, will the error clearly indicate what broke?
3. Is this a distinct branch of logic, or am I re-testing the same path?
4. Am I testing SolidJS or my code?
5. Could this be a more meaningful integration test instead?

If 1–2 fail: don't add the test.
If 3–4 fail: rewrite or delete.
If 5: write the integration test instead.

## 9. Current State
| File | Tests | Covers |
|------|-------|--------|
| `state/voice.test.ts` | 8 | Voice state machine transitions |
| `services/api.test.ts` | 23 | All API endpoints, error handling, FormData |
| `components/chat/InputBar.test.tsx` | 12 | Send, keys, files, dictation, disabled states |
| `components/chat/VoiceControls.test.tsx` | 5 | Start/stop recording, missing callbacks |
| `components/chat/TracePanel.test.tsx` | 9 | Expand/collapse, all sections, citations |

Total: **~57 tests** covering state, services, and critical chat components.

## 10. Known Gaps to Close
- `state/chat.test.ts` — needs fix for dynamic import test
- `state/session.test.ts` — session CRUD, history, branching
- `state/settings.test.ts` — validation, persistence, migration
- `components/chat/MessageList.test.tsx` — auto-scroll, markdown, corrections
- `components/chat/Message.test.tsx` — rendering, meta display
- Integration test: full chat flow (type → send → stream → correction)
- Integration test: voice flow (record → transcribe → fill → send)
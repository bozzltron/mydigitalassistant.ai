# Plan: SolidJS SPA Migration

**Status**: Proposed
**Date**: 2026-09-05
**Owner**: Core team

---

## Executive Summary

Migrate the current vanilla JS chat UI (3,200+ lines in `chat.html` + `shared/utils.js`) to a SolidJS + TypeScript SPA. The migration addresses the core maintainability problem: agents struggle with the monolithic vanilla JS codebase. The build step is already part of Docker, so adding Vite adds zero operational cost.

**Goal**: Replace monolithic vanilla JS with a typed, component-based SolidJS SPA that agents can safely modify.

**Timeline**: ~10-15 working days
**Risk**: Low (incremental migration, existing backend unchanged)

---

## Problem Statement

### Current State
- **Chat UI**: 3,200+ lines in single `chat.html` file with embedded `<script type="module">`
- **Shared utilities**: `static/shared/utils.js` (general-purpose helpers)
- **No type safety**: Agents make runtime errors (null access, wrong props, missing fields)
- **No component boundaries**: Monolithic IIFE, manual DOM manipulation everywhere
- **Agent failure modes**:
  - Missing props/fields on objects
  - Incorrect DOM manipulation order
  - Event handler memory leaks
  - CSS class typos
  - State synchronization bugs

### Desired State
- **Typed components**: Each UI piece is a `.tsx` file with explicit props
- **Component isolation**: Agents modify one component without breaking others
- **Compile-time safety**: TypeScript catches errors before runtime
- **Declarative reactivity**: Signals replace manual DOM sync
- **Testable units**: Unit tests per component with Vitest

---

## Technical Approach

### Stack
| Layer | Technology | Rationale |
|-------|------------|-----------|
| Framework | SolidJS | Fine-grained reactivity, no VDOM, ~6KB, TS-first |
| Build | Vite | Fast HMR, TypeScript, tree-shaking, small config |
| Language | TypeScript (strict) | Compile-time safety for agents |
| Routing | Solid Router | 3KB, file-based or programmatic |
| Testing | Vitest + Testing Library | Fast, component-focused |
| Styling | CSS Modules or vanilla CSS | Existing styles portable, no runtime dep |

### Architecture

```
frontend/
├── package.json
├── tsconfig.json
├── vite.config.ts
├── index.html
├── src/
│   ├── main.tsx                    # Entry point
│   ├── App.tsx                     # Root component + providers
│   ├── routes.tsx                  # Route definitions
│   ├── components/
│   │   ├── chat/
│   │   │   ├── MessageList.tsx
│   │   │   ├── Message.tsx
│   │   │   ├── InputBar.tsx
│   │   │   ├── TracePanel.tsx
│   │   │   ├── VoiceControls.tsx
│   │   │   └── SettingsPanel.tsx
│   │   ├── brain/
│   │   │   ├── BrainGraph.tsx
│   │   │   ├── FrameDetail.tsx
│   │   │   └── SearchPanel.tsx
│   │   ├── files/
│   │   │   ├── FileGrid.tsx
│   │   │   ├── FileViewer.tsx
│   │   │   └── UploadZone.tsx
│   │   └── ui/
│   │       ├── Button.tsx
│   │       ├── Modal.tsx
│   │       ├── Toast.tsx
│   │       └── Icon.tsx
│   ├── state/
│   │   ├── user.ts                 # createSignal<User>
│   │   ├── session.ts              # createSignal<Session>
│   │   ├── voice.ts                # createSignal<VoiceState>
│   │   └── settings.ts             # createStore<Settings>
│   ├── services/
│   │   ├── api.ts                  # Typed fetch + Zod schemas
│   │   ├── websocket.ts            # Optional: future WebSocket
│   │   └── storage.ts              # localStorage helpers
│   └── styles/
│       ├── global.css
│       └── variables.css
├── tests/
│   ├── components/
│   └── services/
└── public/
    └── favicon.ico
```

---

## Migration Phases

### Phase 0: Foundation (Day 1)
**Goal**: Working build pipeline with SolidJS + TypeScript

| Task | Details |
|------|---------|
| Initialize project | `npm create vite@latest frontend -- --template solid-ts` |
| Install deps | `solid-router`, `@solidjs/meta`, `zod`, `vitest`, `@testing-library/solidjs` |
| Configure TypeScript | Strict mode, ESNext modules, JSX preserve |
| Configure Vite | Output to `../assistant/backend/static/`, base `/static/` |
| CI integration | Add `npm run build` to Dockerfile |
| Verify | `npm run build` outputs to `static/` |

**Deliverable**: `npm run build` → `assistant/backend/static/assets/*.js` + `index.html`

---

### Phase 1: Core Infrastructure (Days 2-3)
**Goal**: Type-safe API layer + global state + routing

| Component | Details |
|-----------|---------|
| `services/api.ts` | Typed `fetch` wrapper + Zod response schemas for all endpoints |
| `state/user.ts` | `createSignal<User \| null>` + `createSignal<string>` for session |
| `state/voice.ts` | `createSignal<VoiceState>` + actions (`enter`, `exit`, `listen`) |
| `state/settings.ts` | `createStore<Settings>` + `save/load` from localStorage |
| `routes.tsx` | `/chat`, `/brain`, `/files`, `/settings` with lazy loading |
| `App.tsx` | Providers: Router, Meta, global styles |

**Deliverable**: Skeleton app with routing, typed API, global state working

---

### Phase 2: Chat UI Components (Days 4-8)
**Goal**: Full chat feature parity

| Component | Props/State | Key Features |
|-----------|-------------|--------------|
| `MessageList` | `messages: Message[]` | Virtual scrolling, auto-scroll, streaming |
| `Message` | `role`, `content`, `meta` | Markdown, reactions, correction panel, copy, TTS |
| `InputBar` | `value`, `onSend`, `attachedFiles` | Multi-line, file chips, mic button, queue indicator |
| `TracePanel` | `taskType`, `memory`, `citations`, `searchInfo` | Collapsible sections, copy buttons |
| `VoiceControls` | `state`, `onListen`, `onStop` | Overlay + inline status, barge-in indicator |
| `SettingsPanel` | `settings` signal | TTS, voice select, speed/pitch/volume, Brave toggle |

**State Management**:
```typescript
// src/state/chat.ts
interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  meta?: MessageMeta;
  id: string;
}

const [messages, setMessages] = createSignal<ChatMessage[]>([]);
const [queue, setQueue] = createSignal<QueuedMessage[]>([]);
const [isTurnActive, setTurnActive] = createSignal(false);
```

**API Integration**:
- `POST /chat` with typed request/response
- Streaming via `fetch` + `ReadableStream` (future)
- Message queue for turn-taking

**Deliverable**: Full chat experience working in `/chat` route

---

### Phase 3: Brain Observatory (Days 9-11)
**Goal**: Graph visualization + frame exploration

| Component | Details |
|-----------|---------|
| `BrainGraph` | 3D force graph (reuse existing 3d-force-graph lib), node click → detail |
| `FrameDetail` | Slots table, associations list, confidence badges, conflict history |
| `SearchPanel` | Semantic search input, results with frame preview |

**Data Sources**: Existing `/memory/search`, `/frames/:id`, `/associations` endpoints

---

### Phase 4: Files UI (Days 12-13)
**Goal**: File management UI

| Component | Details |
|-----------|---------|
| `FileGrid` | Search/filter, multi-select, bulk delete, type badges |
| `FileViewer` | Modal with syntax highlight (text/JSON/CSV), table for CSV |
| `UploadZone` | Drag-drop, file chips, progress, validation |

**Endpoints**: Reuse existing `/files/list`, `/files/:id/content`, `POST/DELETE /files`

---

### Phase 4.5: Shared UI Library (Day 14)
**Goal**: Consistent, reusable primitives

| Component | Variants |
|-----------|----------|
| `Button` | primary, secondary, danger, ghost, icon-only, loading |
| `Modal` | size, closeable, portal |
| `Toast` | success, error, warning, info, auto-dismiss |
| `Icon` | Inline SVG, size, color |
| `Select`, `Input`, `Textarea`, `Checkbox`, `Slider` | Form primitives |
| `ThemeToggle` | light/dark/system, persists to localStorage |

---

### Phase 5: Polish & Testing (Days 15-16)
**Goal**: Production-ready quality

| Area | Tasks |
|------|-------|
| **Testing** | Unit tests for API layer, component smoke tests, integration test for chat flow |
| **Accessibility** | ARIA labels, keyboard nav, screen reader support |
| **Backend health** | "Can't reach brain" banner when FastAPI unreachable (local network issues) |
| **Error boundaries** | Global error UI, graceful degradation |
| **Theming** | CSS variables for light/dark mode |

---

## Type Safety Strategy

### Zod Schemas for API
```typescript
// src/services/schemas.ts
export const ChatRequestSchema = z.object({
  user_id: z.number().int(),
  message: z.string(),
  session_id: z.string().uuid().optional(),
  turn_id: z.string().uuid().optional(),
  attached_files: z.array(z.object({
    name: z.string(),
    ext: z.string(),
    preview: z.string(),
    content: z.string(),
    text: z.string(),
    key_entities: z.array(z.string()),
    open_questions: z.array(z.string()),
  })).optional(),
});

export const ChatResponseSchema = z.object({
  session_id: z.string().uuid(),
  response: z.string(),
  task_type: z.enum(['functional', 'introspective', 'search', 'scheduled', 'correction']),
  memory_context: z.string().optional(),
  citations: z.array(z.string()).optional(),
  extraction_summary: z.object({
    slots_applied: z.number(),
    associations_created: z.number(),
    conflicts_created: z.number(),
    frame_ids: z.array(z.number()),
    slots: z.array(SlotSchema),
  }).optional(),
  search_extraction_summary: z.object({ /* ... */ }).optional(),
});
```

### Component Props Types
```typescript
// src/components/chat/Message.tsx
interface MessageProps {
  role: 'user' | 'assistant';
  content: string;
  meta?: MessageMeta;
  onReact?: (kind: 'positive' | 'negative' | 'correction', msgId: string) => void;
}

interface MessageMeta {
  task_type?: string;
  memory_context?: string;
  citations?: string[];
  ogData?: Record<string, OgData>;
  extraction_summary?: ExtractionSummary;
  search_extraction_summary?: ExtractionSummary;
}
```

---

## Docker Integration

### Multi-stage Build
```dockerfile
# Build stage
FROM node:20-alpine AS builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# Final stage (existing)
FROM python:3.11-slim
# ... existing Python setup ...
COPY --from=builder /app/frontend/dist /app/assistant/backend/static
```

### Nginx/Caddy Config (unchanged)
- Static files served from `/static/`
- API routes proxied to FastAPI

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Migration takes longer | Medium | Schedule slip | Incremental: chat → brain → files; ship each route |
| SolidJS learning curve | Low | Velocity dip | Team already knows React; SolidJS is similar |
| Bundle size regression | Low | Load time | SolidJS is 6KB; Vite tree-shakes aggressively |
| HMR issues in Docker | Medium | Dev friction | Use `vite --host 0.0.0.0 --port 5173` + volume mount |
| Backend API drift | Low | Runtime errors | Zod schemas validate at compile + runtime |
| CSS migration issues | Low | Visual bugs | Copy existing CSS variables; use CSS Modules |

---

## Success Criteria

| Metric | Target |
|------|--------|
| **TypeScript errors** | 0 (strict mode) |
| **Bundle size** | < 100KB gzipped (app + framework) |
| **First paint** | < 1s on localhost |
| **Test coverage** | > 80% on services, > 60% on components |
| **Agent modification success** | > 90% first-try on component edits |
| **Feature parity** | 100% of current chat features + brain + files |

---

## Rollout Plan

| Week | Milestone |
|------|-----------|
| 1 | Foundation + Core Infrastructure (Phases 0-1) |
| 2 | Chat UI Complete (Phase 2) |
| 3 | Brain + Files + Polish (Phases 3-5) |
| 4 | Testing, bug fixes, documentation |

---

## Alternatives Considered

| Option | Verdict |
|--------|---------|
| **React** | Larger bundle (40KB), VDOM overhead, more boilerplate |
| **Vue 3** | Good, but SolidJS reactivity model fits better |
| **Svelte** | Good, but SolidJS TS support slightly better |
| **Vanilla TS + signals** | Viable, but loses component model, testing ecosystem |
| **Stay vanilla** | Rejected - agent maintainability blocker |

---

## Appendix: File Mapping (Current → New)

| Current | New Location |
|---------|--------------|
| `chat.html` (lines 1-1340: HTML) | `index.html` + `src/components/chat/*.tsx` |
| `chat.html` (lines 1347-3238: JS) | `src/components/chat/*.tsx`, `src/state/*.ts`, `src/services/api.ts` |
| `static/shared/utils.js` | `src/services/api.ts`, `src/services/storage.ts`, `src/lib/utils.ts` |
| `module_script.js` | **Delete** (superseded) |
| `static/shared/components.css` | `src/styles/global.css` + CSS Modules per component |

---

## Approval

- [ ] Technical lead review
- [ ] Security review (no new external deps)
- [ ] Resource allocation (2 weeks dev time)
- [ ] Rollback plan documented (keep `chat.html` until new SPA verified)
# Frontend

The chat UI and the Brain Observatory, written in SolidJS + TypeScript and built with Vite.

## Structure

```
src/
├── index.tsx            # Entry point
├── App.tsx              # Root component with routing
├── routes.tsx           # Route definitions
├── components/          # UI, by page/feature
│   ├── brain/           # Brain Observatory (force graph, conflicts, search)
│   ├── chat/            # Conversation, trace panel, input bar
│   ├── files/  maps/  tasks/
│   └── ui/              # Shared primitives
├── hooks/               # Reusable reactive hooks (voice recording, …)
├── state/               # Global state (signals/stores) + their tests
├── services/            # API layer with Zod schemas
├── styles/              # CSS modules, design tokens, base classes
├── types/  utils/
└── test-utils.ts        # Test helpers
```

## Development Commands

```bash
npm run dev        # Vite dev server on :5173 (compose publishes it at localhost:5174)
npm run build      # Build for production, output to ../assistant/backend/static/
npm run lint       # Lint source files
npm run typecheck  # Strict, source-only typecheck (tsconfig.typecheck.json)
npm run test       # Run tests (CI mode)
npm run test:watch # Run tests in watch mode
npm run check      # lint + typecheck + test — one command
```

`npm run check` is the gate. `vite build` and `vitest` both transpile without
type-checking, which is why `typecheck` exists as its own strict, source-only pass.

## Gate

The commands live in `scripts/agent-gates.sh`, not here:

```bash
scripts/agent-gates.sh --scoped <backend test paths> --frontend   # inner loop
scripts/agent-gates.sh --full                                     # pre-commit
```

`--frontend` runs `npm run check` with **host Node**. Do not run npm inside the
`assistant` image: its runtime stage is `python:3.11-slim` and Node exists only in the
`frontend-builder` stage, which is discarded — the command exits 127. CI runs the frontend
gate through `run_ci.sh`, which uses the `solid-dev` service (`node:22-alpine`).

## Testing Strategy
See [`TESTING.md`](TESTING.md) for the testing philosophy, what to test, mocking policy, and current test coverage.

## Dependencies

- SolidJS - for reactive UI components
- TypeScript - for type safety
- Zod - for API schema validation
- Vitest + Testing Library - for testing
- Vite - for build tooling

## Architecture Notes

- Component-based architecture with fine-grained reactivity using SolidJS signals
- Type-safe API layer with compile-time validation
- Global state management through SolidJS signals and stores
- CSS Modules for scoped styling; no inline styles
- Testable components and services
- Strict TypeScript mode for catching runtime errors at build time

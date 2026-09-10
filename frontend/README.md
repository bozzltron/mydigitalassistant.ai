# Frontend - SolidJS SPA Migration

This directory contains the frontend implementation of the digital assistant using SolidJS, TypeScript, and modern web standards.

## Structure

```
src/
├── main.tsx             # Entry point
├── App.tsx              # Root component with routing
├── routes.tsx           # Route definitions
├── components/          # UI Components (placeholder skeletons)
├── state/               # Global state management (signals/stores)
├── services/            # API layer with Zod schemas
├── styles/              # CSS modules and global styles
└── tests/               # Unit tests
```

## Implementation Status

### Phase 0 - Foundation (Completed)
- Set up Vite + SolidJS + TypeScript project
- Configured build to output to `../assistant/backend/static/` 
- Created basic directory structure

### Phase 1 - Core Infrastructure (In Progress)
- Implemented typed API service with Zod validation schemas
- Set up global state management:
  - User state (`user.ts`)
  - Session state (`session.ts`) 
  - Voice state (`voice.ts`)
  - Settings state (`settings.ts`)
- Created error handling service (`error.ts`)
- Added unit tests for API service

## Development Commands

```bash
npm run dev        # Start development server
npm run build      # Build for production
npm run lint       # Lint source files
npm run test       # Run tests (CI mode)
npm run test:watch # Run tests in watch mode
```

## Pre-Commit Flow (Required)
**Before every commit, run both lint and tests in Docker:**

```bash
# From repo root
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run lint
docker run -it --rm -v $(pwd)/frontend:/app -w /app assistant npm run test
```

This mirrors the backend requirement (also run in Docker):
```bash
docker run -it --rm -v $(pwd):/app -w /app assistant ruff check .
docker run -it --rm -v $(pwd):/app -w /app assistant pytest assistant/tests/
```

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
- CSS Modules for scoped styling
- Testable components and services
- Strict TypeScript mode for catching runtime errors at build time
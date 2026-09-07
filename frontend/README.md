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
npm run test       # Run tests
npm run test:watch # Run tests in watch mode
```

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
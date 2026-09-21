# Chat Media Redesign Plan

## Overview
Redesign the chat view in SolidJS with enhanced media support (images, video embeds, masonry layouts) and cohesive visual design derived from existing color tokens.

## Current State Analysis

### Existing Infrastructure
- **Backend**: FastAPI with SearXNG (default) + optional Brave Search API
- **OG Preview**: `/og-preview` endpoint fetches Open Graph metadata (title, description, image, site_name)
- **Search Extraction**: `search_extraction_summary` in response meta with slots, conflicts, source URLs
- **Message Types**: User/Assistant messages with `meta.ogData` for link previews
- **Frontend**: SolidJS components in `frontend/src/components/chat/`
- **Styling**: CSS custom properties in `variables.css`, modular CSS in `messages.css`, `components.css`, `layout.css`

### Current Media Support
- Basic OG image rendering in `.msg-images` (flex wrap, max 200px width, 120px height)
- No video embed support
- No masonry/grid layout for multiple images
- Assistant message background: `--assistant-msg: #1a2420` (green-tinted, doesn't match design system)

### Color System (variables.css)
```css
--bg: #0f1117
--surface: #1a1d27
--surface2: #242836
--border: #2e3347
--user-msg: #1e3a5f        /* Blue - good */
--assistant-msg: #1a2420    /* Green - NEEDS CHANGE */
--text: #e8e9ed
--text-dim: #8b8fa3
--accent: #14b8a6          /* Teal - primary brand */
--accent2: #4ec9a0         /* Light teal */
--error: #f47070
--warning: #f0b97c
```

**Design Goal**: Assistant message background should derive from `--accent`/`--surface` family, not a disconnected green.

## Requirements

### 1. Media Rendering Enhancements

#### 1.1 Image Search Results - Masonry Layout
- When multiple images returned from search (via `ogData` or search extraction), render in responsive masonry grid
- CSS Grid with `grid-auto-flow: dense` for optimal packing
- Aspect-ratio preservation with `object-fit: cover`
- Lightbox/fullscreen view on click
- Lazy loading with intersection observer

#### 1.2 Single Image Response - Card Layout
- When response has one primary image (top result), render as hero card at top of message
- Image above text content, full-width of message bubble
- Rounded corners matching message bubble radius
- Source attribution below image

#### 1.3 YouTube Video Embeds
- Detect YouTube URLs in search results or message content
- Extract video ID from various URL formats (`youtube.com/watch?v=`, `youtu.be/`, `youtube.com/embed/`, `youtube.com/shorts/`)
- Render as responsive iframe with `16:9` aspect ratio
- Privacy-enhanced mode (`youtube-nocookie.com`)
- Thumbnail placeholder with play button before load (performance)

#### 1.4 Generic Video Support
- Detect direct video URLs (`.mp4`, `.webm`, `.mov`)
- Render as `<video>` element with controls
- Poster image from OG data if available

### 2. Message Visual Design Overhaul

#### 2.1 Color System Update
```css
/* New assistant message background - derived from accent/surface */
--assistant-msg: color-mix(in srgb, var(--surface2) 85%, var(--accent) 15%);
--assistant-msg-hover: color-mix(in srgb, var(--surface2) 75%, var(--accent) 25%);
--user-msg: #1e3a5f;  /* Keep - works well */

/* Alternative if color-mix not supported: */
--assistant-msg: #1e2d2b;  /* Dark teal-tinted surface */
```

#### 2.2 Message Structure
```
┌─────────────────────────────────────┐
│  Assistant Message                  │
├─────────────────────────────────────┤
│  [Hero Image/Video Card] (optional) │
├─────────────────────────────────────┤
│  Markdown Content                   │
├─────────────────────────────────────┤
│  [Masonry Image Grid] (optional)    │
├─────────────────────────────────────┤
│  Sources / Learned Indicator        │
├─────────────────────────────────────┤
│  Actions (copy, react, correct)     │
└─────────────────────────────────────┘
```

#### 2.3 Typography & Spacing
- Consistent spacing scale using `--space-*` tokens (add to variables.css)
- Improved markdown rendering: better code blocks, tables, blockquotes
- Reduced visual noise in meta/task_type display

### 3. Response Formatters

#### 3.1 Media Detection Pipeline (Frontend)
```typescript
interface MediaContent {
  type: 'image' | 'video' | 'youtube' | 'preview-card';
  url: string;
  thumbnail?: string;
  title?: string;
  description?: string;
  sourceUrl?: string;
  aspectRatio?: number;
}

function extractMediaFromMessage(message: ChatMessage): MediaContent[]
```

Detection sources (priority order):
1. `message.meta.ogData` - Open Graph previews from cited URLs
2. `message.meta.search_extraction_summary` - Search result images
3. Markdown image syntax `![alt](url)` in content
4. YouTube URLs in content or citations
5. Direct video URLs in content

#### 3.2 Layout Decision Logic
| Media Count/Type | Layout |
|------------------|--------|
| 1 image, no video | Hero card (full-width, top) |
| 1 video (YouTube/direct) | Video embed (full-width, top) |
| 2-4 images | 2-column grid |
| 5+ images | Masonry (3-col desktop, 2-col tablet, 1-col mobile) |
| Mixed media | Hero (first video/image) + masonry (remaining images) |

### 4. Backend Enhancements

#### 4.1 Enhanced Search Result Media Extraction
- Extend `SearchResult` type to include thumbnail/image URLs from search engines
- Brave Search API returns `thumbnail` field - capture it
- SearXNG engines may return `img_src` - capture when available
- Pass through to `ChatResponse.search_info.results[].thumbnail`

#### 4.2 YouTube Detection in Search
- When search results contain YouTube URLs, extract video IDs
- Include in `SearchInfo` as `video_results: YouTubeVideo[]`
- Frontend renders as embeds

#### 4.3 OG Preview Batching
- Batch `/og-preview` requests for multiple URLs
- Frontend requests previews for all citation URLs in parallel
- Cache results in frontend (sessionStorage) to avoid re-fetching

### 5. CSS Architecture

#### 5.1 New CSS Modules
```
frontend/src/styles/
├── variables.css          # Design tokens (update)
├── messages.css           # Message bubble styles (major rewrite)
├── media-grid.css         # NEW: Masonry, hero card, video embed styles
├── markdown.css           # NEW: Enhanced markdown rendering
└── components.css         # Shared components (minor updates)
```

#### 5.2 Design Tokens to Add
```css
/* Spacing scale */
--space-xs: 0.25rem;
--space-sm: 0.5rem;
--space-md: 1rem;
--space-lg: 1.5rem;
--space-xl: 2rem;

/* Border radius */
--radius-sm: 6px;
--radius-md: 10px;
--radius-lg: 14px;
--radius-full: 9999px;

/* Transitions */
--transition-fast: 150ms ease;
--transition-base: 200ms ease;
--transition-slow: 300ms ease;

/* Media specific */
--media-radius: var(--radius-md);
--hero-image-max-height: 400px;
--masonry-gap: 0.5rem;
--masonry-column-width: 280px;
```

### 6. SolidJS Component Architecture

#### 6.1 New Components
```
frontend/src/components/chat/
├── MediaGrid.tsx           # Masonry grid for multiple images
├── MediaCard.tsx           # Hero card (single image/video at top)
├── VideoEmbed.tsx          # YouTube/direct video player
├── PreviewCard.tsx         # Link preview card (existing, enhance)
├── MessageContent.tsx      # Extracted content rendering logic
└── Message.tsx             # Main message component (refactor)
```

#### 6.2 Message.tsx Refactor
- Extract media detection to `MessageContent.tsx`
- Use `<Show>` and `<For>` for conditional media rendering
- Remove inline styles completely
- Use CSS Modules or global classes from `media-grid.css`

### 7. Accessibility
- All images: `alt` text from OG title/description or URL
- Video embeds: `title` attribute, keyboard accessible
- Masonry grid: proper heading structure for screen readers
- Focus management for lightbox/fullscreen
- Reduced motion support for animations

### 8. Performance
- Lazy load images with `loading="lazy"` and intersection observer for masonry
- Defer video iframe load until in viewport
- Virtualize masonry grid if >20 images
- CSS-only animations (no JS animation libraries)

## Implementation Phases

### Phase 1: Design Tokens & Color System (Day 1)
- [ ] Update `variables.css` with new assistant message color
- [ ] Add spacing, radius, transition tokens
- [ ] Add media-specific tokens
- [ ] Update `messages.css` to use new tokens

### Phase 2: Media Detection & Types (Day 1-2)
- [ ] Create `MediaContent` type in `frontend/src/types/chat.ts`
- [ ] Implement `extractMediaFromMessage()` utility
- [ ] Add YouTube URL detection utility
- [ ] Extend `ChatMessage` meta types for thumbnails/video_results

### Phase 3: Media Components (Day 2-3)
- [ ] Create `MediaCard.tsx` (hero image/video)
- [ ] Create `VideoEmbed.tsx` (YouTube + direct video)
- [ ] Create `MediaGrid.tsx` (masonry layout)
- [ ] Create `media-grid.css` with all media styles
- [ ] Add lightbox/fullscreen for images (CSS-only or minimal JS)

### Phase 4: Message Component Refactor (Day 3-4)
- [ ] Refactor `Message.tsx` to use new media components
- [ ] Create `MessageContent.tsx` for content + media orchestration
- [ ] Remove all inline styles from `Message.tsx`
- [ ] Update `messages.css` for new message structure

### Phase 5: Backend Enhancements (Day 4-5)
- [ ] Extend `SearchResult` with `thumbnail` field
- [ ] Capture Brave/SearXNG thumbnails in search backends
- [ ] Add YouTube video detection in search results
- [ ] Add `video_results` to `SearchInfo`
- [ ] Batch OG preview endpoint (optional optimization)

### Phase 6: Integration & Polish (Day 5-6)
- [ ] Wire backend media data to frontend types
- [ ] Test all media combinations (hero + grid, video + images, etc.)
- [ ] Responsive testing (mobile, tablet, desktop)
- [ ] Accessibility audit
- [ ] Performance testing (large image sets)
- [ ] Cross-browser testing (Firefox, Chromium)

### Phase 7: Documentation & Cleanup (Day 6)
- [ ] Update AGENTS.md with new media patterns
- [ ] Document CSS token usage
- [ ] Remove dead code
- [ ] Run lint + tests

## Technical Decisions

### CSS Approach
- **Global CSS with design tokens** (current approach) - continue
- No CSS Modules for media components (shared across messages)
- Use `@layer` for cascade control if needed

### Masonry Implementation
- **CSS Grid + `grid-auto-flow: dense`** - modern, no JS layout calc
- Fallback: Flexbox wrap for older browsers (not needed - modern only)
- Column count via `@media` queries

### Video Embed Strategy
- **YouTube**: `youtube-nocookie.com/embed/{id}?rel=0&modestbranding=1`
- **Direct video**: `<video controls preload="metadata">` with poster
- Lazy-load iframes with `loading="lazy"` (supported in modern browsers)

### State Management
- Media extraction is pure function of message props - no additional state
- Lightbox state: local signal in `MediaGrid`/`MediaCard`
- No global store changes needed

## Testing Strategy

### Unit Tests
- `extractMediaFromMessage()` - all detection cases
- YouTube URL parser - all URL formats
- Media layout decision logic

### Component Tests
- `MediaCard` - renders image/video correctly
- `MediaGrid` - masonry layout at different counts
- `VideoEmbed` - YouTube iframe attributes

### E2E Tests (Playwright)
- Image search query → masonry grid renders
- YouTube search → video embed plays
- Single image response → hero card layout
- Mixed media → correct priority

### Visual Regression
- Screenshot tests for each layout variant
- Dark/light theme (if added later)

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Masonry layout shift on image load | Reserve space with `aspect-ratio` or placeholder |
| YouTube iframe blocks main thread | `loading="lazy"`, defer until visible |
| Large image sets cause memory pressure | Virtualize grid >20 items, aggressive lazy load |
| OG preview fetch fails silently | Graceful degradation - show link only |
| Color-mix browser support | Provide fallback hex values |
| Markdown + media interference | Isolate media rendering from markdown parser |

## Success Criteria

1. **Visual cohesion**: Assistant messages use teal-tinted dark surface matching brand
2. **Image search**: "Give me stills of Nicholas Cage" → 10+ images in masonry grid
3. **Hero layout**: Single strong image → full-width card at top of response
4. **Video embed**: "Show me a trailer for Dune" → YouTube embed plays inline
5. **News search**: Event search → preview cards with images in grid
6. **No inline styles**: All styling via CSS classes and design tokens
7. **Performance**: <100ms additional render time for media-heavy messages
8. **Accessibility**: All media keyboard navigable, screen reader friendly

## File Changes Summary

### New Files
- `frontend/src/components/chat/MediaCard.tsx`
- `frontend/src/components/chat/MediaGrid.tsx`
- `frontend/src/components/chat/VideoEmbed.tsx`
- `frontend/src/components/chat/MessageContent.tsx`
- `frontend/src/styles/media-grid.css`
- `frontend/src/styles/markdown.css`

### Modified Files
- `frontend/src/styles/variables.css` - new tokens, updated `--assistant-msg`
- `frontend/src/styles/messages.css` - major rewrite for new structure
- `frontend/src/styles/index.css` - import new CSS files
- `frontend/src/components/chat/Message.tsx` - refactor
- `frontend/src/components/chat/MessageList.tsx` - pass media props if needed
- `frontend/src/types/chat.ts` - extended types for media
- `assistant/backend/pipeline/search.py` - capture thumbnails, YouTube videos
- `assistant/backend/main.py` - extend SearchInfo response

### Deleted/Archived
- None (evolutionary change)
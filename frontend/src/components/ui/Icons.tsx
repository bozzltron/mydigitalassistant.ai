import type { JSX } from 'solid-js'

/**
 * Shared UI icons. Inline SVG only — AGENTS bans emoji as icons and external
 * icon fonts. Stroked with `currentColor` so an icon matches its text.
 * Sources: SVGRepo (the project's preferred icon source).
 */
export function Icon(props: { size?: number; class?: string; children: JSX.Element }) {
  return (
    <svg
      class={props.class}
      viewBox="0 0 24 24"
      width={props.size ?? 16}
      height={props.size ?? 16}
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      {props.children}
    </svg>
  )
}

export const CloseIcon = (props: { class?: string; size?: number }) => (
  <Icon class={props.class} size={props.size}>
    <line x1="18" y1="6" x2="6" y2="18" />
    <line x1="6" y1="6" x2="18" y2="18" />
  </Icon>
)

/** A small diamond, used as an inline "essential" marker. */
export const DiamondIcon = (props: { class?: string; size?: number }) => (
  <Icon class={props.class} size={props.size}>
    <path d="M12 2 22 12 12 22 2 12Z" />
  </Icon>
)

/** A file category, chosen from a MIME type or an extension. */
export type FileKind = 'text' | 'image' | 'code' | 'sheet' | 'doc' | 'calendar' | 'generic'

const SHEET = new Set(['csv', 'tsv', 'xls', 'xlsx'])
const DOC = new Set(['pdf', 'doc', 'docx', 'odt', 'ods', 'odp', 'rtf', 'eml', 'html', 'xml'])
const CALENDAR = new Set(['ics'])

/**
 * Classify a file by MIME type (FileGrid) or extension (FileViewer). Accepts
 * either, because the two callers have different data to hand.
 */
export function fileKind(typeOrExt: string | null | undefined): FileKind {
  const t = (typeOrExt || '').toLowerCase().trim()
  if (!t) return 'generic'
  if (t.startsWith('image/')) return 'image'
  if (t.startsWith('text/calendar') || CALENDAR.has(t)) return 'calendar'
  if (t.includes('spreadsheet') || t === 'application/vnd.ms-excel' || SHEET.has(t)) return 'sheet'
  if (t === 'application/json' || t === 'json') return 'code'
  if (t.includes('xml') || t.startsWith('text/html') || DOC.has(t)) return 'doc'
  if (t.startsWith('text/') || t === 'txt') return 'text'
  return 'generic'
}

export const FileIcon = (props: { kind: FileKind; class?: string; size?: number }) => (
  <Icon class={props.class} size={props.size}>
    {props.kind === 'image' ? (
      <>
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <circle cx="8.5" cy="8.5" r="1.5" />
        <path d="M21 15l-5-5L5 21" />
      </>
    ) : props.kind === 'code' ? (
      <>
        <polyline points="16 18 22 12 16 6" />
        <polyline points="8 6 2 12 8 18" />
      </>
    ) : props.kind === 'sheet' ? (
      <>
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <line x1="3" y1="9" x2="21" y2="9" />
        <line x1="3" y1="15" x2="21" y2="15" />
        <line x1="9" y1="3" x2="9" y2="21" />
      </>
    ) : props.kind === 'calendar' ? (
      <>
        <rect x="3" y="4" width="18" height="18" rx="2" />
        <line x1="16" y1="2" x2="16" y2="6" />
        <line x1="8" y1="2" x2="8" y2="6" />
        <line x1="3" y1="10" x2="21" y2="10" />
      </>
    ) : props.kind === 'text' ? (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="8" y1="13" x2="16" y2="13" />
        <line x1="8" y1="17" x2="16" y2="17" />
      </>
    ) : (
      <>
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
      </>
    )}
  </Icon>
)

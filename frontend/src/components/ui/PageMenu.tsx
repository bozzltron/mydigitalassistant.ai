import { createSignal, For, onCleanup, onMount, Show } from 'solid-js'
import type { JSX } from 'solid-js'
import { getSettings } from '../../services/api'
import styles from './PageMenu.module.css'
import {
  BrainIcon,
  ChatIcon,
  FolderIcon,
  GearIcon,
  MapIcon,
  MenuIcon,
  TaskIcon,
  TraceIcon,
  TrashIcon,
} from './TopBarIcons'

interface PageLink {
  id: string
  label: string
  href: string
  icon: (p: { class?: string }) => JSX.Element
}

/**
 * The one page menu — on **every** page, not just the chat.
 *
 * The other pages used to offer a single "Back to chat" link, so getting from Files to
 * Tasks meant a trip through the conversation. Two pages offered nothing at all.
 *
 * Trace, Settings and Trash appear only where their panels are (`chatControls`), so the
 * menu never offers an action that would do nothing on the page you are looking at. The
 * menu closes on Escape, on a click outside, and on navigation.
 */
export default function PageMenu(props: {
  /** Chat-only controls: the two right-docked panels and the trash. */
  chatControls?: boolean
  traceVisible?: boolean
  onTrace?: () => void
  onSettings?: () => void
  onTrash?: () => void
}) {
  const [open, setOpen] = createSignal(false)
  // Read here rather than passed in: this component is on every page, and only the chat
  // page has the TopBar's settings read to hand.
  const [mapsEnabled, setMapsEnabled] = createSignal(false)
  let ref: HTMLDivElement

  onMount(() => {
    const handleOutside = (e: MouseEvent) => {
      if (ref && !ref.contains(e.target as Node)) setOpen(false)
    }
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', handleOutside)
    document.addEventListener('keydown', handleKey)
    onCleanup(() => {
      document.removeEventListener('mousedown', handleOutside)
      document.removeEventListener('keydown', handleKey)
    })

    // The Maps link follows the same gate as the TopBar button: maps fetch a basemap
    // from OpenFreeMap, so the entry only appears when the user has turned them on.
    void getSettings()
      .then((s) => setMapsEnabled(s.maps_enabled))
      .catch(() => setMapsEnabled(false))
  })

  const pageLinks = (): PageLink[] => {
    const links: PageLink[] = []

    // Chat first, and only where it is somewhere to *go*: this component is the chat
    // page's own menu too, and offering "Chat" while you are in the chat is noise.
    //
    // Read from `window.location` rather than the router's `useLocation()` on purpose:
    // every page renders this, and requiring a Router context would mean wrapping every
    // page test in one. The read is safe because a route change remounts the page —
    // and therefore this component — so it is never stale.
    if (window.location.pathname !== '/') {
      links.push({ id: 'chat', label: 'Chat', href: '/', icon: ChatIcon })
    }

    links.push({ id: 'tasks', label: 'Tasks', href: '/tasks', icon: TaskIcon })
    links.push({ id: 'files', label: 'Files', href: '/files', icon: FolderIcon })
    if (mapsEnabled()) {
      links.push({ id: 'maps', label: 'Maps', href: '/maps', icon: MapIcon })
    }
    links.push({ id: 'brain', label: 'Brain', href: '/brain', icon: BrainIcon })
    return links
  }

  return (
    <div class={styles.pageMenu} ref={(el) => { ref = el }}>
      <button
        class="topbar-btn topbar-btn--icon"
        id="page-menu-toggle"
        title="Pages"
        aria-label="Pages"
        aria-haspopup="menu"
        aria-expanded={open()}
        onClick={() => setOpen((v) => !v)}
      >
        <MenuIcon />
      </button>
      <Show when={open()}>
        <div class={styles.pageMenuPanel} role="menu">
          <For each={pageLinks()}>
            {(link) => (
              <a
                class={styles.pageMenuItem}
                role="menuitem"
                href={link.href}
                onClick={() => setOpen(false)}
              >
                {link.icon({})}
                {link.label}
              </a>
            )}
          </For>

          <Show when={props.chatControls}>
            {/* Destinations are links and these three are buttons — that is the
                distinction, not drift: an `<a>` with no `href` is not focusable, cannot
                be activated with Space, and a screen reader announces "link" for
                something that opens a panel. `.pageMenuItem` styles both identically. */}
            <button
              class={styles.pageMenuItem}
              classList={{ 'is-active': props.traceVisible }}
              id="trace-toggle"
              role="menuitem"
              title="Trace"
              aria-label="Trace"
              aria-pressed={props.traceVisible}
              onClick={() => {
                props.onTrace?.()
                setOpen(false)
              }}
            >
              <TraceIcon />
              Trace
            </button>
            <button
              class={styles.pageMenuItem}
              id="settings-toggle"
              role="menuitem"
              title="Settings"
              aria-label="Settings"
              onClick={() => {
                props.onSettings?.()
                setOpen(false)
              }}
            >
              <GearIcon />
              Settings
            </button>
            <button
              class={styles.pageMenuItem}
              id="trash-toggle"
              role="menuitem"
              title="Trash"
              aria-label="Open trash can"
              onClick={() => {
                props.onTrash?.()
                setOpen(false)
              }}
            >
              <TrashIcon />
              Trash
            </button>
          </Show>
        </div>
      </Show>
    </div>
  )
}

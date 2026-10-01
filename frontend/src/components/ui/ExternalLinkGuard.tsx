import { createSignal, Show, onMount, onCleanup } from 'solid-js'
import { Modal } from './Modal'
import { hostnameOf } from '../../utils/media'
import { isExternalHttpUrl } from '../../utils/externalLink'
import styles from './ExternalLinkGuard.module.css'

/**
 * Confirms before an external link opens in a new tab.
 *
 * A single document-level capture listener intercepts every off-origin anchor,
 * including the ones markdown renders through `innerHTML` (which cannot carry a
 * per-link handler). Internal links and non-http schemes are left alone.
 */
export default function ExternalLinkGuard() {
  const [pending, setPending] = createSignal<string | null>(null)

  const handleClick = (e: MouseEvent) => {
    if (e.defaultPrevented || e.button !== 0) return
    const anchor = (e.target as Element | null)?.closest?.('a')
    if (!anchor) return
    // A `download` link is meant to save a file, not open a tab.
    if (anchor.hasAttribute('download')) return
    const href = anchor.getAttribute('href')
    if (!isExternalHttpUrl(href)) return
    e.preventDefault()
    setPending(new URL(href!, window.location.href).href)
  }

  onMount(() => document.addEventListener('click', handleClick, true))
  onCleanup(() => document.removeEventListener('click', handleClick, true))

  const confirmOpen = () => {
    const url = pending()
    setPending(null)
    if (url) window.open(url, '_blank', 'noopener,noreferrer')
  }

  return (
    <Show when={pending()}>
      <Modal isOpen={true} onClose={() => setPending(null)} title="Open external link?" size="small">
        <p>This link leaves your assistant and opens in a new tab:</p>
        <p class={styles.host}>{hostnameOf(pending())}</p>
        <p class={styles.url}>{pending()}</p>
        <div class="modal-actions">
          <button class="btn-secondary" onClick={() => setPending(null)}>
            Cancel
          </button>
          <button class="btn-primary" onClick={confirmOpen}>
            Open
          </button>
        </div>
      </Modal>
    </Show>
  )
}

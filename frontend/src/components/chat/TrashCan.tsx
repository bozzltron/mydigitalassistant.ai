import { createSignal, createEffect, onMount, For, Show } from 'solid-js'
import { Modal } from '../ui/Modal'
import { getDeletedSessions, restoreConversation } from '../../services/api'
import { user } from '../../state/user'
import type { DeletedSession } from '../../types/chat'

/**
 * Trash glyph, inlined so it inherits `currentColor` from the button text.
 * It used to be an `<img src="/trash.svg">`, whose baked-in black stroke CSS
 * cannot override -- so it never matched the label colour.
 */
function TrashIcon(props: { class?: string }) {
  return (
    <svg
      class={props.class}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
      aria-hidden="true"
    >
      <path d="M4 6H20M16 6L15.7294 5.18807C15.4671 4.40125 15.3359 4.00784 15.0927 3.71698C14.8779 3.46013 14.6021 3.26132 14.2905 3.13878C13.9376 3 13.523 3 12.6936 3H11.3064C10.477 3 10.0624 3 9.70951 3.13878C9.39792 3.26132 9.12208 3.46013 8.90729 3.71698C8.66405 4.00784 8.53292 4.40125 8.27064 5.18807L8 6M18 6V16.2C18 17.8802 18 18.7202 17.673 19.362C17.3854 19.9265 16.9265 20.3854 16.362 20.673C15.7202 21 14.8802 21 13.2 21H10.8C9.11984 21 8.27976 21 7.63803 20.673C7.07354 20.3854 6.6146 19.9265 6.32698 19.362C6 18.7202 6 17.8802 6 16.2V6M14 10V17M10 10V17" />
    </svg>
  )
}

export default function TrashCan() {
  const [isOpen, setIsOpen] = createSignal(false)
  const [deletedSessions, setDeletedSessions] = createSignal<DeletedSession[]>([])
  const [isLoading, setIsLoading] = createSignal(false)
  const [restoringId, setRestoringId] = createSignal<string | null>(null)

  const loadDeletedSessions = async () => {
    const u = user()
    if (!u) return
    
    setIsLoading(true)
    try {
      const sessions = await getDeletedSessions(u.id)
      setDeletedSessions(sessions)
    } catch (error) {
      console.error('Failed to load deleted sessions:', error)
    } finally {
      setIsLoading(false)
    }
  }

  // Listen for archive events to refresh trash can
  onMount(() => {
    const handleArchive = () => {
      if (isOpen()) {
        loadDeletedSessions()
      }
    }
    window.addEventListener('conversation-archived', handleArchive)
    return () => window.removeEventListener('conversation-archived', handleArchive)
  })

  createEffect(() => {
    if (isOpen()) {
      loadDeletedSessions()
    }
  })

  const handleRestore = async (sessionId: string) => {
    const u = user()
    if (!u) return
    
    setRestoringId(sessionId)
    try {
      await restoreConversation(sessionId, u.id)
      await loadDeletedSessions()
    } catch (error) {
      console.error('Failed to restore conversation:', error)
    } finally {
      setRestoringId(null)
    }
  }

  const handlePermanentDelete = async () => {
    // For now, we don't have a permanent delete endpoint
    // This would require a hard delete which we don't support
    alert('Permanent delete not implemented. Use the regular delete to move to trash.')
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Unknown date'
    try {
      return new Date(dateStr).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return dateStr
    }
  }

  const formatEpisodeCount = (count: number) => {
    if (count === 0) return 'empty'
    if (count === 1) return '1 message'
    return `${count} messages`
  }

  return (
    <>
<button
          class="btn-secondary trash-can-trigger"
          onClick={() => setIsOpen(true)}
          aria-label="Open trash can"
        >
          <TrashIcon class="trash-icon" />
          <span>Trash</span>
        </button>

      <Modal
        isOpen={isOpen()}
        onClose={() => setIsOpen(false)}
        title="Trash Can"
        size="large"
      >
        <div class="trash-can-modal">
          <Show when={isLoading()}>
            <div class="trash-loading">Loading trash can...</div>
          </Show>
          <Show when={!isLoading() && deletedSessions().length === 0}>
            <div class="trash-empty">
              <TrashIcon class="trash-empty-icon" />
              <p>Trash is empty</p>
              <p class="trash-empty-hint">Deleted conversations will appear here</p>
            </div>
          </Show>
          <Show when={!isLoading() && deletedSessions().length > 0}>
            <div class="trash-list">
              <For each={deletedSessions()}>
                {(session: DeletedSession) => (
                  <div class="trash-item">
                    <div class="trash-item-info">
                      <div class="trash-item-title">
                        {session.title || 'Untitled Conversation'}
                      </div>
                      <div class="trash-item-meta">
                        <span class="trash-item-date">Deleted: {formatDate(session.deleted_at)}</span>
                        <span class="trash-item-count">{formatEpisodeCount(session.episode_count)}</span>
                        {session.first_user_message && (
                          <span class="trash-item-preview">
                            {session.first_user_message.slice(0, 100)}
                            {session.first_user_message.length > 100 ? '...' : ''}
                          </span>
                        )}
                      </div>
                    </div>
<div class="trash-item-actions">
                      <button
                        class="btn-icon restore-btn"
                        onClick={() => handleRestore(session.id)}
                        disabled={restoringId() === session.id}
                        aria-label="Restore conversation"
>
                        {restoringId() === session.id ? '⏳' : (
              <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M3 7v6h6"/>
                <path d="M21 17a9 9 0 0 0-9-9 9 9 0 0 0-6 2.3L3 13"/>
              </svg>
            )}
                       </button>
<button
                        class="btn-icon permanent-delete-btn"
                        onClick={() => handlePermanentDelete()}
                        aria-label="Permanently delete"
>
                        <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
                       </button>
                     </div>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      </Modal>
    </>
  )
}
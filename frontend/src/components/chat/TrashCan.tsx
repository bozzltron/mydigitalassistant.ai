import { createSignal, createEffect, onMount, For, Show } from 'solid-js'
import { Modal } from '../ui/Modal'
import { getDeletedSessions, restoreConversation } from '../../services/api'
import { user } from '../../state/user'
import type { DeletedSession } from '../../types/chat'

export default function TrashCan() {
  const [isOpen, setIsOpen] = createSignal(false)
  const [deletedSessions, setDeletedSessions] = createSignal<DeletedSession[]>([])
  const [isLoading, setIsLoading] = createSignal(false)
  const [restoringId, setRestoringId] = createSignal<string | null>(null)
  const [permanentlyDeletingId, setPermanentlyDeletingId] = createSignal<string | null>(null)

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

  const handlePermanentDelete = async (sessionId: string) => {
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
          <img src="/trash.svg" alt="trash" class="trash-icon" />
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
              <img src="/trash.svg" alt="trash empty" class="trash-empty-icon" />
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
                        onClick={() => handlePermanentDelete(session.id)}
                        disabled={permanentlyDeletingId() === session.id}
                        aria-label="Permanently delete"
>
                        {permanentlyDeletingId() === session.id ? '⏳' : (
              <svg width="1em" height="1em" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="3 6 5 6 21 6"/>
                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
            )}
                       </button>
                     </div>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      </Modal>

      <style>{`
        .trash-can-trigger {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .trash-icon {
          flex-shrink: 0;
          width: 1em;
          height: 1em;
          vertical-align: middle;
          color: white;
        }
        .trash-icon path {
          stroke: currentColor !important;
        }
        .trash-icon [fill=none] {
          fill: currentColor !important;
        }
        .trash-can-modal {
          max-height: 70vh;
          overflow-y: auto;
        }
        .trash-loading {
          padding: 24px;
          text-align: center;
          color: var(--color-text-secondary);
        }
        .trash-empty {
          padding: 32px;
          text-align: center;
          color: var(--color-text-secondary);
        }
        .trash-empty-icon {
          width: 48px;
          height: 48px;
          margin: 0 auto 12px;
          opacity: 0.5;
        }
        .trash-empty-hint {
          font-size: 0.875rem;
          opacity: 0.7;
        }
        .trash-list {
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .trash-item {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          padding: 12px 16px;
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          border-radius: 8px;
          gap: 16px;
        }
        .trash-item-info {
          flex: 1;
          min-width: 0;
        }
        .trash-item-title {
          font-weight: 500;
          color: var(--color-text);
          margin-bottom: 4px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .trash-item-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 12px;
          font-size: 0.75rem;
          color: var(--color-text-secondary);
        }
        .trash-item-preview {
          max-width: 300px;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .trash-item-actions {
          display: flex;
          gap: 8px;
          flex-shrink: 0;
        }
.btn-icon {
          display: flex;
          align-items: center;
          justify-content: center;
          border: none;
          background: transparent;
          border-radius: 6px;
          color: white;
          transition: all 0.15s ease;
        }
        .btn-icon svg {
          width: 1em;
          height: 1em;
        }
        .btn-icon:hover:not(:disabled) {
          background: var(--color-background-hover);
          color: var(--color-text);
        }
        .btn-icon:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }
        .restore-btn:hover:not(:disabled) {
          background: var(--color-success-bg);
          color: var(--color-success);
        }
        .permanent-delete-btn:hover:not(:disabled) {
          background: var(--color-error-bg);
          color: var(--color-error);
        }
      `}</style>
    </>
  )
}

function formatDate(dateStr: string | null) {
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

function formatEpisodeCount(count: number) {
  if (count === 0) return 'empty'
  if (count === 1) return '1 message'
  return `${count} messages`
}
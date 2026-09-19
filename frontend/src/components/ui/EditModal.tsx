import { createSignal, Show, createEffect } from 'solid-js'

export interface EditModalProps {
  isOpen: boolean
  onClose: () => void
  onSave: (newTitle: string) => void
  sessionId: string
  currentTitle: string
}

export const EditModal = (props: EditModalProps) => {
  const [inputValue, setInputValue] = createSignal(props.currentTitle)
  const [isSaving, setIsSaving] = createSignal(false)

  // Sync inputValue when currentTitle changes (e.g., different conversation selected)
  createEffect(() => {
    setInputValue(props.currentTitle)
  })

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await props.onSave(inputValue())
    } catch (error) {
      console.error('Failed to save title:', error)
    } finally {
      setIsSaving(false)
      props.onClose()
    }
  }

  return (
    <Show when={props.isOpen}>
      <div class="modal-overlay" onClick={() => props.onClose()}>
        <div
          class={`modal medium ${isSaving() ? 'closing' : ''}`}
          onClick={(e) => e.stopPropagation()}
        >
          <div class="modal-header">
            <h3>Edit Conversation Name</h3>
            <button
              class="modal-close"
              onClick={() => {
                props.onClose()
              }}
              aria-label="Close modal"
            >
              ✕
            </button>
          </div>
          <div class="modal-content">
            <input
              type="text"
              value={inputValue()}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Conversation name"
              style={{
                width: '100%',
                padding: '8px 12px',
                fontSize: '0.875rem',
                border: '1px solid var(--color-border)',
                borderRadius: '4px',
                marginBottom: '12px',
                boxSizing: 'border-box',
              }}
              autoFocus
            />
            <div style={{display: 'flex', justifyContent: 'flex-end', gap: '8px'}}>
              <button
                onClick={handleSave}
                disabled={isSaving()}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.875rem',
                  background: 'var(--color-primary)',
                  color: 'white',
                  border: 'none',
                  borderRadius: '4px',
                  cursor: isSaving() ? 'not-allowed' : 'pointer',
                }}
              >
                {isSaving() ? 'Saving...' : 'Save'}
              </button>
              <button
                onClick={() => {
                  setInputValue(props.currentTitle)
                  props.onClose()
                }}
                style={{
                  padding: '6px 16px',
                  fontSize: '0.875rem',
                  background: 'transparent',
                  color: 'var(--color-text)',
                  border: '1px solid var(--color-border)',
                  borderRadius: '4px',
                  cursor: 'pointer',
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      </div>
    </Show>
  )
}
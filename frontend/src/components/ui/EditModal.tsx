import { createSignal, Show, createEffect } from 'solid-js'

export interface EditModalProps {
  isOpen: boolean
  onClose: () => void
  onSave: (newTitle: string) => void
  sessionId: string
  currentTitle: string
}

export const EditModal = (props: EditModalProps) => {
  // The seed is needed, not stale: createEffect below runs after the first
  // render, so without it the input would paint empty and only then take the
  // title. The effect is what keeps it in sync afterwards, so the prop is
  // already tracked and solid/reactivity has nothing to fix here.
  // eslint-disable-next-line solid/reactivity
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
            {/* Presentation comes from .modal-content input (components.css),
                which already set every property the former inline style did and
                added the :focus and ::placeholder states it lacked.
                .modal-field supplies the bottom gap. */}
            <input
              type="text"
              class="modal-field"
              value={inputValue()}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder="Conversation name"
              autoFocus
            />
            <div class="modal-actions">
              {/* Was an inline style object, including
                  `background: var(--color-primary)`. --color-primary is defined
                  in no stylesheet, so the declaration was invalid at
                  computed-value time and the button rendered color:white on no
                  background. .btn-primary's :disabled rule also covers the
                  dynamic `cursor: isSaving() ? ... : ...` ternary. */}
              <button
                class="btn-primary"
                onClick={handleSave}
                disabled={isSaving()}
              >
                {isSaving() ? 'Saving...' : 'Save'}
              </button>
              <button
                class="btn-secondary"
                onClick={() => {
                  setInputValue(props.currentTitle)
                  props.onClose()
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
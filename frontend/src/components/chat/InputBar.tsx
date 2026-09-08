import { createSignal, onCleanup } from 'solid-js'
import VoiceControls from './VoiceControls'
import { postChat } from '../../services/api'

interface InputBarProps {
  onSend: (message: string) => void
  isSending?: boolean
}

export default function InputBar(props: InputBarProps) {
  const [message, setMessage] = createSignal('')
  const [attachedFiles, setAttachedFiles] = createSignal<File[]>([])
  const [fileInputRef, setFileInputRef] = createSignal<HTMLInputElement | null>(null)

  const handleSubmit = async (e: Event) => {
    e.preventDefault()
    if (message().trim() && !props.isSending) {
      props.onSend(message())
      setMessage('')
      setAttachedFiles([])
    }
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as any)
    }
  }

  const handleFileSelect = () => {
    const input = fileInputRef()
    if (input) {
      input.click()
    }
  }

  const handleFileChange = (e: Event) => {
    const target = e.target as HTMLInputElement
    if (target.files && target.files.length > 0) {
      const newFiles = Array.from(target.files)
      setAttachedFiles(prev => [...prev, ...newFiles])
      // Clear input to allow selecting same file again
      target.value = ''
    }
  }

  const removeFile = (index: number) => {
    setAttachedFiles(prev => prev.filter((_, i) => i !== index))
  }

  onCleanup(() => {
    // Cleanup if needed
  })

  return (
    <div id="input-row">
      <textarea
        id="msg-input"
        rows={1}
        placeholder="Type a message..."
        value={message()}
        onInput={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      
      <div class="file-attach">
        <button 
          class="file-attach-btn btn-icon"
          title="Attach file"
          type="button"
          onClick={handleFileSelect}
        >
          <svg width="18" height="18" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 2C9.44772 2 9 2.44772 9 3V12H7V3C7 1.34315 8.34315 0 10 0C11.6569 0 13 1.34315 13 3V11C13 13.7614 10.7614 16 8 16C5.23858 16 3 13.7614 3 11V3.5H5V11C5 12.6569 6.34315 14 8 14C9.65685 14 11 12.6569 11 11V3C11 2.44772 10.5523 2 10 2Z"/>
          </svg>
        </button>
        
        <input
          type="file"
          ref={setFileInputRef}
          id="file-input"
          multiple
          accept=".txt,.csv,.json,.xml,.html,.ics"
          onChange={handleFileChange}
          style="display: none;"
        />
      </div>
      
      <div class="file-chips" id="file-chips-preview">
        <For each={attachedFiles()}>
          {(file, i) => (
            <div class="file-chip">
              <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                <path d="M14 2H8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V4zM14 2v8m-4 2v3M4 2h7.5"/>
              </svg>
              <span class="file-name">{file.name}</span>
              <button 
                type="button" 
                class="remove-file-btn"
                onClick={() => removeFile(i())}
              >
                ×
              </button>
            </div>
          )}
        </For>
      </div>
      
      <VoiceControls />
      
      <button 
        class="btn-primary" 
        id="send-btn"
        type="submit"
        disabled={!message().trim() || props.isSending}
      >
        {props.isSending ? 'Sending...' : 'Send'}
      </button>
    </div>
  )
}
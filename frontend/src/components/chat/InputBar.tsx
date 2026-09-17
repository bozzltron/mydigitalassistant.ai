import { createSignal, onCleanup, onMount, For } from 'solid-js'

interface InputBarProps {
  onSend: (message: string, attachedFiles?: File[]) => void
  isSending?: boolean
  onAttachFile?: () => void
  isDictating?: boolean
  onDictationStart?: () => void
  onDictationStop?: () => void
}

export default function InputBar(props: InputBarProps) {
  const [message, setMessage] = createSignal('')
  const [attachedFiles, setAttachedFiles] = createSignal<File[]>([])
  const [fileInputRef, setFileInputRef] = createSignal<HTMLInputElement | null>(null)
  const [textareaRef, setTextareaRef] = createSignal<HTMLTextAreaElement | null>(null)

  const handleDictationClick = () => {
    if (props.isDictating) {
      props.onDictationStop?.()
    } else {
      props.onDictationStart?.()
    }
  }

  const handleSubmit = (e: Event) => {
    e.preventDefault()
    if (message().trim() && !props.isSending) {
      props.onSend(message(), attachedFiles())
      setMessage('')
      setAttachedFiles([])
    }
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e)
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
      target.value = ''
    }
  }

  const removeFile = (index: number) => {
    setAttachedFiles(prev => prev.filter((_, i) => i !== index))
  }

  onMount(() => {
    const textarea = textareaRef() as HTMLTextAreaElement | null
    if (textarea) {
      textarea.focus()
      const handleResize = () => {
        const currentTextarea = textareaRef()
        if (currentTextarea) {
          currentTextarea.style.height = 'auto'
          currentTextarea.style.height = Math.min(currentTextarea.scrollHeight, 140) + 'px'
        }
      }
      textarea.addEventListener('input', handleResize)
      onCleanup(() => textarea.removeEventListener('input', handleResize))
    }
  })

  return (
    <>
      <textarea
        ref={setTextareaRef}
        id="msg-input"
        rows={1}
        placeholder="Type a message..."
        value={message()}
        onInput={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
      />
      
      <div class="file-attach">
        <button 
          type="button"
          class="file-attach-btn btn-icon"
          title="Attach file"
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
          data-testid="file-input"
          multiple
          accept=".txt,.csv,.json,.xml,.html,.ics"
          onChange={handleFileChange}
          style={{ display: 'none' }}
        />
      </div>
      
      <div class="file-chips">
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
                onClick={() => removeFile(i)}
              >
                ×
              </button>
            </div>
          )}
        </For>
</div>
        
        <button 
          type="button"
          class="mic-btn btn-icon"
          id="mic-btn"
          title={props.isDictating ? 'Stop dictation' : 'Dictate into message box'}
          onClick={handleDictationClick}
          classList={{
            recording: props.isDictating,
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
            <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
            <line x1="12" x2="12" y1="19" y2="22"/>
          </svg>
        </button>
        
       <button
         type="button"
         class="btn-primary"
         id="send-btn"
         disabled={!message().trim() || props.isSending}
         onClick={handleSubmit}
       >
         {props.isSending ? 'Sending...' : 'Send'}
       </button>
     </>
  )
}
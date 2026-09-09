import { createSignal, onCleanup, onMount } from 'solid-js'

interface InputBarProps {
  onSend: (message: string) => void
  isSending?: boolean
  onAttachFile?: () => void
  onDictationStart?: () => void
  onDictationStop?: () => void
  isDictating?: boolean
}

export default function InputBar(props: InputBarProps) {
  const [message, setMessage] = createSignal('')
  const [attachedFiles, setAttachedFiles] = createSignal<File[]>([])
  const [fileInputRef, setFileInputRef] = createSignal<HTMLInputElement | null>(null)
  const [textareaRef, setTextareaRef] = createSignal<HTMLTextAreaElement | null>(null)

  const handleSubmit = (e: Event) => {
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
        textarea.style.height = 'auto'
        textarea.style.height = Math.min(textarea.scrollHeight, 140) + 'px'
      }
      textarea.addEventListener('input', handleResize)
      onCleanup(() => textarea.removeEventListener('input', handleResize))
    }
  })

  return (
    <form onSubmit={handleSubmit} id="input-row" style={{ display: 'flex', gap: '0.65rem', alignItems: 'flex-end', width: '100%' }}>
      <textarea
        ref={setTextareaRef}
        id="msg-input"
        rows={1}
        placeholder="Type a message..."
        value={message()}
        onInput={(e) => setMessage(e.target.value)}
        onKeyDown={handleKeyDown}
        style={{
          flex: 1,
          background: 'var(--surface2)',
          border: '1px solid var(--border)',
          borderRadius: '10px',
          color: 'var(--text)',
          padding: '0.8rem 1rem',
          fontFamily: 'inherit',
          fontSize: '1rem',
          resize: 'none',
          minHeight: '48px',
          maxHeight: '140px',
          lineHeight: '1.6',
        }}
      />
      
      <div class="file-attach" style={{ position: 'relative', flexShrink: 0 }}>
        <button 
          type="button"
          class="file-attach-btn btn-icon"
          title="Attach file"
          onClick={handleFileSelect}
          style={{
            background: 'var(--surface2)',
            border: '1px solid var(--border)',
            color: 'var(--text)',
            borderRadius: '8px',
            padding: '0 0.75rem',
            fontSize: '0.9rem',
            cursor: 'pointer',
            transition: 'background 0.15s',
            height: '52px',
          }}
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
          style={{ display: 'none' }}
        />
      </div>
      
      <div class="file-chips" style={{ display: 'flex', gap: '0.4rem', marginLeft: '0.5rem', flexWrap: 'wrap' }}>
        {attachedFiles().map((file, i) => (
          <div class="file-chip" key={i} style={{
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: '4px',
            padding: '0.2rem 0.5rem',
            fontSize: '0.75rem',
            display: 'flex',
            alignItems: 'center',
            gap: '0.3rem',
            color: 'var(--text)',
          }}>
            <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M14 2H8a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V4zM14 2v8m-4 2v3M4 2h7.5"/>
            </svg>
            <span class="file-name" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '150px' }}>{file.name}</span>
            <button 
              type="button" 
              class="remove-file-btn"
              onClick={() => removeFile(i)}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--error)',
                cursor: 'pointer',
                fontSize: '1rem',
                lineHeight: 1,
                padding: 0,
                display: 'flex',
                alignItems: 'center',
              }}
            >
              ×
            </button>
          </div>
        ))}
      </div>
      
      <button 
        type="button"
        class="mic-btn btn-icon"
        id="mic-btn"
        title="Dictate into message box"
        onClick={() => {
          if (props.isDictating) {
            props.onDictationStop?.()
          } else {
            props.onDictationStart?.()
          }
        }}
        style={{
          background: props.isDictating ? 'var(--error)' : 'var(--surface2)',
          border: props.isDictating ? '1px solid var(--error)' : '1px solid var(--border)',
          color: props.isDictating ? '#fff' : 'var(--text)',
          borderRadius: '8px',
          padding: '0 0.75rem',
          fontSize: '0.9rem',
          cursor: 'pointer',
          transition: 'background 0.15s',
          height: '52px',
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"/>
          <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
          <line x1="12" x2="12" y1="19" y2="22"/>
        </svg>
      </button>
      
      <button 
        type="submit"
        class="btn-primary"
        id="send-btn"
        disabled={!message().trim() || props.isSending}
        style={{
          background: 'var(--accent)',
          border: 'none',
          color: '#fff',
          borderRadius: '8px',
          padding: '0 1.25rem',
          fontSize: '0.95rem',
          fontWeight: 500,
          cursor: props.isSending || !message().trim() ? 'not-allowed' : 'pointer',
          transition: 'opacity 0.15s',
          opacity: props.isSending || !message().trim() ? 0.4 : 1,
          height: '52px',
        }}
      >
        {props.isSending ? 'Sending...' : 'Send'}
      </button>
    </form>
  )
}
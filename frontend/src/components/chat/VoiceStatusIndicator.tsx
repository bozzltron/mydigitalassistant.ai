import { createSignal, createMemo, onMount, onCleanup, Show } from 'solid-js'
import { voice } from '../../state/voice'

interface VoiceStatusIndicatorProps {
  isVoiceMode: boolean
  isDictationMode: boolean
  isRecording: () => boolean
  onStopRecording: () => void
}

export default function VoiceStatusIndicator(props: VoiceStatusIndicatorProps) {
  const [showOverlay, setShowOverlay] = createSignal(false)

  const status = createMemo(() => {
    const v = voice()
    return {
      isListening: v.isListening,
      isProcessing: v.isProcessing,
      isSpeaking: v.isSpeaking,
      status: v.status,
    }
  })

  onMount(() => {
    const checkRecording = () => {
      if (props.isDictationMode && props.isRecording()) {
        setShowOverlay(true)
      } else {
        setShowOverlay(false)
      }
    }
    const interval = setInterval(checkRecording, 100)
    onCleanup(() => clearInterval(interval))
  })

  const getStatusText = () => {
    const s = status()
    if (props.isDictationMode) {
      if (s.isListening) return 'Dictating...'
      if (s.isProcessing) return 'Processing...'
      return 'Ready'
    }
    if (s.isListening) return 'Listening...'
    if (s.isProcessing) return 'Thinking...'
    if (s.isSpeaking) return 'Speaking...'
    return 'Idle'
  }

  const getDotClass = () => {
    const s = status()
    if (s.isProcessing) return 'voice-dot processing'
    if (s.isSpeaking) return 'voice-dot speaking'
    return 'voice-dot'
  }

  return (
    <Show when={props.isVoiceMode || props.isDictationMode}>
      <Show when={showOverlay()}>
        <div class="voice-status-overlay" style={{ 
          position: 'fixed', 
          bottom: '80px', 
          left: '50%', 
          transform: 'translateX(-50%)',
          zIndex: 100,
          background: 'var(--surface2)',
          border: '1px solid var(--border)',
          borderRadius: '6px',
          padding: '0.5rem 1rem',
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          fontSize: '0.8rem',
          color: 'var(--text-dim)',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          animation: 'fadeIn 0.2s ease-out'
        }}>
          <span class={getDotClass()} style={{ 
            width: '8px', 
            height: '8px', 
            borderRadius: '50%', 
            background: 'var(--accent)',
            animation: 'voice-pulse 1.2s ease-in-out infinite',
            flexShrink: 0
          }} />
          <span>{getStatusText()}</span>
          <button 
            class="voice-btn-small danger" 
            onClick={props.onStopRecording}
            style={{
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              color: 'var(--error)',
              borderRadius: '4px',
              padding: '0.15rem 0.5rem',
              fontSize: '0.75rem',
              cursor: 'pointer'
            }}
          >
            {props.isDictationMode ? 'Stop' : 'Cancel'}
          </button>
        </div>
      </Show>

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateX(-50%) translateY(10px); }
          to { opacity: 1; transform: translateX(-50%) translateY(0); }
        }
      `}</style>
    </Show>
  )
}
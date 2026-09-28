import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi } from 'vitest'
import { createSignal } from 'solid-js'
import VoiceControls from './VoiceControls'

// Records the config VoiceControls hands to the hook, then delegates to the real
// implementation so the other tests in this file exercise it unchanged.
let lastHookConfig: { onTranscription: (text: string) => void } | null = null
const hookConfig = () => lastHookConfig

vi.mock('../../hooks/useVoiceRecording', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../hooks/useVoiceRecording')>()
  return {
    ...actual,
    useVoiceRecording: (config: Parameters<typeof actual.useVoiceRecording>[0]) => {
      lastHookConfig = config as { onTranscription: (text: string) => void }
      return actual.useVoiceRecording(config)
    },
  }
})

describe('VoiceControls', () => {
  const defaultProps = {
    isRecording: false,
    onStartRecording: vi.fn(),
    onStopRecording: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
    lastHookConfig = null
  })

  it('renders mic button for dictation mode', () => {
    render(() => <VoiceControls isRecording={defaultProps.isRecording} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    expect(screen.getByRole('button', { name: /dictate into message box/i })).toBeInTheDocument()
  })

  it('calls onStartRecording when clicked and not recording', () => {
    render(() => <VoiceControls isRecording={defaultProps.isRecording} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    const btn = screen.getByRole('button', { name: /dictate into message box/i })
    fireEvent.click(btn)
    
    expect(defaultProps.onStartRecording).toHaveBeenCalled()
    expect(defaultProps.onStopRecording).not.toHaveBeenCalled()
  })

  it('calls onStopRecording when clicked and recording', () => {
    render(() => <VoiceControls isRecording={true} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    const btn = screen.getByRole('button', { name: /stop dictation/i })
    fireEvent.click(btn)
    
    expect(defaultProps.onStopRecording).toHaveBeenCalled()
    expect(defaultProps.onStartRecording).not.toHaveBeenCalled()
  })

  it('handles missing callbacks gracefully', () => {
    render(() => <VoiceControls isRecording={false} />)
    
    const btn = screen.getByRole('button', { name: /dictate into message box/i })
    expect(() => fireEvent.click(btn)).not.toThrow()
  })

  it('handles missing onStopRecording gracefully', () => {
    render(() => <VoiceControls isRecording={true} onStartRecording={vi.fn()} />)
    
    const btn = screen.getByRole('button', { name: /stop dictation/i })
    expect(() => fireEvent.click(btn)).not.toThrow()
  })

  // Regression: `const isVoiceMode = props.isOpen === true` read the prop once,
  // so the mode was fixed at mount. The button's accessible name reports which
  // mode it is in, which makes the freeze observable.
  it('switches from dictation to voice mode when isOpen becomes true', () => {
    const [isOpen, setIsOpen] = createSignal(false)
    render(() => (
      <VoiceControls
        isOpen={isOpen()}
        isRecording={false}
        onStartRecording={vi.fn()}
        onStopRecording={vi.fn()}
      />
    ))

    expect(screen.getByRole('button', { name: /dictate into message box/i })).toBeInTheDocument()

    setIsOpen(true)

    expect(screen.getByRole('button', { name: /voice conversation mode/i })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /dictate into message box/i })).toBeNull()
  })

  // Regression: the icon branch read a `recording` alias captured before
  // render, so toggling the prop did not swap the glyph.
  it('swaps the button icon when the isRecording prop flips', () => {
    const [isRecording, setIsRecording] = createSignal(false)
    const { container } = render(() => (
      <VoiceControls isRecording={isRecording()} onStartRecording={vi.fn()} onStopRecording={vi.fn()} />
    ))

    const pathD = () => container.querySelector('button svg path')?.getAttribute('d')
    const before = pathD()
    expect(container.querySelector('button')!.classList.contains('recording')).toBe(false)

    setIsRecording(true)

    expect(pathD()).not.toBe(before)
    expect(container.querySelector('button')!.classList.contains('recording')).toBe(true)
  })

  // Regression: onTranscription was passed as `props.onTranscription || noop`,
  // capturing whatever function existed at mount. A parent that swaps the
  // callback would keep calling the original, so a swapped handler went
  // unheard. Captures the config the component hands the hook, then checks the
  // captured function forwards to the *current* prop.
  it('forwards transcriptions to the current onTranscription, not the one captured at mount', () => {
    const first = vi.fn()
    const second = vi.fn()
    const [cb, setCb] = createSignal(first)
    render(() => (
      <VoiceControls
        isRecording={false}
        onTranscription={cb()}
        onStartRecording={vi.fn()}
        onStopRecording={vi.fn()}
      />
    ))

    setCb(() => second)

    const config = hookConfig()
    expect(config).not.toBeNull()
    config!.onTranscription('hello')
    expect(second).toHaveBeenCalledWith('hello')
    expect(first).not.toHaveBeenCalled()
  })
})
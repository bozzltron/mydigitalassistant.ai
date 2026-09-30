import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createSignal } from 'solid-js'
import InputBar from './InputBar'

describe('InputBar', () => {
  const defaultProps = {
    onSend: vi.fn(),
    isSending: false,
    onDictationStart: vi.fn(),
    onDictationStop: vi.fn(),
    isDictating: false,
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders textarea and send button', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    expect(screen.getByPlaceholderText('Type a message...')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /send/i })).toBeInTheDocument()
  })

  it('calls onSend on Enter key (no shift)', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Hello' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })
    
    expect(defaultProps.onSend).toHaveBeenCalledWith('Hello', [])
  })

  it('does not send on Shift+Enter', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Line 1' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })
    
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })

  it('clears input after send', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Test' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })
    
    expect(defaultProps.onSend).toHaveBeenCalledWith('Test', [])
    expect(textarea).toHaveValue('')
  })

  it('disables send button when empty', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const sendBtn = screen.getByRole('button', { name: /send/i })
    expect(sendBtn).toBeDisabled()
  })

  it('keeps the send button enabled while isSending so messages can be stacked', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={true} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Queued while busy' } })

    const sendBtn = screen.getByRole('button', { name: /queue/i })
    expect(sendBtn).not.toBeDisabled()
  })

  it('submits a message while isSending (it gets queued, not blocked)', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={true} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)

    const textarea = screen.getByPlaceholderText('Type a message...')
    fireEvent.input(textarea, { target: { value: 'Stack this' } })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false })

    expect(defaultProps.onSend).toHaveBeenCalledWith('Stack this', [])
    expect(textarea).toHaveValue('')
  })

  it('shows "Queue" text when isSending', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={true} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)

    expect(screen.getByRole('button', { name: /queue/i })).toHaveTextContent('Queue')
  })

  it('shows "Queue" text when isStreaming', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isStreaming={true} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)

    expect(screen.getByRole('button', { name: /queue/i })).toHaveTextContent('Queue')
  })

  it('opens file dialog when attach button clicked', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const attachBtn = screen.getByRole('button', { name: /attach file/i })
    const fileInput = screen.getByTestId('file-input') as HTMLInputElement
    const clickSpy = vi.spyOn(fileInput, 'click')
    
    fireEvent.click(attachBtn)
    
    expect(clickSpy).toHaveBeenCalled()
  })

  it('calls onDictationStart when mic clicked and not dictating', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const micBtn = screen.getByRole('button', { name: /dictate into message box/i })
    fireEvent.click(micBtn)
    
    expect(defaultProps.onDictationStart).toHaveBeenCalled()
  })

  it('calls onDictationStop when mic clicked and dictating', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={true} />)
    
    const micBtn = screen.getByRole('button', { name: /stop dictation/i })
    fireEvent.click(micBtn)
    
    expect(defaultProps.onDictationStop).toHaveBeenCalled()
  })

  it('shows mic button as red when dictating', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={true} />)
    
    const micBtn = screen.getByRole('button', { name: /stop dictation/i })
    expect(micBtn).toHaveClass('recording')
  })

  it('adds file chips when files attached', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)
    
    const fileInput = screen.getByTestId('file-input') as HTMLInputElement
    const file = new File(['content'], 'test.txt', { type: 'text/plain' })
    fireEvent.change(fileInput, { target: { files: [file] } })
    
    expect(screen.getByText('test.txt')).toBeInTheDocument()
  })

  it('removes the file chip when its remove button is clicked', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} />)

    const fileInput = screen.getByTestId('file-input') as HTMLInputElement
    const file = new File(['content'], 'test.txt', { type: 'text/plain' })
    fireEvent.change(fileInput, { target: { files: [file] } })

    expect(screen.getByText('test.txt')).toBeInTheDocument()

    fireEvent.click(screen.getByText('×'))

    // Regression: the handler used to receive the <For> index accessor itself
    // (`removeFile(i)`) instead of its value (`removeFile(i())`), so the filter
    // compared a number to a function and never removed anything.
    expect(screen.queryByText('test.txt')).not.toBeInTheDocument()
  })

  it('renders the Max intelligence toggle', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} onToggleMax={vi.fn()} />)
    
    expect(screen.getByRole('button', { name: /max intelligence/i })).toBeInTheDocument()
  })

  it('fires onToggleMax when the Max button is clicked', () => {
    const onToggleMax = vi.fn()
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} onToggleMax={onToggleMax} />)
    
    const maxBtn = screen.getByRole('button', { name: /max intelligence/i })
    fireEvent.click(maxBtn)
    
    expect(onToggleMax).toHaveBeenCalled()
  })

  it('marks the Max button active when maxEnabled', () => {
    render(() => <InputBar onSend={defaultProps.onSend} isSending={defaultProps.isSending} onDictationStart={defaultProps.onDictationStart} onDictationStop={defaultProps.onDictationStop} isDictating={defaultProps.isDictating} maxEnabled={true} onToggleMax={vi.fn()} />)
    
    const maxBtn = screen.getByRole('button', { name: /max intelligence/i })
    expect(maxBtn).toHaveClass('active')
  })

  it('drops dictated text into the box without sending it (regression: dictation auto-sent and started conversation mode)', () => {
    const [seq, setSeq] = createSignal(0)
    render(() => (
      <InputBar
        onSend={defaultProps.onSend}
        dictatedText="hello from voice"
        dictatedSeq={seq()}
      />
    ))

    setSeq(1)

    expect(screen.getByPlaceholderText('Type a message...')).toHaveValue('hello from voice')
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })
})
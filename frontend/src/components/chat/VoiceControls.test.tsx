import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi } from 'vitest'
import VoiceControls from './VoiceControls'

describe('VoiceControls', () => {
  const defaultProps = {
    isRecording: false,
    onStartRecording: vi.fn(),
    onStopRecording: vi.fn(),
  }

  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders mic button', () => {
    render(() => <VoiceControls isRecording={defaultProps.isRecording} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    expect(screen.getByRole('button', { name: /dictate/i })).toBeInTheDocument()
  })

  it('calls onStartRecording when clicked and not recording', () => {
    render(() => <VoiceControls isRecording={defaultProps.isRecording} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    const btn = screen.getByRole('button', { name: /dictate/i })
    fireEvent.click(btn)
    
    expect(defaultProps.onStartRecording).toHaveBeenCalled()
    expect(defaultProps.onStopRecording).not.toHaveBeenCalled()
  })

  it('calls onStopRecording when clicked and recording', () => {
    render(() => <VoiceControls isRecording={true} onStartRecording={defaultProps.onStartRecording} onStopRecording={defaultProps.onStopRecording} />)
    
    const btn = screen.getByRole('button', { name: /dictate/i })
    fireEvent.click(btn)
    
    expect(defaultProps.onStopRecording).toHaveBeenCalled()
    expect(defaultProps.onStartRecording).not.toHaveBeenCalled()
  })

  it('handles missing callbacks gracefully', () => {
    render(() => <VoiceControls isRecording={false} />)
    
    const btn = screen.getByRole('button', { name: /dictate/i })
    expect(() => fireEvent.click(btn)).not.toThrow()
  })

  it('handles missing onStopRecording gracefully', () => {
    render(() => <VoiceControls isRecording={true} onStartRecording={vi.fn()} />)
    
    const btn = screen.getByRole('button', { name: /dictate/i })
    expect(() => fireEvent.click(btn)).not.toThrow()
  })
})
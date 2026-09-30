import { render, screen } from '@solidjs/testing-library'
import { describe, it, expect, vi } from 'vitest'

// Force MessageContent to throw during render so the boundary is exercised.
vi.mock('./MessageContent', () => ({
  default: () => {
    throw new Error('render boom')
  },
}))

import Message from './Message'

describe('Message error boundary', () => {
  it('shows a per-message fallback instead of taking down the transcript', () => {
    render(() => (
      <Message
        message={{ id: 'm1', role: 'assistant', content: 'hi' }}
        onReact={vi.fn()}
        onCopy={vi.fn()}
        onCorrect={vi.fn()}
      />
    ))

    expect(screen.getByText(/could not be rendered/i)).toBeInTheDocument()
  })
})

describe('Message confidence', () => {
  it('renders a confidence chip from meta', () => {
    render(() => (
      <Message
        message={{
          id: 'm1',
          role: 'assistant',
          content: 'hi',
          meta: { confidence: 0.82, confidence_basis: 'memory' },
        }}
        onReact={vi.fn()}
        onCopy={vi.fn()}
        onCorrect={vi.fn()}
      />
    ))

    expect(screen.getByText(/Confidence 82%/)).toBeInTheDocument()
  })
})

import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TopBar from './TopBar'
import { setUser } from '../../state/user'

const mockConversations = [
  { id: 'conv-1', title: 'First Conversation', episode_count: 5, last_activity: '2024-01-01', created_at: '2024-01-01' },
  { id: 'conv-2', title: 'Second Conversation', episode_count: 3, last_activity: '2024-01-02', created_at: '2024-01-02' },
]

describe('TopBar', () => {
  const defaultProps = {
    conversations: mockConversations,
    activeConversation: mockConversations[0],
    onConversationChange: vi.fn(),
    onNewConversationClick: vi.fn(),
    isLoading: false,
    assistantName: 'Test Assistant',
  }

  beforeEach(() => {
    vi.clearAllMocks()
    setUser({ id: 1, name: 'Assistant User' })
    localStorage.clear()
  })

  it('renders assistant name instead of "Cognitive Assistant"', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByText('Test Assistant')).toBeInTheDocument()
    expect(screen.queryByText('Cognitive Assistant')).not.toBeInTheDocument()
  })

  it('renders conversation trigger with active conversation title', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    expect(trigger).toBeInTheDocument()
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox')
  })

  it('opens dropdown when trigger is clicked', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    fireEvent.click(trigger)
    expect(screen.getByText('Second Conversation')).toBeInTheDocument()
    expect(screen.getByText('New')).toBeInTheDocument() // New button in dropdown
  })

  it('calls onConversationChange when conversation item clicked', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    fireEvent.click(trigger)
    const secondConv = screen.getByText('Second Conversation')
    fireEvent.click(secondConv)
    expect(defaultProps.onConversationChange).toHaveBeenCalledWith('conv-2')
  })

  it('calls onNewConversationClick when New button clicked', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const newBtn = screen.getByRole('button', { name: /new/i })
    fireEvent.click(newBtn)
    expect(defaultProps.onNewConversationClick).toHaveBeenCalled()
  })

  it('disables trigger when loading', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={true} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    expect(trigger).toBeDisabled()
  })

  it('does not show user name badge', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.queryByText('Assistant User')).not.toBeInTheDocument()
  })

  it('renders voice mode button', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('button', { name: /let's talk/i })).toBeInTheDocument()
  })

  it('renders settings button', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('button', { name: /settings/i })).toBeInTheDocument()
  })

  it('renders Brain link', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('link', { name: /brain/i })).toBeInTheDocument()
  })

  it('persists selection to localStorage', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    fireEvent.click(trigger)
    const secondConv = screen.getByText('Second Conversation')
    fireEvent.click(secondConv)
    expect(localStorage.getItem('session_id')).toBe('conv-2')
  })

  it('orders the right side Alerts, Let\'s talk, Files, Brain, Settings, Trash', () => {
    render(() => <TopBar {...defaultProps} />)
    const labels = Array.from(
      document.querySelectorAll('.header-right .topbar-btn')
    ).map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim())

    expect(labels).toHaveLength(6)
    expect(labels[0]).toMatch(/for you/i)
    expect(labels[1]).toMatch(/let's talk/i)
    expect(labels[2]).toBe('Files')
    expect(labels[3]).toBe('Brain')
    expect(labels[4]).toBe('Settings')
    expect(labels[5]).toMatch(/trash/i)
  })

  it('gives New a plus icon and every right-side control an svg icon', () => {
    render(() => <TopBar {...defaultProps} />)

    const newBtn = document.getElementById('new-conversation-btn')!
    expect(newBtn.classList.contains('topbar-btn')).toBe(true)
    expect(newBtn.querySelector('svg')).not.toBeNull()

    const controls = document.querySelectorAll('.header-right .topbar-btn')
    expect(controls).toHaveLength(6)
    controls.forEach((el) => expect(el.querySelector('svg')).not.toBeNull())
  })
})
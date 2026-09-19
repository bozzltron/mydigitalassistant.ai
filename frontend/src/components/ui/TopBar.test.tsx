import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TopBar from './TopBar'
import { setUser } from '../../state/user'

const mockConversations = [
  { id: 'conv-1', title: 'First Conversation', createdAt: '2024-01-01', updatedAt: '2024-01-01', episode_count: 5, last_message: 'Hello' },
  { id: 'conv-2', title: 'Second Conversation', createdAt: '2024-01-02', updatedAt: '2024-01-02', episode_count: 3, last_message: 'World' },
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

  it('shows user name in badge', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByText('Assistant User')).toBeInTheDocument()
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
})
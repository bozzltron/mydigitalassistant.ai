import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import TopBar from './TopBar'
import { user, setUser } from '../../state/user'

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
  })

  it('renders assistant name instead of "Cognitive Assistant"', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByText('Test Assistant')).toBeInTheDocument()
    expect(screen.queryByText('Cognitive Assistant')).not.toBeInTheDocument()
  })

  it('renders conversation dropdown with conversations', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const select = screen.getByTestId('conversation-select')
    expect(select).toBeInTheDocument()
    expect(screen.getByText('First Conversation')).toBeInTheDocument()
    expect(screen.getByText('Second Conversation')).toBeInTheDocument()
  })

  it('selects active conversation in dropdown', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const select = screen.getByTestId('conversation-select') as HTMLSelectElement
    expect(select.value).toBe('conv-1')
  })

  it('calls onConversationChange when selection changes', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const select = screen.getByTestId('conversation-select')
    fireEvent.change(select, { target: { value: 'conv-2' } })
    expect(defaultProps.onConversationChange).toHaveBeenCalledWith('conv-2')
  })

  it('calls onNewConversationClick when New button clicked', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const newBtn = screen.getByRole('button', { name: /new/i })
    fireEvent.click(newBtn)
    expect(defaultProps.onNewConversationClick).toHaveBeenCalled()
  })

  it('disables dropdown when loading', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={true} assistantName={defaultProps.assistantName} />)
    const select = screen.getByTestId('conversation-select')
    expect(select).toBeDisabled()
  })

  it('shows user name in badge', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByText('Assistant User')).toBeInTheDocument()
  })

  it('renders voice mode button', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('button', { name: /voice/i })).toBeInTheDocument()
  })

  it('renders settings button', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('button', { name: /settings/i })).toBeInTheDocument()
  })

  it('renders Brain link', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    expect(screen.getByRole('link', { name: /brain/i })).toBeInTheDocument()
  })
})
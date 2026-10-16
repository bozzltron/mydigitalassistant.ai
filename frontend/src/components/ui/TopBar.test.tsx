import { render, screen, fireEvent, waitFor } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
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

  it('reaches Settings and Trash from the menu', () => {
    render(() => <TopBar {...defaultProps} />)
    fireEvent.click(screen.getByRole('button', { name: /pages/i }))
    expect(screen.getByRole('menuitem', { name: /settings/i })).toBeInTheDocument()
    expect(screen.getByRole('menuitem', { name: /open trash can/i })).toBeInTheDocument()
  })

  it('reaches every page from the menu', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)

    // The page links are in the menu now, so they are not in the row by default.
    expect(screen.queryByRole('menuitem', { name: /brain/i })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /pages/i }))

    expect(screen.getByRole('menuitem', { name: /tasks/i })).toHaveAttribute('href', '/tasks')
    expect(screen.getByRole('menuitem', { name: /files/i })).toHaveAttribute('href', '/files')
    expect(screen.getByRole('menuitem', { name: /brain/i })).toHaveAttribute('href', '/brain')
  })

  it('lists every item flat — no wrapper, no divider between them', () => {
    render(() => <TopBar {...defaultProps} />)
    fireEvent.click(screen.getByRole('button', { name: /pages/i }))

    const menu = screen.getByRole('menu')
    const items = screen.getAllByRole('menuitem')
    expect(items.length).toBeGreaterThan(4)
    // Every item is a direct child of the menu: no grouping divs, no `<hr>`.
    for (const item of items) {
      expect(item.parentElement).toBe(menu)
    }
    expect(menu.querySelector('hr')).toBeNull()
  })

  it('closes the menu on Escape and on a click outside', async () => {
    render(() => <TopBar {...defaultProps} />)
    const toggle = screen.getByRole('button', { name: /pages/i })

    fireEvent.click(toggle)
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())

    fireEvent.click(toggle)
    expect(screen.getByRole('menu')).toBeInTheDocument()
    fireEvent.mouseDown(document.body)
    await waitFor(() => expect(screen.queryByRole('menu')).not.toBeInTheDocument())
  })

  it('persists selection to localStorage', () => {
    render(() => <TopBar conversations={defaultProps.conversations} activeConversation={defaultProps.activeConversation} onConversationChange={defaultProps.onConversationChange} onNewConversationClick={defaultProps.onNewConversationClick} isLoading={defaultProps.isLoading} assistantName={defaultProps.assistantName} />)
    const trigger = screen.getByRole('button', { name: /first conversation/i })
    fireEvent.click(trigger)
    const secondConv = screen.getByText('Second Conversation')
    fireEvent.click(secondConv)
    expect(localStorage.getItem('session_id')).toBe('conv-2')
  })

  it('shows the stored voice in the select after voices load asynchronously', async () => {
    const { updateSetting } = await import('../../state/settings')
    updateSetting('voiceUri', 'uri-a')
    vi.mocked(window.speechSynthesis.getVoices).mockReturnValue([
      { name: 'Voice A', lang: 'en-US', localService: true, voiceURI: 'uri-a' } as SpeechSynthesisVoice,
      { name: 'Voice B', lang: 'en-US', localService: true, voiceURI: 'uri-b' } as SpeechSynthesisVoice,
    ])

    render(() => <TopBar {...defaultProps} />)

    await vi.waitFor(() => {
      const select = document.getElementById('voice-select') as HTMLSelectElement
      expect(select.value).toBe('uri-a')
    })
  })

  it('persists the read-aloud toggle to localStorage', async () => {
    const { settings } = await import('../../state/settings')
    render(() => <TopBar {...defaultProps} />)

    const toggle = document.getElementById('tts-enabled') as HTMLInputElement
    fireEvent.click(toggle)

    expect(settings.ttsEnabled).toBe(false)
    const saved = JSON.parse(localStorage.getItem('assistant_settings')!) as { ttsEnabled: boolean }
    expect(saved.ttsEnabled).toBe(false)
  })

  it('persists a changed voice selection and speed to localStorage', async () => {
    vi.mocked(window.speechSynthesis.getVoices).mockReturnValue([
      { name: 'Voice A', lang: 'en-US', localService: true, voiceURI: 'uri-a' } as SpeechSynthesisVoice,
      { name: 'Voice B', lang: 'en-US', localService: true, voiceURI: 'uri-b' } as SpeechSynthesisVoice,
    ])

    render(() => <TopBar {...defaultProps} />)

    const select = document.getElementById('voice-select') as HTMLSelectElement
    await vi.waitFor(() => expect(select.options.length).toBeGreaterThan(1))
    fireEvent.change(select, { target: { value: 'uri-b' } })

    const speed = document.getElementById('voice-speed') as HTMLInputElement
    fireEvent.change(speed, { target: { value: '1.6' } })

    const saved = JSON.parse(localStorage.getItem('assistant_settings')!) as {
      voiceUri: string
      voiceSpeed: number
    }
    expect(saved.voiceUri).toBe('uri-b')
    expect(saved.voiceSpeed).toBe(1.6)
  })

  it('orders the right side Alerts, Let\'s talk, Pages', () => {
    render(() => <TopBar {...defaultProps} />)
    const labels = Array.from(
      document.querySelectorAll('.header-right .topbar-btn')
    ).map((el) => (el.getAttribute('aria-label') || el.textContent || '').trim())

    // The row is down to three: every page *and* the trace/settings/trash controls
    // are in the menu now. The row does not wrap (`header-right` is a single flex
    // line), so it had run out of width rather than of lines.
    expect(labels).toHaveLength(3)
    expect(labels[0]).toMatch(/for you/i)
    expect(labels[1]).toMatch(/let's talk/i)
    expect(labels[2]).toBe('Pages')
  })

  it('gives New a plus icon and every right-side control an svg icon', () => {
    render(() => <TopBar {...defaultProps} />)

    const newBtn = document.getElementById('new-conversation-btn')!
    expect(newBtn.classList.contains('topbar-btn')).toBe(true)
    expect(newBtn.querySelector('svg')).not.toBeNull()

    const controls = document.querySelectorAll('.header-right .topbar-btn')
    expect(controls).toHaveLength(3)
    controls.forEach((el) => expect(el.querySelector('svg')).not.toBeNull())
  })

  it('keeps the trace and settings panels mutually exclusive', async () => {
    // Both dock on the right, so opening one must close the other. They are menu
    // items now, so each click opens the menu first.
    const { settings, updateSetting } = await import('../../state/settings')
    updateSetting('traceVisible', false)
    render(() => <TopBar {...defaultProps} />)

    const openMenu = () => fireEvent.click(screen.getByRole('button', { name: /pages/i }))

    openMenu()
    fireEvent.click(document.getElementById('settings-toggle')!)
    expect(
      document.getElementById('settings-panel')?.classList.contains('open')
    ).toBe(true)

    openMenu()
    fireEvent.click(document.getElementById('trace-toggle')!)
    expect(
      document.getElementById('settings-panel')?.classList.contains('open')
    ).toBe(false)
    expect(settings.traceVisible).toBe(true)

    openMenu()
    fireEvent.click(document.getElementById('settings-toggle')!)
    expect(settings.traceVisible).toBe(false)
  })
})

describe('the page menu item is one look, links and buttons alike', () => {
  // The menu holds `<a>` links and `<button>` toggles, and nothing in the stylesheet
  // styles a bare `button` — so Trace, Settings and Trash kept the UA's `buttonface`
  // background, `outset` border and system font, and read as three separate white chips
  // inside an otherwise flat menu. jsdom computes no layout, so this reads the CSS.
  const css = readFileSync(join(__dirname, 'PageMenu.module.css'), 'utf8')
  const rule = css.match(/\.pageMenuItem\s*\{[^}]*\}/)?.[0] ?? ''

  it('resets the UA button box', () => {
    expect(rule).not.toBe('')
    expect(rule).toMatch(/background:\s*none/)
    expect(rule).toMatch(/border:\s*none/)
    expect(rule).toMatch(/font:\s*inherit/)
  })

  it('sets font-size after the font shorthand, which would otherwise reset it', () => {
    expect(rule).toMatch(/font-size/)
    expect(rule.indexOf('font: inherit')).toBeLessThan(rule.indexOf('font-size'))
  })
})
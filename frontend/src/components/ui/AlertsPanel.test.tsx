import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve as pathResolve } from 'node:path'
import { render, fireEvent, screen, waitFor } from '@solidjs/testing-library'
import { AlertsPanel } from './AlertsPanel'
import { setUser } from '../../state/user'
import {
  getAlerts,
  getAlertConversationOptions,
  openAlertConversation,
  markAllAlertsAsRead,
  type Alert,
} from '../../services/api'

vi.mock('../../services/api')

/**
 * Real timers throughout. The component polls on a 30s interval, and the previous
 * version of this file stubbed that away with fake timers — which then stalled the
 * promise queue every interaction test depends on. Stubbing `window.setInterval`
 * is enough to keep the poll from firing during a test, and it leaves the event
 * loop alone so `await` works normally.
 */
describe('AlertsPanel', () => {
  const mockAlerts: Alert[] = [
    { id: 1, user_id: 1, type: 'task_alert', title: 'Test Alert', message: 'Test message', source_episode_id: null, severity: 'important', created_at: '2024-01-01T00:00:00Z',},
  ]

  let intervalCalls: Array<[() => void, number]>

  beforeEach(() => {
    vi.clearAllMocks()
    setUser({ id: 1, name: 'Test User' })
    vi.mocked(getAlerts).mockResolvedValue({ alerts: mockAlerts, unread_count: 1 })
    vi.mocked(markAllAlertsAsRead).mockResolvedValue({ status: 'ok' })

    intervalCalls = []
    vi.spyOn(window, 'setInterval').mockImplementation(
      (callback: () => void, delay: number) => {
        intervalCalls.push([callback, delay])
        return 123 as unknown as number
      }
    )
    vi.spyOn(window, 'clearInterval').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fetches alerts on mount', async () => {
    render(() => <AlertsPanel />)
    await waitFor(() => expect(getAlerts).toHaveBeenCalledWith(1, 200))
  })

  it('polls at the expected interval and clears it on unmount', async () => {
    const { unmount } = render(() => <AlertsPanel />)

    // onMount runs during render, so the interval is registered synchronously.
    expect(intervalCalls.length).toBe(1)
    expect(intervalCalls[0][1]).toBe(30000)

    unmount()
    expect(window.clearInterval).toHaveBeenCalledWith(123)
  })

  it('does not fetch if no user', async () => {
    setUser(null)
    render(() => <AlertsPanel />)
    // One macrotask turn: if the fetch were going to happen it would have by now.
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAlerts).not.toHaveBeenCalled()
  })

  it('fetches once the user becomes available after mount', async () => {
    // Regression: the fetch ran once on mount, before `user()` was set, and
    // no-opped — the bell stayed empty until the first 30s tick.
    setUser(null)
    render(() => <AlertsPanel />)
    await new Promise((resolve) => setTimeout(resolve, 0))
    expect(getAlerts).not.toHaveBeenCalled()

    setUser({ id: 1, name: 'Test User' })
    await waitFor(() => expect(getAlerts).toHaveBeenCalledWith(1, 200))
  })

  it('does not flash the loading placeholder on a background poll', async () => {
    // Regression: every 30s poll set isLoading, swapping the list for "Loading
    // alerts..." — the flicker that read as alerts appearing and disappearing.
    render(() => <AlertsPanel />)
    fireEvent.click(await screen.findByText('For You'))
    expect(await screen.findByText('Test Alert')).toBeTruthy()

    intervalCalls[0][0]() // fire the registered poll
    await waitFor(() => expect(getAlerts).toHaveBeenCalledTimes(2))

    expect(screen.queryByText('Loading alerts...')).toBeNull()
    expect(screen.getByText('Test Alert')).toBeTruthy()
  })

  it('shows the count of waiting alerts on the trigger', async () => {
    render(() => <AlertsPanel />)
    expect(await screen.findByText('1')).toBeTruthy()
  })

  describe('resolving by conversation', () => {
    beforeEach(() => {
      vi.mocked(getAlertConversationOptions).mockResolvedValue({
        alert_id: 1,
        conversations: [
          {
            session_id: 'conv_recent',
            name: 'Friend Music Records',
            last_activity: '2026-10-01T02:00:00Z',
            message_count: 42,
          },
        ],
      })
      vi.mocked(openAlertConversation).mockResolvedValue({
        status: 'ok',
        alert_id: 1,
        session_id: 'conv_recent',
        episode_id: null,
        seeded: false,
      })
    })

    it('offers conversations to resolve into', async () => {
      render(() => <AlertsPanel />)
      fireEvent.click(await screen.findByText('For You'))
      fireEvent.click(await screen.findByText('Resolve…'))

      await waitFor(() => expect(getAlertConversationOptions).toHaveBeenCalledWith(1, 1))
      expect(await screen.findByText('Friend Music Records')).toBeTruthy()
      // The escape hatch exists but reads as the exception.
      expect(screen.getByText('New conversation')).toBeTruthy()
    })

    it('attaches the alert to the chosen conversation, not a new one', async () => {
      render(() => <AlertsPanel />)
      fireEvent.click(await screen.findByText('For You'))
      fireEvent.click(await screen.findByText('Resolve…'))
      fireEvent.click(await screen.findByText('Friend Music Records'))

      await waitFor(() =>
        expect(openAlertConversation).toHaveBeenCalledWith(1, 1, 'conv_recent')
      )
    })

    it('removes the alert from the list once attached', async () => {
      render(() => <AlertsPanel />)
      fireEvent.click(await screen.findByText('For You'))
      fireEvent.click(await screen.findByText('Resolve…'))
      fireEvent.click(await screen.findByText('Friend Music Records'))

      // The list is a view of the open set, and this alert has left it.
      await waitFor(() => expect(screen.queryByText('Test Alert')).toBeNull())
    })

    it('starts a new conversation only when explicitly asked', async () => {
      vi.mocked(openAlertConversation).mockResolvedValue({
        status: 'ok',
        alert_id: 1,
        session_id: 'conv_alert_1',
        episode_id: 9,
        seeded: true,
      })
      render(() => <AlertsPanel />)
      fireEvent.click(await screen.findByText('For You'))
      fireEvent.click(await screen.findByText('Resolve…'))
      fireEvent.click(await screen.findByText('New conversation'))

      await waitFor(() =>
        expect(openAlertConversation).toHaveBeenCalledWith(1, 1, undefined)
      )
    })

    it('broadcasts the chosen conversation so the chat can open it', async () => {
      const heard: Array<{ sessionId: string; seeded: boolean }> = []
      const listener = (e: Event) => heard.push((e as CustomEvent).detail)
      window.addEventListener('open-conversation', listener)
      try {
        render(() => <AlertsPanel />)
        fireEvent.click(await screen.findByText('For You'))
        fireEvent.click(await screen.findByText('Resolve…'))
        fireEvent.click(await screen.findByText('Friend Music Records'))

        await waitFor(() =>
          expect(heard).toEqual([{ sessionId: 'conv_recent', seeded: false }])
        )
      } finally {
        window.removeEventListener('open-conversation', listener)
      }
    })

    it('offers a new thread when the user has no other conversations', async () => {
      vi.mocked(getAlertConversationOptions).mockResolvedValue({
        alert_id: 1,
        conversations: [],
      })
      render(() => <AlertsPanel />)
      fireEvent.click(await screen.findByText('For You'))
      fireEvent.click(await screen.findByText('Resolve…'))

      await waitFor(() =>
        expect(screen.getByText(/will start one/)).toBeTruthy()
      )
    })
  })

  it('resolves everything on request', async () => {
    render(() => <AlertsPanel />)
    fireEvent.click(await screen.findByText('For You'))
    fireEvent.click(await screen.findByText('Resolve all'))

    await waitFor(() => expect(markAllAlertsAsRead).toHaveBeenCalledWith(1))
  })
})

describe('severity colour means severity only', () => {
  it('does not paint info-severity alerts with the accent colour', () => {
    // The regression: `--accent` is the mint brand colour, so an `info` stripe
    // rendered green — and green read as "resolved". The stripe must mean
    // severity and nothing else, so `info` is neutral.
    const css = readFileSync(
      pathResolve(process.cwd(), 'src/components/ui/AlertsPanel.module.css'),
      'utf8',
    )
    const infoRule = css.match(/\.alertItem:global\(\.alert-info\)\s*\{[^}]*\}/)
    expect(infoRule, 'no .alert-info rule found').toBeTruthy()
    expect(infoRule![0]).not.toContain('--accent')
    expect(infoRule![0]).toContain('--border')
  })

  it('keeps the severity colours that carry meaning', () => {
    const css = readFileSync(
      pathResolve(process.cwd(), 'src/components/ui/AlertsPanel.module.css'),
      'utf8',
    )
    // `important` and `warning` are real signals and must stay distinct.
    expect(css).toMatch(/\.alertItem:global\(\.alert-important\)\s*\{[^}]*--error/)
    expect(css).toMatch(/\.alertItem:global\(\.alert-warning\)\s*\{[^}]*--warning/)
  })
})

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render } from '@solidjs/testing-library'
import { AlertsPanel } from './AlertsPanel'
import { setUser } from '../../state/user'
import { getAlerts, type Alert } from '../../services/api'

vi.mock('../../services/api')

// Mock setInterval/clearInterval globally to avoid fake timer issues
describe('AlertsPanel', () => {
  const mockAlerts: Alert[] = [
    { id: 1, user_id: 1, type: 'learning', title: 'Test Alert', message: 'Test message', source_frame_id: null, source_episode_id: null, severity: 'info', is_read: false, created_at: '2024-01-01T00:00:00Z', read_at: null },
  ]

  beforeEach(() => {
    vi.useFakeTimers()
    vi.clearAllMocks()
    setUser({ id: 1, name: 'Test User' })
    vi.mocked(getAlerts).mockResolvedValue({ alerts: mockAlerts, unread_count: 1 })

    // Mock setInterval to store callbacks without actually scheduling
    vi.spyOn(window, 'setInterval').mockImplementation((_callback: () => void, _delay: number) => {
      return 123 as unknown as number // Fixed ID for testing
    })
    vi.spyOn(window, 'clearInterval').mockImplementation(() => {})
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('fetches alerts on mount', async () => {
    render(() => <AlertsPanel />)
    
    // Wait for the async fetch to complete
    await vi.runAllTimersAsync()
    
    expect(getAlerts).toHaveBeenCalledWith(1, 50)
  })

  it('sets up interval on mount and clears on unmount', async () => {
    const { unmount } = render(() => <AlertsPanel />)
    
    await vi.runAllTimersAsync()
    
    // Should have set up interval with 30000ms
    expect(window.setInterval).toHaveBeenCalled()
    const intervalCall = (window.setInterval as unknown as ReturnType<typeof vi.fn>).mock.calls[0]
    expect(intervalCall[1]).toBe(30000)
    
    unmount()
    
    // Should clear the interval on unmount
    expect(window.clearInterval).toHaveBeenCalledWith(123)
  })

  it('does not fetch if no user', async () => {
    setUser(null)
    render(() => <AlertsPanel />)
    
    await vi.advanceTimersByTimeAsync(100)
    await vi.runAllTimersAsync()
    
    expect(getAlerts).not.toHaveBeenCalled()
  })
})
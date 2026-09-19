import { createSignal, onMount, For, Show } from 'solid-js'
import { api } from '../../services/api'
import { user } from '../../state/user'
import { Modal } from './Modal'
import styles from './AlertsPanel.module.css'

interface Alert {
  id: number
  user_id: number
  type: string
  title: string
  message: string
  source_frame_id: number | null
  source_episode_id: number | null
  severity: string
  is_read: boolean
  created_at: string | null
  read_at: string | null
}

interface AlertsListResponse {
  alerts: Alert[]
  unread_count: number
}

export function AlertsPanel() {
  const [alerts, setAlerts] = createSignal<Alert[]>([])
  const [unreadCount, setUnreadCount] = createSignal(0)
  const [isOpen, setIsOpen] = createSignal(false)
  const [isLoading, setIsLoading] = createSignal(false)

  const fetchAlerts = async () => {
    const u = user()
    if (!u) return
    
    setIsLoading(true)
    try {
      const response = await api.get<AlertsListResponse>('/alerts', {
        params: { user_id: u.id, limit: 50 }
      })
      if (response.data) {
        setAlerts(response.data.alerts)
        setUnreadCount(response.data.unread_count)
      }
    } catch (error) {
      console.error('Failed to fetch alerts:', error)
    } finally {
      setIsLoading(false)
    }
  }

  // Fetch alerts on mount and periodically
  onMount(() => {
    fetchAlerts()
    const interval = setInterval(fetchAlerts, 30000) // Refresh every 30 seconds
    return () => clearInterval(interval)
  })

  const markAsRead = async (alertId: number) => {
    const u = user()
    if (!u) return
    
    try {
      await api.post('/alerts/{alert_id}/read', null, {
        params: { alert_id: alertId, user_id: u.id }
      })
      setAlerts(prev => prev.map(a => a.id === alertId ? { ...a, is_read: true } : a))
      setUnreadCount(prev => Math.max(0, prev - 1))
    } catch (error) {
      console.error('Failed to mark alert as read:', error)
    }
  }

  const markAllAsRead = async () => {
    const u = user()
    if (!u) return
    
    try {
      await api.post('/alerts/read-all', null, { params: { user_id: u.id } })
      setAlerts(prev => prev.map(a => ({ ...a, is_read: true })))
      setUnreadCount(0)
    } catch (error) {
      console.error('Failed to mark all alerts as read:', error)
    }
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return ''
    try {
      return new Date(dateStr).toLocaleString('en-US', {
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    } catch {
      return dateStr
    }
  }

  const getSeverityClass = (severity: string) => {
    switch (severity) {
      case 'warning': return 'alert-warning'
      case 'important': return 'alert-important'
      default: return 'alert-info'
    }
  }

  const getTypeIcon = (type: string) => {
    switch (type) {
      case 'task_result': return '📋'
      case 'search_result': return '🔍'
      case 'conflict': return '⚠️'
      case 'correction': return '✏️'
      case 'learning': return '🧠'
      default: return '🔔'
    }
  }

  return (
    <>
      {unreadCount() > 0 && (
        <button
          class={`alerts-trigger ${styles.alertsTrigger}`}
          onClick={() => setIsOpen(true)}
          aria-label={`Open alerts (${unreadCount()} unread)`}
        >
          <span class={styles.alertIcon}>🔔</span>
          <span class={styles.unreadBadge}>{unreadCount()}</span>
          <span class={styles.alertsLabel}>Alerts</span>
        </button>
      )}

      <Modal
        isOpen={isOpen()}
        onClose={() => setIsOpen(false)}
        title="Learning Monitor"
        size="large"
      >
        <div class={styles.alertsPanel}>
          <div class={styles.alertsHeader}>
            <h3>Learning Monitor</h3>
            <Show when={unreadCount() > 0}>
              <button
                class={styles.markAllReadBtn}
                onClick={markAllAsRead}
              >
                Mark all as read
              </button>
            </Show>
          </div>

          <Show when={isLoading()}>
            <div class={styles.loading}>Loading alerts...</div>
          </Show>

          <Show when={!isLoading() && alerts().length === 0}>
            <div class={styles.empty}>
              <p>No alerts yet</p>
              <p class={styles.emptyHint}>Alerts appear when the agent learns something new</p>
            </div>
          </Show>

          <Show when={!isLoading() && alerts().length > 0}>
            <div class={styles.alertsList}>
              <For each={alerts()}>
                {(alert: Alert) => (
                  <div class={`${styles.alertItem} ${getSeverityClass(alert.severity)} ${alert.is_read ? styles.read : ''}`}>
                    <div class={styles.alertContent}>
                      <div class={styles.alertHeader}>
                        <span class={styles.alertType}>{getTypeIcon(alert.type)} {alert.type.replace('_', ' ')}</span>
                        <span class={styles.alertTime}>{formatDate(alert.created_at)}</span>
                      </div>
                      <div class={styles.alertTitle}>{alert.title}</div>
                      <div class={styles.alertMessage}>{alert.message}</div>
                    </div>
                    <div class={styles.alertActions}>
                      <Show when={!alert.is_read}>
                        <button
                          class={styles.readBtn}
                          onClick={() => markAsRead(alert.id)}
                          aria-label="Mark as read"
                        >
                          Mark read
                        </button>
                      </Show>
                      <Show when={alert.is_read}>
                        <span class={styles.readBadge}>Read</span>
                      </Show>
                    </div>
                  </div>
                )}
              </For>
            </div>
          </Show>

          <style>{`
            .alerts-trigger {
              display: flex;
              align-items: center;
              gap: 6px;
              padding: 6px 12px;
              border: 1px solid var(--color-border);
              border-radius: 20px;
              background: var(--color-surface);
              color: var(--color-text);
              cursor: pointer;
              transition: all 0.15s ease;
            }
            .alerts-trigger:hover {
              background: var(--color-background-hover);
              border-color: var(--color-primary);
            }
            .alert-icon {
              font-size: 1.1em;
            }
            .unread-badge {
              min-width: 20px;
              height: 20px;
              padding: 0 6px;
              background: var(--color-error);
              color: white;
              border-radius: 10px;
              font-size: 0.75rem;
              font-weight: 600;
              display: flex;
              align-items: center;
              justify-content: center;
            }
            .alerts-label {
              font-size: 0.875rem;
              font-weight: 500;
            }
            .alerts-panel {
              max-height: 70vh;
              overflow-y: auto;
            }
            .alerts-header {
              display: flex;
              justify-content: space-between;
              align-items: center;
              padding-bottom: 12px;
              border-bottom: 1px solid var(--color-border);
              margin-bottom: 16px;
            }
            .mark-all-read-btn {
              padding: 6px 12px;
              background: var(--color-primary);
              color: white;
              border: none;
              border-radius: 6px;
              font-size: 0.875rem;
              cursor: pointer;
              transition: opacity 0.15s ease;
            }
            .mark-all-read-btn:hover {
              opacity: 0.9;
            }
            .loading, .empty {
              padding: 32px;
              text-align: center;
              color: var(--color-text-secondary);
            }
            .empty-hint {
              font-size: 0.875rem;
              opacity: 0.7;
            }
            .alerts-list {
              display: flex;
              flex-direction: column;
              gap: 12px;
            }
            .alert-item {
              display: flex;
              justify-content: space-between;
              align-items: flex-start;
              padding: 16px;
              background: var(--color-surface);
              border: 1px solid var(--color-border);
              border-radius: 8px;
              gap: 16px;
              transition: all 0.15s ease;
            }
            .alert-item:not(.read):hover {
              border-color: var(--color-primary);
            }
            .alert-item.read {
              opacity: 0.7;
            }
            .alert-item.alert-warning {
              border-left: 4px solid var(--color-warning);
            }
            .alert-item.alert-important {
              border-left: 4px solid var(--color-error);
            }
            .alert-item.alert-info {
              border-left: 4px solid var(--color-primary);
            }
            .alert-content {
              flex: 1;
              min-width: 0;
            }
            .alert-header {
              display: flex;
              justify-content: space-between;
              align-items: center;
              margin-bottom: 8px;
            }
            .alert-type {
              font-size: 0.75rem;
              font-weight: 600;
              color: var(--color-text-secondary);
              text-transform: uppercase;
              letter-spacing: 0.05em;
            }
            .alert-time {
              font-size: 0.75rem;
              color: var(--color-text-secondary);
            }
            .alert-title {
              font-weight: 600;
              color: var(--color-text);
              margin-bottom: 4px;
            }
            .alert-message {
              font-size: 0.875rem;
              color: var(--color-text-secondary);
              line-height: 1.5;
            }
            .alert-actions {
              display: flex;
              align-items: center;
              gap: 8px;
              flex-shrink: 0;
            }
            .read-btn {
              padding: 6px 12px;
              background: var(--color-primary);
              color: white;
              border: none;
              border-radius: 6px;
              font-size: 0.875rem;
              cursor: pointer;
              transition: opacity 0.15s ease;
            }
            .read-btn:hover {
              opacity: 0.9;
            }
            .read-badge {
              padding: 6px 12px;
              background: var(--color-success-bg);
              color: var(--color-success);
              border-radius: 6px;
              font-size: 0.875rem;
              font-weight: 500;
            }
          `}</style>
        </div>
      </Modal>
    </>
  )
}

export default AlertsPanel
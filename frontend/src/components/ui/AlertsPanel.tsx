import { createSignal, onMount, onCleanup, For, Show } from 'solid-js'
import { getAlerts, markAlertAsRead, markAllAlertsAsRead, type Alert } from '../../services/api'
import { user } from '../../state/user'
import { Modal } from './Modal'
import styles from './AlertsPanel.module.css'

export function AlertsPanel() {
  const [alerts, setAlerts] = createSignal<Alert[]>([])
  const [unreadCount, setUnreadCount] = createSignal(0)
  const [isOpen, setIsOpen] = createSignal(false)
  const [isLoading, setIsLoading] = createSignal(false)

  // Per-component interval ID (not module-level)
  let intervalId: number | null = null

  const fetchAlerts = async () => {
    const u = user()
    if (!u) return
    
    setIsLoading(true)
    try {
      const response = await getAlerts(u.id, 50)
      if (response) {
        setAlerts(response.alerts)
        setUnreadCount(response.unread_count)
      }
    } catch (error) {
      console.error('Failed to fetch alerts:', error)
    } finally {
      setIsLoading(false)
    }
  }

  // Start interval on mount, stop on unmount
  onMount(() => {
    fetchAlerts() // Initial fetch
    intervalId = window.setInterval(fetchAlerts, 30000)
  })

  onCleanup(() => {
    if (intervalId !== null) {
      clearInterval(intervalId)
      intervalId = null
    }
  })

  const markAsRead = async (alertId: number) => {
    const u = user()
    if (!u) return
    
    try {
      await markAlertAsRead(alertId, u.id)
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
      await markAllAlertsAsRead(u.id)
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
      case 'task_alert': return '🚨'
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
          class={styles.alertsTrigger}
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
        </div>
      </Modal>
    </>
  )
}

export default AlertsPanel
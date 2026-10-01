import { createSignal, createEffect, onMount, onCleanup, For, Show, createMemo } from 'solid-js'
import {
  getAlerts,
  getAlertConversationOptions,
  openAlertConversation,
  markAllAlertsAsRead,
  type Alert,
  type AlertConversationOption,
} from '../../services/api'
import { user } from '../../state/user'
import { Modal } from './Modal'
import { BellIcon } from './TopBarIcons'
import styles from './AlertsPanel.module.css'

/**
 * The agent's channel to the user.
 *
 * Alerts are memory of a type, so the set shown here is "things the agent is
 * waiting on" — not a notification log. An alert closes by being *answered*, not
 * by being read, which is why there is no read/unread state: the previous model
 * accumulated 103 unread rows precisely because reading one accomplished nothing.
 *
 * Resolving therefore means choosing where to talk about it. The picker defaults to
 * a conversation the user already has; spawning a thread per alert would fill the
 * conversation list with one-off threads, which is the inbox problem again.
 */

/** The modal is a two-step flow: pick a conversation, then go there. */
type PickerState = {
  alertId: number
  alertTitle: string
} | null

export function AlertsPanel() {
  const [alerts, setAlerts] = createSignal<Alert[]>([])
  const [unreadCount, setUnreadCount] = createSignal(0)
  const [isOpen, setIsOpen] = createSignal(false)
  // `hasLoaded`, not `isLoading`: the 30s poll must not swap the list for a
  // spinner, which read as the alerts flickering away every half minute.
  const [hasLoaded, setHasLoaded] = createSignal(false)
  const [picker, setPicker] = createSignal<PickerState>(null)
  const [options, setOptions] = createSignal<AlertConversationOption[]>([])
  const [optionsLoading, setOptionsLoading] = createSignal(false)
  const [resolvingId, setResolvingId] = createSignal<number | null>(null)

  // Per-component interval ID (not module-level)
  let intervalId: number | null = null

  const fetchAlerts = async () => {
    const u = user()
    if (!u) return

    try {
      const response = await getAlerts(u.id, 50)
      if (response) {
        setAlerts(response.alerts)
        setUnreadCount(response.unread_count)
      }
    } catch (error) {
      console.error('Failed to fetch alerts:', error)
    } finally {
      setHasLoaded(true)
    }
  }

  // `user()` arrives asynchronously, so fetch when it does rather than only once
  // on mount: the mount-time call no-opped before the user was set, and the bell
  // stayed empty until the first 30s tick (which is why a refresh showed nothing).
  createEffect(() => {
    if (user()) void fetchAlerts()
  })

  onMount(() => {
    intervalId = window.setInterval(fetchAlerts, 30000)
  })

  onCleanup(() => {
    if (intervalId !== null) {
      clearInterval(intervalId)
      intervalId = null
    }
  })

  /** Open the picker for an alert and load the conversations it could go in. */
  const chooseConversation = async (alert: Alert) => {
    const u = user()
    if (!u) return

    setPicker({ alertId: alert.id, alertTitle: alert.title })
    setOptionsLoading(true)
    setOptions([])
    try {
      const response = await getAlertConversationOptions(alert.id, u.id)
      setOptions(response.conversations)
    } catch (error) {
      console.error('Failed to load conversations:', error)
    } finally {
      setOptionsLoading(false)
    }
  }

  /**
   * Attach the alert to a conversation and go there.
   *
   * Optimistically removes it from the list: attaching is what resolves it, and the
   * backstop closes the alert as soon as the user replies there. Refetching would
   * also work, but the list is a view of an open set and the item has left it.
   */
  const resolveIn = async (sessionId: string | null) => {
    const u = user()
    const current = picker()
    if (!u || !current) return

    setResolvingId(current.alertId)
    try {
      const result = await openAlertConversation(
        current.alertId,
        u.id,
        sessionId ?? undefined
      )
      setAlerts((prev) => prev.filter((a) => a.id !== current.alertId))
      setUnreadCount((prev) => Math.max(0, prev - 1))
      setPicker(null)
      // Hand the chosen conversation to the chat. The message is the alert being
      // raised where the user can answer it; nothing is sent automatically.
      window.dispatchEvent(
        new CustomEvent('open-conversation', {
          detail: { sessionId: result.session_id, seeded: result.seeded },
        })
      )
    } catch (error) {
      console.error('Failed to resolve alert:', error)
    } finally {
      setResolvingId(null)
    }
  }

  const resolveAll = async () => {
    const u = user()
    if (!u) return

    try {
      await markAllAlertsAsRead(u.id)
      setAlerts([])
      setUnreadCount(0)
    } catch (error) {
      console.error('Failed to resolve all alerts:', error)
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

  /**
   * Icons are inline SVG, not emoji — the project's UI standard. Emoji render
   * differently per platform and cannot inherit `currentColor`.
   */
  const TypeIcon = (props: { type: string }) => {
    const path = createMemo(() => {
      switch (props.type) {
        case 'task_alert':
          // bell-alert
          return 'M12 2a7 7 0 0 0-7 7v4l-1.5 3h17L19 13V9a7 7 0 0 0-7-7Zm0 20a3 3 0 0 0 3-3H9a3 3 0 0 0 3 3Z'
        case 'correction':
          // pencil
          return 'M3 17.25V21h3.75L17.8 9.94l-3.75-3.75L3 17.25ZM20.7 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z'
        default:
          // circle-info
          return 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Zm1 15h-2v-6h2v6Zm0-8h-2V7h2v2Z'
      }
    })
    return (
      <svg class={styles.typeIcon} viewBox="0 0 24 24" aria-hidden="true">
        <path d={path()} fill="currentColor" />
      </svg>
    )
  }

  const humanType = (type: string) => type.replace(/_/g, ' ')

  return (
    <>
      <button
        class="topbar-btn"
        classList={{ 'has-alerts': unreadCount() > 0 }}
        onClick={() => setIsOpen(true)}
        aria-label={
          unreadCount() > 0
            ? `Open alerts (${unreadCount()} waiting)`
            : 'Open alerts'
        }
      >
        <BellIcon />
        <Show when={unreadCount() > 0}>
          <span class={styles.unreadBadge}>{unreadCount()}</span>
        </Show>
        <span class={styles.alertsLabel}>Alerts</span>
      </button>

      <Modal
        isOpen={isOpen()}
        onClose={() => setIsOpen(false)}
        title="Alerts"
        size="large"
      >
        <div class={styles.alertsPanel}>
          <div class={styles.alertsHeader}>
            <h3>Things I want to tell you</h3>
            <Show when={unreadCount() > 0}>
              <button class={styles.markAllReadBtn} onClick={resolveAll}>
                Resolve all
              </button>
            </Show>
          </div>

          <Show when={!hasLoaded()}>
            <div class={styles.loading}>Loading alerts...</div>
          </Show>

          <Show when={hasLoaded() && alerts().length === 0}>
            <div class={styles.empty}>
              <p>Nothing waiting</p>
              <p class={styles.emptyHint}>
                Alerts appear when I learn something while you are away
              </p>
            </div>
          </Show>

          <Show when={hasLoaded() && alerts().length > 0}>
            <div class={styles.alertsList}>
              <For each={alerts()}>
                {(alert: Alert) => (
                  <div
                    class={`${styles.alertItem} ${getSeverityClass(alert.severity)}`}
                  >
                    <div class={styles.alertContent}>
                      <div class={styles.alertHeader}>
                        <span class={styles.alertType}>
                          <TypeIcon type={alert.type} />
                          {humanType(alert.type)}
                        </span>
                        <span class={styles.alertTime}>
                          {formatDate(alert.created_at)}
                        </span>
                      </div>
                      <div class={styles.alertTitle}>{alert.title}</div>
                      <div class={styles.alertMessage}>{alert.message}</div>
                    </div>
                    <div class={styles.alertActions}>
                      <button
                        class={styles.readBtn}
                        onClick={() => chooseConversation(alert)}
                        disabled={resolvingId() === alert.id}
                      >
                        Resolve…
                      </button>
                    </div>
                  </div>
                )}
              </For>
            </div>
          </Show>
        </div>
      </Modal>

      <Modal
        isOpen={picker() !== null}
        onClose={() => setPicker(null)}
        title="Resolve this where?"
        size="medium"
      >
        <div class={styles.picker}>
          <p class={styles.pickerHint}>
            {picker()?.alertTitle} — pick the conversation you want to settle it in.
            <Show when={options().length === 0 && !optionsLoading()}>
              {' '}
              You have no other conversations, so this will start one.
            </Show>
          </p>

          <Show when={optionsLoading()}>
            <div class={styles.loading}>Loading conversations...</div>
          </Show>

          <Show when={!optionsLoading()}>
            <div class={styles.optionsList}>
              <For each={options()}>
                {(option) => (
                  <button
                    class={styles.optionItem}
                    onClick={() => resolveIn(option.session_id)}
                    disabled={resolvingId() !== null}
                  >
                    <span class={styles.optionName}>{option.name}</span>
                    <span class={styles.optionMeta}>
                      {option.message_count} message
                      {option.message_count === 1 ? '' : 's'}
                      <Show when={option.last_activity}>
                        {' · '}
                        {formatDate(option.last_activity)}
                      </Show>
                    </span>
                  </button>
                )}
              </For>
              <button
                class={`${styles.optionItem} ${styles.optionNew}`}
                onClick={() => resolveIn(null)}
                disabled={resolvingId() !== null}
              >
                <span class={styles.optionName}>New conversation</span>
                <span class={styles.optionMeta}>
                  Starts a thread just for this alert
                </span>
              </button>
            </div>
          </Show>
        </div>
      </Modal>
    </>
  )
}

export default AlertsPanel

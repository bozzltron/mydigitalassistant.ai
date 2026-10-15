/**
 * Pure helpers for the Tasks page: no DOM, no fetch, so they are testable without a
 * browser.
 */
import type { Task } from '../types'

/**
 * An ISO timestamp as the user's local date and time, or the raw value if it will not
 * parse — a task with an unreadable `next_run` should show what it actually holds
 * rather than "Invalid Date".
 */
export function formatStamp(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/**
 * The one-line state of a task, as the list shows it.
 *
 * `schedule_cron` is the backend's field name and holds `daily` or `once` — not a cron
 * expression — so the label says **Frequency**, not "schedule".
 */
export function taskStateLabel(task: Task): string {
  if (!task.enabled) return 'Paused'
  return task.schedule_cron === 'once' ? 'Once' : 'Daily'
}

/** When a task next runs, for the list. A paused task has no next run. */
export function taskNextRunLabel(task: Task): string {
  if (!task.next_run) return task.enabled ? 'not scheduled' : '—'
  return `next ${formatStamp(task.next_run)}`
}

/** The first line of a prompt, for a list row when the task has no description. */
export function taskSubtitle(task: Task): string {
  const source = task.description.trim() || task.prompt.trim()
  const firstLine = source.split('\n')[0] ?? ''
  return firstLine.length > 80 ? `${firstLine.slice(0, 80)}…` : firstLine
}

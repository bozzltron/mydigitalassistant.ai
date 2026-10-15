/**
 * A scheduled task, as `/tasks` reports it (`assistant/backend/main.py`).
 *
 * A task is memory: a `scheduled_task` frame whose slots are the authoritative store.
 * This page is a view over it, and it writes exactly **two** of these fields —
 * `prompt` (an edit) and `enabled` (a state action). Everything else is chat's to set,
 * which is why the page shows them as text rather than as disabled inputs.
 */
export interface Task {
  id: number
  name: string
  description: string
  /** `daily` or `once`. Not a cron expression, despite the backend's field name. */
  schedule_cron: string
  /** The script the run executes — the one field the page edits. */
  prompt: string
  enabled: boolean
  last_run: string | null
  next_run: string | null
  last_result_summary: string | null
  source_type: string | null
  created_at: string | null
}

/** What the page may change. Sending anything else is a 422, by design. */
export interface TaskChange {
  prompt?: string
  enabled?: boolean
}

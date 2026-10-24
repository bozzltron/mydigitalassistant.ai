/**
 * A scheduled task, as `/tasks` reports it (`assistant/backend/main.py`).
 *
 * A task is memory: a `scheduled_task` frame whose slots are the authoritative store.
 * This page is a view over it, and it writes exactly **three** of these fields —
 * `prompt` (an edit), `enabled` (a state action) and `use_max` (which model the run
 * uses). Everything else is chat's to set, which is why the page shows them as text
 * rather than as disabled inputs.
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
  /**
   * Run this task on the MAX tier. A scheduled run is where escalation costs
   * nothing: nobody is waiting on the daily tick, so the 27B can do the work.
   */
  use_max: boolean
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
  use_max?: boolean
}

/**
 * The result of running a task on demand (`POST /tasks/run-now/{name}`).
 *
 * `result_summary` is the run's own report, truncated to 2000 characters by the
 * backend — the same text that lands in `last_result_summary`.
 */
export interface TaskRunResult {
  name: string
  success: boolean
  result_summary: string
}

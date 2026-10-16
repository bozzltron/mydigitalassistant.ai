import { createEffect, createSignal, on, onCleanup, Show, untrack } from 'solid-js'
import type { Task } from '../../types/task'
import { renderMarkdown } from '../../utils/markdown'
import { formatStamp } from '../../utils/taskLib'

/**
 * One task's detail: its identity read-only, its prompt editable, and a pause/resume
 * button.
 *
 * `enabled` is a **button**, not a checkbox, because it is a state *action* — "stop
 * this running" — rather than a field of the edit. `name`, `frequency` and
 * `description` are plain text rather than disabled inputs, so the page does not imply
 * an edit it does not offer: chat owns those.
 */
export function TaskForm(props: {
  task: Task
  busy: boolean
  running: boolean
  onSavePrompt: (prompt: string) => void
  onRunNow: () => void
  onToggle: () => void
  onDelete: () => void
}) {
  // The initial value only; the effect below owns every subsequent seed, because
  // `Show` reuses this instance when one task replaces another.
  const [text, setText] = createSignal(untrack(() => props.task.prompt))

  /**
   * Seconds since the run started.
   *
   * A run is one blocking request through the whole cognitive loop — routing, recall,
   * planning, extraction, search, generation, tools — and the backend exposes no
   * stage events on this path, so there is no finer progress to report. A moving number
   * is what separates "working" from "hung" when the wait runs to a minute.
   */
  const [elapsed, setElapsed] = createSignal(0)
  createEffect(() => {
    if (!props.running) return
    const started = Date.now()
    setElapsed(0)
    const timer = setInterval(
      () => setElapsed(Math.round((Date.now() - started) / 1000)),
      1000
    )
    onCleanup(() => clearInterval(timer))
  })

  // Seed on a *different task*, not on every prop change: toggling pause replaces the
  // task object, and re-seeding then would throw away what the user was typing. The
  // callback takes the task as a plain value, so this is not a subscription to its
  // prompt — the id check is what decides.
  let seededFor: number | null = null
  createEffect(
    on(
      () => props.task,
      (task) => {
        if (task.id === seededFor) return
        seededFor = task.id
        setText(task.prompt)
      }
    )
  )

  const dirty = () => text().trim() !== props.task.prompt

  return (
    <form
      class="task-form"
      onSubmit={(e) => {
        e.preventDefault()
        if (text().trim()) props.onSavePrompt(text().trim())
      }}
    >
      <h2>{props.task.name}</h2>

      <dl class="task-meta">
        <dt>Runs</dt>
        <dd>{props.task.schedule_cron === 'once' ? 'Once' : 'Daily'}</dd>
        <dt>State</dt>
        <dd>{props.task.enabled ? 'Enabled' : 'Paused'}</dd>
        <dt>Last run</dt>
        <dd>{props.task.last_run ? formatStamp(props.task.last_run) : 'never'}</dd>
        <dt>Next run</dt>
        <dd>{props.task.next_run ? formatStamp(props.task.next_run) : '—'}</dd>
      </dl>

      <label class="task-prompt-label" for="task-prompt">
        What it runs
      </label>
      <textarea
        id="task-prompt"
        class="task-prompt"
        rows="9"
        value={text()}
        onInput={(e) => setText(e.currentTarget.value)}
      />

      <div class="task-actions">
        <button
          type="submit"
          class="topbar-btn"
          disabled={props.busy || props.running || !dirty()}
        >
          Save
        </button>
        <button
          type="button"
          class="topbar-btn"
          disabled={props.busy || props.running}
          onClick={() => props.onRunNow()}
        >
          Run now
        </button>
        <button
          type="button"
          class="topbar-btn"
          disabled={props.busy || props.running}
          onClick={() => props.onToggle()}
        >
          {props.task.enabled ? 'Pause' : 'Resume'}
        </button>
        <button
          type="button"
          class="topbar-btn task-remove"
          disabled={props.busy || props.running}
          onClick={() => props.onDelete()}
        >
          Delete
        </button>
      </div>

      <Show when={props.running}>
        <p class="task-running" aria-live="polite">
          <span class="task-running-dot" aria-hidden="true" />
          Running… {elapsed()}s
        </p>
      </Show>

      <Show when={props.task.last_result_summary}>
        {(summary) => (
          <section class="task-last-run">
            <h3>Last run said</h3>
            {/* The summary is the model's own output, written as markdown. Rendering it
                beats showing the source: a run's report carries headings, lists and a
                `**Sources:**` footer, which read as noise as raw text. */}
            {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by renderMarkdown */}
            <div class="task-last-run-body" innerHTML={renderMarkdown(summary())} />
          </section>
        )}
      </Show>
    </form>
  )
}

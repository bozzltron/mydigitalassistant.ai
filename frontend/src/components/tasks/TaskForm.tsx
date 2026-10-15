import { createEffect, createSignal, on, Show, untrack } from 'solid-js'
import type { Task } from '../../types/task'
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
  onSavePrompt: (prompt: string) => void
  onToggle: () => void
  onDelete: () => void
}) {
  const [text, setText] = createSignal(props.task.prompt)

  // Seed on a *different task*, not on every prop change: toggling pause replaces the
  // task object, and re-seeding then would throw away what the user was typing. The
  // read is untracked on purpose — it is a snapshot of the newly selected task, not a
  // subscription to its prompt.
  createEffect(on(() => props.task.id, () => setText(untrack(() => props.task.prompt))))

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
        <button type="submit" class="topbar-btn" disabled={props.busy || !dirty()}>
          Save
        </button>
        <button
          type="button"
          class="topbar-btn"
          disabled={props.busy}
          onClick={() => props.onToggle()}
        >
          {props.task.enabled ? 'Pause' : 'Resume'}
        </button>
        <button
          type="button"
          class="topbar-btn task-remove"
          disabled={props.busy}
          onClick={() => props.onDelete()}
        >
          Delete
        </button>
      </div>

      <Show when={props.task.last_result_summary}>
        {(summary) => (
          <section class="task-last-run">
            <h3>Last run said</h3>
            <p>{summary()}</p>
          </section>
        )}
      </Show>
    </form>
  )
}

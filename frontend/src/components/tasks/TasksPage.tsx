import { createMemo, createSignal, For, onMount, Show } from 'solid-js'
import { createTask, deleteTask, getTasks, runTaskNow, updateTask } from '../../services/api'
import { debug } from '../../services/logger'
import type { Task } from '../../types/task'
import { taskNextRunLabel, taskStateLabel, taskSubtitle } from '../../utils/taskLib'
import PageMenu from '../ui/PageMenu'
import { TaskForm } from './TaskForm'

/**
 * `/tasks` — the daily tasks, as memory.
 *
 * A task is a `scheduled_task` frame; this page is a view over it and writes exactly
 * two of its fields — `prompt` (an edit) and `enabled` (pause/resume). Everything else
 * is chat's to set, and the server refuses anything else (`extra="forbid"`), so a
 * mistake here is a 422 rather than a silent change.
 *
 * See `assistant/AGENTS.md` — scheduled tasks are memory, and the page is a view.
 */
export default function TasksPage() {
  const [tasks, setTasks] = createSignal<Task[]>([])
  const [selected, setSelected] = createSignal<number | null>(null)
  const [creating, setCreating] = createSignal(false)
  const [newName, setNewName] = createSignal('')
  const [newPrompt, setNewPrompt] = createSignal('')
  const [busy, setBusy] = createSignal(false)
  const [error, setError] = createSignal('')
  const [loaded, setLoaded] = createSignal(false)
  // The task currently being run. A run is one blocking request through the whole
  // cognitive loop, so it gets its own state: the list must stay usable and the panel
  // must say which task is working.
  const [runningId, setRunningId] = createSignal<number | null>(null)

  const load = async () => {
    try {
      const list = await getTasks()
      setTasks(list)
      debug('[tasks] loaded', list.length, 'task(s)')
    } catch (e) {
      console.error('[tasks] could not load tasks', e)
      setError('Could not reach the assistant.')
    } finally {
      setLoaded(true)
    }
  }

  onMount(() => void load())

  const selectedTask = createMemo(() => tasks().find((t) => t.id === selected()) ?? null)

  /**
   * One place that turns a rejected write into a message. It never clears the user's
   * input: the edit stays on screen and the status line says what happened.
   */
  const attempt = async (fn: () => Promise<void>): Promise<boolean> => {
    setBusy(true)
    setError('')
    try {
      await fn()
      return true
    } catch (e) {
      console.error('[tasks] write failed', e)
      setError('Could not save. Your change is still here — try again.')
      return false
    } finally {
      setBusy(false)
    }
  }

  const replace = (task: Task) =>
    setTasks((list) => list.map((t) => (t.id === task.id ? task : t)))

  const savePrompt = (prompt: string) => {
    const task = selectedTask()
    if (!task) return
    void attempt(async () => {
      replace(await updateTask(task.id, { prompt }))
    })
  }

  const toggle = (task: Task) => {
    void attempt(async () => {
      const updated = await updateTask(task.id, { enabled: !task.enabled })
      replace(updated)
      debug('[tasks] toggled', updated.name, updated.enabled ? 'on' : 'off')
    })
  }

  /**
   * Model tier for this task.
   *
   * This is the one place the MAX model is cheap: a scheduled run waits on nobody,
   * so the big model can do the work there even when a live chat turn would not
   * escalate. It is a state action like pause/resume, not part of the prompt edit.
   */
  const toggleMax = (task: Task) => {
    void attempt(async () => {
      const updated = await updateTask(task.id, { use_max: !task.use_max })
      replace(updated)
      debug('[tasks] max', updated.name, updated.use_max ? 'on' : 'off')
    })
  }

  const remove = (task: Task) => {
    if (!window.confirm(`Delete “${task.name}”? Its past runs stay in memory.`)) return
    void attempt(async () => {
      await deleteTask(task.id)
      setTasks((list) => list.filter((t) => t.id !== task.id))
      setSelected(null)
    })
  }

  /**
   * Run a task now, through the same path the scheduler uses.
   *
   * It is a real run, so the confirmation says what it costs rather than calling it a
   * test: it writes an episode and frames, updates `last_run` and the summary, and a
   * `once` task switches itself off afterwards.
   */
  const runNow = (task: Task) => {
    const effect =
      task.schedule_cron === 'once'
        ? 'It runs once and then switches itself off, so it will not fire again.'
        : 'It counts as today’s run, so the next scheduled run moves to tomorrow.'
    const ok = window.confirm(
      `Run “${task.name}” now?\n\n${effect}\n\n` +
        'The run is written to memory and can take a minute.'
    )
    if (!ok) return

    setRunningId(task.id)
    setError('')
    void (async () => {
      try {
        const result = await runTaskNow(task.name)
        debug('[tasks] ran', task.name, result.success ? 'ok' : 'reported a problem')
      } catch (e) {
        console.error('[tasks] run failed', e)
        setError('The run did not finish. See the console for the reason.')
      } finally {
        setRunningId(null)
        // The run moved last_run, the summary and possibly `enabled`, so re-read the
        // list rather than guess which fields changed.
        await load()
      }
    })()
  }

  const create = () => {
    const name = newName().trim()
    const prompt = newPrompt().trim()
    if (!name || !prompt) return
    void attempt(async () => {
      const task = await createTask(name, prompt)
      setTasks((list) => [task, ...list])
      setCreating(false)
      setNewName('')
      setNewPrompt('')
      setSelected(task.id)
      debug('[tasks] created', task.name)
    })
  }

  return (
    <div class="tasks-page">
      <div class="tasks-header">
        <h1>Tasks</h1>
        <button class="topbar-btn" onClick={() => setCreating((v) => !v)}>
          {creating() ? 'Cancel' : '＋ New task'}
        </button>
        <span class="tasks-status" aria-live="polite">
          {busy() ? 'Saving…' : error()}
        </span>
        <PageMenu />
      </div>

      <div class="tasks-content">
        <div class="tasks-main">
          <Show when={creating()}>
            <section class="task-create">
              <input
                class="task-create-name"
                placeholder="Name"
                aria-label="Task name"
                value={newName()}
                onInput={(e) => setNewName(e.currentTarget.value)}
              />
              <textarea
                class="task-create-prompt"
                rows="4"
                placeholder="What should it do each morning?"
                aria-label="Task prompt"
                value={newPrompt()}
                onInput={(e) => setNewPrompt(e.currentTarget.value)}
              />
              <button
                class="topbar-btn"
                disabled={busy() || !newName().trim() || !newPrompt().trim()}
                onClick={create}
              >
                Create
              </button>
            </section>
          </Show>

          <Show when={loaded() && tasks().length === 0 && !creating()}>
            <p class="tasks-empty">
              No tasks yet. Ask in chat — “add an AI briefing to my mornings” — or create
              one here.
            </p>
          </Show>

          <ul class="task-list">
            <For each={tasks()}>
              {(task) => (
                <li>
                  <button
                    class="task-row"
                    classList={{ 'is-active': task.id === selected(), 'is-paused': !task.enabled }}
                    onClick={() => setSelected(task.id)}
                  >
                    <span class="task-row-head">
                      <span class="task-row-name">{task.name}</span>
                      <span class="task-row-state">{taskStateLabel(task)}</span>
                    </span>
                    <span class="task-row-sub">{taskSubtitle(task)}</span>
                    <span class="task-row-when">{taskNextRunLabel(task)}</span>
                  </button>
                </li>
              )}
            </For>
          </ul>
        </div>

        <aside class="task-detail">
          <Show
            when={selectedTask()}
            fallback={<p class="tasks-empty">Select a task to see what it runs.</p>}
          >
            {(task) => (
              <TaskForm
                task={task()}
                busy={busy()}
                running={runningId() === task().id}
                onSavePrompt={savePrompt}
                onRunNow={() => runNow(task())}
                onToggle={() => toggle(task())}
                onToggleMax={() => toggleMax(task())}
                onDelete={() => remove(task())}
              />
            )}
          </Show>
        </aside>
      </div>
    </div>
  )
}

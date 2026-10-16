import { fireEvent, render, screen, waitFor } from '@solidjs/testing-library'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createTask, deleteTask, getTasks, runTaskNow, updateTask } from '../../services/api'
import type { Task, TaskRunResult } from '../../types/task'
import TasksPage from './TasksPage'

vi.mock('../../services/api', () => ({
  getTasks: vi.fn(),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  deleteTask: vi.fn(),
  runTaskNow: vi.fn(),
}))

function task(over: Partial<Task> = {}): Task {
  return {
    id: 1,
    name: 'briefing',
    description: '',
    schedule_cron: 'daily',
    prompt: 'summarise AI news',
    enabled: true,
    last_run: null,
    next_run: '2026-10-16T09:00:00+00:00',
    last_result_summary: null,
    source_type: 'user',
    created_at: '2026-10-15T09:00:00+00:00',
    ...over,
  }
}

describe('TasksPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(getTasks).mockResolvedValue([])
  })

  it('says so when there are no tasks', async () => {
    render(() => <TasksPage />)
    await waitFor(() => expect(screen.getByText(/No tasks yet/i)).toBeInTheDocument())
  })

  it('lists a task with its real state', async () => {
    vi.mocked(getTasks).mockResolvedValue([task({ name: 'briefing' })])
    render(() => <TasksPage />)
    expect(await screen.findByText('briefing')).toBeInTheDocument()
    expect(screen.getByText('Daily')).toBeInTheDocument()
  })

  it('shows a paused task as paused', async () => {
    vi.mocked(getTasks).mockResolvedValue([task({ enabled: false, next_run: null })])
    render(() => <TasksPage />)
    expect(await screen.findByText('Paused')).toBeInTheDocument()
  })

  it('edits only the prompt, and sends only the prompt', async () => {
    // The write surface is two fields with two meanings; this pins that an edit
    // cannot quietly grow a third. The server forbids extras too (`extra="forbid"`).
    vi.mocked(getTasks).mockResolvedValue([task()])
    vi.mocked(updateTask).mockResolvedValue(task({ prompt: 'new prompt' }))
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    const editor = await screen.findByLabelText('What it runs')
    fireEvent.input(editor, { target: { value: 'new prompt' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(updateTask).toHaveBeenCalled())
    expect(updateTask).toHaveBeenCalledWith(1, { prompt: 'new prompt' })
  })

  it('pauses and resumes through enabled alone', async () => {
    vi.mocked(getTasks).mockResolvedValue([task()])
    vi.mocked(updateTask).mockResolvedValue(task({ enabled: false, next_run: null }))
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }))

    await waitFor(() => expect(updateTask).toHaveBeenCalledWith(1, { enabled: false }))
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeInTheDocument()
  })

  it('creates a task and shows it without a reload', async () => {
    vi.mocked(createTask).mockResolvedValue(task({ name: 'new one', prompt: 'do it' }))
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByRole('button', { name: /New task/i }))
    fireEvent.input(screen.getByLabelText('Task name'), { target: { value: 'new one' } })
    fireEvent.input(screen.getByLabelText('Task prompt'), { target: { value: 'do it' } })
    fireEvent.click(screen.getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(createTask).toHaveBeenCalledWith('new one', 'do it'))
    // It is in the list *and* open in the detail panel — without a reload, which the
    // call count below pins.
    expect(await screen.findByRole('heading', { name: 'new one' })).toBeInTheDocument()
    expect(getTasks).toHaveBeenCalledTimes(1)
  })

  it('keeps the edit on screen when a save fails', async () => {
    // Regression, reused from the map page: a failed save must not discard what the
    // user typed, and must say that it failed.
    vi.mocked(getTasks).mockResolvedValue([task()])
    vi.mocked(updateTask).mockRejectedValue(new Error('nope'))
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    const editor = await screen.findByLabelText('What it runs')
    fireEvent.input(editor, { target: { value: 'typed but unsaved' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.getByText(/Could not save/i)).toBeInTheDocument())
    expect(screen.getByLabelText('What it runs')).toHaveValue('typed but unsaved')
  })

  it('renders the last run summary as markdown, not its source', async () => {
    vi.mocked(getTasks).mockResolvedValue([
      task({ last_result_summary: '## Findings\n\n- a **bold** item' }),
    ])
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))

    // It is markup now, not characters.
    expect(await screen.findByRole('heading', { name: 'Findings' })).toBeInTheDocument()
    expect(screen.getByText('bold')).toBeInTheDocument()
    expect(screen.queryByText(/## Findings/)).not.toBeInTheDocument()
  })

  it('runs the prompt for real, after saying what it costs', async () => {
    vi.mocked(getTasks).mockResolvedValue([task()])
    vi.mocked(runTaskNow).mockResolvedValue({
      name: 'briefing',
      success: true,
      result_summary: '## fresh result',
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))

    // Declined: nothing ran.
    expect(confirm).toHaveBeenCalled()
    expect(runTaskNow).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))
    await waitFor(() => expect(runTaskNow).toHaveBeenCalledWith('briefing'))
    // The list is re-read, because the run moved last_run, the summary and possibly
    // `enabled` — the page does not guess which fields changed.
    await waitFor(() => expect(getTasks).toHaveBeenCalledTimes(2))
    confirm.mockRestore()
  })

  it('says what a run costs before spending it', async () => {
    vi.mocked(getTasks).mockResolvedValue([task({ schedule_cron: 'once' })])
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))

    // A `once` task disables itself when it runs, so the prompt says so rather than
    // calling this a harmless "test".
    expect(confirm.mock.calls[0][0]).toMatch(/switches itself off/i)
    confirm.mockRestore()
  })

  it('shows progress while the run is in flight', async () => {
    vi.mocked(getTasks).mockResolvedValue([task()])
    let release: (r: TaskRunResult) => void = () => {}
    vi.mocked(runTaskNow).mockReturnValue(new Promise((resolve) => { release = resolve }))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    fireEvent.click(screen.getByRole('button', { name: 'Run now' }))

    // The wait is opaque, so the panel says it is working rather than looking hung,
    // and the controls are locked while it runs.
    expect(await screen.findByText(/Running/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run now' })).toBeDisabled()

    release({ name: 'briefing', success: true, result_summary: 'ok' })
    await waitFor(() => expect(screen.queryByText(/Running/)).not.toBeInTheDocument())
    confirm.mockRestore()
  })

  it('asks before deleting', async () => {
    vi.mocked(getTasks).mockResolvedValue([task()])
    vi.mocked(deleteTask).mockResolvedValue({ status: 'ok' })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    render(() => <TasksPage />)

    fireEvent.click(await screen.findByText('briefing'))
    fireEvent.click(await screen.findByRole('button', { name: 'Delete' }))

    expect(confirm).toHaveBeenCalled()
    expect(deleteTask).not.toHaveBeenCalled()
    confirm.mockRestore()
  })
})

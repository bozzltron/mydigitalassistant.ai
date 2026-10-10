import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FileGrid } from './FileGrid'
import * as api from '../../services/api'
import type { FileEntry } from '../../types'

vi.mock('../../services/api', () => ({
  listFiles: vi.fn(),
  deleteFile: vi.fn(),
}))

const files: FileEntry[] = [
  {
    id: '1', name: 'notes', file_name: 'notes.md', file_ext: 'md', file_size: 2048,
    type: 'file', created_at: '2024-01-01T00:00:00Z', updated_at: '2024-01-01T00:00:00Z',
  },
  {
    id: '2', name: 'report', file_name: 'report.pdf', file_ext: 'pdf', file_size: 1048576,
    type: 'file', created_at: '2024-01-02T00:00:00Z', updated_at: '2024-01-02T00:00:00Z',
  },
]

describe('FileGrid', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(api.listFiles).mockResolvedValue(files)
  })

  it('renders the size from file_size instead of NaN (regression: the list response had no size)', async () => {
    render(() => <FileGrid />)
    await screen.findByText('notes.md')
    expect(screen.getByText('2.0 KB')).toBeInTheDocument()
    expect(screen.getByText('1.0 MB')).toBeInTheDocument()
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument()
  })

  it('links each row to the download endpoint', async () => {
    render(() => <FileGrid />)
    await screen.findByText('notes.md')

    // Rows are sorted newest-updated first, so report.pdf (id 2) precedes
    // notes.md (id 1).
    const downloadLinks = screen.getAllByText('Download').map((el) => el.closest('a'))
    expect(downloadLinks[0]?.getAttribute('href')).toBe('/files/2/download')
    expect(downloadLinks[1]?.getAttribute('href')).toBe('/files/1/download')
  })

  it('sorts by last updated, newest first, regardless of the input order', async () => {
    // Reverse the fixture so the order cannot be an accident of how the backend
    // returned it (the backend lists by id, oldest first).
    vi.mocked(api.listFiles).mockResolvedValue([...files].reverse())
    render(() => <FileGrid />)
    await screen.findByText('report.pdf')

    const names = Array.from(document.querySelectorAll('.file-name')).map(
      (el) => el.textContent
    )
    expect(names).toEqual(['report.pdf', 'notes.md'])
  })

  it('asks for confirmation before deleting and does nothing on cancel', async () => {
    render(() => <FileGrid />)
    await screen.findByText('notes.md')

    fireEvent.click(screen.getAllByText('Delete')[0])
    // The confirmation modal is open.
    expect(document.querySelector('.modal')).not.toBeNull()

    fireEvent.click(screen.getByText('Cancel'))
    expect(api.deleteFile).not.toHaveBeenCalled()
  })

  it('deletes only after the confirmation is accepted', async () => {
    vi.mocked(api.deleteFile).mockResolvedValue({ success: true })
    render(() => <FileGrid />)
    await screen.findByText('notes.md')

    // Sorted newest-first, the first row is report.pdf (id 2).
    fireEvent.click(screen.getAllByText('Delete')[0])
    const deleteButtons = screen.getAllByText('Delete')
    fireEvent.click(deleteButtons[deleteButtons.length - 1])

    await vi.waitFor(() => expect(api.deleteFile).toHaveBeenCalledWith('2'))
  })
})

import { render, screen, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, vi, afterEach } from 'vitest'
import FilesPage from './FilesPage'

// The chat deep-links to a file with `/files?file=<frame_id>`; the page opens it.
// The frame id is stable across renames, so the link survives one.

const hoisted = vi.hoisted(() => ({ params: {} as Record<string, string> }))

vi.mock('@solidjs/router', () => ({
  useSearchParams: () => [hoisted.params],
}))
vi.mock('./FileGrid', () => ({
  FileGrid: (props: { selectedFileId?: string | null }) => (
    <div class="grid">grid:{props.selectedFileId ?? 'none'}</div>
  ),
}))
vi.mock('./UploadZone', () => ({ UploadZone: () => <div class="upload" /> }))
vi.mock('./FileViewer', () => ({
  FileViewer: (props: { fileId?: string | null }) => (
    <div class="viewer">viewer:{props.fileId ?? 'none'}</div>
  ),
}))

afterEach(() => {
  cleanup()
  hoisted.params = {}
})

describe('FilesPage', () => {
  it('opens the file named by the ?file= deep link, and marks it in the list', () => {
    hoisted.params = { file: '7' }
    render(() => <FilesPage />)
    expect(screen.getByText('viewer:7')).toBeInTheDocument()
    // The list is told which file is open, so it can highlight the row.
    expect(screen.getByText('grid:7')).toBeInTheDocument()
  })

  it('selects no file when there is no deep link', () => {
    render(() => <FilesPage />)
    expect(screen.getByText('viewer:none')).toBeInTheDocument()
    expect(screen.getByText('grid:none')).toBeInTheDocument()
  })

  it('offers a back button to the chat', () => {
    render(() => <FilesPage />)
    const back = screen.getByText('Back to chat').closest('a')
    expect(back?.getAttribute('href')).toBe('/')
  })
})

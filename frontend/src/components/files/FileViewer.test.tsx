import { render, screen, fireEvent } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FileViewer } from './FileViewer'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  api: vi.fn(),
  renameFile: vi.fn(),
  saveFileContent: vi.fn(),
}))

const content = (over: Record<string, unknown>) => ({
  frame_id: 1,
  frame_name: 'file',
  content: '',
  file_name: 'file',
  file_ext: 'txt',
  file_size: 10,
  ...over,
})

describe('FileViewer', () => {
  beforeEach(() => vi.clearAllMocks())

  it('renders markdown as HTML (not raw text)', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: '# Title\n\nbody', file_name: 'notes.md', file_ext: 'md' }) as never,
    )
    render(() => <FileViewer fileId="1" />)

    await vi.waitFor(() => {
      const heading = document.querySelector('.file-content-markdown h1')
      expect(heading?.textContent).toBe('Title')
    })
    // The raw markdown is not shown as text.
    expect(document.querySelector('.file-content-text')).toBeNull()
  })

  it('offers to open a PDF in a new tab instead of "no preview"', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: '%PDF-1.4 binary', file_name: 'report.pdf', file_ext: 'pdf' }) as never,
    )
    render(() => <FileViewer fileId="9" />)

    await screen.findByText('PDF document')
    const link = screen.getByText('Open in new tab').closest('a')
    // Served inline so the browser renders it rather than saving it.
    expect(link?.getAttribute('href')).toBe('/files/9/download?inline=true')
    expect(link?.getAttribute('target')).toBe('_blank')
  })

  it('does not preview a binary document', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'PK binary', file_name: 'report.docx', file_ext: 'docx' }) as never,
    )
    render(() => <FileViewer fileId="1" />)

    await screen.findByText('No preview for this file type')
    expect(document.querySelector('.file-content-text')).toBeNull()
  })

  it('previews a text file as plain text', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'hello world', file_name: 'notes.txt', file_ext: 'txt' }) as never,
    )
    render(() => <FileViewer fileId="1" />)

    await screen.findByText('hello world')
    expect(document.querySelector('.file-content-text')).not.toBeNull()
  })

  it('offers a download link for the selected file', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'hi', file_name: 'notes.txt', file_ext: 'txt' }) as never,
    )
    render(() => <FileViewer fileId="7" />)

    await screen.findByText('Download')
    expect(screen.getByText('Download').closest('a')?.getAttribute('href')).toBe('/files/7/download')
  })

  it('copies the display name to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      configurable: true,
    })
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'hi', file_name: 'notes.txt', file_ext: 'txt' }) as never,
    )
    render(() => <FileViewer fileId="5" />)

    await screen.findByText('notes.txt')
    fireEvent.click(screen.getByText('Copy name'))

    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith('notes.txt'))
  })

  it('edits a text file in place and saves it', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'old body', file_name: 'notes.txt', file_ext: 'txt' }) as never,
    )
    vi.mocked(api.saveFileContent).mockResolvedValue({ status: 'ok', size: 8 })
    render(() => <FileViewer fileId="5" />)

    await screen.findByText('old body')
    fireEvent.click(screen.getByText('Edit'))

    // The editor is seeded with the current content.
    const textarea = document.querySelector('.file-edit-textarea') as HTMLTextAreaElement
    expect(textarea.value).toBe('old body')

    fireEvent.input(textarea, { target: { value: 'new body' } })
    fireEvent.click(screen.getByText('Save'))

    await vi.waitFor(() =>
      expect(api.saveFileContent).toHaveBeenCalledWith('5', 'new body')
    )
  })

  it('does not offer to edit a binary document', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'PK binary', file_name: 'report.docx', file_ext: 'docx' }) as never,
    )
    render(() => <FileViewer fileId="1" />)

    await screen.findByText('No preview for this file type')
    expect(screen.queryByText('Edit')).toBeNull()
  })

  it('renames via a modal that edits the base name and keeps the extension', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: 'hi', file_name: 'notes.txt', file_ext: 'txt' }) as never,
    )
    vi.mocked(api.renameFile).mockResolvedValue({
      status: 'ok',
      path: 'renamed.txt',
      old_path: 'notes.txt',
    })
    render(() => <FileViewer fileId="5" />)

    await screen.findByText('notes.txt')
    fireEvent.click(screen.getByText('Rename'))

    // Seeded with the base name; the extension is shown, not editable.
    const input = document.getElementById('rename-input') as HTMLInputElement
    expect(input.value).toBe('notes')
    expect(document.querySelector('.rename-ext')?.textContent).toBe('.txt')

    fireEvent.input(input, { target: { value: 'renamed' } })
    fireEvent.click(screen.getByText('Save'))

    await vi.waitFor(() =>
      expect(api.renameFile).toHaveBeenCalledWith('5', 'renamed.txt')
    )
  })
})

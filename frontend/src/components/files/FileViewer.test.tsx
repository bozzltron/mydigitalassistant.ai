import { render, screen } from '@solidjs/testing-library'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { FileViewer } from './FileViewer'
import * as api from '../../services/api'

vi.mock('../../services/api', () => ({
  api: vi.fn(),
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

  it('does not preview a binary file', async () => {
    vi.mocked(api.api).mockResolvedValue(
      content({ content: '%PDF-1.4 binary', file_name: 'report.pdf', file_ext: 'pdf' }) as never,
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
})

import { render, cleanup } from '@solidjs/testing-library'
import { describe, it, expect, afterEach } from 'vitest'
import MessageContent from './MessageContent'
import { setFileList } from '../../state/files'
import type { ChatMessage, FileEntry } from '../../types/chat'

// Regression: a filename mention was linked only after a page reload. The live
// turn rendered the message before the file list had loaded, and the link had to
// appear when the list arrived — so the render must track the list.

const entry = (id: string, name: string): FileEntry => ({
  id,
  name,
  file_name: name,
  file_ext: name.split('.').pop(),
  file_size: 10,
  type: 'file',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
})

afterEach(() => {
  cleanup()
  setFileList([])
})

describe('filename mention linking (live)', () => {
  it('links a mention when the file list arrives after the message rendered', async () => {
    setFileList([])
    const message: ChatMessage = {
      role: 'assistant',
      content: 'I updated report.md for you.',
      id: 'live-1',
      meta: { isStreaming: true },
    }
    const { container } = render(() => <MessageContent message={() => message} />)

    // Before the list loads there is nothing to link to.
    expect(container.querySelector('a.file-mention')).toBeNull()

    // The list arrives (the live turn's mount / files-changed refresh).
    setFileList([entry('7', 'report.md')])

    await Promise.resolve()
    const link = container.querySelector('a.file-mention')
    expect(link).not.toBeNull()
    expect(link?.getAttribute('href')).toBe('/files?file=7')
  })

  it('links immediately when the list is already loaded', () => {
    setFileList([entry('9', 'notes.txt')])
    const message: ChatMessage = {
      role: 'assistant',
      content: 'Saved notes.txt',
      id: 'live-2',
    }
    const { container } = render(() => <MessageContent message={() => message} />)
    expect(container.querySelector('a.file-mention')?.getAttribute('href')).toBe(
      '/files?file=9'
    )
  })
})

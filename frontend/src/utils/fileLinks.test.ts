import { describe, it, expect } from 'vitest'
import { fileLinksFrom, linkifyFileMentions } from './fileLinks'
import type { FileEntry } from '../types'

// A filename the agent mentions should be one click from its contents — but only
// when it names a file that exists. A mention is matched against the loaded file
// list, never a filename-shaped pattern, so there are no dead links.

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

const links = fileLinksFrom([
  entry('7', 'report.md'),
  entry('9', 'subscribers_active.csv'),
])

describe('fileLinksFrom', () => {
  it('ignores names too short to link safely', () => {
    expect(fileLinksFrom([entry('1', 'a.md')])).toEqual([])
  })

  it('uses the display name and dedupes', () => {
    const l = fileLinksFrom([entry('7', 'report.md'), entry('8', 'Report.md')])
    expect(l).toEqual([{ id: '7', name: 'report.md' }])
  })
})

describe('linkifyFileMentions', () => {
  it('links a mention of a known file to its Files-page deep link', () => {
    const html = linkifyFileMentions('<p>I updated report.md for you.</p>', links)
    expect(html).toContain('href="/files?file=7"')
    expect(html).toContain('>report.md</a>')
  })

  it('leaves a filename that is not a known file as plain text', () => {
    const html = linkifyFileMentions('<p>see notes.md</p>', links)
    expect(html).not.toContain('<a')
    expect(html).toContain('notes.md')
  })

  it('does not linkify a name glued to a larger word', () => {
    const html = linkifyFileMentions('<p>myreport.md then report.md</p>', links)
    expect(html.match(/<a /g)?.length).toBe(1)
  })

  it('linkifies inside inline code, but not a code block or an existing link', () => {
    expect(linkifyFileMentions('<p><code>report.md</code></p>', links)).toContain(
      'href="/files?file=7"'
    )
    expect(linkifyFileMentions('<pre>report.md</pre>', links)).not.toContain('<a')
    const existing = linkifyFileMentions(
      '<a href="https://x">report.md</a>',
      links
    )
    expect(existing.match(/<a /g)?.length).toBe(1)
  })

  it('returns the html unchanged when there are no files', () => {
    const html = '<p>report.md</p>'
    expect(linkifyFileMentions(html, [])).toBe(html)
  })
})

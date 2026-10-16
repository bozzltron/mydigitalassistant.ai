import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown', () => {
  it('renders markdown as markup', () => {
    expect(renderMarkdown('**bold**')).toContain('<strong>')
    expect(renderMarkdown('## Heading')).toContain('<h2')
  })

  it('renders links, with rel but no target — the guard opens the tab', () => {
    // `target="_blank"` is set by the `link` override and then dropped by DOMPurify, so
    // it has never been what opens a new tab: `ExternalLinkGuard` intercepts the click
    // and calls `window.open(..., 'noopener,noreferrer')` after confirming. Pinned here
    // so nobody re-adds the attribute expecting it to work.
    const html = renderMarkdown('[example](https://example.com)')
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('rel="noopener"')
    expect(html).not.toContain('target=')
  })

  it('sanitizes — the input can be model output or a fetched document', () => {
    expect(renderMarkdown('<script>alert(1)</script>')).not.toContain('<script>')
    expect(renderMarkdown('<img src=x onerror="alert(1)">')).not.toContain('onerror')
  })
})

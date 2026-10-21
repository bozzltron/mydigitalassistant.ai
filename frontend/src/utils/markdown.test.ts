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

  it('routes markdown images through the backend proxy', () => {
    // The model may place an image inline. Rendering the third-party URL directly
    // would make the browser contact that host — the same IP leak /image-proxy
    // exists to prevent for search thumbnails.
    const html = renderMarkdown('![a ship](https://cdn.example/ship.jpg)')
    expect(html).toContain('src="/image-proxy?url=https%3A%2F%2Fcdn.example%2Fship.jpg"')
    expect(html).toContain('alt="a ship"')
    expect(html).not.toContain('src="https://cdn.example')
  })

  it('keeps a title and escapes the alt', () => {
    const html = renderMarkdown('![a "quote"](https://x.example/a.png "Ship")')
    expect(html).toContain('title="Ship"')
    // The alt cannot break out of the attribute.
    expect(html.match(/<img /g)).toHaveLength(1)
  })

  it('leaves a non-http image source alone', () => {
    const html = renderMarkdown('![x](data:image/png;base64,AAAA)')
    expect(html).not.toContain('/image-proxy')
  })
})

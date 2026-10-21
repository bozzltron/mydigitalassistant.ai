import { describe, expect, it } from 'vitest'
import { sandboxedHtml } from './htmlPreview'

const CSP = 'Content-Security-Policy'

describe('sandboxedHtml', () => {
  it('puts the CSP inside <head> for a full document', () => {
    const out = sandboxedHtml(
      '<!DOCTYPE html><html><head><title>x</title></head><body><p>hi</p></body></html>',
    )
    expect(out.indexOf(CSP)).toBeGreaterThan(out.indexOf('<head>'))
    expect(out.indexOf(CSP)).toBeLessThan(out.indexOf('<title>'))
  })

  it('keeps the doctype first, so the document does not go quirks mode', () => {
    const out = sandboxedHtml('<!DOCTYPE html><p>hi</p>')
    expect(out.startsWith('<!DOCTYPE html>')).toBe(true)
    expect(out).toContain(CSP)
  })

  it('prepends when there is no head or doctype', () => {
    expect(sandboxedHtml('<p>hi</p>').startsWith('<meta')).toBe(true)
  })

  it('forbids network egress but allows inline style and script', () => {
    const out = sandboxedHtml('<p>x</p>')
    expect(out).toContain("default-src 'none'")
    expect(out).toContain("style-src 'unsafe-inline'")
    expect(out).toContain("script-src 'unsafe-inline'")
    // No remote origin is permitted, so a report cannot phone home.
    expect(out).not.toContain('https:')
  })
})

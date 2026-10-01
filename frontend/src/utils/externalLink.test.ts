import { describe, it, expect } from 'vitest'
import { isExternalHttpUrl } from './externalLink'

describe('isExternalHttpUrl', () => {
  it('is true for off-origin http(s) URLs', () => {
    expect(isExternalHttpUrl('https://example.com/a')).toBe(true)
    expect(isExternalHttpUrl('http://other.test/b')).toBe(true)
  })

  it('is false for relative, same-origin, and non-http links', () => {
    expect(isExternalHttpUrl('/brain')).toBe(false)
    expect(isExternalHttpUrl('https://example.com/a')).toBe(true)
    expect(isExternalHttpUrl(`${window.location.origin}/x`)).toBe(false)
    expect(isExternalHttpUrl('mailto:someone@example.com')).toBe(false)
    expect(isExternalHttpUrl('tel:+15551234')).toBe(false)
    expect(isExternalHttpUrl('#section')).toBe(false)
  })

  it('is false for empty and malformed input', () => {
    expect(isExternalHttpUrl(null)).toBe(false)
    expect(isExternalHttpUrl(undefined)).toBe(false)
    expect(isExternalHttpUrl('')).toBe(false)
    expect(isExternalHttpUrl('not a url')).toBe(false)
  })
})

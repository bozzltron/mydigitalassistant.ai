import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  ACCEPT_ATTR,
  SUPPORTED_UPLOAD_EXTS,
  isSupportedUploadExt,
} from './uploadFormats'

// Vitest runs with the frontend directory as cwd, so the repo root is one level
// up. Reading the backend source directly is what makes this a real contract
// test rather than two copies of a list that drift together.
const repoFile = (rel: string) => resolve(process.cwd(), rel)

/**
 * The formats the backend accepts, read from its source rather than restated
 * here.
 *
 * This is the guard for the bug it fixes: `UploadZone` offered the full set
 * while `InputBar` offered six, so the attach button silently refused PDFs and
 * Office documents the assistant supports. A second hardcoded copy in this test
 * would drift the same way, so the backend file is parsed directly — if either
 * side changes without the other, this fails.
 */
function backendUploadExts(): string[] {
  const source = readFileSync(
    repoFile('../assistant/backend/pipeline/files.py'),
    'utf8',
  )
  const block = source.match(/SUPPORTED_UPLOAD_EXTS\s*=\s*frozenset\(\s*\{([^}]*)\}/)
  if (!block) {
    throw new Error('could not find SUPPORTED_UPLOAD_EXTS in the backend source')
  }
  return [...block[1].matchAll(/"([a-z0-9]+)"/g)].map((m) => m[1]).sort()
}

describe('upload formats mirror the backend', () => {
  it('offers exactly the extensions the backend accepts', () => {
    expect([...SUPPORTED_UPLOAD_EXTS].sort()).toEqual(backendUploadExts())
  })

  it('builds the accept attribute from the same list', () => {
    expect(ACCEPT_ATTR).toBe(SUPPORTED_UPLOAD_EXTS.map((e) => `.${e}`).join(','))
  })

  it('includes the formats that were missing from the input bar', () => {
    // The specific regression: these were in UploadZone's accept but not
    // InputBar's, so attaching them from chat did nothing.
    for (const ext of ['pdf', 'docx', 'xlsx', 'pptx', 'xls', 'rtf', 'odt', 'ods', 'odp', 'tsv', 'eml']) {
      expect(isSupportedUploadExt(ext)).toBe(true)
    }
  })

  it('does not advertise the legacy binaries the backend rejects', () => {
    // .doc/.ppt have no offline reader; the backend asks the user to re-save.
    // Offering them in accept would accept a file that then fails.
    expect(isSupportedUploadExt('doc')).toBe(false)
    expect(isSupportedUploadExt('ppt')).toBe(false)
  })

  it('is case-insensitive', () => {
    expect(isSupportedUploadExt('PDF')).toBe(true)
    expect(isSupportedUploadExt('DocX')).toBe(true)
  })
})

describe('no component hardcodes its own accept list', () => {
  it('InputBar and UploadZone both derive it', () => {
    const files = [
      'src/components/chat/InputBar.tsx',
      'src/components/files/UploadZone.tsx',
    ]
    for (const rel of files) {
      const source = readFileSync(repoFile(rel), 'utf8')
      expect(source).toContain('ACCEPT_ATTR')
      // A literal `.pdf,.docx,...` string would be a second source of truth.
      expect(source).not.toMatch(/accept="\.\w+,/)
    }
  })
})

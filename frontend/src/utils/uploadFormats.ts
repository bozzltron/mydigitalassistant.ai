/**
 * The file formats the backend accepts, in one place.
 *
 * This mirrors `SUPPORTED_UPLOAD_EXTS` in `assistant/backend/pipeline/files.py`.
 * The two drifted once: `UploadZone` offered the full set while `InputBar`
 * offered six, so the attach button silently refused formats the assistant
 * supports. An `accept` that under-declares is a bug the user sees as "it won't
 * take my PDF"; over-declaring is worse, because the file is accepted and then
 * fails later. Deriving both from one list — and asserting it against the
 * backend — is the fix.
 *
 * Legacy Word/PowerPoint binaries (`.doc`/`.ppt`) are deliberately absent: there
 * is no good offline pure-Python reader, and the backend asks the user to
 * re-save as `.docx`/`.pdf`. Legacy Excel (`.xls`) is supported, via xlrd.
 *
 * Formats dropped from this set (`.rtf`, `.eml`, `.tsv`, `.html`, `.xml`) remain
 * READABLE by the backend for files already on disk — they are simply no longer
 * accepted for new uploads. This list is the upload allowlist, not the read set.
 */
export const SUPPORTED_UPLOAD_EXTS = [
  'txt',
  'md',
  'csv',
  'json',
  'ics',
  'pdf',
  'docx',
  'odt',
  'xlsx',
  'xls',
  'ods',
  'pptx',
  'odp',
] as const

/** The value for an `<input type="file">` `accept` attribute. */
export const ACCEPT_ATTR = SUPPORTED_UPLOAD_EXTS.map((e) => `.${e}`).join(',')

/** True when the assistant accepts this extension (lower-case, no dot). */
export function isSupportedUploadExt(ext: string): boolean {
  return (SUPPORTED_UPLOAD_EXTS as readonly string[]).includes(ext.toLowerCase())
}

import { createSignal } from 'solid-js'
import { listFiles } from '../services/api'
import type { FileEntry } from '../types'

/**
 * The household's files, shared across the app so two things can use one fetch:
 * the chat links a filename mention to the Files page (`utils/fileLinks`), and
 * the Files page resolves a deep link (`/files?file=<id>`).
 *
 * Refreshed on mount and whenever a tool changes files (the `files-changed`
 * window event the chat already dispatches).
 */
const [fileList, setFileList] = createSignal<FileEntry[]>([])

export { fileList }

export async function refreshFileList(): Promise<void> {
  try {
    setFileList(await listFiles())
  } catch (error) {
    // Non-fatal: the chat still renders, just without filename links.
    console.debug('Could not refresh the file list', error)
  }
}

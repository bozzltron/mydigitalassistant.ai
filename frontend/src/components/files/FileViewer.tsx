import { createSignal, Show, createMemo, createEffect } from 'solid-js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { api, renameFile, saveFileContent } from '../../services/api';
import { Toast } from '../ui/Toast';
import { Modal } from '../ui/Modal';
import { FileIcon, fileKind } from '../ui/Icons';

interface FileContentResponse {
  frame_id: number;
  frame_name: string;
  content: string;
  file_name: string | null;
  file_ext: string | null;
  file_size: number | null;
}

// Extensions whose bytes are readable text. Anything else is treated as binary:
// the content route decodes with errors="replace", so a PDF or .docx rendered as
// text is noise, not a preview.
const TEXT_EXTS = new Set([
  'txt', 'csv', 'tsv', 'json', 'xml', 'html', 'htm', 'yaml', 'yml', 'toml',
  'ini', 'cfg', 'conf', 'log', 'eml', 'ics', 'srt', 'vtt',
  'py', 'js', 'jsx', 'ts', 'tsx', 'css', 'scss', 'sh', 'bash', 'zsh',
  'sql', 'rs', 'go', 'java', 'c', 'h', 'cpp', 'hpp', 'rb', 'php', 'swift', 'kt',
]);

const MARKDOWN_EXTS = new Set(['md', 'markdown']);

export const FileViewer = (props: { fileId?: string | null }) => {
  const [fileData, setFileData] = createSignal<FileContentResponse | null>(null);
  const [isLoading, setIsLoading] = createSignal(false);
  const [toast, setToast] = createSignal<{ message: string; type: 'success' | 'error' } | null>(null);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadFile = async () => {
    const fileId = props.fileId;
    if (!fileId) {
      setFileData(null);
      return;
    }

    setIsLoading(true);
    try {
      const data = await api<FileContentResponse>(`/files/${fileId}/content`);
      setFileData(data);
    } catch (error) {
      console.error('Failed to load file:', error);
      showToast('Failed to load file content', 'error');
      setFileData(null);
    } finally {
      setIsLoading(false);
    }
  };

  createEffect(() => {
    const fileId = props.fileId;
    if (fileId) {
      loadFile();
    } else {
      setFileData(null);
    }
  });

  const ext = createMemo(() => (fileData()?.file_ext || '').toLowerCase());
  const isMarkdown = createMemo(() => MARKDOWN_EXTS.has(ext()));
  const isText = createMemo(() => TEXT_EXTS.has(ext()));
  // The browser renders PDFs natively, so a PDF opens in a tab instead of
  // showing "no preview".
  const isPdf = createMemo(() => ext() === 'pdf');

  const displayName = createMemo(
    () => fileData()?.file_name || fileData()?.frame_name || ''
  );
  // The rename modal edits the base name; the extension is fixed (the backend
  // refuses a change, and the file's bytes would not match a new one anyway).
  const baseName = createMemo(() => {
    const name = displayName();
    const e = ext();
    return e && name.toLowerCase().endsWith(`.${e}`)
      ? name.slice(0, -(e.length + 1))
      : name;
  });

  const [renameOpen, setRenameOpen] = createSignal(false);
  const [renameDraft, setRenameDraft] = createSignal('');
  const [isRenaming, setIsRenaming] = createSignal(false);

  // In-place editing: text files only (a textarea cannot round-trip binary bytes,
  // and the backend refuses them with 415 anyway).
  const canEdit = createMemo(() => isText() || isMarkdown());
  const [isEditing, setIsEditing] = createSignal(false);
  const [editDraft, setEditDraft] = createSignal('');
  const [isSavingEdit, setIsSavingEdit] = createSignal(false);

  const startEdit = () => {
    setEditDraft(fileData()?.content ?? '');
    setIsEditing(true);
  };

  const saveEdit = async () => {
    const fileId = props.fileId;
    if (!fileId) return;
    setIsSavingEdit(true);
    try {
      await saveFileContent(fileId, editDraft());
      setIsEditing(false);
      await loadFile();
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('files-changed'));
      }
      showToast('File saved', 'success');
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to save file',
        'error'
      );
    } finally {
      setIsSavingEdit(false);
    }
  };

  const copyName = async () => {
    try {
      await navigator.clipboard.writeText(displayName());
      showToast('File name copied', 'success');
    } catch {
      showToast('Could not copy the file name', 'error');
    }
  };

  const openRename = () => {
    setRenameDraft(baseName());
    setRenameOpen(true);
  };

  const saveRename = async () => {
    const fileId = props.fileId;
    const draft = renameDraft().trim();
    if (!fileId || !draft) return;
    setIsRenaming(true);
    try {
      await renameFile(fileId, `${draft}.${ext()}`);
      setRenameOpen(false);
      // The frame id is unchanged by a rename, so reload by id to pick up the
      // new name, and let the grid refresh.
      await loadFile();
      if (typeof window !== 'undefined') {
        window.dispatchEvent(new CustomEvent('files-changed'));
      }
      showToast('File renamed', 'success');
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : 'Failed to rename file',
        'error'
      );
    } finally {
      setIsRenaming(false);
    }
  };
  // Sanitized before it touches innerHTML, exactly like chat markdown.
  const markdownHtml = createMemo(() =>
    DOMPurify.sanitize(marked.parse(fileData()?.content || '') as string)
  );

  const formatFileSize = (bytes: number | null | undefined) => {
    if (!bytes) return 'Unknown';
    if (bytes < 1024) return bytes + ' B';
    else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    else return (bytes / 1048576).toFixed(1) + ' MB';
  };

  return (
    <div class="file-viewer">
      <Show when={isLoading()}>
        <div class="loading">Loading file...</div>
      </Show>

      <Show when={!isLoading() && fileData()}>
        <div class="file-content">
          <div class="file-header">
            <span class="file-icon-large"><FileIcon kind={fileKind(fileData()?.file_ext)} size={24} /></span>
            <div class="file-meta-info">
              <h3>{fileData()?.file_name || fileData()?.frame_name}</h3>
              <div class="file-details">
                <span class="file-ext">{fileData()?.file_ext?.toUpperCase() || 'FILE'}</span>
                <span class="file-size">{formatFileSize(fileData()?.file_size)}</span>
              </div>
            </div>
            <div class="file-viewer-actions">
              <Show when={canEdit() && !isEditing()}>
                <button class="btn-secondary" onClick={startEdit}>Edit</button>
              </Show>
              <button class="btn-secondary" onClick={copyName}>Copy name</button>
              <button class="btn-secondary" onClick={openRename}>Rename</button>
              <a
                class="btn-secondary file-download"
                href={`/files/${props.fileId}/download`}
                download=""
              >
                Download
              </a>
            </div>
          </div>

          <Show when={isEditing()}>
            <textarea
              class="file-edit-textarea"
              value={editDraft()}
              onInput={(e) => setEditDraft(e.currentTarget.value)}
              spellcheck={false}
            />
            <div class="file-edit-actions">
              <button class="btn-secondary" onClick={() => setIsEditing(false)}>
                Cancel
              </button>
              <button
                class="btn-primary"
                onClick={() => void saveEdit()}
                disabled={isSavingEdit()}
              >
                {isSavingEdit() ? 'Saving…' : 'Save'}
              </button>
            </div>
          </Show>

          <Show when={!isEditing()}>
            <Show when={isMarkdown()}>
              {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by DOMPurify */}
              <div class="file-content-markdown" innerHTML={markdownHtml()} />
            </Show>
            <Show when={!isMarkdown() && isText()}>
              <pre class="file-content-text">{fileData()?.content || '(empty file)'}</pre>
            </Show>
            <Show when={!isMarkdown() && !isText()}>
              <Show
                when={isPdf()}
                fallback={
                  <div class="file-no-preview">
                    <p>No preview for this file type</p>
                    <p class="file-no-preview-hint">Download it to open it in the right app.</p>
                  </div>
                }
              >
                <div class="file-no-preview">
                  <p>PDF document</p>
                  <p class="file-no-preview-hint">The browser renders PDFs natively.</p>
                  <a
                    class="btn-primary"
                    href={`/files/${props.fileId}/download?inline=true`}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Open in new tab
                  </a>
                </div>
              </Show>
            </Show>
          </Show>
        </div>
      </Show>

      <Show when={!isLoading() && !fileData() && props.fileId}>
        <div class="error-state">
          <p>Failed to load file content</p>
          <p class="error-hint">The file may have been deleted or is inaccessible</p>
        </div>
      </Show>

      <Show when={!isLoading() && !fileData() && !props.fileId}>
        <div class="empty-state">
          <p>Select a file to view its content</p>
        </div>
      </Show>

      <Modal
        isOpen={renameOpen()}
        onClose={() => setRenameOpen(false)}
        title="Rename file"
        size="small"
      >
        <div class="modal-content">
          <label class="modal-field-label" for="rename-input">File name</label>
          <div class="rename-row">
            <input
              id="rename-input"
              class="modal-field"
              value={renameDraft()}
              onInput={(e) => setRenameDraft(e.currentTarget.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveRename()
              }}
              autofocus
            />
            <span class="rename-ext">.{ext()}</span>
          </div>
          <p class="modal-hint">
            The extension is kept. Renaming updates the file's memory so the agent
            knows it by the new name.
          </p>
          <div class="modal-actions">
            <button class="btn-secondary" onClick={() => setRenameOpen(false)}>
              Cancel
            </button>
            <button
              class="btn-primary"
              onClick={() => void saveRename()}
              disabled={isRenaming() || !renameDraft().trim()}
            >
              {isRenaming() ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>
      </Modal>

      <Show when={toast()}>
        <Toast message={toast()!.message} type={toast()!.type} />
      </Show>
    </div>
  );
};

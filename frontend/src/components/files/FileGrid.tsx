import { createSignal, Show, For, createEffect, onCleanup } from 'solid-js';
import { listFiles, deleteFile } from '../../services/api';
import { Toast } from '../ui/Toast';
import { Modal } from '../ui/Modal';
import { FileIcon, fileKind } from '../ui/Icons';
import type { FileEntry } from '../../types';

export const FileGrid = (props: { onFileSelect?: (file: FileEntry) => void }) => {
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [isLoading, setIsLoading] = createSignal(false);
  const [toast, setToast] = createSignal<{ message: string; type: 'success' | 'error' } | null>(null);
  // The file a delete confirmation is open for, if any.
  const [pendingDelete, setPendingDelete] = createSignal<FileEntry | null>(null);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const loadFiles = async () => {
    setIsLoading(true);
    try {
      const data = await listFiles();
      setFiles(data);
    } catch (error) {
      console.error('Failed to load files:', error);
      showToast('Failed to load files', 'error');
    } finally {
      setIsLoading(false);
    }
  };

  createEffect(() => {
    loadFiles();
  });

  // Listen for file changes from tool calls (write_file, edit_file, delete_file, etc.)
  createEffect(() => {
    if (typeof window === 'undefined') return
    const handleFilesChanged = () => {
      loadFiles()
    }
    window.addEventListener('files-changed', handleFilesChanged)
    onCleanup(() => {
      window.removeEventListener('files-changed', handleFilesChanged)
    })
  })

  const confirmDelete = async (file: FileEntry) => {
    setPendingDelete(null);
    try {
      await deleteFile(file.id);
      setFiles(prev => prev.filter(f => f.id !== file.id));
      showToast('File deleted', 'success');
    } catch (error) {
      console.error('Failed to delete file:', error);
      showToast('Failed to delete file', 'error');
    }
  };

  const displayName = (file: FileEntry) => file.file_name || file.name;

  const formatFileSize = (bytes: number | null | undefined) => {
    // The list response only carries a size once the backend supplies the
    // `file_size` slot; guard so a missing value reads as "—", never "NaN MB".
    if (bytes == null || Number.isNaN(bytes)) return '—';
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  };

  const formatDate = (dateStr: string) => {
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  return (
    <div class="file-grid">
      <div class="file-grid-header">
        <h3>Uploaded Files</h3>
        <button class="btn-secondary" onClick={loadFiles} disabled={isLoading()}>
          {isLoading() ? 'Refreshing...' : 'Refresh'}
        </button>
      </div>

      <Show when={isLoading()}>
        <div class="loading">Loading files...</div>
      </Show>

      <Show when={!isLoading() && files().length > 0}>
        <div class="files-container">
          <div class="files-table-header">
            <span>Name</span>
            <span>Size</span>
            <span>Date</span>
            <span>Actions</span>
          </div>
          <div class="files-list">
            <For each={files()}>{file => (
              <div class="file-item" onClick={() => props.onFileSelect?.(file)}>
                <div class="file-cell file-name-cell">
                  <span class="file-icon"><FileIcon kind={fileKind(file.file_ext || file.type)} /></span>
                  <span class="file-name" title={displayName(file)}>{displayName(file)}</span>
                </div>
                <div class="file-cell file-size-cell">
                  <span class="file-size">{formatFileSize(file.file_size)}</span>
                </div>
                <div class="file-cell file-date-cell">
                  <span class="file-date">{formatDate(file.created_at)}</span>
                </div>
                <div class="file-cell file-actions-cell">
                  <a
                    class="btn-secondary file-download"
                    href={`/files/${file.id}/download`}
                    download={displayName(file)}
                    onClick={(e) => e.stopPropagation()}
                  >
                    Download
                  </a>
                  <button
                    class="btn-secondary danger"
                    onClick={(e) => { e.stopPropagation(); setPendingDelete(file); }}
                  >
                    Delete
                  </button>
                </div>
              </div>
            )}</For>
          </div>
        </div>
      </Show>

      <Show when={!isLoading() && files().length === 0}>
        <div class="empty-state">
          <p>No files uploaded yet</p>
          <p class="empty-hint">Drag & drop files in the upload zone above</p>
        </div>
      </Show>

      <Modal
        isOpen={!!pendingDelete()}
        onClose={() => setPendingDelete(null)}
        title="Delete file"
        size="small"
      >
        <div class="modal-content">
          <p>
            Delete <strong>{pendingDelete() ? displayName(pendingDelete()!) : ''}</strong>?
            {' '}This removes the file and its memory, and cannot be undone.
          </p>
          <div class="modal-actions">
            <button class="btn-secondary" onClick={() => setPendingDelete(null)}>Cancel</button>
            <button
              class="btn-primary danger"
              onClick={() => { const file = pendingDelete(); if (file) void confirmDelete(file); }}
            >
              Delete
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

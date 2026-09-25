import { createSignal, Show, For, createEffect } from 'solid-js';
import { listFiles, deleteFile } from '../../services/api';
import { Toast } from '../ui/Toast';

interface FileEntry {
  id: string;
  name: string;
  file_name?: string | null;
  size: number;
  type: string;
  created_at: string;
  updated_at: string;
}

export const FileGrid = (props: { onFileSelect?: (file: FileEntry) => void }) => {
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [isLoading, setIsLoading] = createSignal(false);
  const [toast, setToast] = createSignal<{ message: string; type: 'success' | 'error' } | null>(null);

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

  const handleDelete = async (fileId: string) => {
    try {
      await deleteFile(fileId);
      setFiles(prev => prev.filter(f => f.id !== fileId));
      showToast('File deleted', 'success');
    } catch (error) {
      console.error('Failed to delete file:', error);
      showToast('Failed to delete file', 'error');
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    else return (bytes / 1048576).toFixed(1) + ' MB';
  };

  const formatDate = (dateStr: string) => {
    try {
      const date = new Date(dateStr);
      return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
    } catch {
      return dateStr;
    }
  };

  const getFileIcon = (type: string) => {
    if (type.startsWith('text/')) return '📄';
    if (type.startsWith('image/')) return '🖼️';
    if (type === 'application/json') return '📋';
    if (type === 'text/csv') return '📊';
    if (type === 'application/xml' || type === 'text/xml') return '📰';
    if (type === 'text/calendar') return '📅';
    return '📄';
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
          <div class="files-header">
            <span>Name</span>
            <span>Type</span>
            <span>Size</span>
            <span>Date</span>
            <span>Actions</span>
          </div>
          <div class="files-list">
            <For each={files()}>{file => (
              <div class="file-item" onClick={() => props.onFileSelect?.(file)}>
                <div class="file-cell file-name-cell">
                  <span class="file-icon">{getFileIcon(file.type)}</span>
                  <span class="file-name">{file.file_name || file.name}</span>
                </div>
                <div class="file-cell file-type-cell">
                  <span class="file-type">{file.type}</span>
                </div>
                <div class="file-cell file-size-cell">
                  <span class="file-size">{formatFileSize(file.size)}</span>
                </div>
                <div class="file-cell file-date-cell">
                  <span class="file-date">{formatDate(file.created_at)}</span>
                </div>
                <div class="file-cell file-actions-cell">
                  <button class="btn-secondary" onClick={(e) => { e.stopPropagation(); handleDelete(file.id); }}>
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

      <Show when={toast()}>
        <Toast message={toast()!.message} type={toast()!.type} />
      </Show>
    </div>
  );
};
import { createSignal, Show, createEffect } from 'solid-js';
import { api } from '../../services/api';
import { Toast } from '../ui/Toast';

interface FileContentResponse {
  frame_id: number;
  frame_name: string;
  content: string;
  file_name: string | null;
  file_ext: string | null;
  file_size: number | null;
}

export const FileViewer = (props: { fileId?: string }) => {
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

  const getFileIcon = (ext: string | null) => {
    if (!ext) return '📄';
    switch (ext) {
      case 'txt': return '📄';
      case 'csv': return '📊';
      case 'json': return '📋';
      case 'xml': return '📰';
      case 'html': return '🌐';
      case 'ics': return '📅';
      default: return '📄';
    }
  };

  const formatFileSize = (bytes: number | null) => {
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
            <span class="file-icon-large">{getFileIcon(fileData()?.file_ext)}</span>
            <div class="file-meta-info">
              <h3>{fileData()?.file_name || fileData()?.frame_name}</h3>
              <div class="file-details">
                <span class="file-ext">{fileData()?.file_ext?.toUpperCase() || 'FILE'}</span>
                <span class="file-size">{formatFileSize(fileData()?.file_size)}</span>
              </div>
            </div>
          </div>
          <pre class="file-content-text">{fileData()?.content || '(empty file)'}</pre>
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

      <Show when={toast()}>
        <Toast message={toast()!.message} type={toast()!.type} />
      </Show>
    </div>
  );
};
import { createSignal, Show, For } from 'solid-js';
import { postFileUpload } from '../../services/api';
import { Toast } from '../ui/Toast';

interface UploadedFile {
  id: number;
  name: string;
  size: number;
  status: 'uploading' | 'completed' | 'error';
  error?: string;
}

export const UploadZone = () => {
  const [files, setFiles] = createSignal<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = createSignal(false);
  const [toast, setToast] = createSignal<{ message: string; type: 'success' | 'error' } | null>(null);

  const showToast = (message: string, type: 'success' | 'error') => {
    setToast({ message, type });
    setTimeout(() => setToast(null), 4000);
  };

  const handleDragOver = (e: Event) => {
    e.preventDefault();
    setIsDragging(true);
  };

  const handleDragLeave = () => {
    setIsDragging(false);
  };

  const handleDrop = (e: Event) => {
    e.preventDefault();
    setIsDragging(false);

    if (e instanceof DragEvent && e.dataTransfer?.files) {
      const droppedFiles = Array.from(e.dataTransfer.files);
      processFiles(droppedFiles);
    }
  };

  const handleFileInput = (e: Event) => {
    if (e.target instanceof HTMLInputElement && e.target.files) {
      const selectedFiles = Array.from(e.target.files);
      processFiles(selectedFiles);
    }
  };

  const processFiles = async (fileList: File[]) => {
    for (const file of fileList) {
      const fileId = Date.now() + Math.random();
      const newFile: UploadedFile = {
        id: fileId,
        name: file.name,
        size: file.size,
        status: 'uploading'
      };

      setFiles(prev => [...prev, newFile]);

      try {
        await postFileUpload(file);
        setFiles(prev =>
          prev.map(f =>
            f.id === fileId ? { ...f, status: 'completed' } : f
          )
        );
        showToast(`Uploaded: ${file.name}`, 'success');
      } catch (error) {
        const errorMsg = error instanceof Error ? error.message : 'Upload failed';
        setFiles(prev =>
          prev.map(f =>
            f.id === fileId ? { ...f, status: 'error', error: errorMsg } : f
          )
        );
        showToast(`Failed: ${file.name} - ${errorMsg}`, 'error');
      }
    }
  };

  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    else return (bytes / 1048576).toFixed(1) + ' MB';
  };

  return (
    <div class="upload-zone">
      <h3>Upload Files</h3>

      <div
        class={`drop-area ${isDragging() ? 'dragging' : ''}`}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        <p>Drag & drop files here or click to browse</p>
        <input
          type="file"
          multiple
          onChange={handleFileInput}
          class="file-input"
          accept=".txt,.csv,.json,.xml,.html,.ics,.pdf,.docx,.xlsx,.pptx"
        />
      </div>

      <div class="uploaded-files">
        <Show when={files().length > 0}>
          <ul>
            <For each={files()}>{file => (
              <li class="file-item">
                <span class="file-name">{file.name}</span>
                <span class="file-size">{formatFileSize(file.size)}</span>
                <span class={`file-status ${file.status}`}>
                  {file.status === 'uploading' ? 'Uploading...' :
                   file.status === 'completed' ? 'Uploaded' :
                   file.error ? `Error: ${file.error}` : 'Error'}
                </span>
              </li>
            )}</For>
          </ul>
        </Show>
      </div>

      <Show when={toast()}>
        <Toast message={toast()!.message} type={toast()!.type} />
      </Show>
    </div>
  );
};
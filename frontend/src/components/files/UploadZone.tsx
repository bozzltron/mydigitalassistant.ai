import { createSignal, Show } from 'solid-js';

interface UploadedFile {
  id: number;
  name: string;
  size: number;
  status: 'uploading' | 'completed' | 'error';
}

export const UploadZone = () => {
  const [files, setFiles] = createSignal<UploadedFile[]>([]);
  const [isDragging, setIsDragging] = createSignal(false);
  
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
  
  const processFiles = (fileList: File[]) => {
    // Simulate uploading files
    const newFiles: UploadedFile[] = fileList.map((file, index) => ({
      id: Date.now() + index,
      name: file.name,
      size: file.size,
      status: 'uploading'
    }));
    
    setFiles(prev => [...prev, ...newFiles]);
    
    // Simulate upload completion
    setTimeout(() => {
      setFiles(prev => 
        prev.map(f => 
          newFiles.some(nf => nf.id === f.id) ? {...f, status: 'completed'} : f
        )
      );
    }, 2000);
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
        />
      </div>
      
      <div class="uploaded-files">
        <Show when={files().length > 0}>
          <ul>
            {files().map(file => (
              <li key={file.id} class="file-item">
                <span class="file-name">{file.name}</span>
                <span class="file-size">{formatFileSize(file.size)}</span>
                <span class={`file-status ${file.status}`}>
                  {file.status === 'uploading' ? 'Uploading...' : 
                   file.status === 'completed' ? 'Uploaded' : 'Error'}
                </span>
              </li>
            ))}
          </ul>
        </Show>
      </div>
    </div>
  );
};
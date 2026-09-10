import { createSignal, Show } from 'solid-js';
import { createEffect } from 'solid-js';

interface FileEntry {
  id: number;
  name: string;
  type: string;
  size: number;
  createdAt: string;
}

export const FileGrid = () => {
  const [files, setFiles] = createSignal<FileEntry[]>([]);
  const [isLoading, setIsLoading] = createSignal(false);
  
  // Mock data - in real app this would come from the backend API
  const loadFiles = async () => {
    setIsLoading(true);
    
    try {
      // Simulate API delay
      await new Promise(resolve => setTimeout(resolve, 500));
      
      const mockData: FileEntry[] = [
        { id: 1, name: 'document1.txt', type: 'text/plain', size: 1024, createdAt: '2023-06-15' },
        { id: 2, name: 'image1.png', type: 'image/png', size: 2048, createdAt: '2023-06-16' },
        { id: 3, name: 'data.json', type: 'application/json', size: 512, createdAt: '2023-06-17' },
      ];
      
      setFiles(mockData);
    } catch (error) {
      console.error('Failed to load files:', error);
    } finally {
      setIsLoading(false);
    }
  };
  
  createEffect(() => {
    loadFiles();
  });
  
  const formatFileSize = (bytes: number) => {
    if (bytes < 1024) return bytes + ' B';
    else if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    else return (bytes / 1048576).toFixed(1) + ' MB';
  };
  
  return (
    <div class="file-grid">
      <h3>Uploaded Files</h3>
      
      <Show when={isLoading()}>
        <div>Loading files...</div>
      </Show>
      
      <Show when={!isLoading() && files().length > 0}>
        <div class="files-container">
          <div class="files-header">
            <span>Name</span>
            <span>Type</span>
            <span>Size</span>
            <span>Date</span>
          </div>
          <div class="files-list">
            <For each={files()}>{file => (
              <div class="file-item" >
                <div class="file-info">
                  <span class="file-name">{file.name}</span>
                  <span class="file-type">{file.type}</span>
                </div>
                <div class="file-meta">
                  <span class="file-size">{formatFileSize(file.size)}</span>
                  <span class="file-date">{file.createdAt}</span>
                </div>
              </div>
            )}</For>
          </div>
        </div>
      </Show>
      
      <Show when={!isLoading() && files().length === 0}>
        <p>No files uploaded yet</p>
      </Show>
    </div>
  );
};
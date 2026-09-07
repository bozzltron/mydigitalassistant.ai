import { createSignal, Show } from 'solid-js';

interface FileData {
  id: number;
  name: string;
  content: string;
  type: string;
  size: number;
}

export const FileViewer = (props: { fileId?: number }) => {
  const [fileData, setFileData] = createSignal<FileData | null>(null);
  const [isLoading, setIsLoading] = createSignal(false);
  
  // Mock data - in real app this would fetch from API
  const loadFile = async () => {
    if (!props.fileId) return;
    
    setIsLoading(true);
    
    try {
      // Simulate API call
      await new Promise(resolve => setTimeout(resolve, 500));
      
      const mockData: FileData = {
        id: props.fileId,
        name: `document_${props.fileId}.txt`,
        content: "This is mock file content for demonstration purposes.\n\nIn a real implementation, this would contain the actual file content from the system.",
        type: 'text/plain',
        size: 1024
      };
      
      setFileData(mockData);
    } catch (error) {
      console.error('Failed to load file:', error);
    } finally {
      setIsLoading(false);
    }
  };
  
  // Load file when component mounts or fileId changes
  if (props.fileId) {
    loadFile();
  }
  
  return (
    <div class="file-viewer">
      <Show when={isLoading()}>
        <div>Loading file...</div>
      </Show>
      
      <Show when={!isLoading() && fileData()}>
        <div class="file-content">
          <h3>{fileData()?.name}</h3>
          <pre>{fileData()?.content}</pre>
        </div>
      </Show>
      
      <Show when={!isLoading() && !fileData()}>
        <p>Select a file to view its content</p>
      </Show>
    </div>
  );
};
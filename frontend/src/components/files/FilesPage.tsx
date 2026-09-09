import { FileGrid } from './FileGrid';
import { UploadZone } from './UploadZone';

export default function FilesPage() {
  return (
    <div class="files-page">
      <h1>Files</h1>
      
      <div class="files-content">
        <div class="upload-section">
          <UploadZone />
        </div>
        
        <div class="files-grid">
          <FileGrid />
        </div>
      </div>
    </div>
  );
}
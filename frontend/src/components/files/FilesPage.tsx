import { createSignal } from 'solid-js';
import { FileGrid } from './FileGrid';
import { UploadZone } from './UploadZone';
import { FileViewer } from './FileViewer';

export default function FilesPage() {
  const [selectedFileId, setSelectedFileId] = createSignal<string | null>(null);

  return (
    <div class="files-page">
      <div class="files-header">
        <h1>Files</h1>
      </div>

      <div class="files-content">
        <div class="files-main">
          <section class="upload-section">
            <UploadZone onUploadComplete={() => setSelectedFileId(null)} />
          </section>

          <section class="files-grid-section">
            <FileGrid onFileSelect={(file) => setSelectedFileId(file.id)} />
          </section>
        </div>

        <aside class="file-viewer-panel">
          <FileViewer fileId={selectedFileId()} />
        </aside>
      </div>
    </div>
  );
}
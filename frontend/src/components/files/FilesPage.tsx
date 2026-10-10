import { createSignal, createEffect } from 'solid-js';
import { useSearchParams } from '@solidjs/router';
import { FileGrid } from './FileGrid';
import { UploadZone } from './UploadZone';
import { FileViewer } from './FileViewer';

export default function FilesPage() {
  const [selectedFileId, setSelectedFileId] = createSignal<string | null>(null);
  const [searchParams] = useSearchParams();

  // Deep link from the chat: `/files?file=<frame_id>` opens that file in the
  // viewer. The frame id is stable across a rename, so a link survives one.
  createEffect(() => {
    const raw = searchParams.file;
    const id = Array.isArray(raw) ? raw[0] : raw;
    if (id) setSelectedFileId(String(id));
  });

  return (
    <div class="files-page">
      <div class="files-header">
        <h1>Files</h1>
        <a class="btn-secondary files-back" href="/">Back to chat</a>
      </div>

      <div class="files-content">
        <div class="files-main">
          <section class="upload-section">
            <UploadZone />
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

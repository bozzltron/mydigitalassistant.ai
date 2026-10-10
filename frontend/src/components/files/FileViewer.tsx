import { createSignal, Show, createMemo, createEffect } from 'solid-js';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { api } from '../../services/api';
import { Toast } from '../ui/Toast';
import { FileIcon, fileKind } from '../ui/Icons';

interface FileContentResponse {
  frame_id: number;
  frame_name: string;
  content: string;
  file_name: string | null;
  file_ext: string | null;
  file_size: number | null;
}

// Extensions whose bytes are readable text. Anything else is treated as binary:
// the content route decodes with errors="replace", so a PDF or .docx rendered as
// text is noise, not a preview.
const TEXT_EXTS = new Set([
  'txt', 'csv', 'tsv', 'json', 'xml', 'html', 'htm', 'yaml', 'yml', 'toml',
  'ini', 'cfg', 'conf', 'log', 'eml', 'ics', 'srt', 'vtt',
  'py', 'js', 'jsx', 'ts', 'tsx', 'css', 'scss', 'sh', 'bash', 'zsh',
  'sql', 'rs', 'go', 'java', 'c', 'h', 'cpp', 'hpp', 'rb', 'php', 'swift', 'kt',
]);

const MARKDOWN_EXTS = new Set(['md', 'markdown']);

export const FileViewer = (props: { fileId?: string | null }) => {
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

  const ext = createMemo(() => (fileData()?.file_ext || '').toLowerCase());
  const isMarkdown = createMemo(() => MARKDOWN_EXTS.has(ext()));
  const isText = createMemo(() => TEXT_EXTS.has(ext()));
  // The browser renders PDFs natively, so a PDF opens in a tab instead of
  // showing "no preview".
  const isPdf = createMemo(() => ext() === 'pdf');
  // Sanitized before it touches innerHTML, exactly like chat markdown.
  const markdownHtml = createMemo(() =>
    DOMPurify.sanitize(marked.parse(fileData()?.content || '') as string)
  );

  const formatFileSize = (bytes: number | null | undefined) => {
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
            <span class="file-icon-large"><FileIcon kind={fileKind(fileData()?.file_ext)} size={24} /></span>
            <div class="file-meta-info">
              <h3>{fileData()?.file_name || fileData()?.frame_name}</h3>
              <div class="file-details">
                <span class="file-ext">{fileData()?.file_ext?.toUpperCase() || 'FILE'}</span>
                <span class="file-size">{formatFileSize(fileData()?.file_size)}</span>
              </div>
            </div>
            <a
              class="btn-secondary file-download"
              href={`/files/${props.fileId}/download`}
              download=""
            >
              Download
            </a>
          </div>

          <Show when={isMarkdown()}>
            {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by DOMPurify */}
            <div class="file-content-markdown" innerHTML={markdownHtml()} />
          </Show>
          <Show when={!isMarkdown() && isText()}>
            <pre class="file-content-text">{fileData()?.content || '(empty file)'}</pre>
          </Show>
          <Show when={!isMarkdown() && !isText()}>
            <Show
              when={isPdf()}
              fallback={
                <div class="file-no-preview">
                  <p>No preview for this file type</p>
                  <p class="file-no-preview-hint">Download it to open it in the right app.</p>
                </div>
              }
            >
              <div class="file-no-preview">
                <p>PDF document</p>
                <p class="file-no-preview-hint">The browser renders PDFs natively.</p>
                <a
                  class="btn-primary"
                  href={`/files/${props.fileId}/download?inline=true`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Open in new tab
                </a>
              </div>
            </Show>
          </Show>
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

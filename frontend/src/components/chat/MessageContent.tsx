import { createMemo, For, Show } from 'solid-js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import {
  searchMedia,
  getHeroMedia,
  getGridMedia,
  getExtraVideos,
  getSourceResults,
  hostnameOf,
} from '../../utils/media'
import MediaCard from './MediaCard'
import MediaGrid from './MediaGrid'
import type { ChatMessage, MediaContent } from '../../types/chat'

marked.use({
  renderer: {
    link(token) {
      const href = token.href
      const title = token.title ? ` title="${token.title}"` : ''
      return `<a href="${href}"${title} target="_blank" rel="noopener">${token.text}</a>`
    }
  }
})

export default function MessageContent(props: {
  message: () => ChatMessage
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}) {
  // Media comes only from the message's search results (see `searchMedia`).
  const media = createMemo(() => searchMedia(props.message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  const gridMedia = createMemo(() => getGridMedia(media(), heroMedia()))
  const extraVideos = createMemo(() => getExtraVideos(media(), heroMedia()))
  const sources = createMemo(() => getSourceResults(props.message()))

  const htmlContent = createMemo(() => marked.parse(props.message().content || '') as string)

  return (
    <div class="message-content">
      <Show when={heroMedia()}>
        <MediaCard media={heroMedia()!} onOpenLightbox={props.onOpenLightbox} />
      </Show>

      {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by DOMPurify */}
      <div class="msg-markdown" innerHTML={DOMPurify.sanitize(htmlContent())} />

      <Show when={gridMedia().length > 0}>
        <MediaGrid media={gridMedia()} onOpenLightbox={props.onOpenLightbox} />
      </Show>

      <For each={extraVideos()}>
        {(video) => <MediaCard media={video} onOpenLightbox={props.onOpenLightbox} />}
      </For>

      <Show when={sources().length > 0}>
        <ul class="msg-sources" aria-label="Sources">
          <For each={sources()}>
            {(result) => (
              <li>
                <a href={result.url} target="_blank" rel="noopener">
                  {result.title || hostnameOf(result.url)}
                </a>
                <span class="source-host">{hostnameOf(result.url)}</span>
              </li>
            )}
          </For>
        </ul>
      </Show>
    </div>
  )
}

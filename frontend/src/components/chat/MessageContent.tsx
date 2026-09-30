import { createMemo, For, Show } from 'solid-js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { searchMedia, getHeroMedia, getGridMedia, getExtraVideos } from '../../utils/media'
import { collectGroundingSlots, applyGrounding } from '../../utils/grounding'
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

// The backend appends a `**Sources:**` footer to search answers. Render it after
// the media so it ends the response, instead of sitting between the answer text
// and the extra images/video.
const SOURCES_MARKER = '\n\n**Sources:**'

export default function MessageContent(props: {
  message: () => ChatMessage
  onOpenLightbox?: (media: MediaContent, index: number, allMedia: MediaContent[]) => void
}) {
  // Media comes only from the message's search results (see `searchMedia`).
  const media = createMemo(() => searchMedia(props.message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  const gridMedia = createMemo(() => getGridMedia(media(), heroMedia()))
  const extraVideos = createMemo(() => getExtraVideos(media(), heroMedia()))

  const parts = createMemo(() => {
    const content = props.message().content || ''
    const idx = content.indexOf(SOURCES_MARKER)
    if (idx === -1) return { body: content, sources: '' }
    return { body: content.slice(0, idx), sources: content.slice(idx).trim() }
  })

  const bodyHtml = createMemo(() =>
    // Sanitize first, then annotate: `applyGrounding` only ever adds our own
    // spans (with a controlled `data-confidence` tooltip) to the safe markup.
    applyGrounding(
      DOMPurify.sanitize(marked.parse(parts().body) as string),
      collectGroundingSlots(props.message())
    )
  )
  const sourcesHtml = createMemo(() =>
    parts().sources ? (marked.parse(parts().sources) as string) : ''
  )

  return (
    <div class="message-content">
      <Show when={heroMedia()}>
        <MediaCard media={heroMedia()!} onOpenLightbox={props.onOpenLightbox} />
      </Show>

      {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by DOMPurify, then annotated */}
      <div class="msg-markdown" innerHTML={bodyHtml()} />

      <Show when={gridMedia().length > 0}>
        <MediaGrid media={gridMedia()} onOpenLightbox={props.onOpenLightbox} />
      </Show>

      <For each={extraVideos()}>
        {(video) => <MediaCard media={video} onOpenLightbox={props.onOpenLightbox} />}
      </For>

      <Show when={parts().sources}>
        {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by DOMPurify */}
        <div class="msg-markdown msg-sources-block" innerHTML={DOMPurify.sanitize(sourcesHtml())} />
      </Show>
    </div>
  )
}

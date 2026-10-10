import { createMemo, For, Show } from 'solid-js'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { searchMedia, getHeroMedia } from '../../utils/media'
import { collectGroundingSlots, applyGrounding } from '../../utils/grounding'
import { fileLinksFrom, linkifyFileMentions } from '../../utils/fileLinks'
import { fileList } from '../../state/files'
import MediaCard from './MediaCard'
import type { ChatMessage } from '../../types/chat'

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
// and the hero.
const SOURCES_MARKER = '\n\n**Sources:**'

export default function MessageContent(props: { message: () => ChatMessage }) {
  // Media comes only from the message's search results (see `searchMedia`).
  const media = createMemo(() => searchMedia(props.message()))
  const heroMedia = createMemo(() => getHeroMedia(media()))
  // One hero, by URL rather than by type: when the hero is a video the extras are
  // the other videos, and when it is an image they are all of them.
  const extraVideos = createMemo(() => {
    const hero = heroMedia()
    return media().filter(
      (m) =>
        (m.type === 'youtube' || m.type === 'video') && m.url !== hero?.url
    )
  })

  const parts = createMemo(() => {
    const content = props.message().content || ''
    const idx = content.indexOf(SOURCES_MARKER)
    if (idx === -1) return { body: content, sources: '' }
    return { body: content.slice(0, idx), sources: content.slice(idx).trim() }
  })

  const bodyHtml = createMemo(() =>
    // Sanitize first, then annotate: `applyGrounding` only ever adds our own
    // spans (with a controlled `data-confidence` tooltip) to the safe markup.
    // Filename mentions of known files become links to the Files page; grounding
    // skips inside links, so the two passes do not fight.
    applyGrounding(
      linkifyFileMentions(
        DOMPurify.sanitize(marked.parse(parts().body) as string),
        fileLinksFrom(fileList())
      ),
      collectGroundingSlots(props.message())
    )
  )
  const sourcesHtml = createMemo(() =>
    parts().sources ? (marked.parse(parts().sources) as string) : ''
  )

  return (
    <div class="message-content">
      {/* Dynamic content assembles in one order: the hero block (the lead image,
          or the one video that gets embedded), then the body, then the other
          videos as links, then the sources. Each block owns its own top margin. */}
      <Show when={heroMedia()}>
        <MediaCard media={heroMedia()!} />
      </Show>

      {/* eslint-disable-next-line solid/no-innerhtml -- sanitized by DOMPurify, then annotated */}
      <div class="msg-markdown" innerHTML={bodyHtml()} />

      <Show when={extraVideos().length > 0}>
        <div class="msg-video-links">
          <div class="msg-video-links-heading">More videos</div>
          <ul class="msg-video-links-list">
            <For each={extraVideos()}>
              {(video) => (
                <li>
                  <a href={video.url} target="_blank" rel="noopener">
                    {video.title || video.url}
                  </a>
                </li>
              )}
            </For>
          </ul>
        </div>
      </Show>

      <Show when={parts().sources}>
        {/* eslint-disable-next-line solid/no-innerhtml -- content sanitized by DOMPurify */}
        <div class="msg-markdown msg-sources-block" innerHTML={DOMPurify.sanitize(sourcesHtml())} />
      </Show>
    </div>
  )
}

import { createMemo, For, Show } from 'solid-js'
import DOMPurify from 'dompurify'
import { searchMedia, getHeroMedia } from '../../utils/media'
import { collectGroundingSlots, applyGrounding } from '../../utils/grounding'
import { fileLinksFrom, linkifyFileMentions } from '../../utils/fileLinks'
import { parseMarkdown, renderMarkdown } from '../../utils/markdown'
import { fileList } from '../../state/files'
import MediaCard from './MediaCard'
import type { ChatMessage } from '../../types/chat'

// The backend appends a `**Sources:**` footer to search answers. Render it after
// the media so it ends the response, instead of sitting between the answer text
// and the hero.
const SOURCES_MARKER = '\n\n**Sources:**'

const PROXY_PREFIX = '/image-proxy?url='

/**
 * Drop inline images whose source this turn's search did not return.
 *
 * The prompt tells the model to use only the candidates it was given, but an
 * instruction is not a guarantee: a live turn joined a page URL onto an image
 * URL (`https://site.comhttps://cdn.site.com/…`) and shipped a broken image. The
 * candidate set is known here, so the rule is enforced rather than requested.
 *
 * Local and `data:` sources are left alone — they are not third-party fetches.
 */
export function enforceImageSources(html: string, allowed: Set<string>): string {
  return html.replace(/<img\b[^>]*>/gi, (tag) => {
    const src = /src="([^"]*)"/i.exec(tag)?.[1]
    if (!src || !src.startsWith(PROXY_PREFIX)) return tag
    const url = decodeURIComponent(src.slice(PROXY_PREFIX.length)).replace(/&amp;/g, '&')
    return allowed.has(url) ? tag : ''
  })
}

export default function MessageContent(props: { message: () => ChatMessage }) {
  const parts = createMemo(() => {
    const content = props.message().content || ''
    const idx = content.indexOf(SOURCES_MARKER)
    if (idx === -1) return { body: content, sources: '' }
    return { body: content.slice(0, idx), sources: content.slice(idx).trim() }
  })

  // The images this turn's search actually returned — the only ones an inline
  // image may use (see `enforceImageSources`).
  const allowedImageUrls = createMemo(() => {
    const info = props.message().meta?.search_info
    const urls = new Set<string>()
    for (const result of [...(info?.image_results ?? []), ...(info?.results ?? [])]) {
      if (result.thumbnail) urls.add(result.thumbnail)
      if (result.image) urls.add(result.image)
    }
    return urls
  })

  const bodyHtml = createMemo(() =>
    // Sanitize first, then annotate: `applyGrounding` only ever adds our own
    // spans (with a controlled `data-confidence` tooltip) to the safe markup.
    // Filename mentions of known files become links to the Files page; grounding
    // skips inside links, so the two passes do not fight.
    applyGrounding(
      linkifyFileMentions(
        enforceImageSources(renderMarkdown(parts().body), allowedImageUrls()),
        fileLinksFrom(fileList())
      ),
      collectGroundingSlots(props.message())
    )
  )

  /** An image the model placed inline is already this answer's visual. */
  const hasInlineImage = createMemo(() => /<img\b/i.test(bodyHtml()))

  // Media comes only from the message's search results (see `searchMedia`).
  const media = createMemo(() => searchMedia(props.message()))
  const heroMedia = createMemo(() => {
    const hero = getHeroMedia(media())
    // Suppress the banner only for an *image* hero: it would show the same
    // picture twice, since the model picked from these very candidates. A video
    // hero is not a duplicate of an inline still, so it stays above the text.
    if (hero && hero.type === 'image' && hasInlineImage()) return null
    return hero
  })
  // One hero, by URL rather than by type: when the hero is a video the extras are
  // the other videos, and when it is an image they are all of them.
  const extraVideos = createMemo(() => {
    const hero = heroMedia()
    return media().filter(
      (m) =>
        (m.type === 'youtube' || m.type === 'video') && m.url !== hero?.url
    )
  })
  const sourcesHtml = createMemo(() =>
    parts().sources ? parseMarkdown(parts().sources) : ''
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

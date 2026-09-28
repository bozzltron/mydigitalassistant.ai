import { For } from 'solid-js'
import type { MediaContent } from '../../types/chat'

interface PreviewCardsProps {
  cards: MediaContent[]
}

export default function PreviewCards(props: PreviewCardsProps) {
  return (
    <div class="msg-preview-cards" role="list" aria-label="Link previews">
      {/* props.cards, not a destructured snapshot. <For> is a tracked scope, but
          it can only track what it is given; a destructure handed it a fixed
          array, so the card list would never follow a new `cards` prop.
          MessageContent passes a memo here. */}
      <For each={props.cards}>
        {(card) => (
          <a
            class="msg-preview-card"
            href={card.sourceUrl || card.url}
            target="_blank"
            rel="noopener"
            role="listitem"
          >
            {card.thumbnail ? (
              <img class="msg-preview-card-image" src={card.thumbnail} alt="" loading="lazy" />
            ) : (
              <div class="msg-preview-card-placeholder">
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                  <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
                  <circle cx="8.5" cy="8.5" r="1.5" />
                  <path d="M21 15l-5-5L5 21" />
                </svg>
              </div>
            )}
            <div class="msg-preview-card-body">
              {card.sourceUrl && (
                <div class="msg-preview-card-site">
                  {new URL(card.sourceUrl).hostname}
                </div>
              )}
              {card.title && (
                <div class="msg-preview-card-title">{card.title}</div>
              )}
              {card.description && (
                <div class="msg-preview-card-description">{card.description}</div>
              )}
              {card.sourceUrl && (
                <div class="msg-preview-card-url">
                  {new URL(card.sourceUrl).pathname.slice(0, 50)}
                </div>
              )}
            </div>
          </a>
        )}
      </For>
    </div>
  )
}
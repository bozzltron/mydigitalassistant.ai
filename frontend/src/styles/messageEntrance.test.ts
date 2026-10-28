import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

// The entrance is CSS, so no component test can prove it moves: jsdom never runs
// an animation and nothing observes interpolation. What *can* be pinned is the
// contract the DOM tests rely on — a per-message 200 ms upward slide that leaves
// queued opacity alone, a real reduced-motion branch, and the absence of the
// whole-thread animation that used to make arrival and conversation-open
// compete (plan §Approval). The manual browser matrix remains the only proof of
// actual motion.

const SOURCE = readFileSync(join(__dirname, 'messages.css'), 'utf8')

/** Comment-free source: several comments here name the properties they forbid. */
const css = SOURCE.replace(/\/\*[\s\S]*?\*\//g, '')

/** The block whose `{` sits at `open`, honouring nesting. */
function block(src: string, open: number): { body: string; end: number } {
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return { body: src.slice(open + 1, i), end: i }
    }
  }
  throw new Error(`unbalanced braces at offset ${open}`)
}

/** The body of the first rule whose selector list contains `selector`. */
function ruleBody(src: string, selector: string): string | undefined {
  const re = /([^{}]+)\{/g
  let match: RegExpExecArray | null
  while ((match = re.exec(src))) {
    const selectors = (match[1] ?? '').split(',').map((s) => s.trim())
    if (selectors.includes(selector)) return block(src, match.index + match[0].length - 1).body
  }
  return undefined
}

const MEDIA = '@media (prefers-reduced-motion: reduce)'
const mediaAt = css.indexOf(MEDIA)
const media = mediaAt === -1 ? null : block(css, css.indexOf('{', mediaAt))
// The rules that apply in normal motion: the sheet with the media branch removed,
// so `.msg-arriving` resolves to its base rule whichever order the two appear in.
const base = media === null ? css : css.slice(0, mediaAt) + css.slice(media.end + 1)

describe('message entrance', () => {
  it('defines one per-message 200 ms upward slide on .msg-arriving', () => {
    const rule = ruleBody(base, '.msg-arriving')
    expect(rule, 'no .msg-arriving rule in messages.css').toBeDefined()
    // 200 ms ease-out: the house 0.2 s, and short enough to read as arrival
    // rather than as waiting.
    expect(rule).toMatch(/animation:\s*msgEnter\s+200ms\s+ease-out\s*;/)
    // No fill mode: a held final state would leave a transform in place, which
    // makes the row a containing block for position: fixed descendants.
    expect(rule).not.toMatch(/\b(both|forwards|backwards)\b/)
    // Transform only. Queued rows are opacity 0.55 and animating opacity to 1
    // would erase the pending look.
    expect(rule).not.toMatch(/opacity/)
  })

  it('slides from below to the resting position, with no opacity in the keyframes', () => {
    const head = base.match(/@keyframes\s+msgEnter\s*\{/)
    expect(head, 'no @keyframes msgEnter').not.toBeNull()
    const frames = block(base, (head?.index ?? 0) + head![0].length - 1).body
    expect(frames).toMatch(/from\s*\{[^}]*transform:\s*translateY\(12px\)/)
    expect(frames).toMatch(/to\s*\{[^}]*transform:\s*none/)
    expect(frames, 'queued opacity would be animated to 1').not.toMatch(/opacity/)
  })

  it('no longer animates the whole transcript', () => {
    // The owner's instruction that settles acceptance criterion 3: entry
    // animation and new-message animation must not compete, so opening a
    // conversation is still.
    const transcript = ruleBody(base, '.chat-transcript')
    expect(transcript, '.chat-transcript rule missing').toBeDefined()
    expect(transcript).not.toMatch(/animation/)
    expect(css, 'conversationEnter still animates the thread').not.toContain('conversationEnter')
  })

  it('leaves the pending look of a queued row alone', () => {
    // The falsifier in the plan's measurement: "queued opacity is overridden".
    // The marker must not restyle the row it lands on.
    const queued = ruleBody(base, '.msg-queued')
    expect(queued, 'no .msg-queued rule').toBeDefined()
    expect(queued).toMatch(/opacity:\s*0\.55\s*;/)
  })

  it('switches the slide off under prefers-reduced-motion', () => {
    expect(mediaAt, `no ${MEDIA} branch`).toBeGreaterThan(-1)
    const rule = ruleBody(media?.body ?? '', '.msg-arriving')
    expect(rule, 'the reduced-motion branch does not override .msg-arriving').toBeDefined()
    // A real media-query branch, not a zero duration: the message is painted
    // straight into its final position.
    expect(rule).toMatch(/animation:\s*none\s*;/)
  })

  it('places the reduced-motion override after the base rule so it wins the cascade', () => {
    // Both rules are one class deep, so source order decides. A reduced-motion
    // block placed above `.msg-arriving` would still contain `animation: none`
    // and every other assertion here would stay green, while the slide ran for
    // users who asked for no motion.
    expect(mediaAt, `no ${MEDIA} branch`).toBeGreaterThan(-1)
    const baseAt = css.indexOf('.msg-arriving {')
    expect(baseAt, 'no base .msg-arriving rule').toBeGreaterThan(-1)
    expect(mediaAt, 'reduced-motion block precedes the base rule').toBeGreaterThan(baseAt)
  })

  it('is the class the components actually apply', () => {
    // A rule nothing references is dead CSS, and a renamed class would silently
    // drop the motion while this file kept passing. Message builds the class for
    // transcript rows, ChatPage for queued rows, and MessageList owns the
    // once-per-id decision that feeds them.
    const chat = readFileSync(join(__dirname, '..', 'components', 'chat', 'ChatPage.tsx'), 'utf8')
    const list = readFileSync(join(__dirname, '..', 'components', 'chat', 'MessageList.tsx'), 'utf8')
    const message = readFileSync(join(__dirname, '..', 'components', 'chat', 'Message.tsx'), 'utf8')

    expect(message, 'Message.tsx does not apply msg-arriving').toContain('msg-arriving')
    expect(chat, 'ChatPage.tsx does not apply msg-arriving').toContain('msg-arriving')
    expect(message, 'Message does not accept the arrival flag').toMatch(/arriving\?\s*:\s*boolean/)
    expect(list, 'MessageList no longer passes the arrival flag').toMatch(/arriving=\{arriving\}/)
  })
})

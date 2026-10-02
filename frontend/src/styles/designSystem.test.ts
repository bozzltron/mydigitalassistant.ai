import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

// The `solid/style-prop` lint rule cannot enforce the AGENTS.md ban on inline
// styles. It matched camelCase keys in the 29 warnings it did report, but after
// those were cleared 28 further `style={{...}}` remain in the tree, written with
// kebab-case string keys (`"font-size"`) that the rule does not recognise. It
// also has no opinion at all about `<style>` blocks embedded in JSX, which is
// where the worst of the damage turned out to be.
//
// So the three rules below are enforced by reading the source instead. Each one
// pins a real defect found on 2026-09-28, not a style preference.

const SRC = join(__dirname, '..')
const SRC_REL = relative(join(__dirname, '..', '..'), SRC)

function walk(dir: string, exts: string[]): string[] {
  const out: string[] = []
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) {
      out.push(...walk(full, exts))
    } else if (exts.some((e) => entry.endsWith(e))) {
      out.push(full)
    }
  }
  return out
}

const tsxFiles = walk(SRC, ['.tsx']).filter((f) => !f.includes('.test.'))
const tsFiles = walk(SRC, ['.ts']).filter((f) => !f.includes('.test.'))
const styleFiles = walk(SRC, ['.css']).filter((f) => !f.endsWith('.module.css'))
const read = (f: string) => readFileSync(f, 'utf8')
const show = (f: string) => relative(SRC_REL, f)

/** Strips block and line comments so a rule matches code, not the prose written
 *  to explain it. Several of the comments added with these guards name the very
 *  patterns they describe. */
function stripComments(src: string): string {
  return src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '')
}

describe('design-system guards', () => {
  it('no component uses a CSS custom property that is never defined', () => {
    // A custom property with no definition and no fallback makes the whole
    // declaration invalid at computed-value time, so the property silently
    // computes to its initial value. That is how EditModal's Save button came
    // to render `color: white` against no background: it referenced
    // var(--color-primary), and --color-primary is defined nowhere.
    const defined = new Set<string>()
    const used = new Map<string, Set<string>>()

    const collect = (f: string) => {
      // stripComments so a rule matches code, not the prose written to explain
      // it -- EditModal's comment below names the very pattern it removed.
      const text = stripComments(read(f))
      for (const m of text.matchAll(/(--[a-zA-Z0-9_-]+)\s*:/g)) defined.add(m[1])
      for (const m of text.matchAll(/var\(\s*(--[a-zA-Z0-9_-]+)\s*([,)])/g)) {
        // `var(--x, fallback)` is legal even when --x is undefined.
        if (m[2] === ',') continue
        if (!used.has(m[1])) used.set(m[1], new Set())
        used.get(m[1])!.add(show(f))
      }
    }
    styleFiles.forEach(collect)
    tsxFiles.forEach(collect)

    // Remaining pre-existing debt. Every entry here is a live bug, and each
    // deletion tightens this guard.
    //
    // The --color-* family that used to fill this list is gone as of
    // 2026-09-28. Root cause was a semantic alias layer in variables.css that
    // was only half built: `--color-border: var(--border)` and ten siblings
    // shipped, but --color-primary, --color-success, --color-success-bg,
    // --color-error-bg and --color-text-secondary never did. AlertsPanel and
    // TrashCan wrote their CSS into <style> blocks in JSX against the larger
    // vocabulary, so those declarations were invalid at computed-value time.
    // Both blocks are now stylesheets with the real tokens.
    const KNOWN_UNDEFINED: Record<string, string> = {}
    // Empty: the last entry (--surface3) lived only in the orphan stylesheet
    // chat.css, which has been removed. Any new undefined var() now fails here.

    const offenders: string[] = []
    for (const [token, files] of used) {
      if (defined.has(token) || token in KNOWN_UNDEFINED) continue
      offenders.push(`${token} used in ${[...files].join(', ')} but defined nowhere`)
    }
    expect(offenders, `newly undefined CSS variables:\n${offenders.join('\n')}`).toEqual([])
  })

  it('no component ships CSS in a <style> block or an inline style', () => {
    // The @keyframes version of this test asked the wrong question. Keyframe
    // names being global only bites when a <style> block exists to hold a
    // colliding duplicate, and by the time this migrated there were none left.
    // The real invariant is the block itself: every CSS-in-JSX bug found this
    // session lived in one, and none was visible to lint:
    //   - VoiceStatusIndicator's <style> injected a third @keyframes fadeIn,
    //     appended last, so it won the cascade and bent the modal overlay.
    //   - AlertsPanel's <style> keyed rules on plain class names the JSX never
    //     applied (it uses styles.* from an empty module css), so the Learning
    //     Monitor panel rendered fully unstyled while looking plausible.
    //   - AlertsPanel and TrashCan both referenced --color-* custom properties
    //     that exist nowhere, silently killing those declarations.
    // So this lists every file allowed to carry CSS in JSX -- currently none.
    const offenders = tsxFiles
      .filter((f) => /<style/.test(stripComments(read(f))))
      .map(show)
    expect(offenders, `<style> block in component source (move it to a stylesheet):\n${offenders.join('\n')}`).toEqual([])
  })

  it('the components migrated out of inline styles contain none', () => {
    // Every one of the rules these inline styles were breaking is now expressed
    // in a stylesheet, so the ban is enforceable here even though the lint rule
    // is not. AlertsPanel and TrashCan joined the list when their <style>
    // blocks moved to AlertsPanel.module.css and components.css.
    const migrated = [
      'components/chat/MediaCard.tsx',
      'components/chat/MediaGrid.tsx',
      'components/ui/AlertsPanel.tsx',
      'components/ui/EditModal.tsx',
      'components/chat/TrashCan.tsx',
    ]
    const offenders = migrated
      .map((rel) => join(SRC, rel))
      .filter((f) => /style=\{\{/.test(stripComments(read(f))))
      .map(show)
    expect(offenders, `inline styles reintroduced:\n${offenders.join('\n')}`).toEqual([])
  })

  it('uses inline styles only for CSS custom properties', () => {
    // AGENTS.md bans inline styles. The sanctioned exception is passing a
    // dynamic value to CSS as a custom property (style={{ '--x': v }}), which
    // keeps the actual declaration in the stylesheet. A literal property in an
    // inline style object is a violation.
    const offenders: string[] = []
    for (const f of tsxFiles) {
      const text = stripComments(read(f))
      for (const m of text.matchAll(/style=\{\{([\s\S]*?)\}\}/g)) {
        const keys = [...m[1].matchAll(/(?:'|")?([A-Za-z0-9_-]+)(?:'|")?\s*:/g)].map((k) => k[1])
        const bad = keys.filter((k) => !k.startsWith('--'))
        if (bad.length > 0) offenders.push(`${show(f)}: ${bad.join(', ')}`)
      }
    }
    expect(
      offenders,
      `inline style with a non-custom-property key (use a class or a custom property):\n${offenders.join('\n')}`,
    ).toEqual([])
  })

  it('no bare class selector is defined in more than one stylesheet', () => {
    // A bare `.foo` in two global stylesheets collides: the later import wins the
    // properties it sets, and the earlier one's remaining properties still apply.
    // This is what broke the file viewer: brain.css's `.empty-state` is an
    // absolutely-positioned overlay, and files.css does not reset `position`, so
    // the file page's empty state floated to the wrong place. `.voice-dot` gave
    // the top bar the status indicator's colour, and messages.css's `.file-icon`
    // sized the file page's icon. All are scoped or removed now.
    const owners = new Map<string, Set<string>>()
    for (const f of styleFiles) {
      const text = stripComments(read(f))
      // A rule head is everything between the previous `{`/`}` and this `{`, with
      // at-rules excluded. Grouped selectors (`.a, .b {`) and rules nested in a
      // media query are both matched -- an earlier version keyed on `^\s*\.x {`
      // and so missed `.empty-state,` entirely.
      for (const m of text.matchAll(/(?:^|[{}])\s*([^{}@]+?)\s*\{/g)) {
        const parts = m[1].split(',').map((s) => s.trim())
        if (parts.length === 0) continue
        if (!parts.every((s) => /^\.[a-zA-Z][a-zA-Z0-9_-]*$/.test(s))) continue
        for (const s of parts) {
          if (!owners.has(s)) owners.set(s, new Set())
          owners.get(s)!.add(show(f))
        }
      }
    }
    const offenders = [...owners.entries()]
      .filter(([, files]) => files.size > 1)
      .map(([cls, files]) => `${cls}: ${[...files].join(', ')}`)
    expect(
      offenders,
      `bare class defined in multiple stylesheets (scope one of them):\n${offenders.join('\n')}`,
    ).toEqual([])
  })

  it('no source file emits a literal inline style in an HTML string', () => {
    // The JSX rule above scans .tsx only. brainLib.ts builds the tooltip as an
    // HTML string and set `style="color:…"` there — a literal property that
    // slipped past the guard because .ts files were never read. The sanctioned
    // form in a string is a custom property (`style="--x:…"`); a literal
    // property is not.
    const offenders: string[] = []
    for (const f of [...tsxFiles, ...tsFiles]) {
      const text = stripComments(read(f))
      for (const m of text.matchAll(/style="([^"]*)"/g)) {
        const literal = m[1]
          .split(';')
          .map((d) => d.trim())
          .filter((d) => d.length > 0 && !d.startsWith('--'))
        if (literal.length > 0) offenders.push(`${show(f)}: ${literal.join('; ')}`)
      }
    }
    expect(
      offenders,
      `literal inline style in an HTML string (use a class or a custom property):\n${offenders.join('\n')}`,
    ).toEqual([])
  })
})

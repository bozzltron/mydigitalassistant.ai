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
const styleFiles = walk(SRC, ['.css'])
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
      const text = read(f)
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

    // Pre-existing debt, still present and unrelated to the warning cleanup.
    //
    // Root cause of the --color-* entries: variables.css has a semantic alias
    // layer (`--color-border: var(--border)` and 10 siblings) that is only half
    // built. AlertsPanel and TrashCan write their CSS into a <style> block in
    // JSX against a larger alias vocabulary than the one that shipped, so these
    // five names resolve to nothing and every declaration using them is invalid
    // at computed-value time. Completing the alias layer is a one-line-per-name
    // fix, but three of the names (--color-success and its two backgrounds) have
    // no token in the palette to alias, so the values are a design decision
    // rather than a mechanical repair. Until then: each entry is a live bug, and
    // deleting one tightens this guard.
    const KNOWN_UNDEFINED: Record<string, string> = {
      '--color-primary': 'no --accent alias exists; EditModal used it too, until its inline styles moved to .btn-primary',
      '--color-success': 'no --success token exists in variables.css',
      '--color-success-bg': 'no --success token exists in variables.css',
      '--color-error-bg': 'no --error-background token exists in variables.css',
      '--color-text-secondary': 'the alias block has --color-text-dim, not this name',
      '--surface3': 'chat.css trace panel background -- the declaration is dead, so that background is transparent',
      '--person': 'BrainPage.tsx legend dot -- the dot renders with no background',
      '--concept': 'BrainPage.tsx legend dot -- the dot renders with no background',
      '--event': 'BrainPage.tsx legend dot -- the dot renders with no background',
      '--household': 'BrainPage.tsx legend dot -- the dot renders with no background',
      '--entity': 'BrainPage.tsx legend dot -- the dot renders with no background',
    }

    const offenders: string[] = []
    for (const [token, files] of used) {
      if (defined.has(token) || token in KNOWN_UNDEFINED) continue
      offenders.push(`${token} used in ${[...files].join(', ')} but defined nowhere`)
    }
    expect(offenders, `newly undefined CSS variables:\n${offenders.join('\n')}`).toEqual([])
  })

  it('no component defines @keyframes', () => {
    // Keyframe names are global. VoiceStatusIndicator used to ship a third
    // `@keyframes fadeIn` from inside a `<style>` block in JSX; injected at
    // runtime it was appended after the linked stylesheets, won the cascade,
    // and put its translateX(-50%) on every frame of .modal-overlay's fadeIn.
    // Animations belong in a stylesheet: see the keyframes definitions in
    // styles/*.css, which are the only ones allowed to exist.
    const offenders = tsxFiles
      .filter((f) => /@keyframes/.test(stripComments(read(f))))
      .map(show)
    expect(offenders, `@keyframes defined in component source:\n${offenders.join('\n')}`).toEqual([])
  })

  it('the components migrated out of inline styles contain none', () => {
    // These five carried all 29 style-prop warnings. Every one of the rules
    // those inline styles were breaking is now expressed in a stylesheet, so
    // the ban is enforceable here even though the lint rule is not.
    const migrated = [
      'components/chat/MediaCard.tsx',
      'components/chat/MediaGrid.tsx',
      'components/chat/VideoEmbed.tsx',
      'components/chat/VoiceStatusIndicator.tsx',
      'components/ui/EditModal.tsx',
    ]
    const offenders = migrated
      .map((rel) => join(SRC, rel))
      .filter((f) => /style=\{\{/.test(stripComments(read(f))))
      .map(show)
    expect(offenders, `inline styles reintroduced:\n${offenders.join('\n')}`).toEqual([])
  })
})

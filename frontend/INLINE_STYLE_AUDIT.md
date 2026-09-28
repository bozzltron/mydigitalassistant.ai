# Findings: inline styles and JSX-embedded CSS

Surveyed 2026-09-28 while clearing the 47 SolidJS lint warnings. The warnings
were not cosmetic nits — the inline styles are actively breaking things. Each item
below was confirmed by reading the code and the stylesheets it overrides.

The AGENTS.md rule being applied: "No inline styles. Inline styles
(`style={{...}}`) are prohibited. They break cohesion, make the code hard to
maintain, **bypass the design system (variables)**, and prevent reuse." The
bypass is not theoretical; it is the whole problem.

## The lint rule does not enforce the rule

`solid/style-prop` reported 29 warnings. After those were fixed it reports **zero**,
and **28 further `style={{...}}` remain in the tree**. They are invisible to the
rule because they are written with kebab-case string keys:

```tsx
style={{"font-size":"0.8rem","border-radius":"6px"}}     // not flagged
style={{ fontSize: '0.8rem', borderRadius: '6px' }}      // flagged
```

The rule matches camelCase property names, so a component author can sidestep it
entirely by changing spelling style. It also has no opinion on `<style>` blocks
embedded in JSX, which is where the worst of the damage turned out to be.

**Consequence: the ban cannot be enforced by the linter.** It needs either a
mechanical source check (added: `src/styles/designSystem.test.ts`) or a broader
lint config. 28 of the 57 inline styles use kebab-case keys.

## Bugs caused by inline styles

**1. `var(--color-primary)` is undefined — a button has no background.**
`variables.css` defines a semantic alias layer (lines 26-37) that maps
`--color-border: var(--border)`, `--color-text: var(--text)` and ten siblings to
the real tokens. The layer is **half-built**: `--color-primary`,
`--color-success`, `--color-success-bg`, `--color-error-bg` and
`--color-text-secondary` were never added. `AlertsPanel.tsx` (31 references) and
`TrashCan.tsx` (16 references) write their CSS into a `<style>` block in JSX
against the larger vocabulary, so those declarations are invalid at
computed-value time.

The clearest instance: the EditModal Save button rendered `color: white` on **no
background**, because its `background: var(--color-primary)` resolved to nothing.
That is now fixed, but only because the button moved to the existing
`.btn-primary` — the alias is still missing and still used elsewhere.

**2. The voice status dot's state colours are dead.**
`VoiceStatusIndicator.tsx:77-84` set `background: var(--accent)'` inline. Inline
wins over stylesheet rules, so it overrode `.voice-dot.processing`
(`--warning`) and `.voice-dot.speaking` in `status-indicator.css`. The dot was
always accent-coloured; the state `getDotClass()` computes had no visible effect.
Related: no `.voice-dot.speaking` rule existed at all, so that state fell through
to the undifferentiated base colour even once the inline style was gone.

**3. Three definitions of `@keyframes fadeIn`, and the JSX one wins.**
`styles/chat.css:522`, `styles/components.css:153`, and a third injected at
runtime by `VoiceStatusIndicator.tsx:105` inside a `<style>` block. The runtime
one is appended to `<head>` after the stylesheets, so it takes the cascade. Its
keyframes bake `translateX(-50%)` into every frame. `.modal-overlay`
(`components.css:150`) animates with `fadeIn`, so once the voice overlay had
mounted, the modal overlay inherited that transform and slid sideways. Directly
violates "Animations in CSS... JS only toggles classes."

**4. `.voice-status-overlay` was defined nowhere.**
Applied at `VoiceStatusIndicator.tsx:59`, with no rule in any stylesheet. The
class did nothing; the inline style carried 100% of the presentation.

**5. Five legend dots in the Brain Observatory render with no background.**
`BrainPage.tsx:223-227` set `background: var(--person|--concept|--event|--household|--entity)`.
None of those five is defined, so each dot is transparent. These were written
with a camelCase-looking key (`background`) but a `var()` value, which is why the
style-prop rule stayed silent.

**6. `chat.css:982` sets `background: var(--surface3)`, also undefined.** The
declaration is dead, so the trace panel background is transparent.

## Duplication, not translation

Several inline blocks restate rules that already exist and do them better:

- `EditModal.tsx:57-65` styled the input; `.modal-content input`
  (`components.css:217`) already covers every property *and* adds `:focus` and
  `::placeholder` states the inline version omits. The inline values were worse
  (4px radius vs 8px, 0.875rem vs 1rem).
- `EditModal.tsx:68` used `display:flex; justify-content:flex-end; gap:8px`;
  `.modal-actions` (`components.css:238`) exists, and `.modal-actions button`
  already sets padding/radius/font/cursor/transition.
- `VoiceStatusIndicator.tsx:77-84` restated `.voice-dot` in full.
- `MediaCard` and `VideoEmbed` each carried an identical seven-property style
  object for the video poster image; now one `.video-cover-thumb` rule.

So the correct fix for these is **deletion, not transliteration**. Copying
`borderRadius: '4px'` into a new CSS class would preserve the divergence and
recreate the bypass one layer down.

## Deliberate behaviour changes

Moving off inline styles realigned a few values with the design system rather
than reproducing the old numbers exactly. Each is one step on a token scale:

| Where | Was | Now | Token |
|---|---|---|---|
| EditModal input bottom gap | `12px` | `1rem` | `--space-md` |
| EditModal Save button | `6px 16px` / `0.875rem` / r4 | `0.5rem 1rem` / `0.85rem` / r6 | `.modal-actions button` |
| EditModal input | r4, `0.875rem`, `8px 12px` | r8, `1rem`, `0.75rem 1rem` | `.modal-content input` |

## Reactivity defects (lint `reactivity`, 16)

The class of bug lint cannot judge for severity, so each was read:

- `MediaCard.tsx:18` destructured `props`, and lines 35/63 early-returned. Solid
  components run once, so both froze the branch. `MessageContent.tsx:34` passes
  `media={heroMedia()!}`, a memo, so a changed `media` prop would never reach the
  card. Latent today (instances are stable per message) but wrong by
  construction.
- `MediaGrid.tsx:21` destructured `props`, then memoised `imageMedia()` from the
  snapshot — so the memo had no reactive dependency and could never invalidate.
- `PreviewCards.tsx:9` destructured `props`, then handed the frozen array to
  `<For each={...}>`. `<For>` is a tracked scope, but it can only track what it
  is given.
- `VoiceControls.tsx:14,20,23` read `props.isOpen`, `props.onTranscription` and
  `props.isRecording` into plain consts.
- `VideoEmbed.tsx:13` destructured `props`; the derived `embedUrl`/`thumbUrl`
  were computed once from the snapshot.

Two were not defects and are now explicitly suppressed with the reason inline:
`EditModal.tsx:12` (a `createSignal` seed that the `createEffect` below keeps in
sync) and `TopBar.tsx:517` (a callback read in a promise continuation, where no
tracked scope is wanted).

## Dead code found

`components/chat/VideoEmbed.tsx` has **zero callers** anywhere in the tree,
including tests. `MediaCard` renders its own inline YouTube placeholder, so the
component is superseded rather than merely unused. It was left in place and
corrected rather than deleted, because removing a component is a separate call.

## Note on placement

AGENTS.md prescribes a co-located `.module.css` per component, but the codebase
uses domain stylesheets (`styles/chat.css`, `styles/status-indicator.css`,
`styles/components.css`) for these components already, with only 2 of ~20
components using modules. New rules went into the matching existing stylesheet to
match actual practice; converting these to modules is a separate refactor.

## Not covered by the 47 warnings

- 28 `style={{}}` in 10 files, invisible to the rule because of kebab-case keys:
  `TopBar` (4), `BrainPage` (9), `BrainGraph` (5), `FrameDetail` (3),
  `ChatPage` (2), `MediaGrid` (1), `InputBar` (1), `TracePanel` (1), `Home` (1),
  `BrainGraph3D` (1). Many of these use `var(--…)` values that exist nowhere
  (see bugs 5 and 6 above).
- ~~Two large `<style>` blocks embedded in JSX~~ **resolved 2026-09-28**, and the
  removal turned out to be mostly deletion:
  - `AlertsPanel.tsx` — its `<style>` block (≈160 lines) keyed rules on plain
    class names that the JSX never applied. The component applies `styles.*`
    instead, and `AlertsPanel.module.css` was **empty** (one comment line). So
    19 of 20 rules were dead code, and the Learning Monitor panel rendered fully
    unstyled while looking plausible. The 20 rules now live in
    `AlertsPanel.module.css` under the names the JSX actually uses; the one
    live rule (`.alerts-trigger`) dropped its redundant global twin.
  - `TrashCan.tsx` — its `<style>` block (≈118 lines) used plain names that the
    JSX *does* apply, so it was live, but it also defined `.btn-icon`, a shared
    class that InputBar and VoiceControls use too, in a runtime-injected sheet
    that outranks any real rule. The rules now live in `components.css`; the
    undefined `--color-*` names were remapped to real tokens.
  - Both blocks referenced the five undefined `--color-*` aliases; those
    references are gone with them. The allowlist in `designSystem.test.ts` is
    down to the five Brain legend dots and `--surface3`.


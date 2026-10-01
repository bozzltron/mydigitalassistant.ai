---
date: 2026-10-01
status: active
estimated_hours: 4
---

# Plan G — Top-bar buttons: one style, seven icons, one order

## Objective

Make the top bar read as one control strip. Today the same row mixes four button
styles with three corner radii, two controls with no chrome at all, and only
three of eight controls carrying an icon — in an order that grew rather than was
chosen. Give every control one shared style, one icon, and a deliberate order.

## Measured state (read from the code)

`TopBar.tsx` renders, left to right:

| Control | Element | Class (source) | Radius | Icon |
|---|---|---|---|---|
| New | `<button id="new-conversation-btn">` | `TopBar.module.css .newConversationBtn` | 6px | none |
| Alerts | `<button>` in `AlertsPanel.tsx` | `AlertsPanel.module.css .alertsTrigger` | **20px** | bell (fill) |
| Trash | `<button class="btn-secondary trash-can-trigger">` in `TrashCan.tsx` | `components.css` | 6px | trash (stroke) |
| Let's talk | `<button id="voice-mode-btn">` | `components.css .voice-btn` | 6px | none |
| Stop (TTS) | `<button id="stop-speaking-btn">` | `.voice-btn` | 6px | none |
| Settings | `<button id="settings-toggle">` | `.settings-btn` | none (bare) | gear (stroke) |
| Brain | `<a href="/brain">` | `.nav-link` | 6px | none |
| Files | `<a href="/files">` | `.nav-link` | 6px | none |

Three radii (6 / 20 / bare), four paddings, two controls with no border or
background, four controls with no icon. The only genuinely shared thing is the
header row (`.header-right`, `gap: 0.75rem`).

Current right-side order: **Alerts, Trash, [voice status], Let's talk, [Stop],
Settings, Brain, Files**.

## Requirements

**Functional**

| # | Requirement |
|---|---|
| R1 | Every top-bar control uses one shared button style (background, border, radius, padding, font, hover). |
| R2 | Every control carries an inline SVG icon, coloured by `currentColor` so it matches the label. |
| R3 | New gets a `+` icon. |
| R4 | Let's talk gets a microphone icon. |
| R5 | Files gets a files/folder icon. |
| R6 | Brain gets a brain icon. |
| R7 | Settings is an icon button (gear). |
| R8 | Alerts uses the shared corner radius (currently 20px). |
| R9 | Right-side order: Alerts, Let's talk, Files, Brain, Settings, Trash. New stays after the conversation switcher. |

**Non-functional**

| # | Requirement |
|---|---|
| N1 | Icons are inline `<svg>` (no icon font, no `<img>`), sourced from SVGRepo, defined once in a shared set. |
| N2 | Colours, radii and spacing come from design tokens (`--radius-sm`, `--space-*`, `--text`), not literals. |
| N3 | No inline styles. |
| N4 | Hover / focus / active via classes and pseudo-classes, with the app's ~150ms transitions. |
| N5 | The voice-active and alerts-waiting colour signals are preserved. |
| N6 | Controls stay keyboard-accessible (real `<button>` / `<a>`, visible focus). |
| N7 | Clean ship: styles and classes made dead by the change are removed. |
| N8 | One icon family: every icon is 24×24, `stroke="currentColor"`, `stroke-width 2`, round caps, rendered at 16px. |
| N9 | The header does not overflow or wrap at the widths it renders at today (the row grows by four icons). |

## Design

- Add a shared `.topbar-btn` base class in `components.css` (the project's home
  for shared button patterns): inline-flex, centred, `gap`, `--radius-sm`,
  token spacing, `0.8rem` text, `--surface` / `--border` / `--text`, a 150ms
  transition, and a `:hover`. Icon sizing lives here too
  (`.topbar-btn svg { width: 16px; height: 16px }`).
- `.topbar-btn--icon` for the icon-only controls (Settings, Trash).
- State variants `.is-active` (voice mode) and `.has-alerts` (alerts waiting).
- One `TopBarIcons` module exports the seven icons as Solid components, shared
  by `TopBar`, `AlertsPanel` and `TrashCan` so the set is defined once. The set
  is a cohesive stroke family (24×24, `stroke="currentColor"`, `stroke-width 2`,
  round caps): plus, microphone, folder, brain, gear, bell, trash. The bell is
  currently a filled glyph and the gear/trash are stroked; the plan normalises
  all seven to the stroke family so a row of them reads as one set.
- The two icons that already live inside their components (`AlertsPanel`'s bell,
  `TrashCan`'s `TrashIcon`) move into the shared set; the components import them.
- `AlertsPanel` and `TrashCan` compose `topbar-btn` with their own module class
  for the state-specific bits, instead of carrying their own full button style.

## What this plan does not do

- **Does not restyle the conversation switcher** (the dropdown trigger), which is
  a distinct control with its own open/closed state.
- **Does not restyle the transient voice-status-bar's Stop/Cancel small buttons**
  (`.voice-btn-small`). They appear only during a voice turn and are not part of
  the requested set; leaving them avoids changing live voice-flow controls in the
  same pass.
- **Does not add labels to the icon-only controls beyond `title`/`aria-label`** —
  Settings and Trash stay icon-only, as requested.

## Acceptance

- The right side reads, in order: Alerts, Let's talk, Files, Brain, Settings,
  Trash; New (with `+`) stays after the switcher.
- All eight controls share one radius, padding, font and hover; every one shows
  an icon whose colour matches its label.
- `npm run check` passes; a test pins the order and the icon presence.

## Test strategy

- A component test renders the header and asserts the right-side order and that
  each control contains an `<svg>`.
- Keep the existing `TopBar` conversation-switching tests green.

Gates: `npm run check` (lint + typecheck + tests).

## Principle alignment

| Principle | How |
|---|---|
| Visual feedback & transitions | one hover/active language, ~150ms |
| Clean ship | dead classes removed (`.voice-btn`, `.nav-link`, `.settings-btn`, `.newConversationBtn`, the 20px `.alertsTrigger` radius) |
| Design tokens | radii / spacing / colour from `variables.css` |
| UI standards | inline SVG icons, no emoji, SVGRepo |
| Stability | a test pins the order and the icon presence |

## Rollback

Frontend-only and self-contained: `git revert` restores the previous classes.

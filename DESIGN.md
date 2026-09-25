---
name: nexTix
description: A live board for coding agents where every ticket is a Wallet pass and every state is a stack of passes.
colors:
  accent: "#d8a92b"
  accent-hover: "#dcb345"
  on-accent: "#1c1b17"
  ground: "#1c1c1a"
  pane: "#20201e"
  raised: "#292926"
  field: "#1a1a18"
  line: "#2e2e2b"
  hairline: "#3a3a37"
  ink: "#f3f0e7"
  ink-2: "#c9c5ba"
  ink-3: "#a19d93"
  red: "#ee8479"
  rose: "#d1a2e6"
  green: "#93c996"
  pass-doing: "#665628"
typography:
  readout:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "21px"
    fontWeight: 700
    lineHeight: 1
    letterSpacing: "-0.02em"
    fontFeature: "\"tnum\" 1"
  wordmark:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "22px"
    fontWeight: 700
    letterSpacing: "-0.02em"
  prompt:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "18px"
    fontWeight: 400
    lineHeight: 1.4
  title:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "15px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  title-open:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "17px"
    fontWeight: 650
    lineHeight: 1.3
    letterSpacing: "-0.01em"
  body:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.4
  value:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    fontFeature: "\"tnum\" 1"
  meta:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.45
  strip:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "11px"
    fontWeight: 650
    letterSpacing: "0.04em"
  label:
    fontFamily: "-apple-system, BlinkMacSystemFont, \"SF Pro Text\", Geist, sans-serif"
    fontSize: "10.5px"
    fontWeight: 650
    letterSpacing: "0.05em"
rounded:
  mini: "5px"
  control: "10px"
  pass: "14px"
  popover: "16px"
  composer: "18px"
  pill: "999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  pass-inset: "14px"
  lg: "18px"
  xl: "28px"
components:
  button-primary:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.on-accent}"
    rounded: "{rounded.pill}"
    padding: "9px 18px"
  button-primary-hover:
    backgroundColor: "{colors.accent-hover}"
  button-primary-disabled:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink-3}"
  pass-todo:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pass}"
    padding: "11px 14px 13px"
  pass-doing:
    backgroundColor: "{colors.pass-doing}"
    textColor: "{colors.ink}"
    rounded: "{rounded.pass}"
  pass-needs-input:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pass}"
  pass-failed:
    backgroundColor: "{colors.red}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pass}"
  pass-in-review:
    backgroundColor: "{colors.rose}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pass}"
  pass-done:
    backgroundColor: "{colors.green}"
    textColor: "{colors.ground}"
    rounded: "{rounded.pass}"
  composer:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.prompt}"
    rounded: "{rounded.composer}"
  select:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.ink}"
    typography: "{typography.meta}"
    rounded: "{rounded.control}"
    padding: "7px 30px 7px 11px"
  input-field:
    backgroundColor: "{colors.field}"
    textColor: "{colors.ink}"
    rounded: "{rounded.control}"
    padding: "10px 12px"
  segments:
    backgroundColor: "{colors.field}"
    rounded: "12px"
    padding: "3px"
---

# Design System: nexTix

## Overview

**Creative North Star: "The Wallet of Passes"**

Every ticket is an Apple Wallet pass and every state is a stack of passes. The board refuses the category default of flat grey kanban cards in bordered lanes: six stacks stand side by side, collapsed passes overlap so only their header strips show, and the newest pass in each stack lies open with PassKit anatomy (a header strip carrying repo and issue number, one large primary field, small-caps label-over-value fields, and a boarding-pass tear line). Filing work is one sentence typed into a blank pass at the top.

Color is flat and fully committed, and it belongs to the theme rather than the component. The palette is KeyUp's: the same 15 themes (11 dark, 4 light) with the same values, each setting 16 base tokens from which every nexTix color derives, including the six pass fills. The owner picks a theme in the top bar; until then the OS preference chooses Charcoal & Mustard or Linen & Mustard. The values in this file's frontmatter are Charcoal & Mustard, the default and `:root` fallback; the normative source is the theme table in `web/src/lib/themes.ts` plus the derivation formulas in `web/src/app/globals.css`.

The board is read at a glance between other tasks, so density is high but calm: one type family, weights doing the hierarchy, exact tabular numerals for time and money, and state always carried three ways (fill, glyph, word).

**Key Characteristics:**
- Passes, not cards: 14px corners, a stacked-card shadow that lifts each top edge over the pass beneath, 14px overlap in a stack.
- One fill per state, derived from the theme; no gradients.
- A single accent reserved for action, focus, selection, caret and live work.
- Label-over-value fields in small caps, values in tabular numerals.
- Contrast and color distance enforced by tests, not by eye.

## Colors

A themeable, flat palette: a near-neutral ground and raised surface, a bone-or-ink text scale, one accent, and KeyUp's red, rose and green semantic trio.

### Primary
- **Mustard Accent** (accent): the primary button fill, the focus ring (70% accent), text selection (32%), the input caret, the selected theme swatch outline, the live dot on a running Doing pass, the dashed outline of a stalled pass, and the 35% tint of the Doing fill. Nothing else.
- **Mustard Accent, Hover** (accent-hover): the primary button on hover, the accent mixed 86% with ink (lighter on dark themes, deeper on light).
- **On-Accent Soot** (on-accent): text on the accent fill, gated at 4.5:1 in every theme.

### Tertiary (state hues)
- **Signal Red** (red): the Failed pass fill, the "No heartbeat" readout, error notes, and the offline signal (`--danger`).
- **Review Rose** (rose): the In Review pass fill.
- **Merge Green** (green): the Done pass fill and the top-bar Live connection dot (`--ok`).
- **Warmed Raised** (pass-doing): the Doing fill, the raised surface mixed with 35% accent.

### Neutral
- **Charcoal Ground** (ground): the page, and the color of the tear-line notches cut into passes.
- **Raised Charcoal** (raised): the Todo pass, the composer, selects and the theme button.
- **Pane** (pane): the theme popover.
- **Field** (field): the sign-in input, the switch track, the mobile segment rail, and the disabled primary button.
- **Line** (line): the composer's 1px ring and the popover border.
- **Hairline** (hairline): control borders, empty-stack dashes, inset field rings; ink mixed 14% into the ground.
- **Bone Ink** (ink): primary text and the inverted Needs Input fill.
- **Ink 2** (ink-2): secondary text, top-bar tools, link buttons, notes.
- **Ink 3** (ink-3): placeholders, counts, tertiary notes; gated at 4.5:1 on ground, raised and pane.

### Named Rules
**The Sixteen Tokens Rule.** A theme sets exactly 16 base tokens (bg, sidebar, pane, raised, field, line, ink, ink-2, ink-3, ink-4, accent, on-accent, red, rose, green, shadow) on `<html data-theme>`. Every other color is a `color-mix` of those in globals.css. Never hardcode a color in component CSS; derive a token instead. A new theme needs no CSS.

**The Reserved Accent Rule.** The accent marks only the primary action, focus, selection, the caret, and live work (the Doing tint and its live dot). Links stay neutral ink; state hues never borrow the accent.

**The Clear-of-Red Rule.** A running agent or a question for the owner must never read as a failure. Doing is a warmed raised surface, never a full accent fill; Needs Input is inverted ink (bone on dark, ink-black on light). In every theme, Doing and Needs Input sit at OKLab ΔE of 0.2 or more from the Failed fill, and every pair of state fills at 0.1 or more.

**The Gate Rule.** `web/src/lib/themes.test.ts` is the contrast gate: pass titles and 94% pass labels at 4.5:1 on every pass fill, ink-3 at 4.5:1 on ground, raised and pane, button text at 4.5:1 on the accent, and the stalled outline and live dots at 3:1 on the ground. A theme that fails the gate does not ship.

**The Glyph-and-Word Rule.** No state is carried by color alone: every state has a Lucide glyph (Todo circle-dashed, Doing activity, Needs Input message-question, Failed triangle-alert, In Review git-pull-request, Done circle-check) and a word, in stack heads, pass strips and mobile segments.

## Typography

**Display Font:** SF Pro on Apple platforms (with self-hosted Geist elsewhere, then sans-serif)
**Body Font:** the same stack
**Label/Mono Font:** none distinct; tabular numerals via `font-variant-numeric`

**Character:** one grotesque, Wallet's own face where the platform has it and its closest open cousin where it doesn't. Hierarchy comes from weight (400, 500, 600, 650, 700), size and small-caps tracking, never from a second family.

### Hierarchy
- **Readout** (700, 21px, line-height 1, -0.02em, tabular): the Doing pass's primary field, elapsed time, large enough to read from across the stack. A stalled readout drops to 17px.
- **Wordmark** (700, 22px, -0.02em): "nexTix" in the top bar.
- **Prompt** (400, 18px, 1.4): the composer's one-sentence input.
- **Title** (650, 15px, 1.3, -0.01em): pass titles, clamped to two lines; 17px, four lines, balanced when the pass is open. Stack heads use the same weight at 15px.
- **Body** (400, 15px, 1.4): page default.
- **Value** (600, 14px, tabular): the value half of a pass field.
- **Meta** (400 to 600, 13px, 1.45): controls, notes, counts, links in passes (650).
- **Strip** (650, 11px, 0.04em, uppercase): the pass header strip's repo name; the issue number beside it is 12px, tabular, not uppercased.
- **Label** (650, 10.5px, 0.05em, uppercase): the label half of a pass field and the sign-in field label, colored at a 94% ink mix over the pass fill.

### Named Rules
**The Label-over-Value Rule.** Pass fields follow Wallet: an uppercase tracked label directly over its value. Labels differ from values by caps, tracking and a touch of tone (94% ink), never by a faded grey that fails contrast.

**The Tabular Readout Rule.** Time and money are exact and set in tabular numerals: elapsed time, heartbeat age, cost, counts and issue numbers.

## Layout

A centered container up to 1600px wide with 16px side padding (24px from 640px). The top bar holds the wordmark left and tools right (repo filter, theme button, connection signal, sign out). The composer spans the full width below it with 28px under it.

Stacks sit in a six-column grid, 18px gaps, aligned to the top; 12px gaps under 1440px; three columns with 32px row gaps under 1280px. Under 768px the board becomes one stack at a time beneath a segmented switcher: inactive segments show glyph and count only, the active segment grows and takes its state's fill, the stack heads hide, and the connection signal drops its word to screen readers.

Within a stack, passes overlap by 14px; a collapsed pass pads its foot to 24px so no title sits under the next pass, and the last pass returns to 13px. Pass internals inset 14px horizontally. The spacing rhythm is small and irregular by design (4, 8, 12, 14, 18, 28px) and follows the pass anatomy rather than a strict scale.

## Elevation & Depth

A hybrid: surfaces are flat fills, and depth comes from the stack. Passes carry KeyUp's stacked-card shadow, whose upward component lets each top edge read over the pass beneath it. The composer uses a 1px line ring with an inset top highlight instead of a drop. Only the theme popover floats.

### Shadow Vocabulary
- **Stacked pass** (`box-shadow: 0 1px 0 var(--overlay) inset, 0 -6px 18px -8px var(--shadow), 0 8px 18px -12px var(--shadow)`): every pass.
- **Surface ring** (`box-shadow: 0 1px 0 var(--overlay) inset, 0 0 0 1px var(--line)`): the composer; on focus-within it adds a 2px ring of the focus color.
- **Popover** (`box-shadow: 0 18px 40px -18px var(--shadow), 0 2px 6px color-mix(in srgb, var(--shadow) 35%, transparent)`): the theme picker.
- **Knob and segment** (`0 1px 2px` of the theme shadow at 50 to 60%): the switch knob and the active mobile segment.

### Named Rules
**The Stacked-Card Rule.** Depth belongs to passes in a stack. A new surface is flat with a line ring unless it is a pass or it floats above the board.

## Shapes

Soft rounded rectangles and pills. Passes have 14px corners, the composer 18px, the popover 16px, controls (selects, inputs, theme button, show-all) 10px, the mobile segment rail 12px with 9px segments, and the tiny theme-preview passes 5px. Buttons, switches and dots are full pills.

The one recurring silhouette is the boarding-pass tear line: a 1px dashed rule (8px period, 35% opacity) with a 16px half-circle notch cut into each edge in the ground color. It separates a pass's header from its fields, and the composer's prompt from its controls.

**The Tear Line Rule.** A pass divides header from body with the notched tear line, never with a plain border.

## Components

### Buttons
Confident pills; one primary per view.
- **Shape:** full pill (999px).
- **Primary:** accent fill, on-accent text, 14px weight 600, 9px by 18px padding, optional 16px leading icon.
- **Hover / Active:** fill shifts to the ink-deepened accent over 160ms; press scales to 0.97.
- **Disabled:** KeyUp's neutral-disabled rule: a quiet field-colored button with ink-3 text and a hairline inset ring, never a dimmed accent.
- **Busy:** a spinning loader glyph and a verb label ("Writing the issue…"), 85% opacity.
- **Link button:** unstyled ink-2 text that turns ink and underlines on hover (sign out).

### Chips / Segments
- **Style (mobile only):** a field-colored rail of pill-ish segments; each carries its state glyph in the state's mark color and a tabular count.
- **State:** the pressed segment takes its state's pass fill and ink, grows to fill the rail, and shows its word.

### Cards / Containers (Passes)
- **Corner Style:** 14px.
- **Background:** one flat fill per state: Todo raised; Doing raised warmed 35% by the accent; Needs Input inverted ink; Failed red; In Review rose; Done green. Text on red, rose and green is the ground on dark themes and white on light themes.
- **Shadow Strategy:** the stacked-pass shadow.
- **Border:** none, except a stalled Doing pass: hollow (raised fill, ink text) with a 2px dashed accent outline and no shadow.
- **Internal Padding:** 11px 14px 13px on the header; fields 12px 14px 4px in an auto-fill grid of 88px minimum columns.
- **Anatomy:** header strip (state glyph, repo in uppercase, issue number right-aligned), title, primary field (the Doing readout), tear line, label-over-value fields, then neutral underlined links with an up-right arrow.
- **Motion:** opening grows the body by animating grid rows over 240ms; a fresh pass drops in 10px with a fading focus-colored ring over 520ms; passes travel between stacks with a 380ms view transition.

### Inputs / Fields
- **Style:** selects and the labels input are transparent or raised with a hairline 1px border at 10px radius and 13px text; the sign-in input is a field fill with a hairline inset ring.
- **Focus:** a 2px outline in the focus color (70% accent) offset 2px; inside a pass the outline takes the pass ink. The composer prompt shows focus on the whole composer instead.
- **Caret:** accent.
- **Error:** red text below the field; the composer note switches to red.
- **Switch:** 42 by 26px pill; off is a field track with an ink-2 knob, on is an ink track with a ground knob. It never uses the accent.

### Navigation
- **Top bar:** wordmark left; 13px ink-2 tools right. The connection signal is a green pulsing dot and "Live", a spinning refresh glyph and "Reconnecting" in ink-3, or red when offline.
- **Theme picker:** a raised 10px button with a palette glyph and the theme's first word; it opens a 16px-radius pane popover with Dark and Light groups of swatches, each drawn in its own theme as a tiny three-pass stack (raised, ink, accent). The selected swatch gets a 2px accent outline and a check.

### Live Dot (signature)
An 8px dot in currentColor with a ring that expands from 0.5 to 1.4 scale and fades over 1.8s. Accent on a running Doing pass; green in the top bar. Reduced motion stops the pulse and every other animation.

## Do's and Don'ts

### Do:
- **Do** derive every new color from the 16 theme tokens with `color-mix` in globals.css, and add the check to themes.test.ts.
- **Do** give every state a fill, a glyph and a word.
- **Do** keep Doing and Needs Input far from Failed red (ΔE 0.2 or more) in all 15 themes.
- **Do** set times, costs and counts in tabular numerals.
- **Do** use the notched tear line between a pass's header and its body.
- **Do** render disabled primaries neutral: field fill, ink-3 text, hairline ring.
- **Do** keep pass labels at a 94% ink mix so they pass 4.5:1 on every fill.

### Don't:
- **Don't** hardcode colors in component CSS or give nexTix its own palette apart from KeyUp's themes.
- **Don't** fill a Doing pass with the full accent; it reads as Failed where the accent sits near red.
- **Don't** use the accent for links, hover washes, state hues or decoration; links stay neutral ink.
- **Don't** put gradients on fills; color is flat at full commitment.
- **Don't** draw kanban lanes with borders or grey cards; states are stacks of overlapping passes.
- **Don't** use the pass header strip as a section label; it carries data (repo and issue number).

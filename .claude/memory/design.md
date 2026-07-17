---
name: design
description: Visual/UI design principles for the dragontag TUI. Read before editing templates, CSS, or any user-facing surface.
metadata:
  type: feedback
---

# Design principles

The full anti-slop reference is [slop.md](slop.md) (the pols.dev design law, verbatim). This file
is its **project-specific reconciliation**: how those rules apply to dragontag's deliberate
terminal/TUI aesthetic. When the two seem to conflict, remember slop.md's own overriding clause —
a specific, deliberate, cohesive brand direction wins over the generic default, and *cohesion is
the whole game*. dragontag has one, so protect it.

## The signature (do not drift from it)

dragontag is a **CRT / terminal** interface, chosen on purpose (see [user_preferences](user_preferences.md):
"the TUI aesthetic is deliberate"). The identity, all already built into `base.html` +
`frontend/app.input.css` + `frontend/tailwind.config.js`:

- **Surfaces:** near-black — `#000` page, `#0a0a0a`/`#060606` panels. Never a blue-charcoal or
  slate ink (slop.md "the cool blue-charcoal"), never a cream/gray UI-kit neutral.
- **Accent:** one phosphor green, `#c7f0c7`, used tonally (focus reticles, cursor, active nav,
  the one thing in focus) — not sprayed on every element. Status hues: green `#7fbf7f`,
  red `#ffb4b4`, yellow `#f2e5b5`. That is the whole palette; do not introduce others.
- **Type:** monospace only — JetBrains Mono (signature), IBM Plex Mono fallback, self-hosted
  latin subset. No Google-Fonts rotation, no second display face. Mono here is honest: the whole
  app is a data console, which is exactly the case slop.md allows mono for.
- **Geometry:** zero border-radius everywhere (`borderRadius: 0` in the Tailwind config). Sharp
  corners are the signature; do not round anything.
- **Texture:** the CRT scanline overlay, phosphor text-glow on the accent, the notched `.dt-panel`
  border with corner reticles, the blinking `.dt-cursor`, the sticky keybind `.dt-statusbar`.
  This is the authored "creative layer" slop.md demands — it is why the app is not dead-flat.
- **No CDN, ever.** All assets vendored (fonts, htmx, alpine, compiled `app.css`). Works air-gapped.

## House rules (slop.md, applied here)

Follow these when adding or editing any template/CSS:

1. **Accent stays tonal and scarce.** Green marks focus/active/success, not decoration. Do not
   fill buttons with saturated green or add a green element "for interest". (slop.md: saturated
   accent; tonal accent.)
2. **No glows except the established CRT ones.** The scanline overlay and the phosphor text-glow
   are the signature. Do not add box-glows, inner-glow badges, pulsing "live" dots, or blurred
   drop-shadow blooms. (slop.md: background glow, inner-glow box, the bloom.)
3. **No gradients.** No blue→purple, no candy pastels, no gradient-filled text or pills, no
   gradient tiles. The app has none; keep it that way. (slop.md: purple gradients, gradient pill.)
4. **Chips are for genuine status only.** A bordered chip is legitimate for a real contained state
   (a job's review reason, its status). Do NOT wrap every number/label/score in a tinted pill —
   rank information with weight and colour instead. (slop.md: labels-as-pill-chips-everywhere.)
5. **Every string must clear its background.** Muted greys (`#5a5a5a`–`#8a8a8a`) are fine for
   secondary text on near-black, but never put low-value text on a filled status colour, and
   never let a control's label fail to stand off its fill. (slop.md: text you cannot read.)
6. **Real gutters, real centering.** Give text a deliberate margin from panel edges; the notched
   `.dt-label` and any number/glyph in a box must sit where it belongs. Verify, don't eyeball.
   (slop.md: text jammed against the edge; the chronic centering miss.)
7. **Honest, human copy.** Prefer readable labels over raw enum values (`low score`, not
   `low_score`). Say less; let hierarchy carry meaning. (slop.md: say less; real specificity.)
8. **No dead controls.** Anything that looks clickable must work, confirmed with a real click.
   Buttons that mutate state POST; destructive ones `confirm(...)`. (slop.md: dead controls;
   and see [conventions](conventions.md) Templates/Routes.)
9. **Never hide content behind an entrance animation.** Content is visible by default. Motion is
   only ever applied to already-visible elements (the cursor blink, hover state changes). No
   opacity-0-until-JS reveals. (slop.md: the invisible-content trap.)
10. **Lines earn their place.** The `.dt-panel` notched border and status-bar rules are structural.
    Do not drop bare hairline rules next to labels as decoration. (slop.md: eyebrow tick; unrounded
    hairline rules.)

## Restyling is a correction pass, not a reskin

Because the terminal identity is the deliberate, cohesive signature, "restyle against slop.md"
here means: hunt genuine slop tells (a stray glow, a gratuitous chip, a low-contrast string, a
machine-y enum shown raw, a missing gutter) and fix them — while keeping the one world intact. A
wholesale reskin (new fonts, colours, gradients, rounded cards) would *introduce* slop by
destroying cohesion, which slop.md ranks as the loudest failure of all. Compose from this brand,
don't swap it.

## Workflow reminder

The stylesheet is compiled and committed: after any template class change or edit to
`frontend/app.input.css`, run `bash frontend/build_css.sh` to regenerate
`dragontag/app/web/static/app.css`. New Tailwind utility classes (including arbitrary values like
`text-[#6f6f6f]`) silently do nothing until you rebuild. See [conventions](conventions.md)
"Templates" and [workflow](workflow.md).

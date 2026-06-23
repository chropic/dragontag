# frontend/

Front-end build toolchain for dragontag's UI stylesheet.

- `app.input.css` — Tailwind CSS entry point (`@tailwind base/components/utilities`). Also holds
  the terminal-UI **`@layer components` block** — the reusable `.dt-*` "TUI texture" primitives
  (`.dt-panel` + corner reticles, `.dt-label`, `.dt-statusbar`/`.dt-key`, `.dt-cursor`, `.dt-meter`)
  that keep the page templates readable. Edit these here, then rebuild.
- `tailwind.config.js` — font, colour, safelist config. The font stack is JetBrains Mono-first
  (`mono`/`sans` → `JetBrains Mono` → `IBM Plex Mono` → `monospace`); the `safelist` lists classes
  applied only dynamically (Alpine `:class` / JS `classList`) so the scanner can't see them.
- `build_css.sh` — downloads the Tailwind standalone CLI (no Node required) and compiles
  `dragontag/app/web/static/app.css`; re-run whenever you add or remove utility classes (or edit
  the `.dt-*` layer) in `dragontag/app/web/templates/` or `app.input.css`.

```
bash frontend/build_css.sh
```

## Fonts

The UI renders in **JetBrains Mono**, self-hosted (no CDN) for air-gapped boxes. Two woff2 files
live in `dragontag/app/web/static/fonts/` (`JetBrainsMono-Regular.woff2`, `JetBrainsMono-Bold.woff2`)
and are declared in that directory's `fonts.css`. If they're ever absent, `fonts.css` falls back to
the also-vendored IBM Plex Mono — the UI never lands in a broken/missing-glyph state. The vendored
latin subset omits box-drawing glyphs (`U+2500…`), which fall back to IBM Plex Mono per the
`unicode-range`; swap in a full JetBrains Mono build if you want its native box glyphs.

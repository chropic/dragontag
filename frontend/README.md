# Frontend

dragontag uses server-rendered Jinja2 templates with HTMX and Alpine.js. The
interface is deliberately a compact terminal/TUI rather than a generic web-app
theme. Preserve that identity when changing templates or styles.

## Visual contract

- **Palette:** true or near black surfaces with one restrained phosphor-green
  accent. Green indicates focus, active state, progress, or success; amber is
  review/warning and red is failure. Do not add gradients, decorative color
  blooms, or unrelated accent colors.
- **Type:** use the vendored JetBrains Mono family with IBM Plex Mono fallback.
  The interface is monospace throughout. Keep ASCII art compatible with the
  available font subsets; unsupported box-drawing glyphs can break alignment.
- **Geometry:** corners stay square. Do not introduce rounded cards, pill-shaped
  decoration, floating card stacks, or generic icon tiles.
- **Texture:** reuse the established `.dt-*` components—panels and reticles,
  labels, status bar and keys, cursor, and meter. The scanline and restrained
  phosphor text treatment are the intentional CRT effects; do not add box
  glows, pulsing dots, or entrance effects that hide content until JavaScript
  runs.
- **Hierarchy:** reserve chips for real status values, keep copy concise and
  human-readable, and make every control that looks interactive work. Preserve
  deliberate gutters, alignment, contrast, and keyboard hints.
- **Responsive behavior:** wide tables scroll within their containers and the
  navigation row scrolls rather than widening the page. New layouts must remain
  usable at the existing narrow breakpoint.

Templates extend `dragontag/app/web/templates/base.html`. HTMX fragments use a
leading underscore. Global shortcuts register through the `dtKeys` registry in
`base.html`; page-specific bindings stay with their template. Every shortcut
shown in a status bar must be implemented.

All browser assets are self-hosted. Fonts live under
`dragontag/app/web/static/fonts`, while HTMX and Alpine live under
`web/static/vendor`. Do not add CDN dependencies.

## Stylesheet build

`app.input.css` is the Tailwind entry point and contains the reusable `.dt-*`
component layer. `tailwind.config.js` defines the palette, typography,
zero-radius geometry, and safelist for classes applied only by JavaScript.

The generated stylesheet is committed at
`dragontag/app/web/static/app.css`. Rebuild it whenever templates add or remove
utility classes or when `app.input.css` changes:

```bash
bash frontend/build_css.sh
```

The script downloads Tailwind's standalone CLI into the ignored `build_tmp/`
directory when needed; Node.js is not required. If the download is unavailable,
do not assume a new class exists—check the committed CSS or defer generation to
an environment that can run the build.

After UI changes, verify the affected flow in a browser at desktop and narrow
widths. Check actual clicks, HTMX swaps, form fallbacks, keyboard controls,
overflow, text contrast, and alignment rather than relying only on template
inspection.

# frontend/

Front-end build toolchain for dragontag's UI stylesheet.

- `app.input.css` — Tailwind CSS entry point (`@tailwind base/components/utilities`)
- `tailwind.config.js` — font, colour, safelist config
- `build_css.sh` — downloads the Tailwind standalone CLI (no Node required) and compiles
  `dragontag/app/web/static/app.css`; re-run whenever you add or remove utility classes
  in `dragontag/app/web/templates/`

```
bash frontend/build_css.sh
```

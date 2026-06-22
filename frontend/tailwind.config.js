/** @type {import('tailwindcss').Config} */
// Build the static stylesheet with: ./frontend/build_css.sh
// (mirrors the inline config that used to live in base.html under the Play CDN)
module.exports = {
  content: ["./dragontag/app/web/templates/**/*.html"],
  // Classes that are only ever applied dynamically (Alpine :class bindings or
  // JS classList toggles) and therefore can't be found by scanning markup.
  safelist: [
    "border-white",
    "animate-pulse",
    "w-full",
    "opacity-60",
    "bg-[#0c0c0c]",
    "border-[#c7f0c7]",
    "text-[#c7f0c7]",
    "border-[#ffb4b4]",
    "text-[#ffb4b4]",
    "border-[#4a4a4a]",
    "text-[#cfcfcf]",
    // redesign status-color helpers used in conditional Jinja branches
    "text-[#7fbf7f]",
    "text-[#f2e5b5]",
    "bg-[#0d0e0a]",
  ],
  theme: {
    borderRadius: {
      none: "0", sm: "0", DEFAULT: "0", md: "0", lg: "0",
      xl: "0", "2xl": "0", "3xl": "0", full: "9999px",
    },
    extend: {
      fontFamily: {
        // JetBrains Mono is the redesign face. If its woff2 files aren't present
        // in /static/fonts (see fonts.css), the cascade falls back to the
        // already-vendored IBM Plex Mono — no broken/missing-glyph state.
        mono: ["JetBrains Mono", "IBM Plex Mono", "monospace"],
        sans: ["JetBrains Mono", "IBM Plex Mono", "monospace"],
      },
    },
  },
  plugins: [],
};

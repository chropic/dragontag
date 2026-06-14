/** @type {import('tailwindcss').Config} */
// Build the static stylesheet with: ./scripts/build_css.sh
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
  ],
  theme: {
    borderRadius: {
      none: "0", sm: "0", DEFAULT: "0", md: "0", lg: "0",
      xl: "0", "2xl": "0", "3xl": "0", full: "9999px",
    },
    extend: {
      fontFamily: {
        mono: ["IBM Plex Mono", "JetBrains Mono", "monospace"],
        sans: ["IBM Plex Sans", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};

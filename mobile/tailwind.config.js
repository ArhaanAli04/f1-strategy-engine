/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx,ts,tsx}", "./src/**/*.{js,jsx,ts,tsx}"],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      // Minimal dark-theme palette — mirrors web/src/index.css's real
      // rendered dark-mode values (background/foreground) and its
      // row-tone/pill-surface hex constants directly (those were already
      // hex, not oklch). Not a full port of index.css's oklch token set —
      // extend as later checkpoints need more of it.
      colors: {
        background: "#0a0a0a",
        surface: "#141414",
        pill: "#2a2a2a",
        foreground: "#fafafa",
        muted: "#555555",
        destructive: "#ef4444",
      },
    },
  },
  plugins: [],
};

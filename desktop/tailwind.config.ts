import type { Config } from "tailwindcss";
import tailwindcssAnimate from "tailwindcss-animate";
import tailwindcssTypography from "@tailwindcss/typography";

export default {
  darkMode: "class",
  content: [
    "./index.html",
    "./src/**/*.{ts,tsx}",
    "./node_modules/@ai-matrx/design-system/dist/**/*.{js,mjs}",
  ],
  theme: {
    container: {
      center: true,
      padding: "2rem",
      screens: {
        "2xl": "1400px",
      },
    },
    extend: {
      /* ── Typography ────────────────────────────────── */
      fontFamily: {
        sans: [
          "system-ui",
          "-apple-system",
          "BlinkMacSystemFont",
          '"SF Pro Text"',
          '"Segoe UI"',
          "Roboto",
          "sans-serif",
        ],
        mono: ['"SF Mono"', '"JetBrains Mono"', "monospace"],
      },

      /* ── Colors (CSS variable backed) ──────────────── */
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        /* The status vocabulary @ai-matrx/design-system paints with — Badge
           `success`/`warning`/`info`, Button `success`, Progress tones, Alert.
           Without these three entries the package's variants generate NO
           utility at all and render as unstyled text (design-system 0.4.0
           Consumer action 2b, the failure four consumer apps shipped for a
           week). The VALUES come from the package's own tokens.css defaults
           unless this app overrides them in index.css. */
        success: {
          DEFAULT: "hsl(var(--success))",
          foreground: "hsl(var(--success-foreground))",
        },
        warning: {
          DEFAULT: "hsl(var(--warning))",
          foreground: "hsl(var(--warning-foreground))",
        },
        info: "hsl(var(--info))",
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        sidebar: {
          DEFAULT: "hsl(var(--sidebar))",
          foreground: "hsl(var(--sidebar-foreground))",
          accent: "hsl(var(--sidebar-accent))",
          "accent-foreground": "hsl(var(--sidebar-accent-foreground))",
          border: "hsl(var(--sidebar-border))",
        },
      },

      /* ── Border Radius (Apple-style) ───────────────── */
      borderRadius: {
        lg: "calc(var(--radius) + 4px)",  /* 12px — cards, dropdowns */
        md: "var(--radius)",               /* 8px — inputs, buttons */
        sm: "calc(var(--radius) - 2px)",   /* 6px — tags, chips */
      },

      /* ── Shadows (soft, diffused) ──────────────────── */
      boxShadow: {
        glass: "var(--glass-shadow)",
      },

      /* ── Keyframes ─────────────────────────────────── */
      /* No accordion keyframes here: @ai-matrx/design-system ships
         `.matrx-accordion-content` / `.matrx-collapsible-content` and their
         keyframes in its own styles.css (0.8.0 / 0.9.0 Consumer action), and
         this app had zero `animate-accordion-*` call sites. `tailwindcss-animate`
         DOES stay in the plugin list — unlike matrx-extend, seven components
         here use `animate-in` / `slide-in-from-*` of their own. */
      keyframes: {
        "pulse-subtle": {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.7" },
        },
      },
      animation: {
        "pulse-subtle": "pulse-subtle 2s ease-in-out infinite",
      },
    },
  },
  plugins: [tailwindcssAnimate, tailwindcssTypography],
} satisfies Config;

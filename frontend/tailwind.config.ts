import type { Config } from "tailwindcss";

// Paleta v5: dark moderno (Vercel/Supabase-like) com micro-labels e
// números tabulares grandes — herdado do estilo do dashboard v4.
export default {
  content: ["./index.html", "./src/**/*.{vue,ts,js}"],
  theme: {
    extend: {
      colors: {
        // Backgrounds (do mais escuro para o mais elevado)
        bg: {
          DEFAULT:  "#0a0c12",   // body
          card:     "#161925",   // cards
          elevated: "#1d2030",   // hover/active de cards e linhas
          input:    "#0e111a",   // inputs/dropdowns
        },
        // Borders sutis sobre dark — usar com /opacity
        border: {
          DEFAULT: "rgba(255,255,255,0.06)",
          strong:  "rgba(255,255,255,0.10)",
          accent:  "rgba(34,211,238,0.30)",
        },
        // Texto — clareados em 2026-04-28 para melhor leitura no dark
        text: {
          primary:   "#ffffff",  // títulos, números, body principal
          secondary: "#e2e8f0",  // labels, descrições (era cinza médio)
          muted:     "#cbd5e1",  // metadados pouco relevantes (era cinza escuro)
          dim:       "#94a3b8",  // placeholders, ícones desabilitados
        },
        // Acentos (vivos mas sóbrios)
        primary: {
          DEFAULT: "#22d3ee",    // ciano (Supabase-ish)
          bg:      "rgba(34,211,238,0.10)",
          ring:    "rgba(34,211,238,0.40)",
        },
        accent: {
          green:  "#22c55e",
          amber:  "#f59e0b",
          red:    "#ef4444",
          purple: "#a78bfa",
          blue:   "#3b82f6",
        },
      },
      fontFamily: {
        sans: ["Montserrat", "ui-sans-serif", "-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "Monaco", "Consolas", "monospace"],
      },
      fontSize: {
        // micro = label uppercase tracking-widest do estilo "console operacional"
        "micro": ["10px", { lineHeight: "1.3", letterSpacing: "0.08em" }],
        // valores numéricos grandes/destaque (StatCards)
        "stat":  ["22px", { lineHeight: "1.1", letterSpacing: "-0.02em" }],
        "stat-lg":["28px",{ lineHeight: "1.05", letterSpacing: "-0.025em" }],
      },
      animation: {
        "pulse-dot": "pulse 2s cubic-bezier(0.4,0,0.6,1) infinite",
        "shimmer":   "shimmer 1.5s ease-in-out infinite",
        "fade-in":   "fadeIn 0.2s ease-out",
      },
      keyframes: {
        shimmer: {
          "0%, 100%": { opacity: "0.5" },
          "50%":      { opacity: "1" },
        },
        fadeIn: {
          "from": { opacity: "0", transform: "translateY(4px)" },
          "to":   { opacity: "1", transform: "translateY(0)" },
        },
      },
      boxShadow: {
        "glow-primary": "0 0 0 1px rgba(34,211,238,0.40), 0 0 20px rgba(34,211,238,0.15)",
        "glow-amber":   "0 0 0 1px rgba(245,158,11,0.30), 0 0 20px rgba(245,158,11,0.10)",
      },
    },
  },
  plugins: [],
} satisfies Config;

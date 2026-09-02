/**
 * Design tokens, defined once.
 *
 * Every token is one entry carrying both its CSS custom-property name and its
 * value. The four views callers actually use — `tokens`, `cssVars`,
 * `cssProperties`, `cssVar()` — are derived from that entry rather than
 * maintained beside it.
 *
 * That is the one structural change made during extraction, and it was not
 * cosmetic. The source kept those four as parallel hand-written lists, and they
 * had already disagreed: `cssVar()` returned a different CSS name from the
 * `cssVars` map for 9 of 54 tokens — every spacing stop among them, plus its
 * own documented example. `cssVar('sp4')` produced `var(--sp4)`, which is not
 * a variable anything defines, so the declaration using it was silently dropped.
 * Deriving the views makes that class of disagreement impossible to write.
 *
 * CSS names and values are carried over unchanged: they are the contract with
 * any stylesheet that references them.
 */

/** One token: the CSS custom property it defines, and its default value. */
export interface TokenSpec {
  readonly css: string;
  readonly value: string;
}

const define = <K extends string>(specs: Record<K, TokenSpec>): Record<K, TokenSpec> => specs;

// ── Brand purple — chrome and canvas depth ────────────────────────────

const brandPurpleSpecs = define({
  purpleHeader: { css: "--purple-header", value: "#2c1d4b" },
  purple900: { css: "--purple-900", value: "#1a1130" },
  purple800: { css: "--purple-800", value: "#241a44" },
  canvasInk: { css: "--canvas-ink", value: "#141019" },
});

// ── Neutral surfaces — content and hub ────────────────────────────────

const surfacesSpecs = define({
  bg: { css: "--bg", value: "#18171d" },
  bg2: { css: "--bg-2", value: "#1d1c23" },
  surface: { css: "--surface", value: "#242430" },
  surface2: { css: "--surface-2", value: "#2b2a37" },
  chrome: { css: "--chrome", value: "#1c1a27" },
  chrome2: { css: "--chrome-2", value: "#1c1631" },
});

// ── Lines ─────────────────────────────────────────────────────────────

const linesSpecs = define({
  border: { css: "--border", value: "rgba(139,123,216,.18)" },
  border2: { css: "--border-2", value: "rgba(139,123,216,.32)" },
});

// ── Accent ────────────────────────────────────────────────────────────

const accentSpecs = define({
  accent: { css: "--accent", value: "#34d77f" },
  accentBright: { css: "--accent-bright", value: "#4ee592" },
  accentDeep: { css: "--accent-deep", value: "#1f9d5a" },
});

// ── Status semantics ──────────────────────────────────────────────────

const statusSpecs = define({
  ok: { css: "--ok", value: "#34d77f" },
  warn: { css: "--warn", value: "#f2b342" },
  crit: { css: "--crit", value: "#ff5a6a" },
});

// ── Text ──────────────────────────────────────────────────────────────

const textSpecs = define({
  text: { css: "--text", value: "#ece9f7" },
  dim: { css: "--dim", value: "rgba(236,233,247,.56)" },
  faint: { css: "--faint", value: "rgba(236,233,247,.34)" },
  onAccent: { css: "--on-accent", value: "#0a0712" },
});

// ── Typography ────────────────────────────────────────────────────────

const typographySpecs = define({
  fontUi: { css: "--font-ui", value: "'IBM Plex Sans', system-ui, sans-serif" },
  fontMono: { css: "--font-mono", value: "'JetBrains Mono', ui-monospace, monospace" },
  fsDisplay: { css: "--fs-display", value: "34px" },
  fsH1: { css: "--fs-h1", value: "22px" },
  fsH2: { css: "--fs-h2", value: "16px" },
  fsBody: { css: "--fs-body", value: "13.5px" },
  fsLabel: { css: "--fs-label", value: "11.5px" },
  fsMicro: { css: "--fs-micro", value: "10px" },
  fwReg: { css: "--fw-reg", value: "400" },
  fwMed: { css: "--fw-med", value: "500" },
  fwSemi: { css: "--fw-semi", value: "600" },
  fwBold: { css: "--fw-bold", value: "700" },
});

// ── Spacing — 4px base grid, 7 stops ──────────────────────────────────

const spacingSpecs = define({
  sp1: { css: "--sp-1", value: "4px" },
  sp2: { css: "--sp-2", value: "8px" },
  sp3: { css: "--sp-3", value: "12px" },
  sp4: { css: "--sp-4", value: "16px" },
  sp5: { css: "--sp-5", value: "24px" },
  sp6: { css: "--sp-6", value: "32px" },
  sp7: { css: "--sp-7", value: "48px" },
});

// ── Radius ────────────────────────────────────────────────────────────

const radiusSpecs = define({
  rSm: { css: "--r-sm", value: "6px" },
  rMd: { css: "--r-md", value: "10px" },
  rLg: { css: "--r-lg", value: "14px" },
  rXl: { css: "--r-xl", value: "18px" },
  rPill: { css: "--r-pill", value: "999px" },
});

// ── Shadows ───────────────────────────────────────────────────────────

const shadowsSpecs = define({
  shCard: { css: "--sh-card", value: "0 4px 16px -4px rgba(0,0,0,.5)" },
  shFloat: { css: "--sh-float", value: "0 20px 54px -14px rgba(0,0,0,.66)" },
  glowAccent: { css: "--glow-accent", value: "0 0 18px -2px var(--accent)" },
});

// ── Motion ────────────────────────────────────────────────────────────

const motionSpecs = define({
  easeOut: { css: "--ease-out", value: "cubic-bezier(.4, 0, .2, 1)" },
  easeSpring: { css: "--ease-spring", value: "cubic-bezier(.34, 1.12, .4, 1)" },
  tFast: { css: "--t-fast", value: ".12s" },
  tMed: { css: "--t-med", value: ".2s" },
  tSlow: { css: "--t-slow", value: ".3s" },
});

// ── The whole table ────────────────────────────────────────────────────────

export const tokenSpecs = {
  ...brandPurpleSpecs,
  ...surfacesSpecs,
  ...linesSpecs,
  ...accentSpecs,
  ...statusSpecs,
  ...textSpecs,
  ...typographySpecs,
  ...spacingSpecs,
  ...radiusSpecs,
  ...shadowsSpecs,
  ...motionSpecs,
} as const;

export type TokenKey = keyof typeof tokenSpecs;

/**
 * Light-mode overrides, keyed by token. Only tokens that differ from the dark
 * default appear — everything else is inherited, so a token added to the dark
 * table does not silently go missing in light.
 */
export const lightTokenValues: Partial<Record<TokenKey, string>> = {
  purpleHeader: "#ece7f9",
  purple900: "#d7ccf0",
  purple800: "#e4dcf6",
  bg: "#f6f5fb",
  bg2: "#eeecf6",
  surface: "#ffffff",
  surface2: "#fbfaff",
  chrome: "#ede9f8",
  chrome2: "#e0dbf2",
  border: "rgba(100,80,180,.14)",
  border2: "rgba(100,80,180,.26)",
  accent: "#149a59",
  accentBright: "#18b066",
  accentDeep: "#0d7040",
  ok: "#18b066",
  warn: "#c47e12",
  crit: "#e23a4e",
  text: "#1d1830",
  dim: "rgba(29,24,48,.62)",
  faint: "rgba(29,24,48,.40)",
  onAccent: "#ffffff",
  shCard: "0 4px 16px -4px rgba(40,30,80,.12)",
  shFloat: "0 20px 54px -14px rgba(40,30,80,.18)",
  glowAccent: "0 0 18px -2px color-mix(in srgb, var(--accent) 50%, transparent)",
};

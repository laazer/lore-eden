/**
 * The theme, resolved for whichever mode is active.
 *
 * Built from the token table rather than restated beside it. The source listed
 * every token name a second time here to assemble the object, and that list had
 * already drifted — `chrome2` existed as a token and in the CSS variables, but
 * was missing from the theme's colours, so `theme.colors.chrome2` was
 * `undefined` while `vars.chrome2` worked.
 */

import { useContext, useMemo } from 'react';

import { cssVars } from '../tokens';
import type { TokenKey } from '../tokens/specs';
import { ThemeModeContext } from './ThemeProvider';
import { resolveTokens } from './resolve';
import type { ThemeMode } from './resolve';

export const breakpointValues = {
  xs: 0,
  sm: 600,
  md: 960,
  lg: 1280,
  xl: 1920,
} as const;

export type BreakpointKey = keyof typeof breakpointValues;

export interface Breakpoints {
  values: Record<BreakpointKey, number>;
  /** `@media (min-width: …)` — this breakpoint and wider. */
  up: (key: BreakpointKey) => string;
  /** `@media (max-width: …)` — narrower than this breakpoint, exclusive. */
  down: (key: BreakpointKey) => string;
}

const breakpoints: Breakpoints = {
  values: { ...breakpointValues },
  up: (key) => `@media (min-width: ${breakpointValues[key]}px)`,
  down: (key) => {
    const keys = Object.keys(breakpointValues) as BreakpointKey[];
    // `down` is exclusive, so the bound is one pixel below the breakpoint.
    // `xs` is 0 and has nothing below it; the query is deliberately one that
    // never matches rather than a negative width.
    if (keys.indexOf(key) <= 0) return '@media (max-width: 0px)';
    return `@media (max-width: ${breakpointValues[key] - 1}px)`;
  },
};

/** `spacing(2)` → "8px"; `spacing(1, 2)` → "4px 8px". */
export type SpacingFn = (...factors: number[]) => string;

const spacing: SpacingFn = (...factors) => factors.map((f) => `${f * 4}px`).join(' ');

export interface Theme {
  /** The mode these values were resolved for. */
  mode: ThemeMode;
  /** Every token's value in this mode. */
  values: Record<TokenKey, string>;
  spacing: SpacingFn;
  breakpoints: Breakpoints;
  /**
   * `var()` references. Mode-independent by nature — a reference resolves
   * wherever it is used, which is why it is the better thing to style with.
   */
  vars: Record<TokenKey, string>;
}

const THEMES: Partial<Record<ThemeMode, Theme>> = {};

function buildTheme(mode: ThemeMode): Theme {
  return {
    mode,
    values: resolveTokens(mode),
    spacing,
    breakpoints,
    vars: cssVars,
  };
}

/** The theme for one mode, without a React tree. Cached per mode. */
export function getTheme(mode: ThemeMode): Theme {
  const cached = THEMES[mode];
  if (cached) return cached;
  const built = buildTheme(mode);
  THEMES[mode] = built;
  return built;
}

/**
 * The active theme.
 *
 * Outside a provider this is the dark theme, which is the documented default
 * rather than an error — a component rendered on its own in a test or a story
 * should still have values to work with.
 */
export function useTheme(): Theme {
  const mode = useContext(ThemeModeContext);
  return useMemo(() => getTheme(mode), [mode]);
}

/** Just the active mode, for a component that only needs to branch on it. */
export function useThemeMode(): ThemeMode {
  return useContext(ThemeModeContext);
}

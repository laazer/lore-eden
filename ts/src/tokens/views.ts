/**
 * The views callers use, all derived from `tokenSpecs`.
 *
 * Four ways to reach the same token, because the three call sites want
 * different things:
 *
 *   tokens.accent        // "#34d77f"          — the raw value
 *   vars.accent          // "var(--accent)"    — a reference, for inline styles
 *   cssProperties        // { "--accent": … }  — for injecting into a rule
 *   cssVar('accent')     // "var(--accent)"    — a reference, computed
 *
 * A reference is what you almost always want in a component: it follows the
 * active theme, where a raw value is frozen at the moment it was read.
 */

import { lightTokenValues, tokenSpecs } from './specs';
import type { TokenKey, TokenSpec } from './specs';

const entries = Object.entries(tokenSpecs) as [TokenKey, TokenSpec][];

/** Raw token values, keyed by token name. Frozen at read time — see above. */
export const tokens = Object.fromEntries(
  entries.map(([key, spec]) => [key, spec.value]),
) as Record<TokenKey, string>;

/**
 * `var()` references, keyed by token name. Prefer these in style props:
 *
 *   style={{ color: vars.text, background: vars.surface }}
 */
export const cssVars = Object.fromEntries(
  entries.map(([key, spec]) => [key, `var(${spec.css})`]),
) as Record<TokenKey, string>;

/** Short alias for {@link cssVars}. */
export const vars = cssVars;

/** CSS custom property name → value, for injecting into a rule. */
export const cssProperties: Record<string, string> = Object.fromEntries(
  entries.map(([, spec]) => [spec.css, spec.value]),
);

/** The light-mode subset, in the same shape as {@link cssProperties}. */
export const lightCssProperties: Record<string, string> = Object.fromEntries(
  Object.entries(lightTokenValues).map(([key, value]) => [
    tokenSpecs[key as TokenKey].css,
    value as string,
  ]),
);

/**
 * A `var()` reference for one token.
 *
 * Reads the name off the token's own entry, so it cannot disagree with
 * {@link cssVars} — which the hand-written version it replaces did, for 9 of 54
 * tokens.
 */
export function cssVar(name: TokenKey): string {
  return `var(${tokenSpecs[name].css})`;
}

/** The CSS custom property name a token defines, without the `var()` wrapper. */
export function cssName(name: TokenKey): string {
  return tokenSpecs[name].css;
}

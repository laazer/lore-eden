/**
 * Category views — values only, in the groupings the tokens were authored in.
 *
 * Derived from the table rather than restated, so a token cannot exist in the
 * CSS variables and be missing from its category. It could before: `chrome2`
 * was a real token that the extracted theme object's colour list had dropped.
 */

import { tokenSpecs } from './specs';
import type { TokenKey, TokenSpec } from './specs';

const valuesOf = <K extends string>(specs: Record<K, TokenSpec>): Record<K, string> =>
  Object.fromEntries(
    Object.entries<TokenSpec>(specs).map(([key, spec]) => [key, spec.value]),
  ) as Record<K, string>;

const pick = <K extends TokenKey>(...keys: K[]): Record<K, TokenSpec> =>
  Object.fromEntries(keys.map((key) => [key, tokenSpecs[key]])) as Record<K, TokenSpec>;

// Category views. Values only, matching the shape these had before extraction.

export const brandPurple = valuesOf(
  pick('purpleHeader', 'purple900', 'purple800', 'canvasInk'),
);
export const surfaces = valuesOf(
  pick('bg', 'bg2', 'surface', 'surface2', 'chrome', 'chrome2'),
);
export const lines = valuesOf(pick('border', 'border2'));
export const accent = valuesOf(pick('accent', 'accentBright', 'accentDeep'));
export const status = valuesOf(pick('ok', 'warn', 'crit'));
export const text = valuesOf(pick('text', 'dim', 'faint', 'onAccent'));

export const colors = {
  ...brandPurple,
  ...surfaces,
  ...lines,
  ...accent,
  ...status,
  ...text,
};

export const typography = valuesOf(
  pick(
    'fontUi', 'fontMono',
    'fsDisplay', 'fsH1', 'fsH2', 'fsBody', 'fsLabel', 'fsMicro',
    'fwReg', 'fwMed', 'fwSemi', 'fwBold',
  ),
);
export const spacing = valuesOf(pick('sp1', 'sp2', 'sp3', 'sp4', 'sp5', 'sp6', 'sp7'));
export const radius = valuesOf(pick('rSm', 'rMd', 'rLg', 'rXl', 'rPill'));
export const shadows = valuesOf(pick('shCard', 'shFloat', 'glowAccent'));
export const motion = valuesOf(pick('easeOut', 'easeSpring', 'tFast', 'tMed', 'tSlow'));

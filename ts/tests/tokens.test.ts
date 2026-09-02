/**
 * The token table and the four views derived from it.
 *
 * The views used to be four hand-written lists that had to agree, and they did
 * not. Most of what is asserted here is that they agree by construction now.
 */

import { describe, expect, it } from 'vitest';

import {
  colors,
  cssName,
  cssProperties,
  cssVar,
  cssVars,
  lightCssProperties,
  lightTokenValues,
  motion,
  radius,
  shadows,
  spacing,
  tokenSpecs,
  tokens,
  typography,
  vars,
} from '../src/tokens';
import type { TokenKey } from '../src/tokens';

const keys = Object.keys(tokenSpecs) as TokenKey[];

describe('the table', () => {
  it('carries every token exactly once', () => {
    expect(keys.length).toBe(54);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it('gives every token a distinct CSS custom property', () => {
    const names = keys.map((key) => tokenSpecs[key].css);
    expect(new Set(names).size).toBe(names.length);
  });

  it('names every custom property in the `--kebab-case` form CSS expects', () => {
    for (const key of keys) {
      expect(tokenSpecs[key].css).toMatch(/^--[a-z0-9]+(-[a-z0-9]+)*$/);
    }
  });
});

describe('the three access modes', () => {
  it('gives raw values, keyed by token', () => {
    expect(tokens.accent).toBe('#34d77f');
    expect(tokens.sp4).toBe('16px');
    expect(tokens.fontUi).toContain('IBM Plex Sans');
  });

  it('gives var() references, keyed by token', () => {
    expect(cssVars.accent).toBe('var(--accent)');
    expect(cssVars.sp4).toBe('var(--sp-4)');
    expect(vars).toBe(cssVars);
  });

  it('gives a custom-property map for injection', () => {
    expect(cssProperties['--accent']).toBe('#34d77f');
    expect(cssProperties['--sp-4']).toBe('16px');
    expect(Object.keys(cssProperties).length).toBe(keys.length);
  });

  it('computes a reference for a token chosen at runtime', () => {
    expect(cssVar('accentBright')).toBe('var(--accent-bright)');
    expect(cssName('accentBright')).toBe('--accent-bright');
  });
});

describe('the views cannot disagree', () => {
  it('resolves cssVar() and the cssVars map to the same name for every token', () => {
    // The regression this whole shape exists to prevent. In the source these
    // were computed two different ways and differed for 9 of 54 tokens — every
    // spacing stop among them, plus the helper's own documented example.
    for (const key of keys) {
      expect(cssVar(key)).toBe(cssVars[key]);
    }
  });

  it('resolves every reference to a property the injected block defines', () => {
    // A var() naming an undefined property resolves to nothing and the
    // declaration is silently dropped, so this is the check that catches it.
    for (const key of keys) {
      expect(cssProperties).toHaveProperty(cssName(key));
    }
  });

  it('agrees between the raw values and the injected block', () => {
    for (const key of keys) {
      expect(cssProperties[cssName(key)]).toBe(tokens[key]);
    }
  });

  it('spells the documented example correctly', () => {
    // `cssVar('sp4')` was documented as "var(--sp-4)" and returned
    // "var(--sp4)".
    expect(cssVar('sp4')).toBe('var(--sp-4)');
  });
});

describe('light overrides', () => {
  it('lists only tokens that actually differ', () => {
    for (const [key, value] of Object.entries(lightTokenValues)) {
      expect(value).not.toBe(tokens[key as TokenKey]);
    }
  });

  it('overrides a subset, not everything', () => {
    const count = Object.keys(lightTokenValues).length;
    expect(count).toBeGreaterThan(0);
    expect(count).toBeLessThan(keys.length);
  });

  it('maps to the same custom properties as the dark block', () => {
    for (const name of Object.keys(lightCssProperties)) {
      expect(cssProperties).toHaveProperty(name);
    }
  });

  it('deepens the accent for legibility on white', () => {
    expect(tokens.accent).toBe('#34d77f');
    expect(lightTokenValues.accent).toBe('#149a59');
  });
});

describe('category views', () => {
  it('partition the table without loss', () => {
    const categorised = new Set([
      ...Object.keys(colors),
      ...Object.keys(typography),
      ...Object.keys(spacing),
      ...Object.keys(radius),
      ...Object.keys(shadows),
      ...Object.keys(motion),
    ]);
    expect(categorised.size).toBe(keys.length);
  });

  it('include chrome2, which the extracted theme object had dropped', () => {
    expect(colors.chrome2).toBe(tokens.chrome2);
    expect(colors.chrome2).toBeDefined();
  });

  it('carry values, not references', () => {
    expect(colors.accent).toBe('#34d77f');
    expect(spacing.sp4).toBe('16px');
  });
});

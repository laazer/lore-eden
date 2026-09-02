/**
 * The provider and the hook, in a real DOM.
 *
 * Asserting on the *resolved* custom properties rather than on the string the
 * provider injected: a stylesheet that is present but not applying is exactly
 * the failure a string assertion misses.
 */

import { render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import { cssName, tokens } from '../src/tokens';
import {
  LIGHT_ATTRIBUTE,
  STYLE_TAG_ID,
  ThemeProvider,
  getTheme,
  resolveToken,
  resolveTokens,
  tokensThatDiffer,
  useTheme,
  useThemeMode,
} from '../src/theme';

afterEach(() => {
  document.getElementById(STYLE_TAG_ID)?.remove();
  document.documentElement.removeAttribute(LIGHT_ATTRIBUTE);
});

/** What the browser actually computes for a custom property on :root. */
function resolvedProperty(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function Probe(): React.ReactElement {
  const theme = useTheme();
  const mode = useThemeMode();
  return (
    <div>
      <span data-testid="mode">{mode}</span>
      <span data-testid="theme-mode">{theme.mode}</span>
      <span data-testid="bg">{theme.values.bg}</span>
      <span data-testid="accent">{theme.values.accent}</span>
      <span data-testid="bg-ref">{theme.vars.bg}</span>
    </div>
  );
}

describe('injection', () => {
  it('defines the tokens as custom properties on :root', () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    );

    expect(resolvedProperty(cssName('bg'))).toBe(tokens.bg);
    expect(resolvedProperty(cssName('accent'))).toBe(tokens.accent);
    expect(resolvedProperty(cssName('sp4'))).toBe(tokens.sp4);
  });

  it('defines the font tokens, which a var() reference needs to resolve', () => {
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    );

    expect(resolvedProperty(cssName('fontUi'))).toContain('IBM Plex Sans');
  });

  it('injects one stylesheet however many providers are mounted', () => {
    render(
      <ThemeProvider>
        <ThemeProvider>
          <div />
        </ThemeProvider>
      </ThemeProvider>,
    );

    expect(document.querySelectorAll(`#${STYLE_TAG_ID}`)).toHaveLength(1);
  });

  it('keeps the stylesheet while any provider still needs it', () => {
    // The source removed it whenever *a* provider unmounted, so the first to go
    // stripped the tokens out from under the others.
    const { unmount } = render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    );
    render(
      <ThemeProvider>
        <div />
      </ThemeProvider>,
    );

    unmount();

    expect(document.getElementById(STYLE_TAG_ID)).not.toBeNull();
    expect(resolvedProperty(cssName('bg'))).toBe(tokens.bg);
  });

  it('cleans up once the last provider goes', () => {
    const { unmount } = render(
      <ThemeProvider mode="light">
        <div />
      </ThemeProvider>,
    );

    unmount();

    expect(document.getElementById(STYLE_TAG_ID)).toBeNull();
    // Leaving the attribute would pin the page to light after the tree that
    // asked for it is gone.
    expect(document.documentElement.hasAttribute(LIGHT_ATTRIBUTE)).toBe(false);
  });
});

describe('modes', () => {
  it('is dark unless told otherwise', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('mode')).toHaveTextContent('dark');
    expect(document.documentElement.hasAttribute(LIGHT_ATTRIBUTE)).toBe(false);
    expect(resolvedProperty(cssName('bg'))).toBe(tokens.bg);
  });

  it('resolves different custom properties in light mode', () => {
    render(
      <ThemeProvider mode="light">
        <Probe />
      </ThemeProvider>,
    );

    expect(document.documentElement.getAttribute(LIGHT_ATTRIBUTE)).toBe('light');
    const bg = resolvedProperty(cssName('bg'));
    expect(bg).not.toBe(tokens.bg);
    expect(bg).toBe(resolveToken('bg', 'light'));
  });

  it('leaves tokens with no light override alone', () => {
    render(
      <ThemeProvider mode="light">
        <div />
      </ThemeProvider>,
    );

    // Spacing is not a colour and does not change between modes.
    expect(resolvedProperty(cssName('sp4'))).toBe(tokens.sp4);
  });

  it('switches the resolved properties when the mode changes', () => {
    const { rerender } = render(
      <ThemeProvider mode="dark">
        <div />
      </ThemeProvider>,
    );
    expect(resolvedProperty(cssName('bg'))).toBe(tokens.bg);

    rerender(
      <ThemeProvider mode="light">
        <div />
      </ThemeProvider>,
    );

    expect(resolvedProperty(cssName('bg'))).toBe(resolveToken('bg', 'light'));
  });
});

describe('the hook follows the mode', () => {
  it('hands back light values under a light provider', () => {
    // The departure from the source, where the hook was a dark-only singleton:
    // anything styled from a raw theme value stayed dark while var() references
    // switched, so half a component changed and half did not.
    render(
      <ThemeProvider mode="light">
        <Probe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('theme-mode')).toHaveTextContent('light');
    expect(screen.getByTestId('bg')).toHaveTextContent(resolveToken('bg', 'light'));
    expect(screen.getByTestId('accent')).toHaveTextContent(resolveToken('accent', 'light'));
  });

  it('hands back dark values under a dark provider', () => {
    render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('bg')).toHaveTextContent(tokens.bg);
  });

  it('still gives references, which are mode-independent', () => {
    render(
      <ThemeProvider mode="light">
        <Probe />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('bg-ref')).toHaveTextContent('var(--bg)');
  });

  it('falls back to dark outside any provider', () => {
    // A component rendered alone in a test or a story should still have values.
    render(<Probe />);

    expect(screen.getByTestId('mode')).toHaveTextContent('dark');
    expect(screen.getByTestId('bg')).toHaveTextContent(tokens.bg);
  });
});

describe('resolution', () => {
  it('returns every token for a mode', () => {
    expect(Object.keys(resolveTokens('dark')).length).toBe(Object.keys(tokens).length);
    expect(Object.keys(resolveTokens('light')).length).toBe(Object.keys(tokens).length);
  });

  it('inherits unoverridden tokens into light rather than dropping them', () => {
    expect(resolveToken('sp4', 'light')).toBe(tokens.sp4);
    expect(resolveToken('fontUi', 'light')).toBe(tokens.fontUi);
  });

  it('reports which tokens actually differ', () => {
    const differing = tokensThatDiffer();

    expect(differing).toContain('bg');
    expect(differing).toContain('accent');
    expect(differing).not.toContain('sp4');
  });

  it('caches a theme per mode without sharing one between them', () => {
    expect(getTheme('dark')).toBe(getTheme('dark'));
    expect(getTheme('dark')).not.toBe(getTheme('light'));
  });
});

describe('accent override', () => {
  it('scopes an accent to its own subtree', () => {
    render(
      <ThemeProvider accentOverride="#ff0099">
        <div data-testid="child" />
      </ThemeProvider>,
    );

    const wrapper = screen.getByTestId('child').parentElement as HTMLElement;
    expect(wrapper.style.getPropertyValue('--accent')).toBe('#ff0099');
    // :root is untouched, so the rest of the page keeps its accent.
    expect(resolvedProperty(cssName('accent'))).toBe(tokens.accent);
  });

  it('adds no wrapper when there is nothing to override', () => {
    render(
      <ThemeProvider>
        <div data-testid="child" />
      </ThemeProvider>,
    );

    expect(screen.getByTestId('child').parentElement?.dataset.accentOverride).toBeUndefined();
  });
});

describe('helpers', () => {
  it('spaces on a 4px grid', () => {
    const { spacing } = getTheme('dark');

    expect(spacing(2)).toBe('8px');
    expect(spacing(1, 2)).toBe('4px 8px');
  });

  it('builds media queries either side of a breakpoint', () => {
    const { breakpoints } = getTheme('dark');

    expect(breakpoints.up('md')).toBe('@media (min-width: 960px)');
    // `down` is exclusive, so it stops one pixel short.
    expect(breakpoints.down('md')).toBe('@media (max-width: 959px)');
  });

  it('gives xs a query that never matches rather than a negative width', () => {
    expect(getTheme('dark').breakpoints.down('xs')).toBe('@media (max-width: 0px)');
  });
});

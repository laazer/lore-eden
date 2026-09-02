/**
 * Makes the tokens available to a React tree, two ways at once.
 *
 * As CSS custom properties, injected into `:root` (and a
 * `:root[data-theme="light"]` block), so any stylesheet or `var()` reference
 * follows the active mode. And as a context carrying the mode itself, so
 * {@link useTheme} can hand back *resolved* values rather than dark ones.
 *
 * That second part is the departure from the source. There, `useTheme()` was a
 * singleton built from the dark values and never learned the mode existed — so
 * anything styled from a raw theme value stayed dark in light mode, while
 * anything using `var()` switched. Half a component would change and half would
 * not, which reads as a styling mistake rather than a missing wire.
 *
 *   <ThemeProvider mode="light">
 *     <App />
 *   </ThemeProvider>
 */

import React, { createContext, useEffect, useMemo, useRef } from 'react';

import { cssProperties, lightCssProperties } from '../tokens';
import type { ThemeMode } from './resolve';

export const STYLE_TAG_ID = 'lore-eden-tokens';
export const LIGHT_ATTRIBUTE = 'data-theme';

export const ThemeModeContext = createContext<ThemeMode>('dark');

export interface ThemeProviderProps {
  children: React.ReactNode;
  /** Active mode. Dark unless said otherwise. */
  mode?: ThemeMode;
  /**
   * Scoped accent override, applied to a wrapper element rather than `:root`,
   * so one region can carry its own accent without disturbing the page.
   */
  accentOverride?: string;
}

function buildCssBlock(selector: string, properties: Record<string, string>): string {
  const declarations = Object.entries(properties)
    .map(([name, value]) => `  ${name}: ${value};`)
    .join('\n');
  return `${selector} {\n${declarations}\n}`;
}

export function tokenStyleSheet(): string {
  return [
    buildCssBlock(':root', cssProperties),
    buildCssBlock(`:root[${LIGHT_ATTRIBUTE}="light"]`, lightCssProperties),
  ].join('\n\n');
}

/**
 * How many providers are currently mounted.
 *
 * The source injected the style tag only when absent but removed it on *any*
 * provider unmounting — so with two mounted, the first to unmount stripped the
 * tokens out from under the second and the page lost every variable at once.
 * Counting means the tag lives exactly as long as somebody needs it.
 */
let mountedProviders = 0;

export function ThemeProvider({
  children,
  mode = 'dark',
  accentOverride,
}: ThemeProviderProps): React.ReactElement {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    mountedProviders += 1;
    if (!document.getElementById(STYLE_TAG_ID)) {
      const style = document.createElement('style');
      style.id = STYLE_TAG_ID;
      style.textContent = tokenStyleSheet();
      document.head.appendChild(style);
    }
    return () => {
      mountedProviders -= 1;
      if (mountedProviders > 0) return;
      document.getElementById(STYLE_TAG_ID)?.remove();
      // The attribute is ours; leaving it behind would pin the page to light
      // after the tree that asked for it is gone.
      document.documentElement.removeAttribute(LIGHT_ATTRIBUTE);
    };
  }, []);

  useEffect(() => {
    if (mode === 'light') {
      document.documentElement.setAttribute(LIGHT_ATTRIBUTE, 'light');
    } else {
      document.documentElement.removeAttribute(LIGHT_ATTRIBUTE);
    }
  }, [mode]);

  const content = useMemo(() => {
    if (!accentOverride) return <>{children}</>;
    return (
      <div
        ref={containerRef}
        data-accent-override
        style={{ '--accent': accentOverride } as React.CSSProperties}
      >
        {children}
      </div>
    );
  }, [accentOverride, children]);

  return <ThemeModeContext.Provider value={mode}>{content}</ThemeModeContext.Provider>;
}

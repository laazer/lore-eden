/**
 * What size the window currently is, measured once per resize.
 *
 * The source exported a bare `flexSize()` that read `window.innerWidth` every
 * time it was called, and components called it during render. Reading layout in
 * a render path forces the browser to flush pending style work to answer, and
 * doing it once per component per render is how a resize becomes janky.
 *
 * Fixing that needs more than moving the read into a hook.
 * `useSyncExternalStore` calls its snapshot function on **every render** — that
 * is how it detects tearing — so a snapshot that measures is still a measurement
 * per render. The store below caches instead: it measures when a resize fires,
 * and every reader gets the cached answer.
 *
 * It also notifies only when the *size* changes, not the width. Dragging a
 * window edge fires hundreds of resize events; all but the few that cross a
 * boundary leave every subscriber's answer identical, and waking them to say so
 * is the cost this exists to avoid.
 */

import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { screenSizeFor, type ScreenSize } from './breakpoints';
import { resolveFlexConfig, type FlexConfig } from './flexConfig';

export interface WindowSize {
  width: number;
  height: number;
}

/** Whether there is a window to measure — false during server rendering. */
const canMeasure = (): boolean => typeof window !== 'undefined';

/**
 * The window's size right now, measured on the spot.
 *
 * Outside a browser this reports zero, and {@link screenSizeFor} calls that the
 * narrowest size — the right default for a server render, since a layout that
 * starts narrow and widens is far less jarring than one that starts wide and
 * collapses.
 */
export function windowSizeNow(): WindowSize {
  if (!canMeasure()) return { width: 0, height: 0 };
  return { width: window.innerWidth, height: window.innerHeight };
}

/**
 * The screen size right now, measured on the spot.
 *
 * For imperative callers outside React. Named so the live read is obvious;
 * inside a component use {@link useScreenSize}, which does not measure per
 * render.
 */
export function screenSizeNow(): ScreenSize {
  return screenSizeFor(windowSizeNow().width);
}

// --- the cached store ------------------------------------------------------

let cachedSize: ScreenSize | null = null;
const listeners = new Set<() => void>();
let listening = false;

function refresh(): boolean {
  const next = screenSizeNow();
  if (next === cachedSize) return false;
  cachedSize = next;
  return true;
}

function handleResize(): void {
  // Only a resize that crosses a boundary changes anybody's answer.
  if (refresh()) {
    for (const listener of listeners) listener();
  }
}

function subscribe(onChange: () => void): () => void {
  if (!canMeasure()) return () => undefined;
  // Re-measure as the first subscriber arrives: the window may have changed
  // while nothing was listening, and a stale cache would be handed out as if
  // it were current.
  if (listeners.size === 0) refresh();
  listeners.add(onChange);
  if (!listening) {
    window.addEventListener('resize', handleResize);
    listening = true;
  }
  return () => {
    listeners.delete(onChange);
    if (listeners.size === 0 && listening) {
      window.removeEventListener('resize', handleResize);
      listening = false;
      // Dropped rather than kept: nothing is watching, so the next subscriber
      // must measure rather than trust an answer that has had no chance to be
      // corrected.
      cachedSize = null;
    }
  };
}

function getSnapshot(): ScreenSize {
  if (cachedSize === null) cachedSize = screenSizeNow();
  return cachedSize;
}

/** Server renders get the narrowest size, matching `windowSizeNow`'s reasoning. */
function getServerSnapshot(): ScreenSize {
  return screenSizeFor(0);
}

/**
 * The active screen size, updated when a resize crosses a breakpoint.
 *
 * Measured once per boundary crossing, not once per render.
 */
export function useScreenSize(): ScreenSize {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/**
 * The window's dimensions in pixels, updated on every resize.
 *
 * Separate from {@link useScreenSize} because it genuinely does change on every
 * resize event, so a component using it re-renders far more. Most callers want
 * the size, not the pixels.
 */
export function useWindowSize(): WindowSize {
  const [size, setSize] = useState<WindowSize>(windowSizeNow);

  useEffect(() => {
    // Measured once on mount as well: the window may have changed between the
    // initial state being computed and the listener attaching.
    const update = () => setSize(windowSizeNow());
    update();
    if (!canMeasure()) return;
    window.addEventListener('resize', update);
    return () => window.removeEventListener('resize', update);
  }, []);

  return size;
}

/** Resolve a per-size config against the active size. */
export function useFlexValue<T>(config: FlexConfig<T> | undefined, fallback?: T): T | undefined {
  const size = useScreenSize();
  const resolve = useCallback(
    () => resolveFlexConfig(config, size, fallback),
    [config, size, fallback],
  );
  return useMemo(resolve, [resolve]);
}

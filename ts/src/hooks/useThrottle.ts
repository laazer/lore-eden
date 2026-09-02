/**
 * A throttled callback, stable across renders.
 *
 * Leading edge: the first call in a window runs immediately and the rest are
 * dropped. That is the right default for what this is used for — a scroll or
 * pointer handler wants to react *now* and then stop, not react once the burst
 * is over.
 *
 * Implemented rather than taken from lodash, which the source used. lodash's
 * `throttle` is fine; a whole utility library is a lot to charge every consumer
 * of a UI kit for fifteen lines, and unlike a colour library there is no
 * arithmetic here to get subtly wrong.
 *
 * The source also let its throttled function outlive the component: `useCallback`
 * memoized a lodash throttle that was never cancelled, so a trailing invocation
 * could fire into an unmounted tree. This cancels on unmount and whenever the
 * delay changes.
 */

import { useCallback, useEffect, useMemo, useRef } from 'react';

export interface Throttled<A extends unknown[]> {
  (...args: A): void;
  /** Drop any pending window, so the next call runs immediately. */
  cancel(): void;
}

export function useThrottle<A extends unknown[]>(
  callback: (...args: A) => void,
  delay: number,
): Throttled<A> {
  // The callback lives in a ref so the throttled wrapper never has to be
  // rebuilt — rebuilding it would reset the window, which is how a "throttled"
  // handler ends up firing on every render.
  const saved = useRef(callback);
  useEffect(() => {
    saved.current = callback;
  }, [callback]);

  const lastRun = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const cancel = useCallback(() => {
    if (timer.current !== undefined) {
      clearTimeout(timer.current);
      timer.current = undefined;
    }
    lastRun.current = 0;
  }, []);

  const throttled = useMemo(() => {
    const run = (...args: A): void => {
      const now = Date.now();
      if (now - lastRun.current >= delay) {
        lastRun.current = now;
        saved.current(...args);
        return;
      }
      // Inside the window: dropped, but a timer is armed to reopen it so a
      // caller that stops mid-window is not left waiting on a call that never
      // comes to reset the clock.
      if (timer.current === undefined) {
        timer.current = setTimeout(() => {
          timer.current = undefined;
        }, delay - (now - lastRun.current));
      }
    };
    return Object.assign(run, { cancel }) as Throttled<A>;
  }, [delay, cancel]);

  useEffect(() => cancel, [cancel, delay]);

  return throttled;
}

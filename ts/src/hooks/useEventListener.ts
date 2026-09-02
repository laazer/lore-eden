/**
 * Attach a DOM listener without re-attaching it every render.
 *
 * The handler lives in a ref that is kept current, so the effect that attaches
 * does not depend on it. Passing an inline arrow — which is what callers
 * actually do — would otherwise detach and reattach on every render, and a
 * listener that is absent for an instant each frame drops events.
 *
 * The source took an extra `otherRefs` array and spread it into *both* effects'
 * dependencies. In the attach effect that defeated the whole point, reattaching
 * whenever any of them changed; and a spread of a variable-length array is a
 * dependency list whose size can change between renders, which React does not
 * support. The saved-handler ref makes the parameter unnecessary: the handler
 * always sees current values because it is re-read, not re-bound.
 */

import { useEffect, useRef } from 'react';

export function useEventListener<E extends Event = Event>(
  eventName: string,
  handler: (event: E) => void,
  /** Defaults to `window`. `null` is accepted so a ref can be passed before it fills. */
  target?: EventTarget | null,
  options?: AddEventListenerOptions,
): void {
  const saved = useRef(handler);

  useEffect(() => {
    saved.current = handler;
  }, [handler]);

  // Serialized rather than passed by identity: an options object written inline
  // is a new object every render, and depending on it would reattach every time.
  const optionsKey = options === undefined ? '' : JSON.stringify(options);

  useEffect(() => {
    const element = target ?? (typeof window === 'undefined' ? undefined : window);
    if (element?.addEventListener === undefined) return;
    const listener = (event: Event): void => saved.current(event as E);
    element.addEventListener(eventName, listener, options);
    return () => element.removeEventListener(eventName, listener, options);
    // eslint-disable-next-line react-hooks/exhaustive-deps -- options enters via optionsKey
  }, [eventName, target, optionsKey]);
}

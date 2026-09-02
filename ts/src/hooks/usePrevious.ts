import { useEffect, useRef } from 'react';

/**
 * What this value was on the last render.
 *
 * `undefined` on the first render, which is how a caller tells "no previous
 * value" from "the previous value was falsy". A caller comparing lists to work
 * out what changed needs that distinction — see `TabView`, which must not treat
 * its first render as a list that changed.
 */
export function usePrevious<T>(value: T): T | undefined {
  const ref = useRef<T | undefined>(undefined);
  useEffect(() => {
    ref.current = value;
  });
  return ref.current;
}

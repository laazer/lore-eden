/**
 * Where the pointer is.
 *
 * Coalesced to one update per animation frame. The source set state on every
 * `mousemove`, and a mouse fires those faster than React can render — so a
 * component using it re-rendered dozens of times per frame to display positions
 * that were never painted. A frame is the finest granularity anything visual
 * can actually use.
 *
 * `x`/`y` are `undefined` until the pointer first moves, which is how a caller
 * distinguishes "not known yet" from "at the origin".
 */

import { useEffect, useRef, useState } from 'react';

export interface MousePosition {
  x: number | undefined;
  y: number | undefined;
}

const UNKNOWN: MousePosition = { x: undefined, y: undefined };

export function useMousePosition(): MousePosition {
  const [position, setPosition] = useState<MousePosition>(UNKNOWN);
  const frame = useRef<number | undefined>(undefined);
  const pending = useRef<MousePosition>(UNKNOWN);

  useEffect(() => {
    if (typeof window === 'undefined') return;

    const flush = (): void => {
      frame.current = undefined;
      setPosition(pending.current);
    };

    const onMove = (event: MouseEvent): void => {
      pending.current = { x: event.clientX, y: event.clientY };
      // Already scheduled: the newer position simply overwrites the pending
      // one, so a burst of events costs one render rather than one each.
      if (frame.current === undefined) frame.current = requestAnimationFrame(flush);
    };

    window.addEventListener('mousemove', onMove);
    return () => {
      window.removeEventListener('mousemove', onMove);
      if (frame.current !== undefined) cancelAnimationFrame(frame.current);
    };
  }, []);

  return position;
}

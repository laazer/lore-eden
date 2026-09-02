import { useEffect, useRef } from 'react';

import { useCheckpoints } from './CheckpointProvider';

/**
 * Record a checkpoint when a screen mounts, or when its path changes.
 *
 * For the common case: a screen that says "this is a place worth coming back
 * to" without threading the push through its own render logic.
 *
 * `push` is read through a ref so the effect depends on the path alone. Taking
 * it as a dependency would re-push whenever the provider re-rendered, which is
 * every navigation — and a stack that gains an entry per navigation is browser
 * history again, which is the thing this exists not to be.
 */
export function useMarkCheckpoint(path?: string): void {
  const { push } = useCheckpoints();
  const pushRef = useRef(push);
  pushRef.current = push;

  useEffect(() => {
    pushRef.current(path);
  }, [path]);
}

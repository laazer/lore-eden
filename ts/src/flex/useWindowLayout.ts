/**
 * The layout, resolved for whatever size the window currently is.
 */

import { useMemo } from 'react';

import { useScreenSize } from './useScreenSize';
import {
  DEFAULT_LAYOUT,
  resolveLayout,
  resolveRegion,
  type LayoutSpec,
  type ResolvedLayout,
  type ResolvedRegion,
} from './windowLayout';

/** Every region, resolved for the active screen size. */
export function useWindowLayout(layout: LayoutSpec = DEFAULT_LAYOUT): ResolvedLayout {
  const size = useScreenSize();
  return useMemo(() => resolveLayout(size, layout), [size, layout]);
}

/**
 * One named region.
 *
 * Throws for a name the layout does not define. A component asking for a region
 * that does not exist has a bug in it, and returning an empty box would hide
 * that until someone noticed the chrome was missing.
 */
export function useRegion(name: string, layout: LayoutSpec = DEFAULT_LAYOUT): ResolvedRegion {
  const size = useScreenSize();
  return useMemo(() => {
    const spec = layout[name];
    if (spec === undefined) {
      throw new Error(
        `No region named ${name} in this layout. Defined: ${Object.keys(layout).join(', ')}`,
      );
    }
    return resolveRegion(spec, size);
  }, [name, layout, size]);
}

/**
 * Where a box sits inside its parent, expressed as auto margins.
 *
 * Nine positions from two axes. The trick is that `margin: auto` on a side
 * *pushes away* from it, so anchoring left means an auto margin on the right —
 * which is the one thing about this technique that nobody remembers, and the
 * reason it is worth a named function rather than three lines at each use.
 *
 * An axis left unspecified centres on that axis, so `{ xAnchor: 'left' }` is
 * left and vertically centred.
 */

import type { CSSProperties } from 'react';

export type XAnchor = 'left' | 'center' | 'right';
export type YAnchor = 'top' | 'center' | 'bottom';

export interface Anchored {
  xAnchor?: XAnchor;
  yAnchor?: YAnchor;
}

export function marginFromAnchor(xAnchor?: XAnchor, yAnchor?: YAnchor): CSSProperties {
  const style: CSSProperties = {};

  // Push right to sit left; push left to sit right; both to centre.
  if (xAnchor === undefined || xAnchor === 'center' || xAnchor === 'left') style.marginRight = 'auto';
  if (xAnchor === undefined || xAnchor === 'center' || xAnchor === 'right') style.marginLeft = 'auto';
  if (yAnchor === undefined || yAnchor === 'center' || yAnchor === 'top') style.marginBottom = 'auto';
  if (yAnchor === undefined || yAnchor === 'center' || yAnchor === 'bottom') style.marginTop = 'auto';

  return style;
}

export function marginFromAnchored(anchored: Anchored): CSSProperties {
  return marginFromAnchor(anchored.xAnchor, anchored.yAnchor);
}

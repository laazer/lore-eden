/**
 * Points and rectangles, with no opinion about what is drawing them.
 *
 * A rectangle here is its four *edges* ({@link RectSides}), not an origin and a
 * size. That is the form the containment and overlap tests want, and converting
 * once at the boundary beats converting inside every predicate.
 */

export interface XYCoord {
  x: number;
  y: number;
}

export type XYCardinalDirection = 'top' | 'bottom' | 'left' | 'right';

export const XY_CARDINAL_DIRECTIONS: readonly XYCardinalDirection[] = [
  'top',
  'bottom',
  'left',
  'right',
];

export type RectCorner = 'topLeft' | 'topRight' | 'bottomLeft' | 'bottomRight';

export const RECT_CORNERS: readonly RectCorner[] = [
  'topLeft',
  'topRight',
  'bottomLeft',
  'bottomRight',
];

export type RectCorners = Record<RectCorner, XYCoord>;

/** A rectangle as its four edges. */
export interface RectSides {
  top: number;
  left: number;
  bottom: number;
  right: number;
}

export interface RectSize {
  height: number;
  width: number;
}

export function xyCoord(position: [number, number]): XYCoord {
  return { x: position[0], y: position[1] };
}

/** A rectangle from an origin and a size, optionally centred on the origin. */
export function rectFrom(origin: XYCoord, size: RectSize, fromCenter = false): RectSides {
  const halfWidth = fromCenter ? size.width / 2 : 0;
  const halfHeight = fromCenter ? size.height / 2 : 0;
  return {
    top: origin.y - halfHeight,
    bottom: origin.y - halfHeight + size.height,
    left: origin.x - halfWidth,
    right: origin.x - halfWidth + size.width,
  };
}

export function snapToGrid(x: number, y: number, gridSize: number): [number, number] {
  return [Math.round(x / gridSize) * gridSize, Math.round(y / gridSize) * gridSize];
}

export function coordDelta(a: XYCoord, b: XYCoord): XYCoord {
  return { x: a.x - b.x, y: a.y - b.y };
}

export function coordAbsDelta(a: XYCoord, b: XYCoord): XYCoord {
  return { x: Math.abs(a.x - b.x), y: Math.abs(a.y - b.y) };
}

/** Keep a point inside `[0, max]` on both axes. */
export function clampCoord(coord: XYCoord, max: RectSize): XYCoord {
  return {
    x: Math.max(0, Math.min(max.width, coord.x)),
    y: Math.max(0, Math.min(max.height, coord.y)),
  };
}

/**
 * Whether two rectangles share any area.
 *
 * Touching edges do not count: rectangles ending and starting at the same
 * coordinate are adjacent, not overlapping.
 */
export function rectOverlap(a: RectSides, b: RectSides): boolean {
  return !(a.left >= b.right || a.top >= b.bottom || a.right <= b.left || a.bottom <= b.top);
}

export function expandRect(rect: RectSides, amount: XYCoord): RectSides {
  return {
    top: rect.top - amount.y,
    bottom: rect.bottom + amount.y,
    left: rect.left - amount.x,
    right: rect.right + amount.x,
  };
}

export function shrinkRect(rect: RectSides, amount: XYCoord): RectSides {
  return expandRect(rect, { x: -amount.x, y: -amount.y });
}

/** Whether a point lies within a rectangle, edges included. */
export function rectContainsPosition(rect: RectSides, pos: XYCoord): boolean {
  return rect.top <= pos.y && rect.bottom >= pos.y && rect.left <= pos.x && rect.right >= pos.x;
}

/**
 * Whether `a` fully encloses `b`.
 *
 * A `buffer` loosens the test by that much on each axis — useful when a few
 * pixels of overhang should still read as "inside".
 */
export function rectContains(a: RectSides, b: RectSides, buffer?: XYCoord): boolean {
  if (buffer !== undefined) return rectContains(expandRect(a, buffer), shrinkRect(b, buffer));
  return a.top <= b.top && a.bottom >= b.bottom && a.left <= b.left && a.right >= b.right;
}

/** Whether a point lies inside the rectangle described by an origin and a size. */
export function rectContainsCoord(
  coord: XYCoord,
  origin: XYCoord,
  size: RectSize,
  fromCenter = false,
): boolean {
  return rectContainsPosition(rectFrom(origin, size, fromCenter), coord);
}

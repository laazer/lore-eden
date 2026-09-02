/**
 * Screen sizes, defined once.
 *
 * The source spelled this vocabulary twice — an if-chain of width thresholds in
 * `flexSize()`, and a separate ordered `SCREEN_SIZES` array — so the names and
 * the numbers could drift apart with nothing to catch it. One table here, and
 * both views are derived.
 *
 * Six stops rather than the container tiers' three, and that is not an
 * inconsistency: these answer different questions. A breakpoint says how much
 * room the *page* has, which is what page-level layout responds to. A container
 * tier says how much room *one pane* has — and a pane 300px wide in a 4K window
 * is cramped no matter what the window thinks. Both ship; see `panes/paneSize`.
 */

/** Ordered narrowest to widest. */
export const SCREEN_SIZES = ['xs', 'sm', 'md', 'lg', 'xl', 'x2'] as const;

export type ScreenSize = (typeof SCREEN_SIZES)[number];

/**
 * The widest viewport each size covers. The last is unbounded.
 *
 * Upper bounds rather than the more common min-widths, because that is how the
 * source expressed them and flipping the convention silently would move every
 * boundary by a pixel.
 */
export const SCREEN_SIZE_MAX_WIDTH: Readonly<Record<ScreenSize, number>> = {
  xs: 576,
  sm: 768,
  md: 992,
  lg: 1200,
  xl: 1640,
  x2: Number.POSITIVE_INFINITY,
};

/** The size a viewport of this width falls in. */
export function screenSizeFor(width: number): ScreenSize {
  for (const size of SCREEN_SIZES) {
    if (width <= SCREEN_SIZE_MAX_WIDTH[size]) return size;
  }
  // Unreachable while the last bound is Infinity, and the widest size is the
  // only honest answer if that ever changes.
  return SCREEN_SIZES[SCREEN_SIZES.length - 1];
}

/** How far apart two sizes are, for resolving a config outward from one. */
export function screenSizeDistance(from: ScreenSize, to: ScreenSize): number {
  return Math.abs(SCREEN_SIZES.indexOf(from) - SCREEN_SIZES.indexOf(to));
}

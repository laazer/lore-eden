/**
 * The regions an application reserves, and how big each is at this screen size.
 *
 * An app bar, a toolbar down one side, a body in the middle. Each is *named*, so
 * a component asks where it belongs rather than deriving a position — and each
 * carries a size that can differ per screen size, so the right-hand toolbar can
 * be a third of a narrow window and a little more of a wide one without anyone
 * writing a media query.
 *
 * Named regions with a fixed ladder, which is the opposite of the canvas: there,
 * a user drags items anywhere and decides the stacking themselves. Here the
 * layout is the application's own chrome and nothing at runtime reorders it.
 *
 * ## Sizes are fractions unless they are strings
 *
 * A bare number is a percentage of the containing axis — `height: 12` is 12% of
 * the viewport height. A string is used verbatim, so `"320px"` and `"100%"` both
 * work. That is the source's convention, carried over because the default layout
 * is written in it and reinterpreting the numbers would silently resize
 * everything.
 */

import { resolveFlexConfig, type Flexible, type FlexConfig } from './flexConfig';
import { LAYERS, type LayerName } from './layers';
import type { ScreenSize } from './breakpoints';

/** A dimension: a percentage as a number, or any CSS length as a string. */
export type Extent = number | string;

export interface RegionSpec {
  /** Height, per screen size or flat. */
  height?: Flexible<Extent>;
  /** Width, per screen size or flat. */
  width?: Flexible<Extent>;
  /** Which rung of the ladder this region sits on. */
  layer?: LayerName;
  /** Not rendered at all. Per screen size, so a region can drop on narrow ones. */
  hidden?: Flexible<boolean>;
  /** Anything else the host wants to carry through, e.g. `display: 'grid'`. */
  css?: Record<string, string | number>;
}

/** The regions a layout defines. Names are the host's; these are the defaults'. */
export type LayoutSpec = Record<string, RegionSpec>;

/** One region, resolved for a screen size. */
export interface ResolvedRegion {
  height?: string;
  width?: string;
  zIndex?: number;
  /**
   * True when the region is not rendered at this size.
   *
   * Distinct from a zero height: a hidden region is *absent*, and a caller that
   * treated the two the same would leave an empty box holding space open.
   */
  hidden: boolean;
  css: Record<string, string | number>;
}

export type ResolvedLayout = Record<string, ResolvedRegion>;

/**
 * The default chrome: an app bar, three toolbars, a body, and the overlay
 * regions above them. A host replaces or extends it; the shape is the point,
 * not these particular numbers.
 */
export const DEFAULT_LAYOUT: LayoutSpec = {
  base: { height: '100vh', width: '100%' },
  appBar: { height: 12, layer: 'appBar', hidden: false },
  appBarMenu: { layer: 'appBarMenu' },
  toolbarTop: { height: 5, layer: 'toolbar' },
  toolbarLeft: { layer: 'toolbar' },
  // Narrower windows give the side toolbar proportionally more room, because a
  // third of a small screen is still usable where a fixed width would not be.
  toolbarRight: { width: { xs: 33, x2: 35 }, layer: 'toolbar' },
  body: { layer: 'body' },
  container: { height: '100%', width: '100%', layer: 'container', css: { display: 'grid' } },
  tool: { layer: 'tool' },
  toolOverlay: { layer: 'toolOverlay' },
  toolPopup: { layer: 'toolPopup' },
  toolbarPopup: { layer: 'toolbarPopup' },
  drawer: { layer: 'drawer' },
  appPopup: { layer: 'appPopup' },
  notification: { layer: 'notification' },
};

function asFlexConfig<T>(value: Flexible<T> | undefined): FlexConfig<T> | undefined {
  if (value === undefined) return undefined;
  if (typeof value === 'object' && value !== null) return value as FlexConfig<T>;
  return { base: value };
}

function extentToCss(value: Extent | undefined, axis: 'height' | 'width'): string | undefined {
  if (value === undefined) return undefined;
  if (typeof value === 'string') return value;
  // A bare number is a percentage of the viewport on that axis. `vh`/`vw`
  // rather than `%` because a region's parent may not fill the viewport, and
  // the numbers in the default layout were written against the viewport.
  return `${value}${axis === 'height' ? 'vh' : 'vw'}`;
}

/** Resolve one region for a screen size. */
export function resolveRegion(spec: RegionSpec, size: ScreenSize): ResolvedRegion {
  return {
    height: extentToCss(resolveFlexConfig(asFlexConfig(spec.height), size), 'height'),
    width: extentToCss(resolveFlexConfig(asFlexConfig(spec.width), size), 'width'),
    zIndex: spec.layer === undefined ? undefined : LAYERS[spec.layer],
    hidden: resolveFlexConfig(asFlexConfig(spec.hidden), size, false) ?? false,
    css: { ...(spec.css ?? {}) },
  };
}

/** Resolve a whole layout for a screen size. */
export function resolveLayout(
  size: ScreenSize,
  layout: LayoutSpec = DEFAULT_LAYOUT,
): ResolvedLayout {
  return Object.fromEntries(
    Object.entries(layout).map(([name, spec]) => [name, resolveRegion(spec, size)]),
  );
}

/**
 * A region as style properties, ready to spread onto an element.
 *
 * A hidden region becomes `display: none` rather than being omitted — the
 * caller still renders the element, and something that reserves no space is
 * easier to reason about than a branch at every use site.
 */
export function regionStyle(region: ResolvedRegion): Record<string, string | number> {
  if (region.hidden) return { display: 'none' };
  const style: Record<string, string | number> = { ...region.css };
  if (region.height !== undefined) style.height = region.height;
  if (region.width !== undefined) style.width = region.width;
  if (region.zIndex !== undefined) style.zIndex = region.zIndex;
  return style;
}

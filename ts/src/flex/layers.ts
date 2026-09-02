/**
 * The stacking ladder: what sits above what, decided once.
 *
 * A named rung rather than a number at the call site. The alternative is what
 * every codebase without one ends up with — `z-index: 9999` next to
 * `z-index: 10000` next to `z-index: 999999`, each written by someone who could
 * not see the others and just needed to win. Asking for `LAYERS.toolbar` makes
 * the question "where does this belong?" instead of "what number beats the last
 * one I saw?".
 *
 * The gaps are deliberate and worth keeping: a thousand between rungs leaves
 * room to slot something in without renumbering, and renumbering is exactly the
 * change that quietly reorders things nobody was looking at.
 *
 * This is not the canvas's z-order. That one is *free*: a user drags items and
 * decides what is on top, and the numbers are dense positions in a stack (see
 * `layout/canvas`). This ladder is *fixed*: it describes the application's own
 * chrome, and nothing at runtime reorders it.
 */

/** Rungs, from the back of the page to the front. */
export const LAYER_NAMES = [
  'container',
  'body',
  'tool',
  'toolbar',
  'toolOverlay',
  'toolPopup',
  'toolbarPopup',
  'appBar',
  'appBarMenu',
  'drawer',
  'appPopup',
  'notification',
] as const;

export type LayerName = (typeof LAYER_NAMES)[number];

/**
 * The z-index each rung sits at.
 *
 * Ordering, not magnitude, is what matters — the absolute numbers only need to
 * clear whatever a host's other stylesheets use.
 */
export const LAYERS: Readonly<Record<LayerName, number>> = {
  container: 0,
  body: 1,
  tool: 13_000,
  toolbar: 14_000,
  toolOverlay: 15_000,
  toolPopup: 16_000,
  toolbarPopup: 17_000,
  appBar: 18_000,
  appBarMenu: 18_100,
  drawer: 18_500,
  appPopup: 19_000,
  notification: 20_000,
};

/** Whether `above` really does stack over `below`. */
export function stacksAbove(above: LayerName, below: LayerName): boolean {
  return LAYERS[above] > LAYERS[below];
}

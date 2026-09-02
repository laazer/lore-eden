/**
 * The stored shape of an arrangement.
 *
 * A layout is JSON, deliberately: it is written by whatever backend the host
 * has, and this package neither defines that endpoint nor knows its schema. The
 * functions in `grid` and `canvas` read it into a typed model before editing and
 * write it back, so the narrowing happens once rather than at every call site.
 *
 * These were part of an API client in the codebase this came from. They are not
 * an API concern — they describe the arrangement itself — so they live with the
 * layout code and the host's transport stays its own business.
 */

/** Which arrangement a layout describes. */
export type ViewKind = 'flex_grid' | 'canvas';

/** A stored layout, as JSON. */
export type ViewLayout = Record<string, unknown>;

/** Stored pan/zoom for a canvas. */
export type ViewViewport = Record<string, unknown>;

/** A partial viewport update. */
export interface ViewViewportPatch {
  pan_x?: number;
  pan_y?: number;
  zoom?: number;
}

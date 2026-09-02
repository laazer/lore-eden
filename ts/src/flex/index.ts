/**
 * Responding to the size of the window: breakpoints, per-size values, and the
 * regions an application reserves.
 *
 * Not to be confused with two neighbours that answer different questions:
 *
 * - `panes/paneSize` measures a *container*, not the window. A pane 300px wide
 *   in a 4K window is cramped whatever the breakpoint says.
 * - `layout/canvas` has a *free* z-order a user drags. The ladder here is fixed
 *   application chrome.
 * - `surfaces/FlexGridSurface` is a split grid; its "flex" is the CSS grow
 *   factor, unrelated to the breakpoints here.
 */

export * from './breakpoints';
export * from './flexConfig';
export * from './layers';
export * from './useScreenSize';
export * from './windowLayout';
export * from './useWindowLayout';

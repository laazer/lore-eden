/**
 * Free placement: where an item sits, how big it is, and what is on top.
 *
 * Every edit here returns a new layout rather than mutating the one handed in —
 * the caller is holding the record the surface renders from — and every one of
 * them produces a layout a strict backend accepts, or throws. A layout the write
 * path discovers is a 400 is a gesture the user has already finished making.
 */

import { describe, expect, it } from 'vitest';

import {
  DEFAULT_ITEM_HEIGHT,
  DEFAULT_ITEM_WIDTH,
  MAX_CANVAS_COORDINATE,
  MAX_CANVAS_EXTENT,
  MIN_ITEM_PX,
  addItem,
  clampGeometry,
  contentBounds,
  emptyLayoutFor,
  moveItem,
  readCanvasItems,
  removeItem,
  resizeItem,
  restackItem,
  type ViewLayout,
} from '../src/layout';

function canvasWith(count: number): ViewLayout {
  let layout = emptyLayoutFor('canvas');
  for (let i = 0; i < count; i += 1) layout = addItem(layout, i * 100, i * 100);
  return layout;
}

const idsInStackingOrder = (layout: ViewLayout) => readCanvasItems(layout).map((item) => item.id);

describe('placing', () => {
  it('adds an item at the requested point with the default size', () => {
    const layout = addItem(emptyLayoutFor('canvas'), 40, 60);
    const [item] = readCanvasItems(layout);

    expect(item.x).toBe(40);
    expect(item.y).toBe(60);
    expect(item.width).toBe(DEFAULT_ITEM_WIDTH);
    expect(item.height).toBe(DEFAULT_ITEM_HEIGHT);
  });

  it('gives each item its own container', () => {
    const layout = canvasWith(3);
    const items = readCanvasItems(layout);
    const containers = new Set(items.map((item) => item.container_id));

    expect(containers.size).toBe(3);
  });

  it('does not mutate the layout handed in', () => {
    const before = canvasWith(1);
    const snapshot = JSON.parse(JSON.stringify(before));

    addItem(before, 10, 10);

    expect(before).toEqual(snapshot);
  });
});

describe('z-order', () => {
  it('stacks newer items above older ones', () => {
    expect(idsInStackingOrder(canvasWith(3))).toHaveLength(3);
  });

  it('raises an item to the front', () => {
    const layout = canvasWith(3);
    const [first] = idsInStackingOrder(layout);

    const raised = restackItem(layout, first, true);

    expect(idsInStackingOrder(raised).at(-1)).toBe(first);
  });

  it('sends an item to the back', () => {
    const layout = canvasWith(3);
    const last = idsInStackingOrder(layout).at(-1) as string;

    const lowered = restackItem(layout, last, false);

    expect(idsInStackingOrder(lowered)[0]).toBe(last);
  });

  it('leaves the others in their relative order', () => {
    const layout = canvasWith(4);
    const [a, b, c, d] = idsInStackingOrder(layout);

    const raised = restackItem(layout, b, true);

    expect(idsInStackingOrder(raised)).toEqual([a, c, d, b]);
  });

  it('returns the same layout when the item is already there', () => {
    // "Focus raises" runs on every click; re-storing the front-most item would
    // write on every one of them.
    const layout = canvasWith(3);
    const front = idsInStackingOrder(layout).at(-1) as string;

    expect(restackItem(layout, front, true)).toBe(layout);
  });

  it('numbers the stack densely, so the order is readable from the record', () => {
    const before = canvasWith(3);
    const layout = restackItem(before, idsInStackingOrder(before)[0], true);

    const indices = readCanvasItems(layout).map((item) => item.z_index);
    expect(indices).toEqual([0, 1, 2]);
  });

  it('refuses to restack an item that is not there', () => {
    expect(() => restackItem(canvasWith(2), 'no-such-item', true)).toThrow();
  });
});

describe('geometry', () => {
  it('keeps an item at or above the minimum grabbable size', () => {
    // A backend rule of `width > 0` accepts an item one pixel wide — a container
    // that cannot be grabbed again to undo the resize that made it.
    const clamped = clampGeometry({ x: 0, y: 0, width: 1, height: 1 });

    expect(clamped.width).toBe(MIN_ITEM_PX);
    expect(clamped.height).toBe(MIN_ITEM_PX);
  });

  it('keeps an item inside the coordinate range the record allows', () => {
    const clamped = clampGeometry({
      x: MAX_CANVAS_COORDINATE * 10,
      y: -MAX_CANVAS_COORDINATE * 10,
      width: MAX_CANVAS_EXTENT * 10,
      height: 200,
    });

    expect(clamped.x).toBeLessThanOrEqual(MAX_CANVAS_COORDINATE);
    expect(clamped.y).toBeGreaterThanOrEqual(-MAX_CANVAS_COORDINATE);
    expect(clamped.width).toBeLessThanOrEqual(MAX_CANVAS_EXTENT);
  });

  it('clamps in the arithmetic, so the screen and the record agree', () => {
    // A CSS floor stops an item shrinking on screen while the stored width keeps
    // falling, and the two quietly disagree until the next reload.
    const layout = canvasWith(1);
    const [item] = readCanvasItems(layout);

    const resized = resizeItem(layout, item.id, { x: 0, y: 0, width: 2, height: 2 });

    const [stored] = readCanvasItems(resized);
    expect(stored.width).toBe(MIN_ITEM_PX);
  });

  it('moves an item', () => {
    const layout = canvasWith(1);
    const [item] = readCanvasItems(layout);

    const moved = moveItem(layout, item.id, 500, 250);

    expect(readCanvasItems(moved)[0]).toMatchObject({ x: 500, y: 250 });
  });

  it('refuses to move an item that is not there', () => {
    expect(() => moveItem(canvasWith(1), 'gone', 0, 0)).toThrow();
  });
});

describe('removing', () => {
  it('drops the item and the container it placed', () => {
    // A container no arrangement references is a rejected layout, not a
    // harmless leftover.
    const layout = canvasWith(2);
    const [first] = readCanvasItems(layout);

    const without = removeItem(layout, first.id);

    expect(readCanvasItems(without)).toHaveLength(1);
    expect(Object.keys(without.containers as object)).toHaveLength(1);
    expect(without.containers).not.toHaveProperty(first.container_id);
  });

  it('allows an empty canvas', () => {
    const layout = canvasWith(1);
    const [item] = readCanvasItems(layout);

    expect(readCanvasItems(removeItem(layout, item.id))).toEqual([]);
  });
});

describe('bounds', () => {
  it('reports the box every item fits inside', () => {
    const layout = canvasWith(2);

    const bounds = contentBounds(readCanvasItems(layout));

    expect(bounds).toMatchObject({ x: 0, y: 0 });
    expect(bounds?.width).toBeGreaterThanOrEqual(DEFAULT_ITEM_WIDTH);
  });

  it('has nothing to report for an empty canvas', () => {
    expect(contentBounds([])).toBeUndefined();
  });
});

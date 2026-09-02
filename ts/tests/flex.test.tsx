/**
 * Window breakpoints, per-size values, and the reserved-region layout.
 *
 * Three of these pin defects that were fixed rather than carried: one table for
 * the breakpoints, a base that is not a breakpoint, and a hook that does not
 * read layout on every call.
 */

import { act, render, renderHook, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  DEFAULT_LAYOUT,
  LAYERS,
  LAYER_NAMES,
  SCREEN_SIZES,
  SCREEN_SIZE_MAX_WIDTH,
  isFlexConfig,
  regionStyle,
  resolveFlexConfig,
  resolveFlexible,
  resolveLayout,
  resolveRegion,
  screenSizeFor,
  screenSizeNow,
  stacksAbove,
  useFlexValue,
  useScreenSize,
  useWindowLayout,
  useRegion,
  type ScreenSize,
} from '../src/flex';

const ORIGINAL_WIDTH = window.innerWidth;
const ORIGINAL_HEIGHT = window.innerHeight;

function resizeTo(width: number): void {
  act(() => {
    Object.defineProperty(window, 'innerWidth', { value: width, configurable: true });
    window.dispatchEvent(new Event('resize'));
  });
}

afterEach(() => {
  Object.defineProperty(window, 'innerWidth', { value: ORIGINAL_WIDTH, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: ORIGINAL_HEIGHT, configurable: true });
});

describe('the breakpoint table', () => {
  it('defines a max width for every size, and only those', () => {
    // The defect this replaces: the thresholds lived in an if-chain and the
    // ordered list lived in an array, so the names and the numbers could drift
    // apart with nothing to catch it. This test is what catches it.
    expect(Object.keys(SCREEN_SIZE_MAX_WIDTH).sort()).toEqual([...SCREEN_SIZES].sort());
  });

  it('orders the thresholds narrowest to widest', () => {
    const widths = SCREEN_SIZES.map((size) => SCREEN_SIZE_MAX_WIDTH[size]);

    expect(widths).toEqual([...widths].sort((a, b) => a - b));
  });

  it('leaves the widest size unbounded, so every width lands somewhere', () => {
    expect(SCREEN_SIZE_MAX_WIDTH.x2).toBe(Number.POSITIVE_INFINITY);
  });

  it.each([
    [320, 'xs'],
    [576, 'xs'],
    [577, 'sm'],
    [768, 'sm'],
    [900, 'md'],
    [1100, 'lg'],
    [1400, 'xl'],
    [1920, 'x2'],
  ])('puts %ipx in %s', (width, expected) => {
    expect(screenSizeFor(width)).toBe(expected);
  });

  it('treats each threshold as inclusive', () => {
    for (const size of SCREEN_SIZES) {
      const max = SCREEN_SIZE_MAX_WIDTH[size];
      if (max === Number.POSITIVE_INFINITY) continue;
      expect(screenSizeFor(max)).toBe(size);
    }
  });
});

describe('resolving a per-size value', () => {
  it('takes the exact size when it is defined', () => {
    expect(resolveFlexConfig({ xs: 1, md: 2, xl: 3 }, 'md')).toBe(2);
  });

  it('searches outward, so a config naming one distant size still resolves', () => {
    // Preserved from the source deliberately. Most systems only cascade down,
    // which leaves a value set for `xl` simply absent at `xs`.
    expect(resolveFlexConfig({ xl: 'wide' }, 'xs')).toBe('wide');
    expect(resolveFlexConfig({ xs: 'narrow' }, 'x2')).toBe('narrow');
  });

  it('prefers the nearer size when both directions have one', () => {
    expect(resolveFlexConfig({ sm: 'near', x2: 'far' }, 'md')).toBe('near');
  });

  it('prefers the narrower size at equal distance', () => {
    // A value authored for a smaller screen survives being shown larger more
    // gracefully than the reverse.
    expect(resolveFlexConfig({ sm: 'below', lg: 'above' }, 'md')).toBe('below');
  });

  it('falls back to the base when no size matches', () => {
    expect(resolveFlexConfig({ base: 'always' }, 'md')).toBe('always');
  });

  it('prefers a matching size over the base', () => {
    expect(resolveFlexConfig({ base: 'always', md: 'here' }, 'md')).toBe('here');
  });

  it('falls back to the caller’s default last of all', () => {
    expect(resolveFlexConfig({}, 'md', 'default')).toBe('default');
    expect(resolveFlexConfig(undefined, 'md', 'default')).toBe('default');
  });

  it('pins the whole fallback order', () => {
    const config = { base: 'base', sm: 'sm', xl: 'xl' };

    expect(resolveFlexConfig(config, 'sm', 'default')).toBe('sm'); // exact
    expect(resolveFlexConfig(config, 'md', 'default')).toBe('sm'); // nearest
    expect(resolveFlexConfig({ base: 'base' }, 'md', 'default')).toBe('base'); // base
    expect(resolveFlexConfig({}, 'md', 'default')).toBe('default'); // default
  });

  it('treats a defined falsy value as defined', () => {
    // `0` and `false` are answers, and skipping them would silently fall
    // through to a neighbour that says something different.
    expect(resolveFlexConfig({ md: 0 }, 'md', 99)).toBe(0);
    expect(resolveFlexConfig({ md: false }, 'md', true)).toBe(false);
  });
});

describe('the base is not a breakpoint', () => {
  it('sits outside the size map', () => {
    // The source modelled it as a seventh member of the size union, which is
    // why its resolver needed two special cases for it — one of them dead code
    // that fired only when the *active* size was the base, which never happens.
    expect(SCREEN_SIZES).not.toContain('base');
    expect(SCREEN_SIZES).not.toContain('sx');
  });

  it('is not reachable by the outward search', () => {
    // Only as the final fallback, never as a neighbour.
    expect(resolveFlexConfig({ base: 'base', xs: 'xs' }, 'sm')).toBe('xs');
  });
});

describe('telling a config from a plain value', () => {
  it('recognises a config', () => {
    expect(isFlexConfig({ xs: 1 })).toBe(true);
    expect(isFlexConfig({ base: 1 })).toBe(true);
  });

  it('does not mistake a plain value for one', () => {
    expect(isFlexConfig(320)).toBe(false);
    expect(isFlexConfig('100%')).toBe(false);
    expect(isFlexConfig({})).toBe(false);
    expect(isFlexConfig({ notASize: 1 } as never)).toBe(false);
  });

  it('resolves either form', () => {
    expect(resolveFlexible(320, 'md')).toBe(320);
    expect(resolveFlexible({ md: 640 }, 'md')).toBe(640);
    expect(resolveFlexible(undefined, 'md', 1)).toBe(1);
  });
});

describe('the stacking ladder', () => {
  it('gives every rung a distinct height', () => {
    const values = LAYER_NAMES.map((name) => LAYERS[name]);

    expect(new Set(values).size).toBe(values.length);
  });

  it('orders the rungs as listed', () => {
    const values = LAYER_NAMES.map((name) => LAYERS[name]);

    expect(values).toEqual([...values].sort((a, b) => a - b));
  });

  it('stacks chrome the way anyone would expect', () => {
    // The whole point of naming the rungs: this ordering is a decision written
    // down once, rather than an accident of who last picked a bigger number.
    expect(stacksAbove('notification', 'appPopup')).toBe(true);
    expect(stacksAbove('appPopup', 'drawer')).toBe(true);
    expect(stacksAbove('drawer', 'appBar')).toBe(true);
    expect(stacksAbove('appBar', 'toolbar')).toBe(true);
    expect(stacksAbove('toolbar', 'body')).toBe(true);
    expect(stacksAbove('body', 'container')).toBe(true);
  });

  it('leaves room between rungs to slot something in', () => {
    // Renumbering is exactly the change that quietly reorders things nobody
    // was looking at.
    expect(LAYERS.toolbar - LAYERS.tool).toBeGreaterThanOrEqual(100);
  });
});

describe('the window layout', () => {
  it('resolves every named region', () => {
    const layout = resolveLayout('md');

    expect(Object.keys(layout).sort()).toEqual(Object.keys(DEFAULT_LAYOUT).sort());
  });

  it('gives a region the z-index of its rung', () => {
    expect(resolveLayout('md').appBar.zIndex).toBe(LAYERS.appBar);
  });

  it('resolves the same region differently at two sizes', () => {
    // The responsive half: the right-hand toolbar takes proportionally more of
    // a narrow window than a wide one.
    const narrow = resolveRegion(DEFAULT_LAYOUT.toolbarRight, 'xs');
    const wide = resolveRegion(DEFAULT_LAYOUT.toolbarRight, 'x2');

    expect(narrow.width).not.toBe(wide.width);
    expect(narrow.width).toBe('33vw');
    expect(wide.width).toBe('35vw');
  });

  it('reads a bare number as a fraction of the viewport', () => {
    expect(resolveRegion({ height: 12 }, 'md').height).toBe('12vh');
    expect(resolveRegion({ width: 50 }, 'md').width).toBe('50vw');
  });

  it('passes a string through verbatim', () => {
    expect(resolveRegion({ height: '320px' }, 'md').height).toBe('320px');
    expect(resolveRegion({ width: '100%' }, 'md').width).toBe('100%');
  });

  it('separates a hidden region from a zero-sized one', () => {
    // A hidden region is *absent*; a zero-height one still holds an empty box
    // open. Collapsing them leaves a gap nobody can see the cause of.
    const hidden = resolveRegion({ hidden: true, height: 10 }, 'md');
    const flat = resolveRegion({ height: 0 }, 'md');

    expect(hidden.hidden).toBe(true);
    expect(flat.hidden).toBe(false);
    expect(regionStyle(hidden)).toEqual({ display: 'none' });
    expect(regionStyle(flat)).toMatchObject({ height: '0vh' });
  });

  it('can hide a region at one size and not another', () => {
    const spec = { hidden: { xs: true, lg: false } };

    expect(resolveRegion(spec, 'xs').hidden).toBe(true);
    expect(resolveRegion(spec, 'lg').hidden).toBe(false);
  });

  it('carries a region’s own css through', () => {
    expect(regionStyle(resolveRegion(DEFAULT_LAYOUT.container, 'md'))).toMatchObject({
      display: 'grid',
    });
  });
});

describe('the hooks', () => {
  it('reports the active size', () => {
    resizeTo(1400);
    const { result } = renderHook(() => useScreenSize());

    expect(result.current).toBe('xl');
  });

  it('follows a resize across a boundary', () => {
    resizeTo(1400);
    const { result } = renderHook(() => useScreenSize());
    expect(result.current).toBe('xl');

    resizeTo(400);

    expect(result.current).toBe('xs');
  });

  it('does not read layout on every render', () => {
    // The defect this replaces: the source's `flexSize()` read
    // `window.innerWidth` every time it was called, and components called it
    // during render — a forced style flush per component per render.
    resizeTo(1000);
    const reads = vi.fn(() => 1000);
    Object.defineProperty(window, 'innerWidth', { get: reads, configurable: true });

    const { rerender } = renderHook(() => useScreenSize());
    const afterMount = reads.mock.calls.length;
    rerender();
    rerender();

    // A re-render with no resize must not measure again.
    expect(reads.mock.calls.length).toBe(afterMount);
  });

  it('does not re-render when a resize stays inside one breakpoint', () => {
    // Dragging a window edge fires hundreds of resize events. All but the few
    // that cross a boundary leave every subscriber's answer identical, and
    // waking them to say so is the cost the cached store exists to avoid.
    resizeTo(1000);
    let renders = 0;
    renderHook(() => {
      renders += 1;
      return useScreenSize();
    });
    const afterMount = renders;

    resizeTo(1010);
    resizeTo(1020);
    resizeTo(1030);

    expect(renders).toBe(afterMount);
  });

  it('does re-render when a resize crosses one', () => {
    resizeTo(1000);
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useScreenSize();
    });
    const afterMount = renders;

    resizeTo(400);

    expect(renders).toBeGreaterThan(afterMount);
    expect(result.current).toBe('xs');
  });

  it('resolves a per-size value for the active size', () => {
    resizeTo(400);
    const { result } = renderHook(() => useFlexValue({ xs: 'narrow', xl: 'wide' }));

    expect(result.current).toBe('narrow');
  });

  it('gives a component its region', () => {
    resizeTo(400);

    function Bar() {
      const region = useRegion('toolbarRight');
      return <div data-testid="bar" style={regionStyle(region)} />;
    }
    render(<Bar />);

    expect(screen.getByTestId('bar')).toHaveStyle({ width: '33vw' });
  });

  it('refuses a region the layout does not define', () => {
    // A component asking for a region that does not exist has a bug in it, and
    // an empty box would hide that until someone noticed the chrome missing.
    expect(() => renderHook(() => useRegion('nope'))).toThrow(/No region named nope/);
  });

  it('resolves the whole layout at once', () => {
    resizeTo(1920);
    const { result } = renderHook(() => useWindowLayout());

    expect(result.current.toolbarRight.width).toBe('35vw');
    expect(result.current.appBar.zIndex).toBe(LAYERS.appBar);
  });
});

describe('outside a browser', () => {
  it('reports the narrowest size rather than throwing', () => {
    // A layout that starts narrow and widens is far less jarring than one that
    // starts wide and collapses.
    expect(screenSizeFor(0)).toBe('xs');
    expect<ScreenSize>(screenSizeNow()).toBeTruthy();
  });
});

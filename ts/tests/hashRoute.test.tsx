import React from 'react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';

import { buildHash, parseHash, safeDecode, useHashRoute } from '../src/hooks/useHashRoute';
import * as kit from '../src';

/** Let jsdom dispatch the `hashchange` it queues as a task. */
async function flushHashChange(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  window.location.hash = '';
});

describe('buildHash / parseHash', () => {
  it('round-trips values with spaces and reserved characters', () => {
    const value = 'fire & ice = 100% + a/b? #x';
    const hash = buildHash('abilities', [['q', value], ['empty', ''], ['none', null]]);
    expect(hash).not.toContain('+');
    expect(hash).toBe(`#/abilities?q=${encodeURIComponent(value)}`);
    expect(parseHash(hash).get('q')).toBe(value);
  });

  it('round-trips through the browser, which re-encodes what it is given', async () => {
    const { result } = renderHook(() => useHashRoute());
    const value = 'a b+c&d=e?f';
    act(() => result.current.navigate(buildHash('my section/sub?x', [['q', value]])));
    await flushHashChange();
    expect(result.current.route.segment).toBe('my section/sub?x');
    expect(result.current.route.get('q')).toBe(value);
  });

  it('encodes the segment, so one with a space or `?` reads back unchanged', () => {
    // The source wrote the segment verbatim: `#/my section`, which a browser
    // stores as `#/my%20section` and the source read back as `my%20section`;
    // and `a?b` was split into the segment `a` and the query `b`.
    window.location.hash = buildHash('my section');
    expect(parseHash(window.location.hash).segment).toBe('my section');
    expect(parseHash(buildHash('a?b')).segment).toBe('a?b');
    expect(parseHash(buildHash('a?b')).rawQuery).toBe('');
  });

  it('keeps a literal plus rather than reading it as a space', () => {
    expect(parseHash('#/x?q=a+b').get('q')).toBe('a+b');
  });

  it('reads malformed percent-encoding as absent without throwing', () => {
    const route = parseHash('#/%E0%A4?q=%zz&ok=1&t=%E0%A4%A');
    expect(route.segment).toBe('');
    expect(route.get('q')).toBeNull();
    expect(route.get('t')).toBeNull();
    expect(route.get('ok')).toBe('1');
    expect(route.getRaw('q')).toBe('%zz');
    expect(safeDecode('%')).toBeNull();
    expect(safeDecode(null)).toBeNull();
  });

  it('takes the first occurrence, and a key without `=` as empty', () => {
    const route = parseHash('#/x?a=1&a=2&flag');
    expect(route.get('a')).toBe('1');
    expect(route.get('flag')).toBe('');
    expect(route.get('missing')).toBeNull();
  });

  it('writes a lone surrogate as U+FFFD instead of throwing', () => {
    const hash = buildHash('a\uD800b', [['k\uDC00', 'x\uD83D'], ['pair', '\uD83D\uDE00']]);
    const route = parseHash(hash);
    expect(route.segment).toBe('a\uFFFDb');
    expect(route.get('k\uFFFD')).toBe('x\uFFFD');
    expect(route.get('pair')).toBe('\uD83D\uDE00');
  });

  it('strips exactly one leading slash', () => {
    expect(parseHash('#/enemy').segment).toBe('enemy');
    expect(parseHash('#//abilities').segment).toBe('/abilities');
    expect(parseHash('').segment).toBe('');
    expect(parseHash('#').segment).toBe('');
  });
});

describe('useHashRoute', () => {
  it('re-renders consumers on Back and Forward', async () => {
    function Probe(): React.ReactElement {
      const { route } = useHashRoute();
      return <span data-testid="seg">{route.segment}</span>;
    }
    render(<Probe />);
    const { result } = renderHook(() => useHashRoute());
    act(() => result.current.navigate('#/one'));
    act(() => result.current.navigate('#/two'));
    await flushHashChange();
    expect(screen.getByTestId('seg').textContent).toBe('two');

    // Traversal is queued, and its `hashchange` after it.
    act(() => window.history.back());
    await waitFor(() => expect(screen.getByTestId('seg').textContent).toBe('one'));

    act(() => window.history.forward());
    await waitFor(() => expect(screen.getByTestId('seg').textContent).toBe('two'));
  });

  it('pushes one history entry per navigation and none for the current hash', () => {
    const { result } = renderHook(() => useHashRoute());
    const start = window.history.length;
    act(() => result.current.navigate(buildHash('b', [['q', 'x y']])));
    expect(window.history.length).toBe(start + 1);

    let renders = 0;
    const counted = renderHook(() => {
      renders += 1;
      return useHashRoute();
    });
    const before = renders;
    // The same route in three spellings: as built, unencoded, and without `#`.
    act(() => counted.result.current.navigate('#/b?q=x%20y'));
    act(() => counted.result.current.navigate('#/b?q=x y'));
    act(() => counted.result.current.navigate('/b?q=x%20y'));
    expect(window.history.length).toBe(start + 1);
    expect(renders).toBe(before);
  });

  it('updates the caller synchronously, in the form a reload would parse', () => {
    const { result } = renderHook(() => useHashRoute());
    act(() => result.current.navigate('#/b?q=x y'));
    expect(result.current.route.segment).toBe('b');
    expect(result.current.route.rawQuery).toBe('q=x%20y');
  });

  it('renders once on mount and keeps the route identity across its own hashchange', async () => {
    window.location.hash = '#/start';
    let renders = 0;
    const { result } = renderHook(() => {
      renders += 1;
      return useHashRoute();
    });
    expect(renders).toBe(1);
    const initial = result.current.route;
    await flushHashChange();
    expect(result.current.route).toBe(initial);

    act(() => result.current.navigate('#/next'));
    const navigated = result.current.route;
    const afterNavigate = renders;
    await flushHashChange();
    expect(result.current.route).toBe(navigated);
    expect(renders).toBe(afterNavigate);
  });

  it('stops listening on unmount', async () => {
    const { result, unmount } = renderHook(() => useHashRoute());
    const seen = result.current.route;
    unmount();
    window.location.hash = '#/later';
    await flushHashChange();
    expect(result.current.route).toBe(seen);
  });
});

describe('the kit', () => {
  it('exports both hooks from the barrel', () => {
    expect(kit.useHashRoute).toBe(useHashRoute);
    expect(typeof kit.usePersistedState).toBe('function');
    expect(typeof kit.usePersistedBoolean).toBe('function');
  });

  it('reads window.location only in useHashRoute', () => {
    const root = join(__dirname, '..', 'src');
    const offenders: string[] = [];
    const walk = (dir: string): void => {
      for (const name of readdirSync(dir)) {
        const path = join(dir, name);
        if (statSync(path).isDirectory()) walk(path);
        else if (/\.tsx?$/.test(name) && /\blocation\s*\./.test(readFileSync(path, 'utf8'))) {
          offenders.push(path.slice(root.length + 1));
        }
      }
    };
    walk(root);
    expect(offenders).toEqual(['hooks/useHashRoute.ts']);
  });
});

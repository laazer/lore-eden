import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePersistedBoolean, usePersistedState } from '../src/hooks/usePersistedState';

const KEY = 'lore-eden.test.state';

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe('usePersistedState', () => {
  it('stores an arbitrary serializable value as JSON and reads it back', () => {
    const first = renderHook(() => usePersistedState(KEY, { sort: 'name', open: [1] }));
    act(() => first.result.current[1]({ sort: 'date', open: [2, 3] }));
    expect(JSON.parse(localStorage.getItem(KEY) ?? 'null')).toEqual({ sort: 'date', open: [2, 3] });

    const again = renderHook(() => usePersistedState(KEY, { sort: 'name', open: [1] }));
    expect(again.result.current[0]).toEqual({ sort: 'date', open: [2, 3] });
  });

  it('uses a custom serializer and parser', () => {
    localStorage.setItem(KEY, '3|4');
    const options = {
      serialize: (p: [number, number]) => p.join('|'),
      parse: (raw: string) => raw.split('|').map(Number) as [number, number],
    };
    const { result } = renderHook(() => usePersistedState<[number, number]>(KEY, [0, 0], options));
    expect(result.current[0]).toEqual([3, 4]);
    act(() => result.current[1]([5, 6]));
    expect(localStorage.getItem(KEY)).toBe('5|6');
  });

  it('accepts an updater and keeps the setter stable', () => {
    const { result } = renderHook(() => usePersistedState(KEY, 1));
    const setter = result.current[1];
    act(() => result.current[1]((n) => n + 1));
    act(() => result.current[1]((n) => n + 1));
    expect(result.current[0]).toBe(3);
    expect(result.current[1]).toBe(setter);
    expect(localStorage.getItem(KEY)).toBe('3');
  });

  it('reads the default when the stored value does not parse', () => {
    localStorage.setItem(KEY, '{not json');
    const { result } = renderHook(() => usePersistedState(KEY, 'fallback'));
    expect(result.current[0]).toBe('fallback');
  });

  it('does not write on mount, so a changed default still reaches untouched users', () => {
    renderHook(() => usePersistedState(KEY, 'old default'));
    expect(localStorage.getItem(KEY)).toBeNull();
    const later = renderHook(() => usePersistedState(KEY, 'new default'));
    expect(later.result.current[0]).toBe('new default');
  });

  it('reads the new key when the key changes, without overwriting it', () => {
    localStorage.setItem('a', JSON.stringify('value of a'));
    localStorage.setItem('b', JSON.stringify('value of b'));
    const { result, rerender } = renderHook(({ k }) => usePersistedState(k, 'default'), {
      initialProps: { k: 'a' },
    });
    act(() => result.current[1]('set on a'));
    rerender({ k: 'b' });
    expect(result.current[0]).toBe('value of b');
    expect(localStorage.getItem('b')).toBe(JSON.stringify('value of b'));
    expect(localStorage.getItem('a')).toBe(JSON.stringify('set on a'));
  });

  it('returns the default and keeps working in memory when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new DOMException('blocked', 'SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('full', 'QuotaExceededError');
    });
    const { result } = renderHook(() => usePersistedState(KEY, 'default'));
    expect(result.current[0]).toBe('default');
    act(() => result.current[1]('changed'));
    expect(result.current[0]).toBe('changed');
  });

  it('returns the default when localStorage is unavailable', () => {
    vi.stubGlobal('localStorage', undefined);
    const { result } = renderHook(() => usePersistedState(KEY, 7));
    expect(result.current[0]).toBe(7);
    act(() => result.current[1](8));
    expect(result.current[0]).toBe(8);
  });

  it('survives a value the serializer rejects', () => {
    const cyclic: { self?: unknown } = {};
    cyclic.self = cyclic;
    const { result } = renderHook(() => usePersistedState<unknown>(KEY, null));
    act(() => result.current[1](cyclic));
    expect(result.current[0]).toBe(cyclic);
    expect(localStorage.getItem(KEY)).toBeNull();
  });
});

describe('usePersistedBoolean', () => {
  it('stores "1" / "0" and reads values the source wrote', () => {
    localStorage.setItem(KEY, '0');
    const { result } = renderHook(() => usePersistedBoolean(KEY, true));
    expect(result.current[0]).toBe(false);
    act(() => result.current[1](true));
    expect(localStorage.getItem(KEY)).toBe('1');
    act(() => result.current[1]((v) => !v));
    expect(localStorage.getItem(KEY)).toBe('0');
  });

  it('reads "true" and "false", and anything else as the default', () => {
    localStorage.setItem(KEY, 'true');
    expect(renderHook(() => usePersistedBoolean(KEY, false)).result.current[0]).toBe(true);
    localStorage.setItem(KEY, 'false');
    expect(renderHook(() => usePersistedBoolean(KEY, true)).result.current[0]).toBe(false);
    localStorage.setItem(KEY, 'garbage');
    expect(renderHook(() => usePersistedBoolean(KEY, true)).result.current[0]).toBe(true);
  });
});

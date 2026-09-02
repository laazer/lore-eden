import { describe, expect, it, vi } from 'vitest';

import { ANY_CHANGE, Observable, ObservableDict } from '../src/observable';
import { LruOrderedSetCache } from '../src/collections';
import { formatAsUuid, randomId, shortId, sortableId } from '../src/util/ids';
import { devLog, setDevLogEnabled } from '../src/util/devLog';
import { asError, describeError } from '../src/util/errors';
import {
  DEFAULT_ALIASES,
  addAlias,
  popCheckpoint,
  pushCheckpoint,
  resolveJump,
  samePlace,
} from '../src/nav/checkpoints';
import {
  IDLE,
  failed,
  fromQueryLike,
  loading,
  mapQueried,
  queriedOr,
  succeeded,
} from '../src/query';
import { reconcileSelection } from '../src/components/TabView';

describe('Observable', () => {
  it('calls a subscriber once for an emission touching several of its channels', () => {
    class Doc extends Observable<string> {
      change(types: string[]): void {
        this.emitChanges(types);
      }
    }
    const doc = new Doc('body');
    const seen = vi.fn();
    doc.observeChangeTypes(seen, ['title', 'body']);
    doc.change(['title', 'body']);
    // A per-channel fan-out would call this twice and leave the caller to dedupe.
    expect(seen).toHaveBeenCalledTimes(1);
    expect(seen).toHaveBeenCalledWith('body', ['title', 'body'], undefined);
  });

  it('wakes the wildcard channel for a named change', () => {
    class Doc extends Observable<string> {
      change(types: string[]): void {
        this.emitChanges(types);
      }
    }
    const doc = new Doc('x');
    const specific = vi.fn();
    const all = vi.fn();
    doc.observeChangeType(specific, 'title');
    doc.observe(all);
    doc.change(['body']);
    expect(specific).not.toHaveBeenCalled();
    expect(all).toHaveBeenCalledTimes(1);
  });

  it('does not double-call a wildcard subscriber on an ANY_CHANGE emission', () => {
    class Doc extends Observable<string> {
      change(types: string[]): void {
        this.emitChanges(types);
      }
    }
    const doc = new Doc('x');
    const all = vi.fn();
    doc.observe(all);
    doc.change([ANY_CHANGE]);
    expect(all).toHaveBeenCalledTimes(1);
  });

  it('unsubscribes an inline arrow through the returned function', () => {
    class Doc extends Observable<string> {
      change(): void {
        this.emitChanges(['title']);
      }
    }
    const doc = new Doc('x');
    const seen = vi.fn();
    const off = doc.observeChangeType((...args) => seen(...args), 'title');
    doc.change();
    off();
    doc.change();
    expect(seen).toHaveBeenCalledTimes(1);
    expect(doc.observerCount).toBe(0);
  });

  it('drops a channel once its last subscriber leaves', () => {
    class Doc extends Observable<string> {}
    const doc = new Doc('x');
    const offs = Array.from({ length: 5 }, (_unused, i) =>
      doc.observeChangeType(vi.fn(), `type-${i}`),
    );
    expect(doc.observerCount).toBe(5);
    for (const off of offs) off();
    expect(doc.observerCount).toBe(0);
  });

  it('clears staged context before delivering, not after', () => {
    class Doc extends Observable<string, string> {
      changeWith(context: string): void {
        this.setContext(context);
        this.emitChanges(['a']);
      }
    }
    const doc = new Doc('x');
    const contexts: (string | undefined)[] = [];
    doc.observeChangeType((_observed, _types, context) => {
      contexts.push(context);
      // Re-entrant emission: it must not inherit this emission's context.
      if (contexts.length === 1) doc.changeWith('');
    }, 'a');
    doc.changeWith('first');
    expect(contexts).toEqual(['first', '']);
  });
});

describe('ObservableDict', () => {
  it('emits on the channel named by the key', () => {
    const dict = new ObservableDict<{ title: string; body: string }>({ title: 'a', body: 'b' });
    const onTitle = vi.fn();
    dict.observeChangeType(onTitle, 'title');
    dict.update('body', 'changed');
    expect(onTitle).not.toHaveBeenCalled();
    dict.update('title', 'changed');
    expect(onTitle).toHaveBeenCalledTimes(1);
  });

  it('observes the data, not the wrapper', () => {
    const dict = new ObservableDict<{ title: string }>({ title: 'a' });
    const seen = vi.fn();
    dict.observe(seen);
    dict.update('title', 'b');
    expect(seen.mock.calls[0][0]).toEqual({ title: 'b' });
  });

  it('announces a multi-field write in one emission', () => {
    const dict = new ObservableDict<{ a: number; b: number }>({ a: 0, b: 0 });
    const seen = vi.fn();
    dict.observeChangeTypes(seen, ['a', 'b']);
    dict.updateAll({ a: 1, b: 2 });
    expect(seen).toHaveBeenCalledTimes(1);
    expect(dict.get('a')).toBe(1);
    expect(dict.get('b')).toBe(2);
  });
});

describe('LruOrderedSetCache', () => {
  it('does not alias the array it was constructed with', () => {
    // The source assigned the caller's array to both internal lists, so they
    // aliased each other and the caller's; one push appended twice.
    const seed = ['a', 'b'];
    const cache = new LruOrderedSetCache<string>(5, seed);
    cache.push('c');
    expect(seed).toEqual(['a', 'b']);
    expect(cache.asList()).toEqual(['c', 'a', 'b']);
    expect(cache.length).toBe(3);
  });

  it('keeps insertion order stable when an existing item is touched', () => {
    const cache = new LruOrderedSetCache<string>(3);
    cache.push('a');
    cache.push('b');
    cache.push('c');
    cache.push('a');
    expect(cache.asList()).toEqual(['c', 'b', 'a']);
    expect(cache.asRecencyList()[0]).toBe('a');
  });

  it('evicts the least recently used, not the oldest inserted', () => {
    const cache = new LruOrderedSetCache<string>(3);
    cache.push('a');
    cache.push('b');
    cache.push('c');
    cache.push('a');
    cache.push('d');
    expect(cache.contains('b')).toBe(false);
    expect(cache.contains('a')).toBe(true);
    expect(cache.asList()).toEqual(['d', 'c', 'a']);
  });

  it('adopts the new bound in updateMaxSize', () => {
    // The source evicted to the new size but never stored it, so the next push
    // enforced the old bound.
    const cache = new LruOrderedSetCache<string>(5);
    for (const item of ['a', 'b', 'c', 'd']) cache.push(item);
    expect(cache.updateMaxSize(2)).toEqual(['a', 'b']);
    expect(cache.maxSize).toBe(2);
    cache.push('e');
    expect(cache.length).toBe(2);
  });

  it('trims a seed that already exceeds the bound', () => {
    const cache = new LruOrderedSetCache<string>(2, ['a', 'b', 'c']);
    expect(cache.length).toBe(2);
  });

  it('hands back copies, not its own arrays', () => {
    const cache = new LruOrderedSetCache<string>(3, ['a']);
    cache.asList().push('rogue');
    expect(cache.length).toBe(1);
  });
});

describe('ids', () => {
  it('gives the requested amount of randomness regardless of prefix', () => {
    // The source sliced prefix+random to a fixed total, so a longer prefix
    // silently bought less entropy.
    expect(randomId('observer_', 12)).toHaveLength('observer_'.length + 12);
    expect(randomId('x_', 12)).toHaveLength('x_'.length + 12);
  });

  it('makes ids that sort by creation time', () => {
    const times = [1, 1_000, 1_000_000_000_000, 2_000_000_000_000, 9_999_999_999_999];
    const ids = times.map((now) => sortableId({ now, grouped: false }));
    expect([...ids].sort()).toEqual(ids);
  });

  it('pads the timestamp so width changes cannot reorder ids', () => {
    // 1e12 is ten hex digits and 2e12 is eleven. Unpadded — as the source left
    // them — the shorter string sorts after the longer one here, so two ids
    // made either side of that boundary compare backwards.
    const early = sortableId({ now: 1_000_000_000_000, grouped: false });
    const late = sortableId({ now: 2_000_000_000_000, grouped: false });
    expect(early < late).toBe(true);
    expect(early.slice(0, 12)).toHaveLength(late.slice(0, 12).length);
  });

  it('groups into the 8-4-4-4-12 shape', () => {
    const id = sortableId({ now: 1_700_000_000_000 });
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(formatAsUuid('a'.repeat(32))).toHaveLength(36);
  });

  it('caps a short id at its maximum', () => {
    expect(shortId('tab_', 16)).toHaveLength(16);
  });

  it('does not repeat itself', () => {
    const ids = new Set(Array.from({ length: 500 }, () => randomId('', 16)));
    expect(ids.size).toBe(500);
  });
});

describe('devLog', () => {
  it('can be forced off and back on', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    setDevLogEnabled(false);
    devLog('hidden');
    expect(spy).not.toHaveBeenCalled();
    setDevLogEnabled(true);
    devLog('shown');
    expect(spy).toHaveBeenCalledWith('shown');
    setDevLogEnabled(undefined);
    spy.mockRestore();
  });

  it('does not evaluate a thunk condition when logging is off', () => {
    const spy = vi.spyOn(console, 'log').mockImplementation(() => undefined);
    const expensive = vi.fn(() => true);
    setDevLogEnabled(false);
    devLog('x', expensive);
    expect(expensive).not.toHaveBeenCalled();
    setDevLogEnabled(undefined);
    spy.mockRestore();
  });
});

describe('asError', () => {
  it('passes an Error through untouched', () => {
    const error = new Error('boom');
    expect(asError(error)).toBe(error);
  });

  it('describes a thrown object by its fields, not [object Object]', () => {
    expect(describeError({ code: 42 })).toBe('{"code":42}');
  });

  it('falls back for values with nothing to say', () => {
    expect(describeError(undefined, 'nothing')).toBe('nothing');
    expect(describeError('', 'nothing')).toBe('nothing');
    expect(describeError(null, 'nothing')).toBe('nothing');
  });

  it('survives a value that cannot be serialized', () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(describeError(cyclic)).toContain('object');
  });
});

describe('checkpoints', () => {
  it('treats aliased paths as the same place', () => {
    expect(samePlace('/', '/home', DEFAULT_ALIASES)).toBe(true);
    expect(samePlace('/home', '/', DEFAULT_ALIASES)).toBe(true);
    expect(samePlace('/a', '/b', DEFAULT_ALIASES)).toBe(false);
  });

  it('does not push a path the top already points at', () => {
    const stack = ['/docs'];
    expect(pushCheckpoint(stack, '/docs')).toBe(stack);
    expect(pushCheckpoint(['/'], '/home')).toEqual(['/']);
    expect(pushCheckpoint(['/docs'], '/settings')).toEqual(['/docs', '/settings']);
  });

  it('pops without mutating', () => {
    // The source called `.pop()` on the array held in React state and passed
    // the same reference to the setter, so React skipped the render while the
    // state it was rendering from had already changed.
    const stack = ['/a', '/b'];
    expect(popCheckpoint(stack)).toEqual(['/a']);
    expect(stack).toEqual(['/a', '/b']);
  });

  it('skips checkpoints that resolve to where we already are', () => {
    const result = resolveJump(['/docs', '/', '/home'], '/home');
    expect(result.path).toBe('/docs');
    expect(result.stack).toEqual([]);
  });

  it('falls back when the stack is exhausted', () => {
    expect(resolveJump(['/home'], '/', DEFAULT_ALIASES, '/start')).toEqual({
      path: '/start',
      stack: ['/start'],
    });
    expect(resolveJump([], '/anywhere').path).toBe('/');
  });

  it('aliases both ways', () => {
    const aliases = addAlias(DEFAULT_ALIASES, '/a', '/b');
    expect(samePlace('/a', '/b', aliases)).toBe(true);
    expect(samePlace('/b', '/a', aliases)).toBe(true);
  });
});

describe('query results', () => {
  it('narrows to data only on success', () => {
    const result = succeeded(42);
    expect(result.isSuccess && result.data).toBe(42);
    expect(queriedOr(result, 0)).toBe(42);
    expect(queriedOr(loading(), 7)).toBe(7);
  });

  it('maps data of any shape', () => {
    // The source spread the mapped value into the result, so mapping to a
    // number or an array produced a result with no usable data.
    expect(mapQueried(succeeded(2), (n) => n * 2)).toMatchObject({ data: 4 });
    expect(mapQueried(succeeded('a'), (s) => [s, s])).toMatchObject({ data: ['a', 'a'] });
  });

  it('passes non-success through untouched', () => {
    const error = failed(new Error('nope'));
    expect(mapQueried(error, () => 1)).toBe(error);
    expect(mapQueried(IDLE, () => 1)).toBe(IDLE);
  });

  it('adapts a query client result', () => {
    expect(fromQueryLike({ isSuccess: true, data: 5 })).toMatchObject({ status: 'success' });
    expect(fromQueryLike({ isLoading: true })).toMatchObject({ status: 'loading' });
    expect(fromQueryLike({ isError: true, error: 'boom' })).toMatchObject({
      status: 'error',
      errorMessage: 'boom',
    });
    expect(fromQueryLike({})).toBe(IDLE);
  });

  it('does not call a success with no data a success', () => {
    expect(fromQueryLike({ isSuccess: true, isLoading: true })).toMatchObject({
      status: 'loading',
    });
  });
});

describe('reconcileSelection', () => {
  it('selects a newly added tab', () => {
    expect(reconcileSelection(['a', 'b'], ['a', 'b', 'c'], 'a')).toBe('c');
  });

  it('follows the selected tab when others are removed', () => {
    // An index-based implementation would leave the selection on position 1,
    // which is now a different document.
    expect(reconcileSelection(['a', 'b', 'c'], ['b', 'c'], 'c')).toBe('c');
  });

  it('takes the previous neighbour when the selected tab closes', () => {
    expect(reconcileSelection(['a', 'b', 'c'], ['a', 'c'], 'b')).toBe('a');
  });

  it('takes the first tab when the selected one was first', () => {
    expect(reconcileSelection(['a', 'b'], ['b'], 'a')).toBe('b');
  });

  it('survives every tab closing', () => {
    expect(reconcileSelection(['a'], [], 'a')).toBe('');
  });

  it('stays put when tabs are merely reordered', () => {
    expect(reconcileSelection(['a', 'b', 'c'], ['c', 'b', 'a'], 'b')).toBe('b');
  });
});

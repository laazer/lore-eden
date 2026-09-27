/**
 * Route state in `location.hash`, with no router dependency.
 *
 * The URL shape is `<origin><path>#/<segment>?<query>`. Only the hash is ever
 * read or written — never the document path — and the hook subscribes to
 * `hashchange`, so Back, Forward and a hand-edited address bar all re-render
 * consumers. This is the only module in the kit that reads `window.location`:
 * the component that owns the route calls the hook and hands the state to deep
 * components as props, which keeps them testable without a URL.
 *
 * The query string is built by hand rather than with
 * `URLSearchParams.toString()`. That form-urlencodes a space as `+`, while the
 * read path decodes with `decodeURIComponent`, which reads `+` as a literal plus
 * — so a value with a space would not survive the round trip. Reading is by
 * hand for the same reason: `URLSearchParams` would turn a literal `+` into a
 * space. Malformed percent-encoding reads as absent rather than letting a
 * `URIError` reach a consumer.
 *
 * Differs from the source: the segment is encoded on write and decoded on read.
 * The source wrote it verbatim, so `buildHash('my section', [])` produced
 * `#/my section`, which the browser stores as `#/my%20section` and the source
 * then read back as the segment `my%20section`; a segment containing `?` was
 * split into a segment and a query. Each `/`-separated piece is encoded, so a
 * nested segment such as `abilities/gust` keeps its slash.
 */

import { useCallback, useEffect, useState } from 'react';

/** A parsed hash route. */
export interface HashRoute {
  /** The decoded path after `#/`. Empty when absent or malformed. */
  segment: string;
  /** Everything after the first `?`, still encoded. Empty when absent. */
  rawQuery: string;
  /** The decoded first value for `key`; null when absent or malformed. */
  get: (key: string) => string | null;
  /** The still-encoded first value for `key`; null when absent. */
  getRaw: (key: string) => string | null;
}

/** Decode one component, returning null instead of throwing on malformed input. */
export function safeDecode(value: string | null): string | null {
  if (value === null) return null;
  try {
    return decodeURIComponent(value);
  } catch {
    // A truncated UTF-8 sequence or `%zz`: absent, never a URIError.
    return null;
  }
}

/**
 * The first raw value for `key`. Splitting on `&` and the first `=` is safe
 * because every value is written with `encodeURIComponent`, which escapes both.
 * A key with no `=` has the empty value.
 */
function getRawParam(rawQuery: string, key: string): string | null {
  for (const pair of rawQuery.split('&')) {
    if (pair === '') continue;
    const eq = pair.indexOf('=');
    const rawKey = eq === -1 ? pair : pair.slice(0, eq);
    if ((safeDecode(rawKey) ?? rawKey) === key) return eq === -1 ? '' : pair.slice(eq + 1);
  }
  return null;
}

/**
 * Parse a raw hash such as `#/abilities?ability=gust`. Exactly one leading `/`
 * is stripped, so `#//abilities` reads as `/abilities` — an id that matches
 * nothing, which is the intended fallback for a hostile hash.
 */
export function parseHash(rawHash: string): HashRoute {
  const body = rawHash.startsWith('#') ? rawHash.slice(1) : rawHash;
  const queryStart = body.indexOf('?');
  const pathPart = queryStart === -1 ? body : body.slice(0, queryStart);
  const rawQuery = queryStart === -1 ? '' : body.slice(queryStart + 1);
  const rawSegment = pathPart.startsWith('/') ? pathPart.slice(1) : pathPart;
  return {
    segment: safeDecode(rawSegment) ?? '',
    rawQuery,
    get: (key) => safeDecode(getRawParam(rawQuery, key)),
    getRaw: (key) => getRawParam(rawQuery, key),
  };
}

/** A high surrogate not followed by a low one, or a low one not preceded by a high one. */
const LONE_SURROGATE = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g;

/**
 * `encodeURIComponent`, with each lone surrogate written as U+FFFD rather than
 * throwing a `URIError` — what `String.prototype.toWellFormed` does, which the
 * ES2022 lib this package targets does not declare.
 */
function encodeComponent(value: string): string {
  return encodeURIComponent(value.replace(LONE_SURROGATE, '\uFFFD'));
}

/**
 * Serialize a segment and ordered query entries to `#/<segment>?<k=v&...>`.
 * Null and empty values are omitted; entry order is kept, so the same state
 * always writes the same hash.
 */
export function buildHash(
  segment: string,
  entries: ReadonlyArray<readonly [string, string | null]> = [],
): string {
  const path = segment.split('/').map(encodeComponent).join('/');
  const pairs: string[] = [];
  for (const [key, value] of entries) {
    if (value !== null && value !== '') {
      pairs.push(`${encodeComponent(key)}=${encodeComponent(value)}`);
    }
  }
  return pairs.length > 0 ? `#/${path}?${pairs.join('&')}` : `#/${path}`;
}

/** The hash `hash` would become once assigned, in the form `location.hash` reports. */
function normalizeHash(hash: string): string {
  return new URL(hash.startsWith('#') ? hash : `#${hash}`, window.location.href).hash;
}

/**
 * The current hash route, and `navigate` to change it.
 *
 * `navigate` pushes a history entry so Back and Forward walk visited routes.
 * Navigating to the current hash is a no-op, compared in the browser's
 * normalized form — so `#/a?q=x y` while at `#/a?q=x%20y`, or `/a` while at
 * `#/a`, pushes nothing and does not re-render.
 */
export function useHashRoute(): { route: HashRoute; navigate: (hash: string) => void } {
  // The raw hash is kept beside its parse so an unchanged hash — the mount-time
  // sync, or the `hashchange` that follows `navigate` — keeps the same route
  // object and skips the re-render.
  const [state, setState] = useState<{ hash: string; route: HashRoute }>(() => {
    // No `window` under server rendering: the route reads as empty.
    const hash = typeof window === 'undefined' ? '' : window.location.hash;
    return { hash, route: parseHash(hash) };
  });

  const sync = useCallback((): void => {
    const hash = window.location.hash;
    setState((prev) => (prev.hash === hash ? prev : { hash, route: parseHash(hash) }));
  }, []);

  useEffect(() => {
    window.addEventListener('hashchange', sync);
    // The hash may have changed between the initial render and this effect.
    sync();
    return () => window.removeEventListener('hashchange', sync);
  }, [sync]);

  const navigate = useCallback(
    (hash: string): void => {
      const target = normalizeHash(hash);
      if (target === window.location.hash) return;
      window.location.hash = target;
      // `hashchange` is dispatched as a later task; update now so the caller's
      // next render already shows the route it asked for. Read back from
      // `location` so local state is what a reload would parse.
      sync();
    },
    [sync],
  );

  return { route: state.route, navigate };
}

/**
 * Turning an unknown thrown value into something you can report.
 *
 * `catch (error)` gives you `unknown`, because JavaScript lets anything be
 * thrown — a string, `undefined`, a rejected fetch's response. Every call site
 * that wants a message therefore needs the same narrowing, and written inline
 * it is a ternary repeated across a codebase, each copy free to disagree with
 * the others about what a non-`Error` should say.
 *
 * One helper, used everywhere. The gates enforce that.
 */

/** The value as an `Error`, wrapping it if it is not one already. */
export function asError(value: unknown, fallback = 'Unknown error'): Error {
  if (value instanceof Error) return value;
  if (value === undefined || value === null) return new Error(fallback);
  if (typeof value === 'string') return new Error(value === '' ? fallback : value);
  // A thrown object stringifies to "[object Object]", which tells a reader
  // nothing. JSON keeps whatever fields it had.
  try {
    return new Error(JSON.stringify(value));
  } catch {
    return new Error(String(value));
  }
}

/** A human-readable message for anything that was thrown. */
export function describeError(value: unknown, fallback = 'Unknown error'): string {
  return asError(value, fallback).message;
}

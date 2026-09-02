/**
 * An async result as a discriminated union.
 *
 * The value of the shape is that the type system stops you reading data off a
 * result that has none. After `if (result.isSuccess)`, `result.data` exists;
 * before it, there is nothing to read — no optional chaining, no non-null
 * assertion, no branch you forgot.
 *
 * ## Not tied to a query library
 *
 * The source imported `react-query`'s `QueryStatus` and `UseQueryResult` purely
 * to re-export them, which bound the union to react-query v3 — a version whose
 * `'idle'` status was removed in v4. A shared library cannot pin its consumers
 * to one data-fetching library, let alone a superseded major.
 *
 * These are plain types. {@link fromQueryLike} adapts whatever a host's client
 * returns, and works for react-query, TanStack Query, SWR, or a hand-rolled
 * fetch hook, because it reads only the three booleans they all agree on.
 */

import { asError } from '../util/errors';

export type QueryStatus = 'idle' | 'loading' | 'error' | 'success';

interface QueryFlags {
  isIdle: boolean;
  isLoading: boolean;
  isError: boolean;
  isSuccess: boolean;
}

export interface QueriedIdle extends QueryFlags {
  status: 'idle';
  isIdle: true;
  isLoading: false;
  isError: false;
  isSuccess: false;
}

export interface QueriedLoading extends QueryFlags {
  status: 'loading';
  isIdle: false;
  isLoading: true;
  isError: false;
  isSuccess: false;
}

export interface QueriedError extends QueryFlags {
  status: 'error';
  isIdle: false;
  isLoading: false;
  isError: true;
  isSuccess: false;
  error: Error;
  errorMessage: string;
}

export interface QueriedSuccess<T> extends QueryFlags {
  status: 'success';
  isIdle: false;
  isLoading: false;
  isError: false;
  isSuccess: true;
  data: T;
}

export type Queried<T> = QueriedIdle | QueriedLoading | QueriedError | QueriedSuccess<T>;

/** Every non-success case, for a caller that only wants to pass them through. */
export type QueriedNonSuccess = QueriedIdle | QueriedLoading | QueriedError;

export const IDLE: QueriedIdle = {
  status: 'idle',
  isIdle: true,
  isLoading: false,
  isError: false,
  isSuccess: false,
};

export function loading(): QueriedLoading {
  return { status: 'loading', isIdle: false, isLoading: true, isError: false, isSuccess: false };
}

export function failed(error: Error): QueriedError {
  return {
    status: 'error',
    isIdle: false,
    isLoading: false,
    isError: true,
    isSuccess: false,
    error,
    errorMessage: error.message,
  };
}

export function succeeded<T>(data: T): QueriedSuccess<T> {
  return { status: 'success', isIdle: false, isLoading: false, isError: false, isSuccess: true, data };
}

/**
 * Transform the data of a successful result, passing every other case through.
 *
 * The source spread the mapped value into the result object, so a `map` to
 * anything that is not a plain object — a number, an array, a string — produced
 * a result with no usable data. Here the value lives under `data`, so it can be
 * anything.
 */
export function mapQueried<T, R>(result: Queried<T>, fn: (data: T) => R): Queried<R> {
  return result.isSuccess ? succeeded(fn(result.data)) : result;
}

/** The data if there is any, otherwise `fallback`. */
export function queriedOr<T>(result: Queried<T>, fallback: T): T {
  return result.isSuccess ? result.data : fallback;
}

/** The three booleans every query library agrees on. */
export interface QueryLike<T> {
  data?: T;
  error?: unknown;
  isLoading?: boolean;
  isError?: boolean;
  isSuccess?: boolean;
}

/**
 * Adapt a query client's result into a {@link Queried}.
 *
 * Success is required to carry data: a result flagged successful with `data`
 * still undefined is treated as loading rather than as a success holding
 * `undefined`, which is the shape that makes `result.data` lie.
 */
export function fromQueryLike<T>(source: QueryLike<T>): Queried<T> {
  if (source.isError === true) return failed(asError(source.error, 'Query failed'));
  if (source.isSuccess === true && source.data !== undefined) return succeeded(source.data);
  if (source.isLoading === true) return loading();
  return source.data === undefined ? IDLE : succeeded(source.data);
}

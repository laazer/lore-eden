/**
 * Logging that disappears in a production build.
 *
 * The condition may be a thunk, which is the reason to use this over a bare
 * `console.log` behind an `if`: the expensive part of a diagnostic — walking a
 * tree, stringifying a graph — goes inside the thunk and never runs in
 * production.
 *
 * ## Detecting production without assuming a bundler
 *
 * The source read `process.env.NODE_ENV`, which does not exist in a browser
 * unless a bundler substituted it. That is fine in an application, where there
 * is exactly one bundler; a shared library is imported by projects using
 * different ones, and by Node, and by test runners. So the check is guarded and
 * falls back to *enabled* — a library that silently swallowed a host's
 * diagnostics because it could not identify the environment would be worse
 * than one that logs when it should not.
 *
 * A host that knows better calls {@link setDevLogEnabled}.
 */

export type DevCondition = boolean | (() => boolean);

let override: boolean | undefined;

function detectEnabled(): boolean {
  // Vite and friends.
  const viteMode = (import.meta as { env?: { PROD?: boolean } }).env;
  if (viteMode?.PROD !== undefined) return !viteMode.PROD;
  // Webpack, Node, Jest.
  const nodeEnv = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process
    ?.env?.NODE_ENV;
  if (nodeEnv !== undefined) return nodeEnv !== 'production';
  return true;
}

/** Force dev logging on or off. `undefined` restores detection. */
export function setDevLogEnabled(enabled: boolean | undefined): void {
  override = enabled;
}

function isEnabled(condition?: DevCondition): boolean {
  if (!(override ?? detectEnabled())) return false;
  if (condition === undefined) return true;
  return typeof condition === 'function' ? condition() : condition;
}

export function devLog(message: string, condition?: DevCondition, ...data: unknown[]): void {
  if (isEnabled(condition)) console.log(message, ...data);
}

export function devWarn(message: string, condition?: DevCondition, ...data: unknown[]): void {
  if (isEnabled(condition)) console.warn(message, ...data);
}

export function devError(message: string, condition?: DevCondition, ...data: unknown[]): void {
  if (isEnabled(condition)) console.error(message, ...data);
}

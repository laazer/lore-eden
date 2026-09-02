/**
 * "Go back to where I was" — as a stack, not as browser history.
 *
 * Browser history is where you *came from*, which is not the same question. A
 * user who wandered through six settings screens wants one gesture back to the
 * document they were editing, not six presses. A checkpoint is pushed
 * deliberately at the places worth returning to.
 *
 * ## Aliases
 *
 * Two paths can be the same place — `/` and `/home`, a canonical URL and a
 * shortlink. Without aliasing, returning to a checkpoint you are already
 * standing on is a no-op that reads as a broken button. {@link resolveJump}
 * skips any checkpoint that resolves to where you already are.
 *
 * Everything here is pure. The provider wires it to a router; these functions
 * are testable without one.
 */

/** Which paths mean the same place. Symmetric: adding one adds its inverse. */
export type PathAliases = Readonly<Record<string, string>>;

export interface CheckpointState {
  readonly stack: readonly string[];
  readonly aliases: PathAliases;
}

/** Where a jump should go, and what the stack looks like afterwards. */
export interface JumpResult {
  readonly path: string;
  readonly stack: readonly string[];
}

export const DEFAULT_ALIASES: PathAliases = { '/': '/home', '/home': '/' };

export function samePlace(a: string, b: string, aliases: PathAliases): boolean {
  return a === b || aliases[a] === b || aliases[b] === a;
}

/**
 * Push a checkpoint, unless it is where the top of the stack already points.
 *
 * Returns the same array when nothing changed, so a caller storing this in
 * React state re-renders only on a real change.
 */
export function pushCheckpoint(
  stack: readonly string[],
  path: string,
  aliases: PathAliases = DEFAULT_ALIASES,
): readonly string[] {
  if (path === '') return stack;
  const top = stack[stack.length - 1];
  if (top !== undefined && samePlace(top, path, aliases)) return stack;
  return [...stack, path];
}

export function popCheckpoint(stack: readonly string[]): readonly string[] {
  // A new array, never `stack.pop()`. The source mutated the array held in
  // React state and then passed the same reference to the setter — so React
  // saw an unchanged value and skipped the render, while the state it was
  // rendering from had already been altered underneath it.
  return stack.length === 0 ? stack : stack.slice(0, -1);
}

/**
 * The nearest checkpoint that is not where we already are.
 *
 * `fallback` is used when the stack runs out, so the caller always has
 * somewhere to go rather than a silently dead control.
 */
export function resolveJump(
  stack: readonly string[],
  currentPath: string,
  aliases: PathAliases = DEFAULT_ALIASES,
  fallback = '/',
): JumpResult {
  let index = stack.length - 1;
  while (index >= 0 && samePlace(stack[index], currentPath, aliases)) index -= 1;
  if (index < 0) return { path: fallback, stack: [fallback] };
  return { path: stack[index], stack: stack.slice(0, index) };
}

/** Add an alias pair. Symmetric, so either path resolves to the other. */
export function addAlias(aliases: PathAliases, from: string, to: string): PathAliases {
  return { ...aliases, [from]: to, [to]: from };
}

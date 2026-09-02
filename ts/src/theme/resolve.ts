/**
 * Resolving token values for a mode.
 *
 * Light is expressed as a sparse set of overrides on top of dark, which is the
 * shape the source used and the right one: a token added to the dark table is
 * inherited by light automatically, rather than silently going missing until
 * somebody notices the light theme lost a colour.
 */

import { lightTokenValues, tokenSpecs } from '../tokens/specs';
import type { TokenKey } from '../tokens/specs';

/** Dark is the default. Light is a set of overrides on top of it. */
export type ThemeMode = 'dark' | 'light';

const DARK_VALUES = Object.fromEntries(
  Object.entries(tokenSpecs).map(([key, spec]) => [key, spec.value]),
) as Record<TokenKey, string>;

const LIGHT_VALUES = { ...DARK_VALUES, ...lightTokenValues } as Record<TokenKey, string>;

/** Every token's value in `mode`. */
export function resolveTokens(mode: ThemeMode): Record<TokenKey, string> {
  return mode === 'light' ? LIGHT_VALUES : DARK_VALUES;
}

/** One token's value in `mode`. */
export function resolveToken(name: TokenKey, mode: ThemeMode): string {
  return resolveTokens(mode)[name];
}

/** Token names whose value differs between the two modes. */
export function tokensThatDiffer(): TokenKey[] {
  return (Object.keys(lightTokenValues) as TokenKey[]).filter(
    (key) => lightTokenValues[key] !== DARK_VALUES[key],
  );
}

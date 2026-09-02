/**
 * A value given per screen size, and how one is chosen.
 *
 *     <Box width={{ xs: '100%', md: '50%', xl: 320 }} />
 *
 * ## The base is not a breakpoint
 *
 * The source modelled it as one — a seventh member of the size union, named
 * `sx` — and then had to special-case it twice in the resolver because it does
 * not behave like the others. It is the value that applies at *every* size,
 * merged under whatever the breakpoint resolves to. One of those special cases
 * was dead code: it fired only when the *active* size was `sx`, and the active
 * size always comes from measuring the window, which never returns it.
 *
 * Here `base` sits outside the size map, which is what it always was.
 *
 * ## Resolution searches outward
 *
 * Most systems cascade down — take the nearest defined size at or below the
 * active one. This searches outward in *both* directions, nearest first, and
 * that behaviour is preserved deliberately: a config that names only `xl` still
 * gives something at `xs`, so a value set for one size is never simply absent
 * somewhere else. A partial config always resolves to something rather than
 * silently falling through to a default the author did not think about.
 *
 * Order: the exact size, then outward by distance, then `base`, then the
 * fallback.
 */

import { SCREEN_SIZES, type ScreenSize } from './breakpoints';

/** A value per size. Every entry optional; none required. */
export type ScreenSizeMap<T> = Partial<Record<ScreenSize, T>>;

/**
 * A per-size value, plus a `base` that applies at every size.
 *
 * `base` is separate from the sizes so it cannot be mistaken for one, and so
 * the resolver does not have to exclude it from its search.
 */
export interface FlexConfig<T> extends ScreenSizeMap<T> {
  base?: T;
}

/** A value that may be given plainly or per size. */
export type Flexible<T> = T | FlexConfig<T>;

/**
 * True when this is a per-size config rather than a plain value.
 *
 * Structural, because a config is a plain object and a value may be anything.
 * A caller passing an object *as* the value has to wrap it in `{ base: … }` —
 * which is why `Flexible` is worth using only for primitives.
 */
export function isFlexConfig<T>(value: Flexible<T>): value is FlexConfig<T> {
  if (typeof value !== 'object' || value === null) return false;
  const keys = Object.keys(value);
  if (keys.length === 0) return false;
  return keys.every((key) => key === 'base' || (SCREEN_SIZES as readonly string[]).includes(key));
}

/**
 * The value for `size`, searching outward, then `base`, then `fallback`.
 */
export function resolveFlexConfig<T>(
  config: FlexConfig<T> | undefined,
  size: ScreenSize,
  fallback?: T,
): T | undefined {
  if (config === undefined) return fallback;

  const exact = config[size];
  if (exact !== undefined) return exact;

  const index = SCREEN_SIZES.indexOf(size);
  for (let distance = 1; distance < SCREEN_SIZES.length; distance += 1) {
    // Narrower first at equal distance: a value authored for a smaller screen
    // is likelier to survive being shown on a larger one than the reverse.
    const below = SCREEN_SIZES[index - distance];
    if (below !== undefined && config[below] !== undefined) return config[below];
    const above = SCREEN_SIZES[index + distance];
    if (above !== undefined && config[above] !== undefined) return config[above];
  }

  return config.base !== undefined ? config.base : fallback;
}

/** Resolve something that may or may not be per-size. */
export function resolveFlexible<T>(
  value: Flexible<T> | undefined,
  size: ScreenSize,
  fallback?: T,
): T | undefined {
  if (value === undefined) return fallback;
  if (!isFlexConfig(value)) return value;
  return resolveFlexConfig(value, size, fallback);
}

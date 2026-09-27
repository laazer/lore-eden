/**
 * State mirrored to `localStorage`, for UI that should survive a reload: a
 * collapsed panel, a remembered tab, a chosen sort.
 *
 * Every storage access is guarded. A private window, a blocked store, a quota
 * error, a missing `localStorage` or a stored value the parser rejects all fall
 * back to the default rather than throwing — this is a preference, and losing
 * one is never worth a crash.
 *
 * Generalized from the source's `usePersistedBoolean`, whose boolean was the
 * case that came up first rather than the shape of the problem. Values are JSON
 * by default; pass `serialize` and `parse` for any other encoding.
 *
 * Differs from the source in two ways, each with a test:
 *
 * - Changing `storageKey` reads the new key. The source kept the old key's value
 *   and its write effect then stored it under the new key, overwriting whatever
 *   the new key held.
 * - Mounting does not write. The source wrote the default on mount, so a user
 *   who never touched the setting had the default of that day frozen into
 *   storage, and a later change to the default never reached them. Only a set
 *   writes.
 *
 * `usePersistedBoolean` stays as a wrapper so the source can cut over without
 * rewriting call sites or stored values. One further difference there: a stored
 * value that is not a boolean reads as the default, where the source read it as
 * `false`.
 */

import { useCallback, useEffect, useRef, useState, type SetStateAction } from 'react';

export interface PersistedStateOptions<T> {
  /** Defaults to `JSON.stringify`. */
  serialize?: (value: T) => string;
  /** Defaults to `JSON.parse`. A throw reads as the default. */
  parse?: (raw: string) => T;
}

function readStored<T>(key: string, defaultValue: T, parse: (raw: string) => T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? defaultValue : parse(raw);
  } catch {
    return defaultValue;
  }
}

function writeStored<T>(key: string, value: T, serialize: (value: T) => string): void {
  try {
    localStorage.setItem(key, serialize(value));
  } catch {
    // silent-ok: quota, private mode, no storage, or a value the serializer
    // rejects; the state keeps working in memory, which is the documented contract.
  }
}

interface Slot<T> {
  key: string;
  value: T;
  /** Set by the caller since the key was last read, so storage needs the write. */
  dirty: boolean;
}

/**
 * `useState`, persisted under `storageKey`. The setter is stable and accepts an
 * updater, as `useState`'s does — which is why `T` cannot itself be a function,
 * and a function would not serialize anyway.
 */
export function usePersistedState<T>(
  storageKey: string,
  defaultValue: T,
  options: PersistedStateOptions<T> = {},
): [T, (next: SetStateAction<T>) => void] {
  const parse = options.parse ?? (JSON.parse as (raw: string) => T);
  const serialize = options.serialize ?? JSON.stringify;
  // Callers pass inline arrows; the write effect should use the latest without
  // re-running every render.
  const serializeRef = useRef(serialize);
  serializeRef.current = serialize;

  const [slot, setSlot] = useState<Slot<T>>(() => ({
    key: storageKey,
    value: readStored(storageKey, defaultValue, parse),
    dirty: false,
  }));

  let current = slot;
  if (slot.key !== storageKey) {
    // Derived during render rather than in an effect, so no frame shows the
    // old key's value under the new key.
    current = { key: storageKey, value: readStored(storageKey, defaultValue, parse), dirty: false };
    setSlot(current);
  }

  useEffect(() => {
    if (slot.dirty) writeStored(slot.key, slot.value, serializeRef.current);
  }, [slot]);

  const setValue = useCallback((next: SetStateAction<T>): void => {
    setSlot((prev) => ({
      key: prev.key,
      value: typeof next === 'function' ? (next as (prev: T) => T)(prev.value) : next,
      dirty: true,
    }));
  }, []);

  return [current.value, setValue];
}

const BOOLEAN_CODEC: PersistedStateOptions<boolean> = {
  serialize: (value) => (value ? '1' : '0'),
  parse: (raw) => {
    if (raw === '1' || raw === 'true') return true;
    if (raw === '0' || raw === 'false') return false;
    throw new Error(`not a stored boolean: ${raw}`);
  },
};

/**
 * `usePersistedState` for a boolean stored as `"1"` / `"0"`, the encoding the
 * source used, so values it already wrote read back unchanged. `"true"` and
 * `"false"` also read; anything else reads as the default rather than `false`.
 */
export function usePersistedBoolean(
  storageKey: string,
  defaultValue: boolean,
): [boolean, (next: SetStateAction<boolean>) => void] {
  return usePersistedState(storageKey, defaultValue, BOOLEAN_CODEC);
}

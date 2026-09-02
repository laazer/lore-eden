/**
 * A record whose keys are its change channels.
 *
 * Setting `title` emits on the channel named `"title"`, so a subscriber can
 * watch one field without being woken by every other write — and a wildcard
 * subscriber still hears all of them.
 */

import { Observable } from './Observable';

export class ObservableDict<T extends object> extends Observable<T> {
  readonly data: T;

  constructor(data?: T) {
    super();
    this.data = data ?? ({} as T);
    // The dictionary is what callers observe, not the wrapper. The base class
    // would otherwise default to observing `this`, and a subscriber reading
    // `observed.title` would find nothing there.
    this.setObservedData();
  }

  private setObservedData(): void {
    this.setObserved(this.data);
  }

  get<K extends keyof T>(key: K): T[K] {
    return this.data[key];
  }

  /** Write one field and announce it on that field's channel. */
  update<K extends keyof T>(key: K, value: T[K]): [K, T[K]] {
    this.data[key] = value;
    this.emitChanges([String(key)]);
    return [key, value];
  }

  /** Write several fields, announcing all of them in one emission. */
  updateAll(values: Partial<T>): void {
    const keys = Object.keys(values) as (keyof T)[];
    if (keys.length === 0) return;
    for (const key of keys) {
      const value = values[key];
      if (value !== undefined) this.data[key] = value;
    }
    // One emission naming every key, so a subscriber watching two of them
    // renders once rather than once per field.
    this.emitChanges(keys.map(String));
  }
}

export default ObservableDict;

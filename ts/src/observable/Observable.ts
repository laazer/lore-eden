/**
 * An observer registry with typed change channels.
 *
 * Not an `EventEmitter` clone, and the difference is the reason it exists: a
 * subscriber names the *kinds* of change it cares about, and gets called once
 * per emission no matter how many of those kinds an emission touched. A wildcard
 * channel, `'any'`, hears everything.
 *
 *     const doc = new Observable(state);
 *     const off = doc.observeChangeTypes(render, ['title', 'body']);
 *
 * `render` runs once when a single change reports both `title` and `body`, not
 * twice. An emitter that fanned out per channel would double-render, and the
 * caller would have to dedupe by hand — which is the work this does.
 *
 * Subscribing returns its own unsubscribe. A registry that instead required
 * handing the same function back to `off()` cannot unsubscribe an inline arrow,
 * which is what callers actually pass.
 */

import { randomId } from '../util/ids';

/** Called with the observed value, the kinds of change, and whatever context was staged. */
export type Observer<T = unknown, C = unknown> = (
  observed: T,
  changeTypes: string[],
  context: C | undefined,
) => void;

export type Unsubscribe = () => void;

/** The channel that hears every change. */
export const ANY_CHANGE = 'any';

export class Observable<T = unknown, C = unknown> {
  protected observers = new Map<string, Map<string, Observer<T, C>>>();
  protected contextBuffer?: C;
  private _observed: T;

  constructor(observed?: T, contextBase?: C) {
    // A subclass that models the thing being observed passes nothing and
    // observes itself.
    this._observed = observed ?? (this as unknown as T);
    this.contextBuffer = contextBase;
  }

  get observed(): T {
    return this._observed;
  }

  /** Stage context for the next emission. Cleared once delivered. */
  protected setContext(context: C | undefined): void {
    this.contextBuffer = context;
  }

  protected setObserved(next: T): void {
    this._observed = next;
    this.emitChanges([ANY_CHANGE]);
  }

  protected emitChanges(changeTypes: string[]): void {
    // Collected into one map keyed by subscription id, so a subscriber
    // listening on two of these channels is called once rather than twice.
    const due = new Map<string, Observer<T, C>>();
    for (const type of changeTypes) {
      const channel = this.observers.get(type);
      if (channel !== undefined) for (const [id, fn] of channel) due.set(id, fn);
    }
    if (!changeTypes.includes(ANY_CHANGE)) {
      const wildcard = this.observers.get(ANY_CHANGE);
      if (wildcard !== undefined) for (const [id, fn] of wildcard) due.set(id, fn);
    }

    const context = this.contextBuffer;
    // Cleared before delivery, not after: an observer that emits again during
    // its own callback would otherwise see this emission's context attached to
    // the next one.
    this.contextBuffer = undefined;
    for (const fn of due.values()) fn(this.observed, changeTypes, context);
  }

  /** Hear every change. */
  observe(onChange: Observer<T, C>): Unsubscribe {
    return this.observeChangeTypes(onChange, [ANY_CHANGE]);
  }

  observeChangeType(onChange: Observer<T, C>, changeType: string): Unsubscribe {
    return this.observeChangeTypes(onChange, [changeType]);
  }

  observeChangeTypes(onChange: Observer<T, C>, changeTypes: string[]): Unsubscribe {
    const id = randomId('obs_');
    for (const type of changeTypes) {
      const channel = this.observers.get(type) ?? new Map<string, Observer<T, C>>();
      channel.set(id, onChange);
      this.observers.set(type, channel);
    }
    return () => {
      for (const type of changeTypes) {
        const channel = this.observers.get(type);
        if (channel === undefined) continue;
        channel.delete(id);
        // Dropped when empty, so a long-lived observable that has seen many
        // short-lived change types does not accumulate empty maps forever.
        if (channel.size === 0) this.observers.delete(type);
      }
    };
  }

  /** How many subscriptions are live, across every channel. For tests and diagnostics. */
  get observerCount(): number {
    const ids = new Set<string>();
    for (const channel of this.observers.values()) for (const id of channel.keys()) ids.add(id);
    return ids.size;
  }
}

export default Observable;

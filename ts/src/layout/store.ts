/**
 * Writing an arrangement back, without losing an edit to the one before it.
 *
 * The surfaces do not persist anything themselves — the host owns its transport.
 * What the host cannot easily own is the *ordering*, because getting it wrong
 * destroys layouts in ways that look like nothing went wrong at all.
 *
 * Four properties are load-bearing here, and in the codebase this came from each
 * of them was a bug first:
 *
 * **The base layout is the newest one known, not one captured at click time.**
 * A body composed when the pointer went down reverts whatever the in-flight
 * write was saving, and a backend that replaces the layout wholesale accepts it
 * happily. So the edit is a *function* — given the current layout, return the
 * next one — and it is called when the queue reaches it, not when it is queued.
 *
 * **Writes to one layout are serialized.** Two edits composed against the same
 * base both save, and the second silently discards the first.
 *
 * **A write carries its own identity.** Nothing in here reads which layout it is
 * editing from an enclosing scope. A write issued against one view that lands
 * after the user opened another must not write the first view's record into the
 * second — which is what happens when the handler closes over the id.
 *
 * **A read older than a write must not land on top of it.** A refetch issued
 * before the write and resolving after it puts the pre-edit layout back, and the
 * revert is invisible: every layout involved is valid and nothing fails. It is
 * the *next* edit that does the damage, composing from that stale record. The
 * queue cannot cancel the host's reads, so it announces each landed write
 * through `onWritten` and the host cancels there.
 */

import type { ViewLayout } from './types';

/**
 * Given the newest layout, the layout to store.
 *
 * Throwing is a supported outcome — an edit that cannot be made (a split deeper
 * than the backend accepts, a node that has since been closed) rejects the
 * queued promise rather than storing something wrong.
 */
export type LayoutEdit = (layout: ViewLayout) => ViewLayout;

export interface LayoutStore {
  /**
   * The newest layout this client knows.
   *
   * Called once per write, immediately before the edit is applied — never
   * cached by the queue, because the point is to read it late.
   */
  read: () => ViewLayout;

  /** Persist, and resolve with whatever the backend stored. */
  write: (layout: ViewLayout) => Promise<ViewLayout>;

  /**
   * A write landed. The host uses this to put the stored record where `read`
   * will find it, and to cancel any read still in flight — see the note above
   * about stale reads landing on top of a write.
   */
  onWritten?: (stored: ViewLayout) => void;
}

/** Serializes edits to one layout. One queue per layout, not one per app. */
export class LayoutQueue {
  private tail: Promise<unknown> = Promise.resolve();

  constructor(private readonly store: LayoutStore) {}

  /**
   * Queue an edit. Resolves when it has been stored, rejects if it could not be.
   *
   * A rejected edit does not stop the ones behind it: one impossible edit is not
   * a reason to drop the rest of the session's work on the floor.
   */
  edit(edit: LayoutEdit): Promise<void> {
    const run = this.tail.then(
      () => this.apply(edit),
      () => this.apply(edit),
    );
    // The tail swallows rejection so the next edit still runs; the caller keeps
    // the real outcome through the promise it was handed.
    this.tail = run.catch(() => undefined);
    return run;
  }

  private async apply(edit: LayoutEdit): Promise<void> {
    const next = edit(this.store.read());
    const stored = await this.store.write(next);
    this.store.onWritten?.(stored);
  }

  /** Resolves when everything queued so far has settled. For tests and teardown. */
  async settled(): Promise<void> {
    await this.tail;
  }
}

export function createLayoutQueue(store: LayoutStore): LayoutQueue {
  return new LayoutQueue(store);
}

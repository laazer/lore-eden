/**
 * The write queue.
 *
 * Each of these was a bug before it was a property. They are asserted against an
 * in-memory store with no React Query, no HTTP and no framework of any kind —
 * which is the point: the ordering guarantees belong to the layout, not to one
 * host's data layer.
 */

import { describe, expect, it, vi } from 'vitest';

import { createLayoutQueue, type LayoutStore, type ViewLayout } from '../src/layout';

/** A store that records the order things happened in, with controllable latency. */
function memoryStore(options: { latency?: (n: number) => number } = {}) {
  let current: ViewLayout = { items: [] };
  const written: ViewLayout[] = [];
  const readsAt: ViewLayout[] = [];
  let writes = 0;

  const store: LayoutStore = {
    read: () => {
      readsAt.push(current);
      return current;
    },
    write: async (layout) => {
      const index = writes++;
      const delay = options.latency?.(index) ?? 0;
      if (delay > 0) await new Promise((resolve) => setTimeout(resolve, delay));
      written.push(layout);
      return layout;
    },
    onWritten: (stored) => {
      current = stored;
    },
  };

  return { store, get current() { return current; }, written, readsAt };
}

const append = (name: string) => (layout: ViewLayout): ViewLayout => ({
  ...layout,
  items: [...((layout.items as string[]) ?? []), name],
});

describe('ordering', () => {
  it('stores an edit', async () => {
    const { store, written } = memoryStore();
    const queue = createLayoutQueue(store);

    await queue.edit(append('a'));

    expect(written).toEqual([{ items: ['a'] }]);
  });

  it('serializes edits so neither is lost', async () => {
    // Two edits composed against the same base both save, and the second
    // silently discards the first — on a backend that replaces the layout
    // wholesale, which is the normal case.
    const { store, written } = memoryStore({ latency: (n) => (n === 0 ? 20 : 0) });
    const queue = createLayoutQueue(store);

    const first = queue.edit(append('a'));
    const second = queue.edit(append('b'));
    await Promise.all([first, second]);

    expect(written).toEqual([{ items: ['a'] }, { items: ['a', 'b'] }]);
  });

  it('composes each edit against the newest layout, not one captured at queue time', async () => {
    const { store, written } = memoryStore({ latency: (n) => (n === 0 ? 20 : 0) });
    const queue = createLayoutQueue(store);

    // Both edits are queued before the first write lands. The second must still
    // see the first's result.
    const seen: ViewLayout[] = [];
    const record = (layout: ViewLayout): ViewLayout => {
      seen.push(layout);
      return append('b')(layout);
    };
    await Promise.all([queue.edit(append('a')), queue.edit(record)]);

    expect(seen).toEqual([{ items: ['a'] }]);
    expect(written[1]).toEqual({ items: ['a', 'b'] });
  });

  it('reads once per write, and late', async () => {
    const { store, readsAt } = memoryStore({ latency: () => 5 });
    const queue = createLayoutQueue(store);

    queue.edit(append('a'));
    queue.edit(append('b'));
    await queue.settled();

    expect(readsAt).toHaveLength(2);
    // The second read happened after the first write landed, which is what makes
    // "newest layout" true rather than aspirational.
    expect(readsAt[1]).toEqual({ items: ['a'] });
  });

  it('announces each landed write so a host can settle its own cache', async () => {
    const onWritten = vi.fn();
    const store: LayoutStore = {
      read: () => ({}),
      write: async (layout) => layout,
      onWritten,
    };

    await createLayoutQueue(store).edit(append('a'));

    expect(onWritten).toHaveBeenCalledWith({ items: ['a'] });
  });
});

describe('failure', () => {
  it('rejects the caller when an edit cannot be made', async () => {
    const { store } = memoryStore();
    const queue = createLayoutQueue(store);

    await expect(
      queue.edit(() => {
        throw new Error('split too deep');
      }),
    ).rejects.toThrow('split too deep');
  });

  it('rejects the caller when the write fails', async () => {
    const store: LayoutStore = {
      read: () => ({}),
      write: async () => {
        throw new Error('rejected by the server');
      },
    };

    await expect(createLayoutQueue(store).edit(append('a'))).rejects.toThrow('rejected');
  });

  it('keeps running the edits behind a failed one', async () => {
    // One impossible edit is not a reason to drop the rest of the session's
    // work on the floor.
    const { store, written } = memoryStore();
    const queue = createLayoutQueue(store);

    const failed = queue.edit(() => {
      throw new Error('nope');
    });
    const after = queue.edit(append('b'));

    await expect(failed).rejects.toThrow('nope');
    await after;
    expect(written).toEqual([{ items: ['b'] }]);
  });

  it('does not let a failed edit poison the queue for later ones', async () => {
    const store: LayoutStore = {
      read: () => ({}),
      write: async () => {
        throw new Error('down');
      },
    };
    const queue = createLayoutQueue(store);

    await expect(queue.edit(append('a'))).rejects.toThrow();
    await expect(queue.edit(append('b'))).rejects.toThrow();
    // Both attempted, rather than the second being dropped silently.
  });
});

describe('independence', () => {
  it('keeps two layouts' + ' out of each other’s way', async () => {
    // A write carries its own identity: one queue per layout, so a write issued
    // against one cannot land in the other's record.
    const a = memoryStore({ latency: () => 10 });
    const b = memoryStore();
    const queueA = createLayoutQueue(a.store);
    const queueB = createLayoutQueue(b.store);

    await Promise.all([queueA.edit(append('a')), queueB.edit(append('b'))]);

    expect(a.current).toEqual({ items: ['a'] });
    expect(b.current).toEqual({ items: ['b'] });
  });
});

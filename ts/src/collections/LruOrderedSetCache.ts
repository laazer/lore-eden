/**
 * A bounded set that remembers two orders at once.
 *
 * Insertion order is what you read back — a recently-used list reordered by
 * every touch is unreadable as a list of open tabs or recent files. Recency is
 * what decides who gets dropped when the cache is full. Keeping both is the
 * point: a plain LRU gives you eviction but shuffles the display, and an
 * insertion-ordered set gives you a stable display but no eviction policy.
 *
 *     const tabs = new LruOrderedSetCache<string>(3);
 *     tabs.push('a'); tabs.push('b'); tabs.push('c');
 *     tabs.push('a');            // touch — 'a' stays where it is in the list
 *     tabs.push('d');            // evicts 'b', the least recently used
 *     tabs.asList();             // ['d', 'c', 'a']
 */

export class LruOrderedSetCache<T> {
  /** Most-recently-used first. Decides eviction. */
  private recency: T[];
  /** Most-recently-inserted first. What `asList` returns. */
  private entries: T[];
  private limit: number | undefined;

  constructor(maxSize?: number, entries?: T[]) {
    // Copied, and copied *separately*. The source assigned the caller's array
    // to both fields, so they aliased each other and aliased the caller's — a
    // single push then appended the item twice to one shared array, and every
    // later eviction read an order that was never true.
    this.entries = entries === undefined ? [] : [...entries];
    this.recency = entries === undefined ? [] : [...entries];
    this.limit = maxSize;
    this.trim();
  }

  get length(): number {
    return this.entries.length;
  }

  get maxSize(): number | undefined {
    return this.limit;
  }

  /** Evict down to `size` and adopt it. Returns what was dropped, oldest-used first. */
  updateMaxSize(size: number | undefined): T[] {
    // The source evicted to the new size but never stored it, so the next push
    // enforced the old bound — a resize that took effect once and then undid
    // itself.
    this.limit = size;
    return this.trim();
  }

  private trim(): T[] {
    const dropped: T[] = [];
    while (this.limit !== undefined && this.limit >= 0 && this.length > this.limit) {
      const evicted = this.pop();
      if (evicted === undefined) break;
      dropped.push(evicted);
    }
    return dropped;
  }

  /** Insertion order, newest first. A copy, so a caller cannot edit the cache through it. */
  asList(): T[] {
    return [...this.entries];
  }

  /** Recency order, most recently used first. */
  asRecencyList(): T[] {
    return [...this.recency];
  }

  /**
   * Add an item, or mark an existing one as used.
   *
   * A touch does not move the item in the insertion list — that is what makes
   * the list stable to read while eviction still follows use.
   */
  push(item: T): number {
    if (this.contains(item)) {
      this.recency = [item, ...this.recency.filter((entry) => entry !== item)];
      return this.length;
    }
    if (this.limit !== undefined && this.length >= this.limit) this.pop();
    this.entries.unshift(item);
    this.recency.unshift(item);
    return this.length;
  }

  remove(item: T): number {
    this.recency = this.recency.filter((entry) => entry !== item);
    this.entries = this.entries.filter((entry) => entry !== item);
    return this.length;
  }

  /** Drop the least recently used item and return it. */
  pop(): T | undefined {
    const evicted = this.recency.pop();
    if (evicted !== undefined) this.entries = this.entries.filter((entry) => entry !== evicted);
    return evicted;
  }

  contains(item: T): boolean {
    return this.recency.includes(item);
  }

  clear(): void {
    this.entries = [];
    this.recency = [];
  }
}

export default LruOrderedSetCache;

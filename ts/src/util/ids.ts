/**
 * Identifiers: random ones, and ones that sort by when they were made.
 *
 * The sortable kind is the interesting one. A random id tells you nothing about
 * order, so a list of them needs a separate timestamp column to sort on; an id
 * whose leading digits are the creation time sorts correctly as a plain string,
 * which is what an index, a filename, or a `Map` iteration order will do anyway.
 */

const HEX = '0123456789abcdef';

/** Random hex, from `crypto` when there is one and `Math.random` when there is not. */
function randomHex(length: number): string {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  const webCrypto = globalThis.crypto;
  if (webCrypto?.getRandomValues !== undefined) {
    webCrypto.getRandomValues(bytes);
  } else {
    // Not a security boundary — these ids label observers and DOM nodes. A
    // caller needing unguessable ids should not be reaching for this module.
    for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256);
  }
  let out = '';
  for (const value of bytes) out += HEX[value >> 4] + HEX[value & 0x0f];
  return out.slice(0, length);
}

/**
 * A random id, optionally prefixed.
 *
 * `length` counts the random part only. The source counted the prefix too and
 * sliced the whole string to 16, so a longer prefix silently bought less
 * randomness — `prefixedUuid('observer_')` had 7 hex digits of entropy.
 */
export function randomId(prefix = '', length = 12): string {
  return `${prefix}${randomHex(length)}`;
}

/** Group a 32-character hex string into the 8-4-4-4-12 shape. */
export function formatAsUuid(hex: string): string {
  const padded = hex.padEnd(32, '0').slice(0, 32);
  return [
    padded.slice(0, 8),
    padded.slice(8, 12),
    padded.slice(12, 16),
    padded.slice(16, 20),
    padded.slice(20),
  ].join('-');
}

/**
 * Milliseconds since the epoch, in hex, zero-padded.
 *
 * The padding is the whole point. An unpadded timestamp changes width as it
 * crosses a power of sixteen — `1e12` is ten hex digits and `2e12` is eleven —
 * and a shorter string sorts *before* a longer one only by accident of its
 * first character. So ids made either side of such a boundary sort wrongly
 * against each other, which is the one thing a sortable id must not do. Twelve
 * digits is 48 bits, enough until the year 10889.
 */
const TIMESTAMP_HEX_DIGITS = 12;

/**
 * An id whose leading digits are the creation time, so ids sort chronologically.
 *
 * Not an RFC 9562 UUIDv6 — it carries no version or variant bits, and nothing
 * should treat it as a UUID beyond its shape. The source called it `uuidV6`,
 * which invites exactly that. It is a sortable id, and this is what it is
 * called.
 *
 * `now` is injectable so a caller can make ids deterministic in a test.
 */
export function sortableId(options: { grouped?: boolean; now?: number } = {}): string {
  const { grouped = true, now = Date.now() } = options;
  // `.toString(16)`, not `.toString("16")` — the source passed a *string*
  // radix, which works only because it is coerced back to a number.
  const prefix = BigInt(now).toString(16).padStart(TIMESTAMP_HEX_DIGITS, '0');
  const body = (prefix + randomHex(32)).slice(0, 32);
  return grouped ? formatAsUuid(body) : body;
}

/** A short id with a prefix, capped at `maxLength` overall. For DOM ids and keys. */
export function shortId(prefix = '', maxLength = 16): string {
  return (prefix + randomHex(maxLength)).slice(0, maxLength);
}

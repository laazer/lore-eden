/**
 * Hex colour text: sanitizing, normalizing, and the clipboard in both
 * directions.
 *
 * From blobert's asset editor (`utils/clipboardHex.ts`). A colour is stored as
 * six lowercase hex digits with no `#`; the clipboard carries `#rrggbb`, the
 * common interchange form.
 *
 * `normalizeHexForBuildOption` is `normalizeHex` here — "build option" is the
 * source's own noun, and a kit that names its callers' domain is one that
 * cannot be reused.
 *
 * One change, with a test that fails against the source: reading text that was
 * not a hex code. The source's last resort stripped *every* non-hex character
 * from the text and accepted whatever six digits were left, so a pasted
 * `12, 34, 56` became `#123456` and `12:34:56` did too — a colour nobody
 * chose, applied without a word. The recovery path now takes a run of exactly
 * six hex digits standing on its own, which still finds `"ff0000"` inside
 * quotes or a line of CSS and no longer invents one from scattered digits.
 */

const SIX_HEX = /^[0-9a-fA-F]{6}$/;
const HASHED_HEX = /#([0-9a-fA-F]{6})/;
const STANDALONE_HEX = /(?:^|[^0-9a-fA-F])([0-9a-fA-F]{6})(?![0-9a-fA-F])/;

/**
 * What `<input type="color">` shows when the value is incomplete or invalid.
 * The native picker cannot be empty — it needs *some* `#rrggbb` — so this is a
 * value for that element, not a style, and it is a neutral grey rather than
 * black so that an empty value does not read as a chosen one.
 */
const NATIVE_PICKER_FALLBACK = '#6b6b6b';

/** True for exactly six hex digits, no `#`. */
export function isHex6(raw: unknown): raw is string {
  return typeof raw === 'string' && SIX_HEX.test(raw);
}

/**
 * Format stored 6-char hex (no `#`) for `<input type="color">`, which requires
 * `#rrggbb`. Falls back to a neutral grey when the value is incomplete or
 * invalid.
 */
export function hexForColorInput(raw: string): string {
  if (typeof raw !== 'string') return NATIVE_PICKER_FALLBACK;
  const h = raw.replace(/^#/, '').trim();
  return SIX_HEX.test(h) ? `#${h.toLowerCase()}` : NATIVE_PICKER_FALLBACK;
}

/**
 * Strip `#` and non-hex characters; keep at most 6 hex digits, lowercase. Only
 * a full 6-digit result is kept; partial or corrupted input clears, so the
 * caller does not persist invalid hex.
 */
export function sanitizeHex(raw: string): string {
  const t = raw.replace(/^#/, '').replace(/[^0-9a-fA-F]/g, '').slice(0, 6).toLowerCase();
  return t.length === 6 ? t : '';
}

/**
 * Parse text that should *be* a hex colour — `#rrggbb` or `rrggbb`, surrounding
 * whitespace allowed — into 6 lowercase digits, or null. Strict: anything else
 * in the string rejects it.
 */
export function normalizeHex(raw: string): string | null {
  if (typeof raw !== 'string') return null;
  const t = raw.trim();
  const body = t.startsWith('#') ? t.slice(1) : t;
  return SIX_HEX.test(body) ? body.toLowerCase() : null;
}

/**
 * Recover a hex colour from text that *contains* one — a line of CSS, a quoted
 * JSON value, a chat message. Strict parse first, then a `#rrggbb` anywhere
 * (so `#ff0000ff` yields its RGB), then a standalone run of six hex digits.
 */
export function findHexInText(text: string): string | null {
  if (typeof text !== 'string') return null;
  const strict = normalizeHex(text);
  if (strict !== null) return strict;
  const hashed = HASHED_HEX.exec(text);
  if (hashed?.[1] !== undefined) return hashed[1].toLowerCase();
  const standalone = STANDALONE_HEX.exec(text);
  return standalone?.[1] !== undefined ? standalone[1].toLowerCase() : null;
}

/** Write `#rrggbb` to the clipboard. Resolves false when it could not. */
export async function copyHexToClipboard(hexWithOrWithoutHash: string): Promise<boolean> {
  const body = normalizeHex(hexWithOrWithoutHash);
  if (body === null) return false;
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(`#${body}`);
      return true;
    }
  } catch {
    // A denied or missing clipboard is an answer, not an error: the caller is
    // told `false` and decides whether to say so.
    return false;
  }
  return false;
}

/** Read clipboard text and return 6-char hex (no `#`), or null. */
export async function readHexFromClipboard(): Promise<string | null> {
  try {
    if (typeof navigator === 'undefined' || !navigator.clipboard?.readText) return null;
    return findHexInText(await navigator.clipboard.readText());
  } catch {
    // Permission denied reads as "nothing usable on the clipboard".
    return null;
  }
}

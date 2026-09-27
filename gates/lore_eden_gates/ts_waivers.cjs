/**
 * The waiver contract the TypeScript gates share: a marker on the line, and a
 * reason of substance after it. The marker alone is not a waiver.
 *
 *     } catch { /* silent-ok: probe only; the poll re-checks in 2s *\/ }
 *     {/* ux-ok: backdrop; Esc closes and the dialog traps focus *\/}
 *
 * A reason may sit in the comment block directly above the span it waives.
 * Which lines are comment is decided by a forward pass, not by testing each
 * line's prefix: the continuation lines of a `/* … *\/` block start with
 * ordinary prose, so the prefix test stopped at the last line of the block and
 * never reached the marker on its first. loregarden's UX gate had learned that
 * and its silent-failure gate had not — a correctly written multi-line waiver
 * above a `.catch(() => {})` was ignored and the discard reported anyway. One
 * implementation, so the two cannot disagree again.
 */

/** Shorter than this and the "reason" is a shrug, not a reason. */
const MIN_WAIVER_REASON_CHARS = 12;

/**
 * 1-based line numbers that are entirely comment.
 *
 * In JSX the only way to comment above an element is `{/* … *\/}`, so a line
 * opening with `{/*` counts as a comment line too.
 */
function commentLines(lines) {
  const inComment = new Set();
  let open = false;
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const trimmed = line.trim();
    if (open) {
      inComment.add(i + 1);
      if (line.includes("*/")) open = false;
      continue;
    }
    if (trimmed.startsWith("/*") || trimmed.startsWith("{/*")) {
      inComment.add(i + 1);
      // A block that opens and closes on one line leaves nothing open.
      if (!line.includes("*/", line.indexOf("/*") + 2)) open = true;
      continue;
    }
    if (trimmed.startsWith("//")) inComment.add(i + 1);
  }
  return inComment;
}

/** Whether any of `start..end` is a line this change touched. */
function spanTouched(added, start, end) {
  for (let i = start; i <= end; i += 1) if (added.has(i)) return true;
  return false;
}

/**
 * The contract for one marker (`silent-ok:`, `ux-ok:`) over one file, given
 * its lines and the parser's `comments`.
 *
 * `waived(start, end)` — a substantive waiver covers the span, on its lines or
 * in the comment block directly above it.
 * `shortWaiverFindings(added, filePath, advice)` — every touched line whose
 * waiver is too thin, reported once each. A bare marker is itself the finding:
 * it would otherwise read as a decision somebody made.
 */
function waiverContract(marker, lines, comments) {
  const commentOnly = commentLines(lines);

  // Only comment text can carry a waiver. Scanning raw lines read a marker
  // inside a string — `const MARKER = "silent-ok:";` — as a waiver with the
  // reason `";`, and a string long enough would have waived the span it sat in.
  const textByLine = new Map();
  for (const comment of comments || []) {
    comment.value.split("\n").forEach((text, offset) => {
      const lineno = comment.loc.start.line + offset;
      if (!textByLine.has(lineno)) textByLine.set(lineno, []);
      textByLine.get(lineno).push(text);
    });
  }

  const reasonOn = (lineno) => {
    for (const text of textByLine.get(lineno) || []) {
      const at = text.indexOf(marker);
      if (at !== -1) {
        return text
          .slice(at + marker.length)
          .replace(/(?:\*\/|\}|\s)*$/, "")
          .trim();
      }
    }
    return null;
  };

  /** Walk up over the comment block above, where a waiver for the span lives. */
  const firstLine = (start) => {
    let first = start;
    while (first > 1 && commentOnly.has(first - 1)) first -= 1;
    return first;
  };

  const findInSpan = (start, end, wanted) => {
    for (let i = firstLine(start); i <= Math.min(end, lines.length); i += 1) {
      const reason = reasonOn(i);
      if (reason !== null && wanted(reason)) return i;
    }
    return null;
  };

  const waived = (start, end) =>
    findInSpan(start, end, (reason) => reason.length >= MIN_WAIVER_REASON_CHARS) !== null;

  const shortWaiverFindings = (added, filePath, advice) => {
    const reported = new Set();
    for (const lineno of [...added].sort((a, b) => a - b)) {
      const short = findInSpan(lineno, lineno, (reason) => reason.length < MIN_WAIVER_REASON_CHARS);
      // Each touched line under one comment block walks up to the same marker;
      // it is one finding, not one per line beneath it.
      if (short !== null) reported.add(short);
    }
    return [...reported].map(
      (lineno) =>
        `${filePath}:${lineno}: '${marker}' with no substantive reason — ${advice}, ` +
        `in at least ${MIN_WAIVER_REASON_CHARS} characters`,
    );
  };

  return { waived, shortWaiverFindings };
}

module.exports = { MIN_WAIVER_REASON_CHARS, spanTouched, waiverContract };

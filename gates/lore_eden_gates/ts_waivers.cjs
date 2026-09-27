/**
 * The waiver contract the TypeScript gates share: a marker in a comment, and a
 * reason of substance after it. The marker alone is not a waiver.
 *
 *     } catch { /* silent-ok: probe only; the poll re-checks in 2s *\/ }
 *     {/* ux-ok: backdrop; Esc closes and the dialog traps focus *\/}
 *
 * **Which construct a waiver covers.** A marker waives exactly one construct —
 * one of the things the gate grades (a catch clause, a `.catch(...)` call, a
 * `<button>`, …): the innermost of those that
 *
 *   - contains the comment (a catch body, a `.catch` handler, a JSX child), or
 *   - starts on the comment's line, or
 *   - starts on the line directly below the comment block the marker is in.
 *
 * "Innermost" is what keeps a waiver where it was written. Accepting a marker
 * anywhere in a span let the reason written for an inner, retried probe also
 * waive the catch around it:
 *
 *     } catch {
 *       try { run(); } catch { /* silent-ok: inner probe is retried every 2s *\/ }
 *     }
 *
 * and the outer catch — which nothing retries — passed on the inner's word.
 *
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

/** Whether position `a` is at or before position `b` (`{ line, column }`). */
const atOrBefore = (a, b) => a.line < b.line || (a.line === b.line && a.column <= b.column);

/** Whether `outer`'s source span encloses `inner`'s (both carry `loc`). */
const encloses = (outer, inner) =>
  atOrBefore(outer.loc.start, inner.loc.start) && atOrBefore(inner.loc.end, outer.loc.end);

/**
 * The contract for one marker (`silent-ok:`, `ux-ok:`) over one file, given
 * its lines, the parser's `comments`, and every node the gate grades — the
 * constructs a waiver can belong to. They are needed up front: which construct
 * owns a marker depends on which others are nested around and inside it.
 *
 * `waived(node)` — a substantive waiver belongs to that construct (header).
 * `shortWaiverFindings(added, filePath, advice)` — every touched line whose
 * waiver is too thin, reported once each. A bare marker is itself the finding:
 * it would otherwise read as a decision somebody made.
 */
function waiverContract(marker, lines, comments, constructs) {
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

  const reasonIn = (text) => {
    const at = text.indexOf(marker);
    if (at === -1) return null;
    return text
      .slice(at + marker.length)
      .replace(/(?:\*\/|\}|\s)*$/, "")
      .trim();
  };

  const reasonOn = (lineno) => {
    for (const text of textByLine.get(lineno) || []) {
      const reason = reasonIn(text);
      if (reason !== null) return reason;
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

  /** Whether every line from `from` up to (not including) `to` is comment. */
  const commentBlockBetween = (from, to) => {
    for (let i = from; i < to; i += 1) if (!commentOnly.has(i)) return false;
    return true;
  };

  /** The one construct a marker on `lineno`, in `comment`, waives — or null. */
  const ownerOf = (comment, lineno) => {
    const candidates = (constructs || []).filter((node) => {
      const start = node.loc.start.line;
      return (
        encloses(node, comment) ||
        start === lineno ||
        (start > lineno && commentBlockBetween(lineno, start))
      );
    });
    // Innermost: a candidate with another candidate inside it is not the one
    // the comment was written for.
    const innermost = candidates.filter(
      (node) => !candidates.some((other) => other !== node && encloses(node, other)),
    );
    if (innermost.length === 0) return null;
    // Siblings on one line: the one the comment sits inside, else the nearest.
    const inside = innermost.find((node) => encloses(node, comment));
    if (inside) return inside;
    return innermost.reduce((best, node) =>
      atOrBefore(best.loc.start, node.loc.start) ? node : best,
    );
  };

  const owned = new Set();
  for (const comment of comments || []) {
    comment.value.split("\n").forEach((text, offset) => {
      const reason = reasonIn(text);
      if (reason === null || reason.length < MIN_WAIVER_REASON_CHARS) return;
      const owner = ownerOf(comment, comment.loc.start.line + offset);
      if (owner) owned.add(owner);
    });
  }

  const waived = (node) => owned.has(node);

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

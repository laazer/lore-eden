#!/usr/bin/env node
/**
 * Keep frontend failures visible to the person using the app — the TypeScript
 * counterpart of py_silent_except_check.py.
 *
 * A `catch {}` compiles, an empty list renders, and the user reads "no data"
 * where the truth was "the request failed". oxlint cannot do this job: `no-empty`
 * is not enabled, and `no-floating-promises` / `no-misused-promises` are
 * type-aware `@typescript-eslint` rules oxlint does not implement.
 *
 * Checks (diff-scoped, on .ts/.tsx/.cjs under the detected TypeScript root):
 *   1. catch block that neither rethrows, nor records state, nor surfaces
 *   2. catch whose only statement is a console.* call
 *   3. `.catch(() => {})` / `.catch(console.error)` — a rejection discarded
 *   4. Promise.all/allSettled over raw fetch with no `.ok` check in the file:
 *      a 500 resolves, so "0 failed" is reported when everything failed
 *
 * A suppression that is genuinely fine says so on the line, in a comment, with
 * a reason:
 *
 *     } catch { /* silent-ok: probe only; the poll re-checks in 2s *\/ }
 *
 * The marker alone is not enough — the reason must be substantive.
 *
 * Extracted from loregarden's `.lefthook/scripts/`. Parser resolution, scope
 * and file reading come from ts_gate_harness.cjs; the waiver contract from
 * ts_waivers.cjs. Where this differs from the source it is a defect fixed, and
 * each has a test in gates/tests/test_ts_no_silent_failures_gate.py.
 */

const {
  isTestFile,
  parseGradedFile,
  readSource,
  runGate,
  walk,
} = require("./ts_gate_harness.cjs");
const { spanTouched, waiverContract } = require("./ts_waivers.cjs");

const ALLOW_MARKER = "silent-ok:";

// Names that mean the failure reached a human or a rendered surface. These are
// loregarden's, where the gate was written; a repo with its own names satisfies
// the gate by recording state, rethrowing, or waiving.
const SURFACING_CALLEES = new Set([
  "pushToast",
  "toastActionFailed",
  "toastWarning",
  "describeError",
  "errorDetail",
  "pushInboxNotification",
  "captureException",
]);

function calleeName(node) {
  if (!node || node.type !== "CallExpression") return null;
  const callee = node.callee;
  if (!callee) return null;
  if (callee.type === "Identifier") return callee.name;
  if (callee.type === "MemberExpression" && callee.property) {
    return callee.property.name ?? null;
  }
  return null;
}

function isConsoleCall(node) {
  return (
    node &&
    node.type === "CallExpression" &&
    node.callee &&
    node.callee.type === "MemberExpression" &&
    node.callee.object &&
    node.callee.object.type === "Identifier" &&
    node.callee.object.name === "console"
  );
}

/** Methods that put a value into a collection someone reads later. */
const COLLECTING_METHODS = new Set(["push", "unshift", "add", "set"]);

function isCollectingCall(node) {
  const callee = node.callee;
  return (
    callee &&
    callee.type === "MemberExpression" &&
    !callee.computed &&
    callee.property &&
    COLLECTING_METHODS.has(callee.property.name)
  );
}

/** What a catch block does about the error it caught. */
function observeCatch(handler) {
  const seen = {
    rethrows: false,
    consoleOnly: false,
    surfaces: false,
    recordsState: false,
    statements: 0,
  };
  let nonConsoleCalls = 0;
  let consoleCalls = 0;

  walk(handler.body, (node) => {
    if (node.type === "ThrowStatement") seen.rethrows = true;
    else if (node.type === "CallExpression") {
      if (isConsoleCall(node)) {
        consoleCalls += 1;
        // Pruned: whatever is computed *for* the console goes to the console.
        // The source kept walking, so `console.error(describeError(err))` found
        // `describeError`, counted it as surfacing, and passed a catch whose
        // only effect is a line no user reads.
        return false;
      }
      nonConsoleCalls += 1;
      const name = calleeName(node);
      if (name && SURFACING_CALLEES.has(name)) seen.surfaces = true;
      // A setter is how a component records a failure it will render:
      // setError(...), setBootError(...), setSaveError(...).
      if (name && /^set[A-Z]/.test(name)) seen.recordsState = true;
      // Collecting the failure to report it later: `attempts.push(...)`,
      // `failures.add(...)`. The source read that as "neither rethrows nor
      // surfaces", which fails the resolution chain in ts_gate_harness.cjs —
      // each attempt's error is pushed, and all of them are thrown together.
      if (isCollectingCall(node)) seen.recordsState = true;
    } else if (node.type === "AssignmentExpression") {
      // Assigning outward is recording. Declaring a local is not: the source
      // counted every `VariableDeclarator`, so
      // `catch (err) { const message = String(err); }` — a message built and
      // dropped — passed as "records state".
      seen.recordsState = true;
    } else if (node.type === "ReturnStatement" && node.argument) {
      // Returning something built from the error hands it to the caller.
      seen.recordsState = true;
    }
    return true;
  });

  seen.statements = (handler.body.body || []).length;
  seen.consoleOnly = consoleCalls > 0 && nonConsoleCalls === 0 && !seen.rethrows;
  return seen;
}

function catchErrors(filePath, ast, added, waivers) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CatchClause") return true;
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(start, end)) return true;

    const seen = observeCatch(node);
    if (seen.rethrows || seen.surfaces || seen.recordsState) return true;

    if (seen.statements === 0) {
      found.push(
        `${filePath}:${start}: empty catch block — the failure is invisible to the user; ` +
          `surface it, record it in state you render, rethrow, ` +
          `or waive with '${ALLOW_MARKER} <reason>'`,
      );
    } else if (seen.consoleOnly) {
      found.push(
        `${filePath}:${start}: catch only writes to the console, which no user sees; ` +
          `surface it or record it in state you render`,
      );
    } else {
      found.push(
        `${filePath}:${start}: catch neither rethrows nor surfaces the failure; ` +
          `surface it, record it in rendered state, rethrow, or waive with ` +
          `'${ALLOW_MARKER} <reason>'`,
      );
    }
    return true;
  });
  return found;
}

/** `() => {}`, `() => undefined`, `() => null`, `() => void 0` — one discard, four spellings. */
function isDiscardingHandler(arg) {
  const isFn = arg.type === "ArrowFunctionExpression" || arg.type === "FunctionExpression";
  if (!isFn) return false;
  const body = arg.body;
  if (body.type === "BlockStatement") return body.body.length === 0;
  return (
    (body.type === "Identifier" && body.name === "undefined") ||
    (body.type === "Literal" && (body.value === null || body.value === false)) ||
    (body.type === "UnaryExpression" && body.operator === "void")
  );
}

/** `.catch(() => {})` and `.catch(console.error)` — a rejection thrown away. */
function discardedRejectionErrors(filePath, ast, added, waivers) {
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return true;
    if (calleeName(node) !== "catch" || node.arguments.length !== 1) return true;
    const arg = node.arguments[0];
    const line = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, line, end)) return true;
    if (waivers.waived(line, end)) return true;

    const isConsoleRef =
      arg.type === "MemberExpression" &&
      arg.object &&
      arg.object.type === "Identifier" &&
      arg.object.name === "console";

    if (isDiscardingHandler(arg)) {
      found.push(
        `${filePath}:${line}: the .catch handler discards the rejection (() => {} / ` +
          `() => undefined); surface it, record it in rendered state, ` +
          `or waive with '${ALLOW_MARKER} <reason>'`,
      );
    } else if (isConsoleRef) {
      found.push(
        `${filePath}:${line}: .catch(console.*) reports where no user looks; surface it instead`,
      );
    }
    return true;
  });
  return found;
}

/** Whether anything in the file reads `.ok` — `res.ok`, `r?.ok`, `!response.ok`. */
function readsOk(ast) {
  let found = false;
  walk(ast, (node) => {
    if (node.type === "MemberExpression" && node.property && node.property.name === "ok") {
      found = true;
    }
    return !found;
  });
  return found;
}

/**
 * `Promise.allSettled(ids.map((id) => fetch(...)))` then counting `rejected`.
 * fetch resolves for 4xx/5xx, so a bulk action reports zero failures when the
 * server rejected every one of them.
 *
 * "No `.ok` check in the file", as the source documented it — not "inside the
 * `Promise.all` call", as the source implemented it. The usual correct shape
 * checks after the await:
 *
 *     const responses = await Promise.all(ids.map((id) => fetch(url(id))));
 *     const failed = responses.filter((r) => !r.ok).length;
 *
 * and the call-local search reported exactly that as a violation.
 */
function settledFetchErrors(filePath, ast, added, waivers) {
  if (readsOk(ast)) return [];
  const found = [];
  walk(ast, (node) => {
    if (node.type !== "CallExpression") return true;
    const name = calleeName(node);
    if (name !== "allSettled" && name !== "all") return true;
    const callee = node.callee;
    if (
      !callee ||
      callee.type !== "MemberExpression" ||
      !callee.object ||
      callee.object.name !== "Promise"
    ) {
      return true;
    }
    const start = node.loc.start.line;
    const end = node.loc.end.line;
    if (!spanTouched(added, start, end)) return true;
    if (waivers.waived(start, end)) return true;

    let callsFetch = false;
    walk(node, (inner) => {
      if (inner.type === "CallExpression" && calleeName(inner) === "fetch") callsFetch = true;
      return !callsFetch;
    });
    if (callsFetch) {
      found.push(
        `${filePath}:${start}: Promise.${name} over raw fetch without checking response.ok — ` +
          `fetch resolves for 4xx/5xx, so server-side failures are counted as successes; ` +
          `check .ok per response`,
      );
    }
    return true;
  });
  return found;
}

runGate({
  title: "silent failure",
  suffixes: [".ts", ".tsx", ".cjs"],
  grade: ({ repoRoot }, { files, touched }) => {
    const errors = [];
    for (const filePath of files) {
      if (isTestFile(filePath)) continue;
      const content = readSource(filePath, repoRoot);
      const ast = parseGradedFile(filePath, content);
      const lines = content.split("\n");
      const { added } = touched(filePath, lines.length);
      const waivers = waiverContract(ALLOW_MARKER, lines, ast.comments);
      errors.push(...catchErrors(filePath, ast, added, waivers));
      errors.push(...discardedRejectionErrors(filePath, ast, added, waivers));
      errors.push(...settledFetchErrors(filePath, ast, added, waivers));
      errors.push(
        ...waivers.shortWaiverFindings(
          added,
          filePath,
          "say why the failure is safe to swallow (transient, retryable, or expected " +
            "with a surfaced alternate path)",
        ),
      );
    }
    return errors;
  },
});

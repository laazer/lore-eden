#!/usr/bin/env node
/**
 * TypeScript/React organization guardrails for staged client files.
 *
 * Checks (on staged .ts/.tsx files):
 *   1. File size limit — only when this commit *grows* an already-over-limit file
 *   2. No direct fetch/axios calls in .tsx — only on newly added lines
 *   3. Within-file duplicate function bodies (>= MIN_DUPLICATE_BODY_LINES)
 *   4. Cross-codebase DRY against the rest of client/src
 *   5. Barrel index.ts size limit (growth-only)
 *   6. No inline `instanceof Error` ternary — only on newly added lines
 *
 * Diff-scoped size/API rules match server py_organization_check.py so existing
 * debt (e.g. Dashboard.tsx) does not block unrelated edits.
 */

const fs = require("fs");
const path = require("path");
const {
  isTestFile,
  parseFile,
  parseGradedFile,
  readSource,
  runGate,
  tsSourceRoot,
} = require("./ts_gate_harness.cjs");

const MAX_FILE_LINES = 1200;
const MAX_TSX_FILE_LINES = 1200;
const MAX_INDEX_LINES = 80;
const MIN_DUPLICATE_BODY_LINES = 8;

const API_CALL_PATTERNS = [/\bfetch\s*\(/, /\baxios\s*\./, /\baxios\s*\(/];

const ALLOW_INSTANCEOF = "ts-org: allow-instanceof";

function isComponentFile(filePath) {
  return filePath.endsWith(".tsx");
}

function isIndexFile(filePath) {
  return path.basename(filePath) === "index.ts" || path.basename(filePath) === "index.tsx";
}

function normalizeBody(node, lines) {
  if (!node.body || !node.body.loc) return [];
  const start = node.body.loc.start.line - 1;
  const end = node.body.loc.end.line;
  return lines
    .slice(start, end)
    .map((l) => l.trim().replace(/\s+/g, " "))
    .filter((l) => l && !l.startsWith("//") && !l.startsWith("*"));
}

function extractFunctions(ast, lines) {
  const functions = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    const isFn =
      node.type === "FunctionDeclaration" ||
      node.type === "FunctionExpression" ||
      node.type === "ArrowFunctionExpression";
    if (isFn && node.body && node.body.type === "BlockStatement" && node.loc) {
      const name =
        node.id?.name ||
        (node.parent?.type === "VariableDeclarator" ? node.parent.id?.name : null) ||
        "<anonymous>";
      const bodyLines = normalizeBody(node, lines);
      if (bodyLines.length >= MIN_DUPLICATE_BODY_LINES) {
        functions.push({ name, line: node.loc.start.line, key: bodyLines.join("\n") });
      }
    }
    for (const key of Object.keys(node)) {
      if (key === "parent") continue;
      const child = node[key];
      if (Array.isArray(child)) {
        child.forEach((c) => {
          if (c && typeof c === "object" && c.type) {
            c.parent = node;
            visit(c);
          }
        });
      } else if (child && typeof child === "object" && child.type) {
        child.parent = node;
        visit(child);
      }
    }
  }
  visit(ast);
  return functions;
}

function isErrorInstanceofTest(node) {
  if (!node) return false;
  if (node.type === "UnaryExpression" && node.operator === "!") {
    return isErrorInstanceofTest(node.argument);
  }
  return (
    node.type === "BinaryExpression" &&
    node.operator === "instanceof" &&
    node.right?.type === "Identifier" &&
    node.right.name === "Error"
  );
}

// Names a repo might give its "turn an unknown throw into a message" helper.
const ERROR_HELPER_NAMES = ["describeError", "errorMessage", "formatError", "toErrorMessage"];

let errorHelperCache;

/**
 * Find this repo's own error-narrowing helper, so the message points somewhere
 * that exists in the workspace being checked rather than naming loregarden's.
 * Returns `{ name, file }`, or null when the repo has no such helper yet — then
 * the advice is to extract one.
 */
function findErrorHelper(repoRoot) {
  if (errorHelperCache !== undefined) return errorHelperCache;
  const pattern = new RegExp(`export\\s+(?:async\\s+)?function\\s+(${ERROR_HELPER_NAMES.join("|")})\\b`);
  errorHelperCache = null;
  const root = tsSourceRoot(repoRoot);
  const stack = [root];
  while (stack.length > 0 && errorHelperCache === null) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      // silent-ok: this search only picks which helper the advice names; a
      // directory it cannot list leaves the advice at "extract one", still true.
      continue;
    }
    for (const entry of entries) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name !== "node_modules" && entry.name !== "__tests__") stack.push(full);
      } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name) && !isTestFile(full)) {
        let match;
        try {
          match = pattern.exec(fs.readFileSync(full, "utf8"));
        } catch {
          // silent-ok: as above — a file it cannot read only means the advice
          // names no helper; nothing graded is passed or failed by it.
          continue;
        }
        if (match) {
          errorHelperCache = { name: match[1], file: path.relative(repoRoot, full) };
          break;
        }
      }
    }
  }
  return errorHelperCache;
}

/**
 * `err instanceof Error ? err.message : "Failed to …"` — the hand-rolled unknown
 * narrowing, copy-pasted ~45 times in loregarden alone. A shared helper says the
 * same thing, and recovers whatever richer error type the ternary throws away.
 *
 * Only the ternary form. A real type guard inside a helper (`if (!(e instanceof
 * Error)) return …`) is how narrowing is supposed to work and stays legal — which
 * is also why the helper's own file is exempt.
 */
function errorNarrowingErrors(filePath, ast, lines, added, repoRoot) {
  if (isTestFile(filePath)) return [];
  const helper = findErrorHelper(repoRoot);
  if (helper && path.resolve(repoRoot, helper.file) === path.resolve(filePath)) return [];
  const advice = helper
    ? `use \`${helper.name}(error, "fallback")\` from ${helper.file}`
    : `extract one shared helper (\`describeError(error, fallback)\`) instead of repeating the ternary`;
  const found = [];
  function visit(node) {
    if (!node || typeof node !== "object") return;
    if (node.type === "ConditionalExpression" && isErrorInstanceofTest(node.test) && node.loc) {
      const lineno = node.loc.start.line;
      const text = lines[lineno - 1] ?? "";
      if (added.has(lineno) && !text.includes(ALLOW_INSTANCEOF)) {
        found.push(`${filePath}:${lineno}: inline \`instanceof Error\` ternary; ${advice}`);
      }
    }
    for (const key of Object.keys(node)) {
      if (key === "parent") continue;
      const child = node[key];
      if (Array.isArray(child)) {
        child.forEach((c) => c && typeof c === "object" && c.type && visit(c));
      } else if (child && typeof child === "object" && child.type) {
        visit(child);
      }
    }
  }
  visit(ast);
  return found;
}

function checkFile(filePath, content, lines, { added, netGrowing, repoRoot }) {
  const fileErrors = [];
  const lineCount = lines.length;

  if (isIndexFile(filePath)) {
    if (lineCount > MAX_INDEX_LINES && netGrowing) {
      fileErrors.push(
        `${filePath}: index file is ${lineCount} lines (max ${MAX_INDEX_LINES}); keep barrel files minimal (re-exports only)`,
      );
    }
  } else {
    const maxLines = isComponentFile(filePath) ? MAX_TSX_FILE_LINES : MAX_FILE_LINES;
    if (lineCount > maxLines && netGrowing) {
      fileErrors.push(
        `${filePath}: file is ${lineCount} lines (max ${maxLines}); split into smaller modules`,
      );
    }
  }

  if (isComponentFile(filePath) && !isTestFile(filePath)) {
    lines.forEach((line, idx) => {
      const lineno = idx + 1;
      if (!added.has(lineno)) return;
      for (const pattern of API_CALL_PATTERNS) {
        if (pattern.test(line)) {
          fileErrors.push(
            `${filePath}:${lineno}: direct API call in component; move to a custom hook (useXxx) or service module`,
          );
          break;
        }
      }
    });
  }

  const ast = parseGradedFile(filePath, content);
  {
    fileErrors.push(...errorNarrowingErrors(filePath, ast, lines, added, repoRoot));
    const functions = extractFunctions(ast, lines);
    const seen = new Map();
    for (const fn of functions) {
      if (seen.has(fn.key)) {
        const first = seen.get(fn.key);
        fileErrors.push(
          `${filePath}: duplicated function bodies detected (${first.name}@${first.line}, ${fn.name}@${fn.line}); extract shared helper to keep DRY`,
        );
      } else {
        seen.set(fn.key, fn);
      }
    }
  }

  return fileErrors;
}

function buildCatalog(changedSet, repoRoot) {
  const catalog = new Map();
  const clientSrc = tsSourceRoot(repoRoot);
  if (!fs.existsSync(clientSrc)) return catalog;

  function walk(dir) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === "node_modules" || entry.name === "__tests__") continue;
        walk(full);
      } else if (entry.isFile() && /\.(ts|tsx)$/.test(entry.name)) {
        if (changedSet.has(full)) continue;
        if (isTestFile(full)) continue;
        try {
          const content = fs.readFileSync(full, "utf8");
          const lines = content.split("\n");
          const ast = parseFile(full, content);
          if (!ast) continue;
          for (const fn of extractFunctions(ast, lines)) {
            if (!catalog.has(fn.key)) catalog.set(fn.key, []);
            catalog.get(fn.key).push({ file: full, name: fn.name, line: fn.line });
          }
        } catch (err) {
          // silent-ok: this is a command-line gate, so stderr is where its user
          // reads; the note prints beside the report it qualifies.
          // Background for the DRY catalog, not a file this run grades, so it
          // cannot make the run report a violation clean. It can still weaken a
          // DRY match, so it is reported rather than dropped.
          console.error(
            `note: catalog skipped an unreadable file, so DRY matches may be incomplete: ${full}: ${err.message}`,
          );
        }
      }
    }
  }
  walk(clientSrc);
  return catalog;
}

function crossDryErrors(filePath, content, lines, catalog) {
  const fileErrors = [];
  const ast = parseGradedFile(filePath, content);
  for (const fn of extractFunctions(ast, lines)) {
    const matches = catalog.get(fn.key);
    if (!matches || matches.length === 0) continue;
    const refs = matches
      .slice(0, 3)
      .map((m) => `${path.relative(process.cwd(), m.file)}:${m.name}@${m.line}`)
      .join(", ");
    fileErrors.push(
      `${filePath}:${fn.line}: function \`${fn.name}\` duplicates existing code (${refs}); reuse existing logic to keep DRY`,
    );
  }
  return fileErrors;
}

/** Suffixes this gate grades. `.cjs` so the gates' own scripts are graded too. */
const SUFFIXES = [".ts", ".tsx", ".cjs"];

runGate({
  title: "TypeScript organization",
  suffixes: SUFFIXES,
  grade: ({ repoRoot }, { files, touched }) => {
    const errors = [];
    const changedSet = new Set(files.map((a) => path.resolve(a)));
    const catalog = buildCatalog(changedSet, repoRoot);
    for (const filePath of files) {
      const content = readSource(filePath, repoRoot);
      const lines = content.split("\n");
      const { added, addedCount, deletedCount } = touched(filePath, lines.length);
      const netGrowing = addedCount > deletedCount;
      errors.push(...checkFile(filePath, content, lines, { added, netGrowing, repoRoot }));
      if (!isTestFile(filePath)) {
        errors.push(...crossDryErrors(filePath, content, lines, catalog));
      }
    }
    return errors;
  },
});

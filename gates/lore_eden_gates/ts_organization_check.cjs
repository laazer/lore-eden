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
const { createRequire } = require("module");
const { spawnSync } = require("child_process");

/**
 * Resolve the TypeScript parser, preferring this package's own dependency.
 *
 * This used to resolve from a sibling `client/node_modules` by relative path,
 * which worked only while the gate lived inside the repo it graded. Once the
 * gate is installed elsewhere — the whole point of a shared library — that path
 * points at nothing, or worse, at a *different* repo's parser version.
 *
 * Order:
 *   1. this package's own node_modules (normal case once installed);
 *   2. the graded repo's node_modules, so a repo that already has the parser
 *      need not have a second copy installed for the gate;
 *   3. throw. There is no fourth option: a gate that cannot parse cannot report
 *      a file clean, so it must fail loudly rather than skip.
 *
 * Lazy, because the graded repo is not known until argv is parsed.
 */
let cachedParse = null;
/** The repo being graded, set once argv is parsed; the parser fallback needs it. */
let gradedRepoRoot = null;
function loadParse(repoRoot) {
  if (cachedParse) return cachedParse;

  const attempts = [];
  try {
    cachedParse = require("@typescript-eslint/typescript-estree").parse;
    return cachedParse;
  } catch (err) {
    attempts.push(`  - ${__dirname} (this gate's own dependencies): ${err.code || err.message}`);
  }

  if (repoRoot) {
    for (const dir of [repoRoot, path.join(repoRoot, "client")]) {
      const manifest = path.join(dir, "package.json");
      try {
        cachedParse = createRequire(manifest)("@typescript-eslint/typescript-estree").parse;
        return cachedParse;
      } catch (err) {
        attempts.push(`  - ${dir}: ${err.code || err.message}`);
      }
    }
  }

  throw new Error(
    "cannot load @typescript-eslint/typescript-estree; tried:\n" +
      attempts.join("\n") +
      "\nInstall this gate package's dependencies (npm ci in its directory), " +
      "or add the parser to the repo being checked."
  );
}

const MAX_FILE_LINES = 1200;
const MAX_TSX_FILE_LINES = 1200;
const MAX_INDEX_LINES = 80;
const MIN_DUPLICATE_BODY_LINES = 8;

const API_CALL_PATTERNS = [/\bfetch\s*\(/, /\baxios\s*\./, /\baxios\s*\(/];

const ALLOW_INSTANCEOF = "ts-org: allow-instanceof";

const errors = [];

function isComponentFile(filePath) {
  return filePath.endsWith(".tsx");
}

function isIndexFile(filePath) {
  return path.basename(filePath) === "index.ts" || path.basename(filePath) === "index.tsx";
}

function isTestFile(filePath) {
  return (
    filePath.includes("/__tests__/") ||
    filePath.includes(".test.") ||
    filePath.includes(".spec.")
  );
}

function parseFile(filePath, content) {
  // `loadParse` throws when the parser is missing entirely — that is a failure
  // to run, not a parse failure, and it must not be swallowed into `null` and
  // read as "nothing found here".
  const parse = loadParse(gradedRepoRoot);
  // JSX by extension, which is TypeScript's own rule rather than a preference:
  // in a `.ts` file `<T>` opens a type parameter, and in a `.tsx` file it opens
  // a JSX element. The two readings are mutually exclusive, which is why the
  // language splits them by suffix.
  //
  // This passed `jsx: true` for everything. On a `.ts` file with a generic —
  // `useQuery<DataPage<Record>>(...)` — the parser reports "Unexpected token.
  // Did you mean `{'>'}`?", `parseGradedFile` turns that into an
  // UnexaminableFileError, and the gate refuses the whole run. Correct
  // behaviour on a wrong premise: the file parses fine, under the rule its
  // extension asks for.
  //
  // Invisible here, because nothing in this package has a generic in a `.ts`
  // file. It surfaced the first time the gate was pointed at a real consumer,
  // which is the entire argument for cutting one over.
  const jsx = filePath.endsWith(".tsx") || filePath.endsWith(".jsx");
  try {
    return parse(content, { jsx, loc: true, range: false, comment: false });
  } catch {
    return null;
  }
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

/**
 * This run could not examine something it was asked to grade.
 *
 * **The invariant of every gate here: a gate may not report success over
 * anything it did not actually read.** Both ways of failing it live under this
 * one type and leave through one handler, so a third way inherits the
 * behaviour instead of becoming the next silent pass. Mirrors
 * `UnexaminableError` in precommit_git_diff.py.
 */
class UnexaminableError extends Error {}

/**
 * A file this run was told to grade but could not read or parse — missing (a
 * cone sparse-checkout, a `skip-worktree` entry whose file was removed),
 * unreadable, or unparseable. `existsSync` + `continue` treated every one of
 * those exactly like a file that graded clean: `examined 1 file(s)` +
 * `checks passed.` + exit 0, over a violation sitting in the commit.
 * Mirrors `UnexaminableFileError` in precommit_git_diff.py.
 */
class UnexaminableFileError extends UnexaminableError {}

/**
 * A source file larger than this is not graded. Mirrors `MAX_SOURCE_BYTES` in
 * precommit_git_diff.py: no hand-written module comes near it, and a path that
 * does is a device, a stream, or a mistake.
 */
const MAX_SOURCE_BYTES = 8 * 1024 * 1024;

/**
 * The text of a file this gate is about to grade, or a loud failure. Never
 * null: the caller cannot tell "nothing wrong here" from "I never read it",
 * and it took the first reading every time.
 *
 * The same rule as `read_source_text` in precommit_git_diff.py, in the same
 * order: resolve, refuse a non-regular target, refuse a target that leaves the
 * repository, cap the size, then read. A bare `readFileSync` follows a
 * committed `src/x.ts -> /dev/zero` until the host gives out, and reads and
 * reports on a file the repository does not contain — one gate refusing that
 * while its mirror does not is a rule that exists only in one language.
 *
 * The boundary is crossed only when the *listed* path is inside `repoRoot` and
 * its target is not; a path the caller named outright scoped the run itself.
 * Both sides are real-pathed, or a checkout behind a symlinked prefix (macOS
 * `/var` -> `/private/var`) has the check silently skipped for every file.
 */
function readSource(filePath, repoRoot) {
  let real;
  let stat;
  try {
    real = fs.realpathSync(filePath);
    stat = fs.statSync(real);
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not read it, so it cannot be reported clean (${err.message})`,
    );
  }
  if (!stat.isFile()) {
    throw new UnexaminableFileError(
      `${filePath}: not a regular file (resolves to ${real}), so it cannot be graded and cannot be reported clean`,
    );
  }
  if (repoRoot) {
    const root = fs.realpathSync(repoRoot);
    // The listed path with its *directory* resolved but not the file itself —
    // `locatedPath` in precommit_git_diff.py, and for the same reason.
    const located = path.join(fs.realpathSync(path.dirname(filePath)), path.basename(filePath));
    // Component containment, not `startsWith`: `/w/repo` is a string prefix of
    // `/w/repo-vendor/x.ts`, which is where vendored trees sit.
    const inside = (candidate) => candidate === root || candidate.startsWith(root + path.sep);
    if (inside(located) && !inside(real)) {
      throw new UnexaminableFileError(
        `${filePath}: resolves to ${real}, outside the repository at ${root}, so it cannot be graded and cannot be reported clean`,
      );
    }
  }
  if (stat.size > MAX_SOURCE_BYTES) {
    throw new UnexaminableFileError(
      `${filePath}: ${stat.size} bytes exceeds the ${MAX_SOURCE_BYTES}-byte grading limit (resolves to ${real}), so it cannot be graded and cannot be reported clean`,
    );
  }
  try {
    return fs.readFileSync(real, "utf8");
  } catch (err) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not read it, so it cannot be reported clean (${err.message})`,
    );
  }
}

/** The AST of a file this gate grades. A file it cannot parse is not a file it cleared. */
function parseGradedFile(filePath, content) {
  const ast = parseFile(filePath, content);
  if (ast === null) {
    throw new UnexaminableFileError(
      `${filePath}: this run could not parse it, so it cannot be reported clean`,
    );
  }
  return ast;
}

/** The base a gate falls back to when its caller named none. */
const DEFAULT_BASE_REF = "main";

/**
 * Ask `precommit_git_diff.py` what this run should examine.
 *
 * This used to be ~560 lines of hand-ported Python living in this file: the
 * error classes, git-path decoding, env scrubbing, ref validation, scope
 * resolution, untracked discovery, submodule announcement and diff-suppression
 * detection. None of it was TypeScript-specific, and the two copies drifting
 * apart was itself a source of defects — each fix having to be written twice,
 * in two languages, by whoever remembered the other existed. The copy in this
 * file was already a version behind: it still asked "did the diff emit a header
 * for this path", which a `diff=<driver>` printing three header lines walks
 * straight through.
 *
 * The gate already shells out to git repeatedly. One more subprocess buys a
 * single implementation of scope policy, so a scope fix now lands in Python and
 * both languages get it.
 *
 * What stays on this side is the part that is genuinely TypeScript's: which
 * suffixes to grade and which source root to confine discovery to. Those are
 * passed *in*, so the file count is still computed after this gate's own filter
 * — by the same code that counts for the Python gates.
 *
 * A bare `python3`, deliberately: it is what the installed lefthook block runs
 * and what every Python gate here runs under. On an interpreter older than 3.10
 * the resolver refuses by name with nothing on stdout, which arrives below as
 * an unexaminable run carrying that message — not as a pass.
 */
function resolveGateScope({ label, repoRoot, diffScope, baseRef, files }) {
  const emitted = spawnSync(
    "python3",
    [
      path.join(__dirname, "precommit_git_diff.py"),
      "--emit-scope-json",
      "--repo",
      repoRoot,
      "--scope",
      diffScope,
      "--base",
      baseRef,
      "--label",
      label,
      "--suffix",
      ".ts",
      "--suffix",
      ".tsx",
      "--suffix",
      ".cjs",
      "--select-root",
      tsSourceRoot(repoRoot),
      ...files.map((f) => path.resolve(f)),
    ],
    { encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
  );
  if (emitted.error) {
    throw new UnexaminableError(`could not run the scope resolver: ${emitted.error.message}`);
  }
  let payload;
  try {
    payload = JSON.parse(emitted.stdout);
  } catch (parseError) {
    // Anything that is not JSON means the resolver did not get far enough to
    // answer — a missing interpreter, one too old, an import failure, a crash.
    // None of those are "nothing to examine", so none of them may become a pass.
    throw new UnexaminableError(
      `the scope resolver produced no usable answer (exit ${emitted.status}): ` +
        `${(emitted.stderr || emitted.stdout || "").trim().split("\n").slice(-3).join(" ")}`,
    );
  }
  // Printed here rather than by the resolver: stdout there is the JSON channel,
  // and these lines have to appear in this gate's own order.
  for (const notice of payload.notices || []) console.log(notice);
  if (payload.error) throw new UnexaminableError(payload.error);

  // Both sides real-pathed, and the file's *parent* only — `locatedPath` in
  // precommit_git_diff.py, whose relpath keys these maps. A checkout reached
  // through a symlinked prefix (macOS `/tmp` -> `/private/tmp`, every agent
  // worktree under a linked home) otherwise produces `../../private/tmp/...`,
  // which matches no key: every lookup below misses, the touched-line set comes
  // back empty, and the gate prints a credible file count and a pass. Resolving
  // the file itself instead would follow a symlinked source out of the tree and
  // lose it the same way.
  const rootReal = fs.realpathSync(repoRoot);
  const relOf = (filePath) => {
    const abs = path.resolve(filePath);
    const located = path.join(fs.realpathSync(path.dirname(abs)), path.basename(abs));
    return path.relative(rootReal, located);
  };
  const untracked = new Set(payload.untracked || []);
  const undiffable = new Set(payload.undiffable || []);
  const additions = payload.additions || {};
  const counts = payload.counts || {};

  const touched = (filePath, lineCount) => {
    const rel = relOf(filePath);
    // Untracked, or changed in a way git would not describe: there is no
    // smaller honest answer than the whole file, and the empty set is what let
    // `-diff` pass everything.
    if (untracked.has(rel) || undiffable.has(rel)) return wholeFile(lineCount);
    const [addedCount = 0, deletedCount = 0] = counts[rel] || [];
    return { added: new Set(additions[rel] || []), addedCount, deletedCount };
  };

  return { scope: payload.scope, files: payload.files, touched };
}

function wholeFile(lineCount) {
  const added = new Set();
  for (let i = 1; i <= lineCount; i += 1) added.add(i);
  return { added, addedCount: lineCount, deletedCount: 0 };
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

/**
 * Where this repo keeps its TypeScript. loregarden uses client/src; other
 * workspaces put it at src/ or app/. Detected, not hardcoded, because these
 * checks run against every workspace the control plane drives.
 *
 * The *project* directory, not the source directory inside it: `ts`, not
 * `ts/src`. Discovery is confined to whatever this returns, and confining it to
 * `ts/src` left `ts/tests/` ungraded — sixteen files, including every test in
 * the package. The hook never noticed, because lefthook passes staged paths
 * explicitly and an explicit list is not narrowed; CI's `--scope branch` run
 * has no explicit list, so it was the one that went blind.
 *
 * What the confinement is actually for is not grading a repo-root `vite.config`
 * or a `scripts/` directory by rules written for application code, and the
 * project directory still excludes those.
 */
function tsSourceRoot(repoRoot) {
  for (const candidate of ["client", "ts", "frontend", "app", "src"]) {
    const full = path.resolve(repoRoot, candidate);
    if (fs.existsSync(full) && fs.statSync(full).isDirectory()) return full;
  }
  return path.resolve(repoRoot);
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

function parseArgv(argv) {
  const files = [];
  let repoArg = null;
  let diffScope = "staged";
  let baseRef = DEFAULT_BASE_REF;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) repoArg = argv[(i += 1)];
    else if (argv[i] === "--scope" && argv[i + 1]) diffScope = argv[(i += 1)];
    else if (argv[i] === "--base" && argv[i + 1]) baseRef = argv[(i += 1)];
    else if (/\.(ts|tsx|cjs)$/.test(argv[i])) files.push(argv[i]);
  }
  // Real-pathed, and that is load-bearing rather than tidy. The scope resolver
  // answers with paths under the *resolved* root, while everything derived from
  // this one — `tsSourceRoot`, the catalog walk — is derived from the string the
  // caller passed. Behind a symlinked prefix (`/tmp` -> `/private/tmp`, an agent
  // worktree under a linked home) the two never compare equal, so
  // `buildCatalog`'s `changedSet.has(full)` guard missed every graded file, put
  // it in the DRY catalog, and reported it as duplicating itself:
  //
  //   ChatComposer.tsx:19: function `ChatComposer` duplicates existing code
  //     (../../../../var/folders/.../ChatComposer.tsx:ChatComposer@19)
  //
  // Same class as `relOf` below and as `read_source_text`'s resolved-prefix
  // check: one path, two spellings, compared as strings.
  const repoRoot = fs.realpathSync(repoArg ? path.resolve(repoArg) : process.cwd());
  const label = diffScope === "staged" && !repoArg ? "pre-commit" : "gate";
  return { files, repoRoot, diffScope, baseRef, label };
}

function run({ files, repoRoot, diffScope, baseRef, label }) {
  const { files: args, touched } = resolveGateScope({
    label,
    repoRoot,
    diffScope,
    baseRef,
    files,
  });
  if (args.length === 0) {
    return 0;
  }

  const changedSet = new Set(args.map((a) => path.resolve(a)));
  const catalog = buildCatalog(changedSet, repoRoot);

  for (const filePath of args) {
    const content = readSource(filePath, repoRoot);
    const lines = content.split("\n");
    const { added, addedCount, deletedCount } = touched(filePath, lines.length);
    const netGrowing = addedCount > deletedCount;
    errors.push(...checkFile(filePath, content, lines, { added, netGrowing, repoRoot }));
    if (!isTestFile(filePath)) {
      errors.push(...crossDryErrors(filePath, content, lines, catalog));
    }
  }

  if (errors.length > 0) {
    console.error(`${label}: TypeScript organization check failed:`);
    for (const err of errors) {
      console.error(` - ${err}`);
    }
    return 1;
  }

  console.log(`${label}: TypeScript organization checks passed.`);
  return 0;
}

const invocation = parseArgv(process.argv.slice(2));
gradedRepoRoot = invocation.repoRoot;
try {
  process.exit(run(invocation));
} catch (err) {
  if (!(err instanceof UnexaminableError)) throw err;
  // One handler for the one invariant: a scope this run could not resolve, and
  // a file it could not read, are both things it did not examine.
  console.error(`${invocation.label}: cannot determine what to examine: ${err.message}`);
  process.exit(1);
}

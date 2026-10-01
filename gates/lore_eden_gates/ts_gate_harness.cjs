/**
 * What every TypeScript gate here shares: finding a parser, deciding what to
 * examine, reading a file it may grade, and leaving by the right exit.
 *
 * The JS counterpart of `precommit_git_diff.py`, and deliberately thin: scope
 * policy itself is not reimplemented here, it is asked of that module through
 * `--emit-scope-json`. What lives here is only the part a second language
 * cannot avoid owning — loading a parser, reading and parsing a file, and
 * printing the result in the gate's own voice.
 *
 * It exists because the TypeScript gates arrived from loregarden each carrying
 * their own copy of this: `ts_organization_check.cjs` had its own, and the two
 * gates after it required a `ts_git_diff.cjs` that re-parsed `git diff` by
 * hand, coerced an unknown scope to `staged`, skipped any file it could not
 * parse, and resolved its parser from a sibling `client/` by relative path. Two
 * copies of diff logic drift — that is what #42 had to reconcile — so there is
 * one, and each gate keeps only its rules.
 */

const fs = require("fs");
const path = require("path");
const { createRequire } = require("module");
const { spawnSync } = require("child_process");

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
 * Resolve the TypeScript parser, preferring this package's own dependency.
 *
 * This used to resolve from a sibling `client/node_modules` by relative path,
 * which worked only while the gate lived inside the repo it graded. Once the
 * gate is installed elsewhere — the whole point of a shared library — that path
 * points at nothing, or worse, at a *different* repo's parser version.
 *
 * Order:
 *   1. this package's own node_modules (normal case once installed);
 *   2. the same package in the primary checkout, when this one sits in a linked
 *      git worktree — see `primaryCopyOfThisPackage`;
 *   3. the graded repo's node_modules, so a repo that already has the parser
 *      need not have a second copy installed for the gate;
 *   4. throw. There is no fifth option: a gate that cannot parse cannot report
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

  const primaryCopy = primaryCopyOfThisPackage(attempts);
  if (primaryCopy) {
    try {
      cachedParse = createRequire(path.join(primaryCopy, "package.json"))(
        "@typescript-eslint/typescript-estree",
      ).parse;
      return cachedParse;
    } catch (err) {
      attempts.push(`  - ${primaryCopy} (the primary checkout's copy of this package): ${err.code || err.message}`);
    }
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
      "or add the parser to the repo being checked.",
  );
}

/**
 * This gate package's directory in the primary checkout, when this copy sits in
 * a linked git worktree; otherwise null.
 *
 * A worktree is created without `gates/node_modules`, and git hooks are shared
 * across worktrees — so before `scripts/bootstrap-worktree.sh` has run there,
 * every TypeScript commit was refused for want of a parser the primary checkout
 * already has. `gate-python.sh` (#61) falls back to the primary's venv for the same
 * reason. The parser there is the one this package's lockfile pins, because it
 * is the same package.
 *
 * Read from the worktree's `.git` file rather than asked of `git`: scope policy
 * is the only git this harness consults, and only through the resolver. A
 * linked worktree's `.git` is a file reading `gitdir: <admin dir>`, and that
 * admin dir's `commondir` names the shared `.git`, whose parent is the primary
 * checkout. Both paths may be relative — to the worktree, and to the admin dir.
 * A `.git` directory is the primary itself; a `gitdir` with no `commondir` is a
 * submodule. Neither has a primary to fall back to, and neither is a failure.
 *
 * Anything else that goes wrong is recorded in `attempts`, so the refusal says
 * why this step offered nothing.
 */
function primaryCopyOfThisPackage(attempts) {
  const packageDir = path.dirname(__dirname);
  let checkout = packageDir;
  while (!fs.existsSync(path.join(checkout, ".git"))) {
    const parent = path.dirname(checkout);
    if (parent === checkout) return null;
    checkout = parent;
  }
  const dotGit = path.join(checkout, ".git");
  try {
    if (fs.statSync(dotGit).isDirectory()) return null;
    const pointer = /^gitdir:\s*(.+?)\s*$/m.exec(fs.readFileSync(dotGit, "utf8"));
    if (!pointer) {
      attempts.push(`  - ${dotGit}: names no gitdir, so the primary checkout is unknown`);
      return null;
    }
    const adminDir = path.resolve(checkout, pointer[1]);
    const commondirFile = path.join(adminDir, "commondir");
    if (!fs.existsSync(commondirFile)) return null;
    const commonDir = path.resolve(adminDir, fs.readFileSync(commondirFile, "utf8").trim());
    return path.join(path.dirname(commonDir), path.relative(checkout, packageDir));
  } catch (err) {
    attempts.push(`  - ${dotGit}: could not find the primary checkout from it (${err.code || err.message})`);
    return null;
  }
}

/**
 * The AST of one file, or null when it does not parse.
 *
 * Null is for background reads only — a file the run looks at for context and
 * does not grade. A graded file goes through `parseGradedFile`, which refuses.
 */
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
  // Did you mean `{'>'}`?", and the file is refused. Correct behaviour on a
  // wrong premise: the file parses fine, under the rule its extension asks for.
  const jsx = filePath.endsWith(".tsx") || filePath.endsWith(".jsx");
  try {
    // Comments are kept because a waiver is one: a marker counts only inside a
  // comment, never inside a string that happens to contain it.
  return parse(content, { jsx, loc: true, range: false, comment: true });
  } catch {
    // silent-ok: null is this function's documented answer for "does not parse"; a graded file is refused by parseGradedFile
    return null;
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

function isTestFile(filePath) {
  return (
    filePath.includes("/__tests__/") ||
    filePath.includes(".test.") ||
    filePath.includes(".spec.")
  );
}

/** Every line of a file, as the touched-line set of a change git cannot narrow. */
function wholeFile(lineCount) {
  const added = new Set();
  for (let i = 1; i <= lineCount; i += 1) added.add(i);
  return { added, addedCount: lineCount, deletedCount: 0 };
}

/** The base a gate falls back to when its caller named none. */
const DEFAULT_BASE_REF = "main";

/**
 * Ask `precommit_git_diff.py` what this run should examine.
 *
 * This used to be ~560 lines of hand-ported Python living in the organization
 * gate: the error classes, git-path decoding, env scrubbing, ref validation,
 * scope resolution, untracked discovery, submodule announcement and
 * diff-suppression detection. None of it was TypeScript-specific, and the two
 * copies drifting apart was itself a source of defects — each fix having to be
 * written twice, in two languages, by whoever remembered the other existed.
 *
 * What stays on this side is the part that is genuinely each gate's own: which
 * suffixes it grades and which source root to confine discovery to. Those are
 * passed *in*, so the file count is still computed after the gate's own filter
 * — by the same code that counts for the Python gates.
 *
 * A bare `python3`, deliberately: it is what the installed lefthook block runs
 * and what every Python gate here runs under. On an interpreter older than 3.10
 * the resolver refuses by name with nothing on stdout, which arrives below as
 * an unexaminable run carrying that message — not as a pass.
 */
function resolveGateScope({ label, repoRoot, diffScope, baseRef, files, suffixes }) {
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
      ...suffixes.flatMap((suffix) => ["--suffix", suffix]),
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
  } catch {
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

/**
 * The `--repo` / `--scope` / `--base` / positional-files shape every gate here
 * accepts. A positional is a file only when it carries one of `suffixes`.
 *
 * The scope is passed through as written, never coerced. loregarden's
 * `ts_git_diff.cjs` turned anything unrecognised into `staged`, so
 * `--scope wrokTree` graded an empty index and exited 0. The resolver refuses
 * an unknown scope by name, and that refusal reaches the caller as a failure.
 */
function parseArgv(argv, suffixes) {
  const files = [];
  let repoArg = null;
  let diffScope = "staged";
  let baseRef = DEFAULT_BASE_REF;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--repo" && argv[i + 1]) repoArg = argv[(i += 1)];
    else if (argv[i] === "--scope" && argv[i + 1]) diffScope = argv[(i += 1)];
    else if (argv[i] === "--base" && argv[i + 1]) baseRef = argv[(i += 1)];
    else if (suffixes.some((suffix) => argv[i].endsWith(suffix))) files.push(argv[i]);
  }
  // Real-pathed, and that is load-bearing rather than tidy. The scope resolver
  // answers with paths under the *resolved* root, while everything derived from
  // this one — `tsSourceRoot`, the organization gate's catalog walk — is derived
  // from the string the caller passed. Behind a symlinked prefix (`/tmp` ->
  // `/private/tmp`, an agent worktree under a linked home) the two never compare
  // equal, so the catalog's `changedSet.has(full)` guard missed every graded
  // file, put it in the DRY catalog, and reported it as duplicating itself.
  // Same class as `relOf` above and as `read_source_text`'s resolved-prefix
  // check: one path, two spellings, compared as strings.
  const repoRoot = fs.realpathSync(repoArg ? path.resolve(repoArg) : process.cwd());
  const label = diffScope === "staged" && !repoArg ? "pre-commit" : "gate";
  gradedRepoRoot = repoRoot;
  return { files, repoRoot, diffScope, baseRef, label };
}

/**
 * Walk an ESTree AST depth-first. A visitor that returns `false` prunes the
 * node's children — how a caller says "what is inside this does not count".
 */
function walk(node, visit) {
  if (!node || typeof node.type !== "string") return;
  if (visit(node) === false) return;
  for (const key of Object.keys(node)) {
    if (key === "parent" || key === "loc") continue;
    const value = node[key];
    if (Array.isArray(value)) {
      for (const child of value) walk(child, visit);
    } else if (value && typeof value.type === "string") {
      walk(value, visit);
    }
  }
}

/**
 * Run one gate end to end and exit.
 *
 * `grade(invocation, scope)` returns the findings for the files `scope.files`
 * names; everything around it — argv, scope, the report, the exit code, the
 * one handler for "could not examine" — is the same for every gate, so it is
 * written once. `footer` lines follow a failure report, for a gate whose rules
 * are one half of something a reader needs to be pointed at.
 */
function runGate({ title, suffixes, grade, footer = [] }) {
  const invocation = parseArgv(process.argv.slice(2), suffixes);
  const { label } = invocation;
  let findings;
  try {
    const scope = resolveGateScope({ ...invocation, suffixes });
    if (scope.files.length === 0) process.exit(0);
    findings = grade(invocation, scope);
  } catch (err) {
    if (!(err instanceof UnexaminableError)) throw err;
    // One handler for the one invariant: a scope this run could not resolve, and
    // a file it could not read, are both things it did not examine.
    console.error(`${label}: cannot determine what to examine: ${err.message}`);
    process.exit(1);
  }

  if (findings.length > 0) {
    console.error(`${label}: ${title} check failed:`);
    for (const finding of findings) console.error(` - ${finding}`);
    for (const line of footer) console.error(line);
    process.exit(1);
  }
  console.log(`${label}: ${title} checks passed.`);
  process.exit(0);
}

module.exports = {
  UnexaminableError,
  UnexaminableFileError,
  isTestFile,
  parseFile,
  parseGradedFile,
  readSource,
  runGate,
  tsSourceRoot,
  walk,
};

#!/usr/bin/env node
/**
 * Rerun the vitest tests an acceptance criterion names as its proof, and fail
 * unless every one of them ran and passed.
 *
 * Loregarden's criteria check calls this from the repository root, once per
 * criterion, with that criterion's tests as `ts/<path>::"exact test name"`:
 *
 *     node ts/scripts/criterion-vitest.cjs 'ts/tests/a.test.ts::"its name"'
 *
 * Vitest's own exit code cannot answer that question. A `-t` pattern that
 * matches nothing (a renamed test, an `it.skip`, a name that only appears in a
 * comment) skips every test in the file, and vitest can still exit 0, which
 * loregarden would read as "the named test passes". Here every named test must
 * appear in vitest's JSON report, in the file it was named in, as passed.
 *
 * A name matches a test's own title, or its full name (the describe titles and
 * the title, space-separated, as vitest's JSON report writes it).
 *
 * Exit codes: 0 every named test passed; 1 one failed, was skipped, or does
 * not exist; 69 (EX_UNAVAILABLE) vitest could not run at all — not installed,
 * or no report — which loregarden records as "not run" rather than "failed".
 */
"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const EX_UNAVAILABLE = 69;
const TS_ROOT = path.resolve(__dirname, "..");
const VITEST_BIN = path.join("node_modules", ".bin", "vitest");

/** `ts/tests/a.test.ts::"its name"` → `{ id, file: "tests/a.test.ts", name: "its name" }`, or null. */
function parseTestId(testId) {
  const at = testId.indexOf("::");
  if (at < 0) return null;
  const file = testId.slice(0, at).trim().replace(/^ts\//, "");
  const name = testId
    .slice(at + 2)
    .trim()
    .replace(/^["']|["']$/g, "");
  if (!file || !name) return null;
  return { id: "ts/" + file + '::"' + name + '"', file: file, name: name };
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** True when `file` names an existing file inside `root` (no `../` escapes). */
function fileInside(root, file) {
  const absolute = path.resolve(root, file);
  const relative = path.relative(root, absolute);
  if (relative.startsWith("..") || path.isAbsolute(relative)) return false;
  return fs.existsSync(absolute) && fs.statSync(absolute).isFile();
}

/**
 * Each named test's outcome in a vitest `--reporter=json` report: "passed",
 * "failed", "skipped"/"pending"/"todo", or "missing" when the report has no
 * such test in that file. More than one match passes only if all of them did.
 */
function outcomes(tests, report, root) {
  return tests.map(function (test) {
    const suite = (report.testResults || []).find(function (result) {
      return path.resolve(root, test.file) === path.resolve(result.name || "");
    });
    const found = suite
      ? (suite.assertionResults || []).filter(function (a) {
          return a.title === test.name || a.fullName === test.name;
        })
      : [];
    if (!found.length) return { test: test, status: "missing" };
    const notPassed = found.find(function (a) {
      return a.status !== "passed";
    });
    return { test: test, status: notPassed ? notPassed.status : "passed" };
  });
}

/** The primary checkout's root when `dir` is in a linked git worktree, else null. */
function primaryCheckout(dir) {
  const env = Object.assign({}, process.env);
  // A git hook exports GIT_DIR, and it beats cwd.
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  const result = spawnSync("git", ["rev-parse", "--path-format=absolute", "--git-common-dir"], {
    cwd: dir,
    env: env,
    encoding: "utf8",
  });
  if (result.status !== 0 || !result.stdout.trim()) return null;
  return path.dirname(result.stdout.trim());
}

function readOrNull(file) {
  return fs.existsSync(file) ? fs.readFileSync(file, "utf8") : null;
}

/**
 * Make vitest runnable in `root`, or say why it cannot be.
 *
 * A git worktree is created without `ts/node_modules`. Running the primary
 * checkout's vitest binary is not enough: vitest loads `vitest.config.ts`, and
 * the tests import react and friends, by resolving from `root` itself. So when
 * the primary checkout has the same lockfile installed, `root/node_modules`
 * becomes a symlink to it, the way `gates/scripts/gate-python.sh` borrows the
 * primary checkout's venv. It is left in place for the next run, and
 * `.gitignore` ignores it.
 *
 * Returns `{ vitest }` or `{ reason }`.
 */
function ensureVitest(root) {
  const vitest = path.join(root, VITEST_BIN);
  if (fs.existsSync(vitest)) return { vitest: vitest };
  const own = path.join(root, "node_modules");
  const install = "run `npm ci` in " + root;
  const primary = primaryCheckout(root);
  const primaryTs = primary ? path.join(primary, "ts") : null;
  if (!primaryTs || path.resolve(primaryTs) === path.resolve(root)) {
    return { reason: "vitest is not installed under " + own + "; " + install };
  }
  if (!fs.existsSync(path.join(primaryTs, VITEST_BIN))) {
    return { reason: "vitest is installed neither here nor in " + primaryTs + "; " + install };
  }
  const lock = readOrNull(path.join(root, "package-lock.json"));
  if (lock === null || lock !== readOrNull(path.join(primaryTs, "package-lock.json"))) {
    return {
      reason: "ts/package-lock.json differs from the primary checkout's, so its node_modules may not match; " + install,
    };
  }
  if (fs.existsSync(own) || isDanglingLink(own)) {
    return { reason: own + " exists but has no vitest; " + install };
  }
  fs.symlinkSync(path.join(primaryTs, "node_modules"), own, "dir");
  process.stderr.write("borrowing " + path.join(primaryTs, "node_modules") + " via a symlink at " + own + "\n");
  return { vitest: vitest };
}

function isDanglingLink(file) {
  try {
    return fs.lstatSync(file).isSymbolicLink();
  } catch (error) {
    if (error.code === "ENOENT") return false;
    throw error;
  }
}

function printResults(results) {
  results.forEach(function (r) {
    process.stdout.write(r.status + ": " + r.test.id + "\n");
  });
  const bad = results.filter(function (r) {
    return r.status !== "passed";
  });
  if (bad.length) {
    process.stderr.write(
      bad.length + " of " + results.length + " named test(s) did not pass:\n" +
        bad.map(function (r) { return "  " + r.status + ": " + r.test.id; }).join("\n") + "\n",
    );
  }
  return bad.length ? 1 : 0;
}

function runVitest(vitest, root, tests) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "criterion-vitest-"));
  const out = path.join(dir, "report.json");
  const files = Array.from(new Set(tests.map((t) => t.file)));
  const pattern = tests.map((t) => escapeRegExp(t.name)).join("|");
  const workers = process.env.LOREGARDEN_CAPACITY_WORKERS || "1";
  try {
    spawnSync(
      vitest,
      [
        "run",
        "--reporter=json",
        "--outputFile=" + out,
        "--minWorkers=1",
        "--maxWorkers=" + workers,
        "--testNamePattern=" + pattern,
      ].concat(files),
      { cwd: root, stdio: ["ignore", "inherit", "inherit"] },
    );
    const report = readOrNull(out);
    return report === null ? null : JSON.parse(report);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

function main(argv, root) {
  const tests = argv.map(parseTestId);
  if (!tests.length || tests.some((t) => t === null)) {
    process.stderr.write("usage: criterion-vitest.cjs 'ts/<path>::\"exact test name\"'...\n");
    return EX_UNAVAILABLE;
  }
  // A file that is not there cannot hold the test; say so without asking
  // vitest, whose path arguments are substring filters, not exact paths.
  const present = tests.filter((t) => fileInside(root, t.file));
  const absent = tests
    .filter((t) => present.indexOf(t) < 0)
    .map((t) => ({ test: t, status: "missing" }));
  if (!present.length) return printResults(absent);

  const setup = ensureVitest(root);
  if (!setup.vitest) {
    process.stderr.write(setup.reason + "\n");
    return EX_UNAVAILABLE;
  }
  const report = runVitest(setup.vitest, root, present);
  if (report === null) {
    process.stderr.write("vitest wrote no report, so no test can be said to have run\n");
    return EX_UNAVAILABLE;
  }
  const results = outcomes(present, report, root).concat(absent);
  return printResults(tests.map((t) => results.find((r) => r.test === t)));
}

module.exports = { parseTestId: parseTestId, outcomes: outcomes, EX_UNAVAILABLE: EX_UNAVAILABLE };

if (require.main === module) {
  process.exitCode = main(process.argv.slice(2), TS_ROOT);
}

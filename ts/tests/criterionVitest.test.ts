/**
 * `scripts/criterion-vitest.cjs`, driven end to end against a throwaway `ts/`.
 *
 * Each case copies the script into a temporary `ts/scripts/` beside a fixture
 * test file and its own vitest config, so the fixture's deliberately failing
 * test never joins this suite. `node_modules` is a symlink to this package's.
 */
import { spawnSync } from "child_process";
import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { afterEach, describe, expect, it } from "vitest";

const TS_ROOT = path.resolve(__dirname, "..");
const SCRIPT = path.join(TS_ROOT, "scripts", "criterion-vitest.cjs");
const NODE_MODULES = fs.realpathSync(path.join(TS_ROOT, "node_modules"));

const FIXTURE_TEST = `
import { describe, expect, it } from "vitest";
describe("group", () => {
  it("passes", () => expect(1).toBe(1));
  it("fails", () => expect(1).toBe(2));
  it.skip("is skipped", () => expect(1).toBe(1));
});
it("has (parens) and [brackets]", () => expect(true).toBe(true));
`;

const made: string[] = [];

afterEach(() => {
  for (const dir of made.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

/** A `ts/` package in a fresh directory; returns that directory (the repo root). */
function fixtureRepo(options: { nodeModules: boolean } = { nodeModules: true }): string {
  const repo = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "criterion-vitest-test-")));
  made.push(repo);
  const ts = path.join(repo, "ts");
  fs.mkdirSync(path.join(ts, "scripts"), { recursive: true });
  fs.mkdirSync(path.join(ts, "tests"));
  fs.copyFileSync(SCRIPT, path.join(ts, "scripts", "criterion-vitest.cjs"));
  fs.writeFileSync(path.join(ts, "vitest.config.mjs"), "export default { test: { include: ['tests/**/*.test.ts'] } };\n");
  fs.writeFileSync(path.join(ts, "package-lock.json"), '{"lockfileVersion": 3}\n');
  fs.writeFileSync(path.join(ts, "tests", "sample.test.ts"), FIXTURE_TEST);
  if (options.nodeModules) fs.symlinkSync(NODE_MODULES, path.join(ts, "node_modules"), "dir");
  return repo;
}

function run(repo: string, ...ids: string[]) {
  const env = { ...process.env };
  // The suite itself may run inside a git hook; the fixture is not that repo.
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  const result = spawnSync("node", ["ts/scripts/criterion-vitest.cjs", ...ids], {
    cwd: repo,
    env,
    encoding: "utf8",
  });
  return { code: result.status, stdout: result.stdout, stderr: result.stderr };
}

function git(cwd: string, ...args: string[]) {
  const env = { ...process.env };
  delete env.GIT_DIR;
  delete env.GIT_WORK_TREE;
  const result = spawnSync(
    "git",
    ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false", ...args],
    { cwd, env, encoding: "utf8" },
  );
  if (result.status !== 0) throw new Error(`git ${args.join(" ")} failed: ${result.stderr}`);
}

const SAMPLE = "ts/tests/sample.test.ts";

describe("criterion-vitest", { timeout: 60_000 }, () => {
  it("exits 0 when the named test passes, by title or by full name", () => {
    const repo = fixtureRepo();
    const result = run(repo, `${SAMPLE}::"passes"`, `${SAMPLE}::"group passes"`);
    expect(result.code).toBe(0);
    expect(result.stdout).toContain(`passed: ${SAMPLE}::"passes"`);
    expect(result.stdout).toContain(`passed: ${SAMPLE}::"group passes"`);
  });

  it("matches a name holding regex metacharacters literally", () => {
    const repo = fixtureRepo();
    expect(run(repo, `${SAMPLE}::"has (parens) and [brackets]"`).code).toBe(0);
  });

  it("exits 1 when a named test fails, even beside one that passes", () => {
    const repo = fixtureRepo();
    const result = run(repo, `${SAMPLE}::"passes"`, `${SAMPLE}::"fails"`);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain(`failed: ${SAMPLE}::"fails"`);
  });

  it("exits 1 when the name matches no test in the file", () => {
    const repo = fixtureRepo();
    const result = run(repo, `${SAMPLE}::"was renamed"`);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain(`missing: ${SAMPLE}::"was renamed"`);
  });

  it("exits 1 when the named test is skipped", () => {
    const repo = fixtureRepo();
    const result = run(repo, `${SAMPLE}::"is skipped"`);
    expect(result.code).toBe(1);
    expect(result.stdout).not.toContain("passed:");
  });

  it("exits 1 when the named file does not exist", () => {
    const repo = fixtureRepo();
    const result = run(repo, `ts/tests/gone.test.ts::"passes"`);
    expect(result.code).toBe(1);
    expect(result.stdout).toContain(`missing: ts/tests/gone.test.ts::"passes"`);
  });

  it("does not let a name in another file stand in for the named file", () => {
    const repo = fixtureRepo();
    fs.writeFileSync(path.join(repo, "ts", "tests", "other.test.ts"), FIXTURE_TEST.replace(/"fails"/, '"elsewhere"'));
    expect(run(repo, `${SAMPLE}::"elsewhere"`).code).toBe(1);
  });

  it("exits 69 when vitest is not installed and there is no checkout to borrow from", () => {
    const repo = fixtureRepo({ nodeModules: false });
    const result = run(repo, `${SAMPLE}::"passes"`);
    expect(result.code).toBe(69);
  });

  it("exits 69 on an id with no test name", () => {
    expect(run(fixtureRepo(), SAMPLE).code).toBe(69);
  });

  describe("in a git worktree without node_modules", () => {
    function primaryWithWorktree(): { primary: string; worktree: string } {
      const primary = fixtureRepo();
      fs.writeFileSync(path.join(primary, ".gitignore"), "node_modules\n");
      git(primary, "init", "-q");
      git(primary, "add", ".");
      git(primary, "commit", "-q", "-m", "fixture");
      const worktree = path.join(primary, "..", path.basename(primary) + "-wt");
      made.push(worktree);
      git(primary, "worktree", "add", "-q", "--detach", worktree);
      return { primary, worktree };
    }

    it("borrows the primary checkout's node_modules when the lockfiles match", () => {
      const { worktree } = primaryWithWorktree();
      expect(run(worktree, `${SAMPLE}::"passes"`).code).toBe(0);
      expect(fs.lstatSync(path.join(worktree, "ts", "node_modules")).isSymbolicLink()).toBe(true);
    });

    it("exits 69 rather than borrowing when the lockfiles differ", () => {
      const { worktree } = primaryWithWorktree();
      fs.writeFileSync(path.join(worktree, "ts", "package-lock.json"), '{"lockfileVersion": 3, "changed": true}\n');
      const result = run(worktree, `${SAMPLE}::"passes"`);
      expect(result.code).toBe(69);
      expect(fs.existsSync(path.join(worktree, "ts", "node_modules"))).toBe(false);
    });
  });
});

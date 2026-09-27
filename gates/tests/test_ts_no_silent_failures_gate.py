"""The TypeScript silent-failure gate: the four shapes, the waiver, and the seams.

Extracted from loregarden's ``.lefthook/scripts/ts_no_silent_failures_check.cjs``.
Every repo here has no ``node_modules`` of its own — the parser comes from this
package, which is the seam that made the source un-extractable. The classes at
the bottom each pin a defect the source had, and fail against its behaviour.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

GATE = "ts_no_silent_failures_check.cjs"


def ts_repo(repo, relpath: str, content: str):
    """TypeScript under src/, uncommitted so every line is an addition."""
    repo.write("package.json", '{"name":"graded","private":true}\n')
    repo.commit("layout")
    repo.write(relpath, content)
    return repo


def output(result) -> str:
    return f"{result.stdout}\n{result.stderr}"


def run_gate(repo, *files: str):
    return repo.gate(GATE, "--repo", str(repo.root), "--scope", "worktree", *files)


def assert_fails(result, needle: str) -> None:
    assert result.returncode == 1, output(result)
    assert needle in output(result), output(result)


def assert_passes(result) -> None:
    assert result.returncode == 0, output(result)
    assert "examined 1 file(s)" in output(result), output(result)
    assert "silent failure checks passed" in output(result), output(result)


class TestTheFourShapes:
    def test_an_empty_catch_fails(self, repo):
        ts_repo(repo, "src/load.ts", "export function f() {\n  try { g(); } catch {}\n}\n")
        assert_fails(run_gate(repo), "empty catch block")

    def test_a_catch_that_neither_rethrows_records_nor_surfaces_fails(self, repo):
        source = "export function f() {\n  try { g(); } catch (err) { retry(); }\n}\n"
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch neither rethrows nor surfaces")

    def test_the_same_shape_fails_in_a_tsx_file(self, repo):
        source = (
            "export function Panel() {\n"
            "  try { g(); } catch {}\n"
            "  return <div>panel</div>;\n"
            "}\n"
        )
        ts_repo(repo, "src/Panel.tsx", source)
        assert_fails(run_gate(repo), "empty catch block")

    def test_a_console_only_catch_fails(self, repo):
        source = "export function f() {\n  try { g(); } catch (err) { console.error(err); }\n}\n"
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch only writes to the console")

    def test_an_empty_catch_handler_discards_the_rejection(self, repo):
        ts_repo(repo, "src/load.ts", "export function f() {\n  void load().catch(() => {});\n}\n")
        assert_fails(run_gate(repo), "discards the rejection")

    def test_catch_console_error_reports_where_no_user_looks(self, repo):
        source = "export function f() {\n  void load().catch(console.error);\n}\n"
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), ".catch(console.*)")

    def test_promise_all_over_raw_fetch_without_an_ok_check_fails(self, repo):
        source = (
            "export async function bulk(ids: string[]) {\n"
            "  const done = await Promise.allSettled(ids.map((id) => fetch(`/x/${id}`)));\n"
            '  return done.filter((d) => d.status === "rejected").length;\n'
            "}\n"
        )
        ts_repo(repo, "src/bulk.ts", source)
        assert_fails(run_gate(repo), "Promise.allSettled over raw fetch")


class TestWhatCountsAsHandled:
    @pytest.mark.parametrize(
        "body",
        [
            "throw err;",
            "setError(String(err));",
            "pushToast(describeError(err));",
            "failed = true;",
            "return { error: err };",
        ],
    )
    def test_a_catch_that_does_something_with_the_failure_passes(self, repo, body):
        source = (
            "let failed = false;\n"
            "export function f() {\n"
            f"  try {{ g(); }} catch (err) {{ {body} }}\n"
            "  return failed;\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_a_catch_in_an_untouched_line_is_not_this_changes_finding(self, repo):
        # Diff-scoped, like every gate here: existing debt does not block an
        # unrelated edit.
        ts_repo(repo, "src/load.ts", "export function f() {\n  try { g(); } catch {}\n}\n")
        repo.commit("old debt")
        repo.write(
            "src/load.ts",
            "export function f() {\n  try { g(); } catch {}\n}\nexport const x = 1;\n",
        )
        result = run_gate(repo)
        assert result.returncode == 0, output(result)
        assert "examined 1 file(s)" in output(result)

    def test_test_files_are_not_graded(self, repo):
        ts_repo(repo, "src/load.test.ts", "it('x', () => {\n  try { g(); } catch {}\n});\n")
        result = run_gate(repo)
        assert result.returncode == 0, output(result)


class TestTheWaiverContract:
    def test_a_substantive_reason_waives_the_catch(self, repo):
        source = (
            "export function f() {\n"
            "  try { g(); } catch { /* silent-ok: probe only; the poll re-checks in 2s */ }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_the_marker_alone_is_not_a_waiver(self, repo):
        source = "export function f() {\n  try { g(); } catch { /* silent-ok: */ }\n}\n"
        ts_repo(repo, "src/load.ts", source)
        result = run_gate(repo)
        assert_fails(result, "'silent-ok:' with no substantive reason")
        assert "empty catch block" in output(result)

    def test_a_reason_below_the_minimum_length_is_not_a_waiver(self, repo):
        source = "export function f() {\n  try { g(); } catch { // silent-ok: fine\n  }\n}\n"
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "'silent-ok:' with no substantive reason")

    def test_a_waiver_covers_a_discarded_rejection_from_the_line_above(self, repo):
        source = (
            "export function f() {\n"
            "  // silent-ok: best-effort prefetch; the page loads it again on open\n"
            "  void load().catch(() => {});\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_a_marker_inside_a_string_is_not_a_waiver(self, repo):
        # Only a comment can waive. A raw-line scan read this string as a
        # waiver with a long enough reason and passed the empty catch.
        source = (
            "export function f() {\n"
            '  try { g(); } catch { log("silent-ok: this string is not a comment"); }\n'
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch neither rethrows nor surfaces")


class TestTheSeams:
    def test_the_parser_comes_from_this_package_not_the_graded_repo(self, repo):
        ts_repo(repo, "src/load.ts", "export function f() {\n  try { g(); } catch {}\n}\n")
        assert not (repo.root / "node_modules").exists()
        assert not (repo.root / "client").exists()
        assert_fails(run_gate(repo), "empty catch block")

    def test_staged_files_as_lefthook_passes_them(self, repo):
        ts_repo(repo, "src/load.ts", "export function f() {\n  try { g(); } catch {}\n}\n")
        repo.stage("src/load.ts")
        result = repo.gate(GATE, "src/load.ts")
        assert result.returncode == 1, output(result)
        assert "pre-commit: silent failure check failed" in output(result)

    def test_an_unknown_scope_is_refused_not_coerced_to_staged(self, repo):
        # The source's `ts_git_diff.cjs` turned any unrecognised scope into
        # `staged`: `--scope wrokTree` graded an empty index and exited 0.
        ts_repo(repo, "src/load.ts", "export function f() {\n  try { g(); } catch {}\n}\n")
        result = repo.gate(GATE, "--repo", str(repo.root), "--scope", "wrokTree")
        assert result.returncode == 1, output(result)
        assert "unknown scope" in output(result)

    def test_an_unparseable_file_is_refused_not_skipped(self, repo):
        # The source's `if (!ast) continue` reported a file it never read as clean.
        ts_repo(repo, "src/broken.ts", "export function ( { { {\n")
        result = run_gate(repo)
        assert result.returncode == 1, output(result)
        assert "could not parse" in output(result)


class TestDefectsFixedInExtraction:
    """Each fails against the loregarden source's behaviour."""

    def test_a_generic_arrow_in_a_ts_file_is_graded(self, repo):
        # The source parsed every file with `jsx: true`, so `<T>(...) =>` in a
        # `.ts` file did not parse, and `if (!ast) continue` passed it — along
        # with the empty catch inside it.
        source = (
            "export const firstOf = <T>(items: T[]): T | undefined => {\n"
            "  try { return items[0]; } catch {}\n"
            "};\n"
        )
        ts_repo(repo, "src/first.ts", source)
        assert_fails(run_gate(repo), "empty catch block")

    def test_a_surfacing_name_inside_console_error_is_still_console_only(self, repo):
        # The walk descended into console's arguments, found `describeError`,
        # and counted a line in the devtools as surfacing to the user.
        source = (
            "export function f() {\n"
            "  try { g(); } catch (err) { console.error(describeError(err)); }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch only writes to the console")

    def test_an_ok_check_after_the_await_counts(self, repo):
        # The header said "no .ok check in the file"; the code searched only
        # inside the Promise.all call, and failed this — the usual correct shape.
        source = (
            "export async function bulk(ids: string[]) {\n"
            "  const responses = await Promise.all(ids.map((id) => fetch(`/x/${id}`)));\n"
            "  return responses.filter((r) => !r.ok).length;\n"
            "}\n"
        )
        ts_repo(repo, "src/bulk.ts", source)
        assert_passes(run_gate(repo))

    def test_a_multi_line_block_waiver_above_a_catch_call_is_honoured(self, repo):
        # The source found comment lines by prefix, so the continuation line of
        # this block read as code and the walk never reached the marker.
        source = (
            "export function f() {\n"
            "  /* silent-ok: probing only; the poll\n"
            "     re-checks in two seconds anyway */\n"
            "  void load().catch(() => {});\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_a_local_declared_and_dropped_is_not_recording_state(self, repo):
        source = (
            "export function f() {\n"
            "  try { g(); } catch (err) { const message = String(err); }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch neither rethrows nor surfaces")

    def test_collecting_the_failure_to_report_later_is_recording_state(self, repo):
        # The resolution chain in ts_gate_harness.cjs has this shape, and the
        # source failed it the first time the gate graded this package.
        source = (
            "export function load(attempts: string[]) {\n"
            "  try { return g(); } catch (err) { attempts.push(String(err)); }\n"
            '  throw new Error(attempts.join("\\n"));\n'
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))


class TestDefectsFixedInReview:
    """Each fails against the gate as first extracted (PR #59 review)."""

    @pytest.mark.parametrize(
        "body",
        [
            'seen.add("x");',
            "cache.set(key, []);",
            'url.searchParams.set("retry", "1");',
            "attempts.push(key);",
        ],
    )
    def test_collecting_something_other_than_the_error_is_not_recording_it(self, repo, body):
        # A collection method was read as recording the failure whatever went
        # into it; only the caught error, or something built from it, is.
        source = (
            "export function f(seen: Set<string>, cache: Map<string, string[]>, url: URL,\n"
            "  attempts: string[], key: string) {\n"
            f"  try {{ g(); }} catch (err) {{ {body} }}\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch neither rethrows nor surfaces")

    @pytest.mark.parametrize(
        "body",
        ["attempts.push(err);", "failures.set(id, err.message);", "attempts.push(`${err}`);"],
    )
    def test_collecting_the_caught_error_is_recording_it(self, repo, body):
        source = (
            "export function f(attempts: unknown[], failures: Map<string, string>, id: string) {\n"
            f"  try {{ g(); }} catch (err) {{ {body} }}\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_void_around_a_report_is_not_a_discard(self, repo):
        # `void` keeps the arrow's return value out of the chain; the call is
        # the report.
        source = (
            "export function f(p: Promise<void>, report: (e: unknown) => void) {\n"
            "  p.catch((e) => void report(e));\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    @pytest.mark.parametrize("handler", ["() => void 0", "(e) => void null", "() => void flag"])
    def test_void_of_nothing_is_still_a_discard(self, repo, handler):
        source = (
            "const flag = true;\n"
            "export function f(p: Promise<void>) {\n"
            f"  p.catch({handler});\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "the .catch handler discards the rejection")

    def test_an_inner_waiver_does_not_waive_the_catch_around_it(self, repo):
        # The marker anywhere in the outer span waived it: the reason written
        # for the retried inner probe excused the outer catch nothing retries.
        source = (
            "export function f() {\n"
            "  try {\n"
            "    run();\n"
            "  } catch {\n"
            "    try { run(); } catch { /* silent-ok: inner probe is retried every 2 seconds */ }\n"
            "  }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        result = run_gate(repo)
        assert_fails(result, "src/load.ts:4: catch neither rethrows nor surfaces")
        assert "src/load.ts:5:" not in output(result)

    def test_an_inner_waiver_on_the_outer_line_does_not_waive_it(self, repo):
        source = (
            "export function f() {\n"
            "  try { run(); } catch { try { run(); } catch "
            "{ /* silent-ok: inner probe is retried every 2 seconds */ } }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_fails(run_gate(repo), "catch neither rethrows nor surfaces")

    def test_a_multi_line_waiver_inside_the_catch_body_is_honoured(self, repo):
        source = (
            "export function f() {\n"
            "  try {\n"
            "    run();\n"
            "  } catch {\n"
            "    // silent-ok: the poll that owns this probe\n"
            "    // re-checks the endpoint every two seconds\n"
            "  }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_a_waiver_inside_the_handler_waives_the_catch_call(self, repo):
        source = (
            "export function f() {\n"
            "  void load().catch(() => {\n"
            "    /* silent-ok: best-effort prefetch; the page loads it again on open */\n"
            "  });\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        assert_passes(run_gate(repo))

    def test_a_waiver_above_an_inner_catch_call_is_that_calls(self, repo):
        source = (
            "export function f() {\n"
            "  try { run(); } catch {\n"
            "    // silent-ok: best-effort prefetch; the page loads it again on open\n"
            "    void load().catch(() => {});\n"
            "  }\n"
            "}\n"
        )
        ts_repo(repo, "src/load.ts", source)
        result = run_gate(repo)
        assert_fails(result, "src/load.ts:2: catch neither rethrows nor surfaces")
        assert "discards the rejection" not in output(result)

"""The CSS motion gate: layout properties, hardcoded durations, reduced motion.

Ported from loregarden's ``.lefthook/scripts/ts_motion_check.cjs`` and its
tests. Both failure directions are pinned: a gate that accuses a colour fade or
a 1.4s pulse is noise nobody keeps, and one that misses ``transition: width``
is a vacuous pass. Scope and waivers come from ``ts_gate_harness.cjs`` and
``ts_waivers.cjs``, shared with the TypeScript gates.
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

GATE = "css_motion_check.cjs"

ROOT_STYLESHEET = """:root {
  --t-fast: 0.12s;
  --t-med: 0.2s;
  --t-slow: 0.3s;
}

@media (prefers-reduced-motion: reduce) {
  * {
    animation-duration: 0.01ms !important;
  }
}
"""


def output(result) -> str:
    return f"{result.stdout}\n{result.stderr}"


def run_gate(repo):
    return repo.gate(GATE, "--repo", str(repo.root), "--scope", "worktree")


def tokens_repo(repo):
    """A client whose root stylesheet defines the --t-* tokens and a global reduced-motion rule."""
    repo.write("package.json", '{"name":"graded","private":true}\n')
    repo.write("client/src/index.css", ROOT_STYLESHEET)
    repo.commit("layout")
    return repo


def bare_repo(repo):
    """A client with no root stylesheet: no tokens, no global reduced-motion policy."""
    repo.write("package.json", '{"name":"graded","private":true}\n')
    repo.write("client/src/.keep", "")
    repo.commit("layout")
    return repo


def assert_fails(result, needle: str) -> None:
    assert result.returncode == 1, output(result)
    assert needle in output(result), output(result)


def assert_passes(result) -> None:
    assert result.returncode == 0, output(result)
    assert "motion checks passed" in output(result), output(result)


class TestLayoutProperties:
    @pytest.mark.parametrize("prop", ["width", "max-height", "left", "margin-top"])
    def test_a_layout_transition_fails(self, repo, prop):
        tokens_repo(repo).write("client/src/a.css", f".a {{ transition: {prop} var(--t-med) ease; }}\n")
        assert_fails(run_gate(repo), f"transitions '{prop}'")

    def test_transition_all_fails(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { transition: all var(--t-fast); }\n")
        assert_fails(run_gate(repo), "'transition: all'")

    def test_transition_none_is_not_transition_all(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { transition: none; }\n")
        assert_passes(run_gate(repo))

    def test_a_layout_keyframe_fails(self, repo):
        tokens_repo(repo).write(
            "client/src/a.css", "@keyframes slide {\n  from { left: 0; }\n  to { left: 10px; }\n}\n"
        )
        assert_fails(run_gate(repo), "a keyframe animates 'left'")

    def test_transform_and_opacity_pass(self, repo):
        tokens_repo(repo).write(
            "client/src/a.css",
            ".a { transition: transform var(--t-med) var(--ease-out), opacity var(--t-fast); }\n",
        )
        assert_passes(run_gate(repo))


class TestDurations:
    @pytest.mark.parametrize(
        ("value", "token"),
        [("0.12s", "var(--t-fast)"), ("200ms", "var(--t-med)"), (".35s", "var(--t-slow)")],
    )
    def test_a_hardcoded_ui_duration_names_its_token(self, repo, value, token):
        tokens_repo(repo).write("client/src/a.css", f".a {{ transition: opacity {value} ease; }}\n")
        result = run_gate(repo)
        assert_fails(result, f"hardcodes '{value}'")
        assert token in output(result)

    def test_a_delay_after_a_token_is_not_a_duration(self, repo):
        tokens_repo(repo).write(
            "client/src/a.css", ".a { transition: opacity var(--t-fast) var(--ease-out) 0.3s; }\n"
        )
        assert_passes(run_gate(repo))

    def test_an_ambient_loop_is_left_alone(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { animation: pulse 1.4s ease-in-out infinite; }\n")
        assert_passes(run_gate(repo))

    def test_tokens_named_by_the_configured_token_source_count(self, repo):
        bare_repo(repo)
        repo.write(".lore-eden-gates.json", '{"css_token_source": "tokens.ts"}\n')
        repo.write("tokens.ts", 'const t = { tFast: { css: "--t-fast", value: ".12s" } };\n')
        repo.commit("tokens")
        repo.write(
            "client/src/a.css",
            ".a { transition: opacity 0.2s; }\n"
            "@media (prefers-reduced-motion: reduce) { .a { transition: none; } }\n",
        )
        assert_fails(run_gate(repo), "hardcodes '0.2s'")

    def test_a_repo_without_tokens_is_not_asked_for_them(self, repo):
        bare_repo(repo).write(
            "client/src/a.css",
            ".a { transition: opacity 0.2s; }\n"
            "@media (prefers-reduced-motion: reduce) { .a { transition: none; } }\n",
        )
        assert_passes(run_gate(repo))


class TestReducedMotion:
    def test_motion_with_no_policy_fails(self, repo):
        bare_repo(repo).write("client/src/a.css", ".a { animation: spin 1s linear infinite; }\n")
        assert_fails(run_gate(repo), "prefers-reduced-motion")

    def test_a_policy_in_the_file_itself_passes(self, repo):
        bare_repo(repo).write(
            "client/src/a.css",
            ".a { animation: spin 1s linear infinite; }\n"
            "@media (prefers-reduced-motion: reduce) { .a { animation: none; } }\n",
        )
        assert_passes(run_gate(repo))

    def test_the_global_policy_covers_every_file(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { animation: spin 1s linear infinite; }\n")
        assert_passes(run_gate(repo))

    def test_a_colour_fade_moves_nothing(self, repo):
        bare_repo(repo).write("client/src/a.css", ".a { transition: background-color 1s, opacity 1s; }\n")
        assert_passes(run_gate(repo))


class TestScopeAndWaivers:
    def test_an_untouched_line_is_not_reported(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { transition: width 0.2s; }\n")
        repo.commit("inherited")
        repo.write("client/src/a.css", ".a { transition: width 0.2s; }\n.b { opacity: 1; }\n")
        assert_passes(run_gate(repo))

    def test_a_waiver_with_a_reason_passes(self, repo):
        tokens_repo(repo).write(
            "client/src/a.css",
            ".a { transition: width var(--t-med); } "
            "/* motion-ok: a progress bar inside a fixed track; nothing around it reflows */\n",
        )
        assert_passes(run_gate(repo))

    def test_a_waiver_on_the_line_above_passes(self, repo):
        tokens_repo(repo).write(
            "client/src/a.css",
            ".a {\n  /* motion-ok: the rail's width is the layout change itself */\n"
            "  transition: width var(--t-med);\n}\n",
        )
        assert_passes(run_gate(repo))

    def test_a_waiver_without_a_reason_fails(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { transition: width var(--t-med); } /* motion-ok: */\n")
        assert run_gate(repo).returncode == 1

    def test_an_unparseable_stylesheet_is_reported_not_skipped(self, repo):
        tokens_repo(repo).write("client/src/a.css", ".a { transition: width 0.2s;\n")
        assert_fails(run_gate(repo), "could not parse")

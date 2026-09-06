"""The rule that asks whether anything is looking at a file at all.

Six gates were green over a repository in which 45 tracked files had never been
opened by any of them — every stylesheet, every shell script including the ones
the hooks execute, every CI workflow. Nothing was broken. The globs named `.py`,
`.ts` and `.tsx`, so a commit touching only the rest ran the whole pre-commit
stage in 0.08s and printed "no files for inspection" seven times.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from gate_coverage_check import (  # noqa: E402
    GATED_SUFFIXES,
    tracked_paths,
    uncovered,
    unused_globs,
)
from house_rules import HouseRulesError, load_house_rules  # noqa: E402


class TestUncovered:
    def test_a_gated_suffix_is_covered(self):
        assert uncovered(["app/x.py", "app/y.tsx", "app/z.css"], {}) == []

    def test_an_ungated_suffix_with_no_exemption_is_reported(self):
        assert uncovered(["deploy.sh", "notes.md"], {}) == ["deploy.sh", "notes.md"]

    def test_an_exemption_by_glob_covers_it(self):
        assert uncovered(["notes.md", "deploy.sh"], {"*.md": "prose"}) == ["deploy.sh"]

    def test_an_exemption_matches_on_the_bare_name_too(self):
        # `LICENSE` at the root and `python/LICENSE` are the same decision, and
        # requiring both spellings is how an exemption list goes stale.
        assert uncovered(["LICENSE", "python/LICENSE"], {"LICENSE": "licence text"}) == []

    def test_a_path_glob_still_works(self):
        assert uncovered([".github/workflows/ci.yml"], {".github/workflows/*": "CI"}) == []


class TestStaleExemptions:
    """An exemption matching nothing is a decision about a repo that changed."""

    def test_an_exemption_matching_nothing_is_reported(self):
        assert unused_globs(["app/x.py"], {"*.md": "prose"}) == ["*.md"]

    def test_an_exemption_that_matches_is_not(self):
        assert unused_globs(["notes.md"], {"*.md": "prose"}) == []


class TestConfiguration:
    def test_a_reason_is_required(self, repo):
        # A bare list is what lets a file type go ungraded for a year without
        # anyone choosing it. The reason is the decision.
        repo.write(".lore-eden-gates.json", json.dumps({"ungated_globs": {"*.md": ""}}) + "\n")
        repo.commit("config")
        with pytest.raises(HouseRulesError, match="non-empty reason"):
            load_house_rules(Path(repo.root))

    def test_a_list_instead_of_a_mapping_is_refused(self, repo):
        repo.write(".lore-eden-gates.json", json.dumps({"ungated_globs": ["*.md"]}) + "\n")
        repo.commit("config")
        with pytest.raises(HouseRulesError, match="must be an object"):
            load_house_rules(Path(repo.root))

    def test_a_valid_mapping_loads(self, repo):
        repo.write(
            ".lore-eden-gates.json",
            json.dumps({"ungated_globs": {"*.md": "prose"}}) + "\n",
        )
        repo.commit("config")
        assert load_house_rules(Path(repo.root)).ungated_globs == {"*.md": "prose"}

    def test_the_default_is_no_exemptions(self, repo):
        assert load_house_rules(Path(repo.root)).ungated_globs == {}


#: The `repo` fixture commits a `.gitkeep`, which no gate grades — so every
#: end-to-end case here has to say something about it. Exempting it in the
#: baseline keeps each test about the one file it is actually testing.
BASELINE = {
    ".gitkeep": "an empty-directory marker, with nothing in it to grade",
    # The configuration file is itself a tracked `.json`, so a repo that
    # configures this rule must account for the file doing the configuring.
    "*.json": "configuration; malformed JSON fails the tool that reads it",
}


def config(**ungated: str) -> str:
    return json.dumps({"ungated_globs": {**BASELINE, **ungated}}) + "\n"


class TestEndToEnd:
    def test_it_fails_on_an_uncovered_file_and_names_it(self, repo):
        repo.write(".lore-eden-gates.json", config())
        repo.write("deploy.sh", "echo hi\n")
        repo.commit("script")
        result = repo.gate("gate_coverage_check.py", "--repo", str(repo.root))
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "deploy.sh" in out
        assert "1 uncovered" in out

    def test_it_passes_once_the_decision_is_recorded(self, repo):
        repo.write("deploy.sh", "echo hi\n")
        repo.write(".lore-eden-gates.json", config(**{"*.sh": "shellcheck is not a dependency here"}))
        repo.commit("script")
        result = repo.gate("gate_coverage_check.py", "--repo", str(repo.root))
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "file coverage passed" in out

    def test_it_reports_the_counts_even_when_it_passes(self, repo):
        # "coverage passed" over an empty index is the failure this rule is
        # about, so the numbers are the finding on success too.
        repo.write(".lore-eden-gates.json", config())
        repo.write("app/x.py", "A = 1\n")
        repo.commit("code")
        result = repo.gate("gate_coverage_check.py", "--repo", str(repo.root))
        out = result.stdout + result.stderr
        assert "graded by a gate" in out and "0 uncovered" in out, out

    def test_a_stale_exemption_fails_the_run(self, repo):
        repo.write("app/x.py", "A = 1\n")
        repo.write(".lore-eden-gates.json", config(**{"*.rb": "no ruby here any more"}))
        repo.commit("code")
        result = repo.gate("gate_coverage_check.py", "--repo", str(repo.root))
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "match nothing" in out and "*.rb" in out

    def test_the_index_is_what_is_read_not_the_working_tree(self, repo):
        # An untracked file is not something this repository ships, and walking
        # the tree instead would grade node_modules and every build artifact.
        repo.write(".lore-eden-gates.json", config())
        repo.write("app/x.py", "A = 1\n")
        repo.commit("code")
        (Path(repo.root) / "untracked.sh").write_text("echo hi\n", encoding="utf-8")
        result = repo.gate("gate_coverage_check.py", "--repo", str(repo.root))
        assert result.returncode == 0, result.stdout + result.stderr

    def test_css_is_among_the_suffixes_a_gate_now_grades(self):
        # The regression this whole change is about.
        assert ".css" in GATED_SUFFIXES

    def test_tracked_paths_reads_real_git(self, repo):
        repo.write("app/x.py", "A = 1\n")
        repo.commit("code")
        assert "app/x.py" in tracked_paths(Path(repo.root))

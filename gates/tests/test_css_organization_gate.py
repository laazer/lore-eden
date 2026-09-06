"""The CSS gate, rule by rule, each with a planted violation.

CSS was ungraded entirely: lefthook's globs match `.py`, `.ts` and `.tsx`, so a
commit touching only stylesheets ran no gate and the stage printed "no files for
inspection". The defect that prompted this was 81 declarations naming a custom
property nothing defines — invisible for the whole life of the files.

Only the undefined-token rule fires on this repo's own stylesheets today. The
other four would pass whether they worked or not, so each one here plants the
violation it is meant to catch and asserts the clean form beside it.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from css_organization_check import (  # noqa: E402
    check_file,
    load_token_names_and_colours,
    normalised_colour,
    third_party_packages,
)
from house_rules import HouseRulesError, load_house_rules  # noqa: E402

TOKENS_TS = """
const specs = define({
  accent: { css: "--accent", value: "#34d77f" },
  surface2: { css: "--surface-2", value: "#2b2a37" },
  fsBody: { css: "--fs-body", value: "13.5px" },
  border: { css: "--border", value: "rgba(139,123,216,.18)" },
});
"""


def css_repo(repo):
    """A repo with a token source, a manifest, and an importer for `app/x.css`.

    The importer is here rather than in each test because the orphan rule is a
    whole-file property: without it every fixture is an orphan and every test in
    this module reports two findings instead of the one it is about. The orphan
    tests use their own filename, which nothing imports.
    """
    repo.write("tokens.ts", TOKENS_TS)
    repo.write(
        ".lore-eden-gates.json", json.dumps({"css_token_source": "tokens.ts"}) + "\n"
    )
    repo.write("package.json", json.dumps({"dependencies": {"react-datepicker": "^7"}}) + "\n")
    repo.write("app/App.tsx", "import './x.css';\nexport const A = 1;\n")
    repo.commit("layout")
    return repo


def findings(repo, relpath: str, *, net_growing: bool = False) -> list[str]:
    """Every rule against one file, unscoped — the whole-file answer.

    `touched_lines=None` is what a run with no diff to scope against gets, and
    it is the honest setting for a unit test: scoping is `resolve_gate_scope`'s
    job and is tested where it lives.
    """
    root = Path(repo.root)
    rules = load_house_rules(root)
    names, colours = load_token_names_and_colours(root, rules)
    path = root / relpath
    return check_file(
        path,
        content=path.read_text(encoding="utf-8"),
        touched_lines=None,
        net_growing=net_growing,
        repo=root,
        token_names=names,
        token_colours=colours,
        third_party=third_party_packages(root),
    )


class TestUndefinedToken:
    """The defect this gate was built for: `var(--fsBody)` where it is `--fs-body`."""

    def test_a_camelcase_spelling_of_a_real_token_is_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { font-size: var(--fsBody); }\n")
        found = findings(repo, "app/x.css")
        assert len(found) == 1, found
        assert "--fsBody" in found[0] and "nothing defines" in found[0]

    def test_the_correct_spelling_is_not(self, repo):
        # The control. Without this the rule could be flagging every var().
        css_repo(repo)
        repo.write("app/x.css", ".a { font-size: var(--fs-body); }\n")
        assert findings(repo, "app/x.css") == []

    def test_a_fallback_saves_the_declaration_so_it_is_not_flagged(self, repo):
        # `var(--x, 12px)` renders the fallback; the declaration is not dropped.
        css_repo(repo)
        repo.write("app/x.css", ".a { font-size: var(--nope, 12px); }\n")
        assert findings(repo, "app/x.css") == []

    def test_a_property_the_same_file_declares_is_not_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ":root { --local-gap: 4px; }\n.a { gap: var(--local-gap); }\n")
        assert findings(repo, "app/x.css") == []

    def test_a_reference_inside_a_comment_is_not_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", "/* use var(--madeUp) here */\n.a { color: red; }\n")
        assert findings(repo, "app/x.css") == []


class TestDuplicateColour:
    """A colour literal that is already a token, in either notation."""

    def test_a_hex_literal_matching_a_token_is_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { color: #34d77f; }\n")
        found = findings(repo, "app/x.css")
        assert len(found) == 1 and "--accent" in found[0], found

    def test_the_same_colour_written_as_rgb_is_flagged_too(self, repo):
        # #34d77f is rgb(52, 215, 127). Catching only one spelling would have
        # missed every rgba() in this repo's own stylesheets.
        css_repo(repo)
        repo.write("app/x.css", ".a { color: rgba(52, 215, 127, 0.28); }\n")
        found = findings(repo, "app/x.css")
        assert len(found) == 1 and "--accent" in found[0], found

    def test_a_colour_that_is_not_a_token_is_not_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { color: #123456; }\n")
        assert findings(repo, "app/x.css") == []

    def test_the_waiver_is_honoured(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { color: #34d77f; } /* css-org: allow-colour (screenshot) */\n")
        assert findings(repo, "app/x.css") == []

    def test_uppercase_and_shorthand_hex_normalise(self):
        assert normalised_colour("#6faE8f") == "6fae8f"
        assert normalised_colour("#FFF") == "ffffff"
        assert normalised_colour("rgb(111, 174, 143)") == "6fae8f"
        assert normalised_colour("rgba(111,174,143,.28)") == "6fae8f"
        assert normalised_colour("12px") is None


class TestImportant:
    """`!important` needs a waiver naming a package we do not control."""

    def test_a_bare_important_is_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { z-index: 9 !important; }\n")
        found = findings(repo, "app/x.css")
        assert len(found) == 1 and "specificity" in found[0], found

    def test_a_waiver_naming_a_real_dependency_is_allowed(self, repo):
        css_repo(repo)
        repo.write(
            "app/x.css",
            ".a { z-index: 9 !important; } /* css-org: allow-important (react-datepicker) */\n",
        )
        assert findings(repo, "app/x.css") == []

    def test_a_waiver_naming_something_that_is_not_a_dependency_is_refused(self, repo):
        # A waiver nobody verifies is a comment. This is the whole reason the
        # exception names the package rather than just saying "needed".
        css_repo(repo)
        repo.write(
            "app/x.css",
            ".a { z-index: 9 !important; } /* css-org: allow-important (made-up-lib) */\n",
        )
        found = findings(repo, "app/x.css")
        assert len(found) == 1 and "not a dependency" in found[0], found

    def test_a_waiver_naming_one_of_our_own_packages_is_refused(self, repo):
        # The exception is for a stylesheet we do not control. Ours we can fix.
        css_repo(repo)
        repo.write("package.json", json.dumps({"dependencies": {"@lore-eden/ui": "^1"}}) + "\n")
        repo.write(
            "app/x.css",
            ".a { z-index: 9 !important; } /* css-org: allow-important (@lore-eden/ui) */\n",
        )
        found = findings(repo, "app/x.css")
        assert len(found) == 1 and "which is ours" in found[0], found

    def test_the_waiver_may_sit_on_the_line_above(self, repo):
        css_repo(repo)
        repo.write(
            "app/x.css",
            "/* css-org: allow-important (react-datepicker) */\n.a { z-index: 9 !important; }\n",
        )
        assert findings(repo, "app/x.css") == []


class TestFileLength:
    def test_a_long_file_that_is_growing_is_flagged(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { color: red; }\n" * 601)
        found = findings(repo, "app/x.css", net_growing=True)
        assert len(found) == 1 and "max 600" in found[0], found

    def test_the_same_file_shrinking_is_not(self, repo):
        # The control that matters: a cap firing on any touch blocks the
        # cleanup that would shorten the file.
        css_repo(repo)
        repo.write("app/x.css", ".a { color: red; }\n" * 601)
        assert findings(repo, "app/x.css", net_growing=False) == []

    def test_a_short_file_is_not(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { color: red; }\n" * 10)
        assert findings(repo, "app/x.css", net_growing=True) == []


class TestOrphan:
    def test_a_stylesheet_nothing_imports_is_flagged(self, repo):
        css_repo(repo)
        repo.write("app/lonely.css", ".a { color: red; }\n")
        found = findings(repo, "app/lonely.css")
        assert len(found) == 1 and "nothing imports" in found[0], found

    def test_an_imported_stylesheet_is_not(self, repo):
        css_repo(repo)
        repo.write("app/lonely.css", ".a { color: red; }\n")
        repo.write("app/Other.tsx", "import './lonely.css';\n")
        assert findings(repo, "app/lonely.css") == []

    def test_a_stylesheet_reached_by_css_import_is_not(self, repo):
        css_repo(repo)
        repo.write("app/lonely.css", ".a { color: red; }\n")
        repo.write("app/main.css", '@import "./lonely.css";\n')
        assert findings(repo, "app/lonely.css") == []

    def test_the_waiver_is_honoured(self, repo):
        css_repo(repo)
        repo.write(
            "app/lonely.css",
            "/* css-org: allow-orphan (loaded by the host app) */\n.a{color:red}\n",
        )
        assert findings(repo, "app/lonely.css") == []


class TestTokenSourceConfiguration:
    def test_no_token_source_turns_the_token_rules_off(self, repo):
        repo.write("app/x.css", ".a { color: var(--madeUp); }\n")
        repo.commit("css")
        root = Path(repo.root)
        names, colours = load_token_names_and_colours(root, load_house_rules(root))
        assert names == frozenset() and colours == {}

    def test_a_token_source_that_is_not_there_raises(self, repo):
        repo.write(".lore-eden-gates.json", json.dumps({"css_token_source": "nope.ts"}) + "\n")
        repo.commit("config")
        root = Path(repo.root)
        with pytest.raises(HouseRulesError, match="not a file"):
            load_token_names_and_colours(root, load_house_rules(root))

    def test_a_token_source_defining_nothing_raises(self, repo):
        # Silence here would be a rule that is on, reads an empty token set, and
        # flags every var() in the repo — or, worse, flags none.
        repo.write("tokens.ts", "export const nothing = 1;\n")
        repo.write(".lore-eden-gates.json", json.dumps({"css_token_source": "tokens.ts"}) + "\n")
        repo.commit("config")
        root = Path(repo.root)
        with pytest.raises(HouseRulesError, match="defines no custom properties"):
            load_token_names_and_colours(root, load_house_rules(root))

    def test_a_css_token_source_is_read_too(self, repo):
        repo.write("theme.css", ":root { --accent: #34d77f; --gap: 4px; }\n")
        repo.write(".lore-eden-gates.json", json.dumps({"css_token_source": "theme.css"}) + "\n")
        repo.commit("config")
        root = Path(repo.root)
        names, colours = load_token_names_and_colours(root, load_house_rules(root))
        assert names == {"--accent", "--gap"}
        assert colours == {"34d77f": "--accent"}


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
class TestEndToEnd:
    def test_the_gate_reports_the_examined_count_and_fails(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { font-size: var(--fsBody); }\n")
        result = repo.gate(
            "css_organization_check.py", "--repo", str(repo.root), "--scope", "worktree"
        )
        out = result.stdout + result.stderr
        assert "examined 1 file(s)" in out, out
        assert result.returncode == 1, out
        assert "--fsBody" in out

    def test_a_clean_stylesheet_passes_having_examined_it(self, repo):
        css_repo(repo)
        repo.write("app/x.css", ".a { font-size: var(--fs-body); }\n")
        repo.write("app/App.tsx", "import './x.css';\n")
        result = repo.gate(
            "css_organization_check.py", "--repo", str(repo.root), "--scope", "worktree"
        )
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "examined 1 file(s)" in out
        assert "examined 0 file(s)" not in out

    def test_an_unconfigured_repo_says_the_token_rules_are_off(self, repo):
        # Saying so on every run, in both invocation forms: a rule that is off
        # and silent is indistinguishable from one that found nothing.
        repo.write("app/x.css", ".a { color: var(--madeUp); }\n")
        result = repo.gate(
            "css_organization_check.py", "--repo", str(repo.root), "--scope", "worktree"
        )
        assert "token rules are off" in result.stdout + result.stderr

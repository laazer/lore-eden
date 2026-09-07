"""The shell gate, which closed a gap the coverage check had recorded.

`*.sh` was an exemption reading KNOWN GAP: nine tracked scripts, two of them the
ones git executes on every commit and push, and nothing that had ever read one.
Recording a gap beats not knowing about it and is worse than closing it.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lore_eden_gates"))

from sh_shellcheck_check import FAILING_LEVELS, run_shellcheck  # noqa: E402

# `shellcheck-py` installs the binary into the same bin directory as the
# interpreter running these tests, which is not on PATH when pytest is invoked
# as `python/.venv/bin/python -m pytest`. Without this every test here skips —
# and nine silent skips look exactly like nine passes in a summary line, which
# is the failure this whole library is about.
_BIN = Path(sys.executable).parent
if shutil.which("shellcheck") is None and (_BIN / "shellcheck").exists():
    os.environ["PATH"] = f"{_BIN}{os.pathsep}{os.environ.get('PATH', '')}"

pytestmark = pytest.mark.skipif(
    shutil.which("shellcheck") is None, reason="shellcheck not installed"
)

UNQUOTED = "#!/bin/sh\nrm $UNQUOTED\n"
CLEAN = '#!/bin/sh\nset -eu\ntarget="${1:-}"\nprintf "%s\\n" "$target"\n'


def run(repo, relpath: str, scope: str = "worktree"):
    return repo.gate("sh_shellcheck_check.py", "--repo", str(repo.root), "--scope", scope)


class TestItActuallyReadsScripts:
    def test_an_unquoted_variable_fails(self, repo):
        repo.write("deploy.sh", UNQUOTED)
        result = run(repo, "deploy.sh")
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "SC2086" in out
        assert "examined 1 file(s)" in out

    def test_a_clean_script_passes_having_examined_it(self, repo):
        repo.write("deploy.sh", CLEAN)
        result = run(repo, "deploy.sh")
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "examined 1 file(s)" in out
        assert "examined 0 file(s)" not in out

    def test_info_is_a_failing_level(self):
        # The bug this pins. SC2086 — an unquoted variable, the most common
        # shell defect there is — is severity `info`, and shellcheck's gcc
        # format collapses `info` and `style` into one label, `note`. A first
        # draft read gcc output and excluded notes, so it passed a planted
        # `rm $UNQUOTED`. Reading JSON is what makes the two separable.
        assert "info" in FAILING_LEVELS
        assert "style" not in FAILING_LEVELS

    def test_style_findings_do_not_fail_the_gate(self, repo):
        # `[ x = y ]` over `[[ ]]` and friends are style advice. A gate that
        # fails on advice is one people turn off rather than argue with.
        repo.write("deploy.sh", '#!/bin/sh\nset -eu\nx=1\nif [ "$x" = 1 ]; then echo hi; fi\n')
        result = run(repo, "deploy.sh")
        assert result.returncode == 0, result.stdout + result.stderr


class TestSourcedFiles:
    def test_a_sourced_sibling_is_followed(self, repo):
        # -x plus --source-path=SCRIPTDIR. Without -x shellcheck will not open
        # the sourced file; without SCRIPTDIR it resolves the directive against
        # the current directory instead of the script's own and still cannot
        # find it. Either alone leaves SC1091 on every script that sources one,
        # which is three of this repo's own.
        # `export`, because shellcheck is right that a bare assignment in a
        # sourced file is an unused variable from its own file's point of view.
        repo.write("scripts/helper.sh", "#!/bin/sh\nexport HELPER=1\n")
        repo.write(
            "scripts/main.sh",
            '#!/bin/sh\nset -eu\n# shellcheck source=helper.sh\n. "$(dirname "$0")/helper.sh"\n'
            'printf "%s\\n" "$HELPER"\n',
        )
        result = run(repo, "scripts/main.sh")
        out = result.stdout + result.stderr
        assert result.returncode == 0, out
        assert "SC1091" not in out


class TestMissingTool:
    def test_an_absent_shellcheck_refuses_rather_than_reporting_clean(self, repo, tmp_path):
        # "The linter is not installed" and "the scripts are clean" produce the
        # same empty output. A machine without it would otherwise report a pass
        # on every commit, indefinitely — the exact failure `require_tool_ran`
        # exists to refuse for ruff and pylint.
        only_python = tmp_path / "path"
        only_python.mkdir()
        for tool in ("git",):
            (only_python / tool).symlink_to(shutil.which(tool))
        repo.write("deploy.sh", UNQUOTED)
        result = repo.gate(
            "sh_shellcheck_check.py",
            "--repo",
            str(repo.root),
            "--scope",
            "worktree",
            env_overlay={"PATH": str(only_python)},
        )
        out = result.stdout + result.stderr
        assert result.returncode == 1, out
        assert "shellcheck is not installed" in out
        assert "passed" not in out


class TestScoping:
    def test_a_pre_existing_finding_on_an_untouched_line_does_not_block(self, repo):
        # Don't-make-it-worse, like every other gate here: a script full of
        # existing warnings must not fail an unrelated one-line edit.
        repo.write("deploy.sh", UNQUOTED)
        repo.commit("with a finding")
        repo.write("deploy.sh", UNQUOTED + 'printf "ok\\n"\n')
        result = run(repo, "deploy.sh")
        assert result.returncode == 0, result.stdout + result.stderr

    def test_the_same_finding_on_a_touched_line_does(self, repo):
        repo.write("deploy.sh", '#!/bin/sh\nprintf "ok\\n"\n')
        repo.commit("clean")
        repo.write("deploy.sh", '#!/bin/sh\nprintf "ok\\n"\nrm $UNQUOTED\n')
        result = run(repo, "deploy.sh")
        assert result.returncode == 1, result.stdout + result.stderr


class TestUnreadableOutput:
    def test_a_non_json_answer_is_not_treated_as_clean(self, repo, tmp_path, monkeypatch):
        # Unparseable output means the tool did not deliver a verdict. Reading
        # that as "no findings" is a pass over unread scripts.
        fake = tmp_path / "path"
        fake.mkdir()
        stub = fake / "shellcheck"
        stub.write_text("#!/bin/sh\necho 'not json'\nexit 1\n")
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{fake}:{Path(sys.executable).parent}")
        with pytest.raises(Exception, match="could not read"):
            run_shellcheck([Path(repo.root) / "x.sh"], Path(repo.root))

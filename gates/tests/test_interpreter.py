"""Refusing an old interpreter in words, and doing it before the crash.

The bug this pins: on Python 3.9 every organization-family gate died at import
with ``AttributeError: module 'ast' has no attribute 'match_case'`` — a message
that names no version, no interpreter and no fix, and that a consuming
workspace received on every commit from a repo it does not have checked out.

Two properties keep it fixed, and they are different claims:

- the message says what is needed, what was found, and what to do; and
- the check runs *before* the import that needs the version, because a check
  that ran afterwards would never run at all.
"""

from __future__ import annotations

import io
import subprocess
import sys

import pytest
from conftest import GATES_DIR
from interpreter import (
    EXIT_UNSUPPORTED_INTERPRETER,
    require_python,
    unsupported_interpreter_message,
)


class TestTheMessage:
    def test_it_names_the_version_the_interpreter_and_the_fix(self) -> None:
        message = unsupported_interpreter_message((3, 10), (3, 9, 7), "/usr/bin/python3")

        assert "3.10" in message
        assert "3.9.7" in message
        assert "/usr/bin/python3" in message
        # The actionable half. Without it the reader knows they are stuck and
        # not what to do, which is most of what the AttributeError already did.
        assert "PATH" in message

    def test_it_says_nothing_about_ast_attributes(self) -> None:
        """The cause is a version, not a missing attribute. Naming the symbol
        sends the reader to read our source instead of their PATH."""
        message = unsupported_interpreter_message((3, 10), (3, 9, 7), "/usr/bin/python3")

        assert "AttributeError" not in message
        assert "match_case" not in message


class TestTheGuard:
    def test_an_old_interpreter_is_refused_with_a_nonzero_exit(self) -> None:
        stream = io.StringIO()

        with pytest.raises(SystemExit) as exit_info:
            require_python((3, 10), (3, 9, 7), "/usr/bin/python3", stream)

        assert exit_info.value.code == EXIT_UNSUPPORTED_INTERPRETER
        assert exit_info.value.code != 0
        assert "3.9.7" in stream.getvalue()

    def test_the_minimum_itself_passes(self) -> None:
        """Off-by-one on the boundary would reject exactly the version the
        project requires."""
        require_python((3, 10), (3, 10, 0), "/usr/bin/python3", io.StringIO())

    def test_a_newer_interpreter_passes_and_prints_nothing(self) -> None:
        stream = io.StringIO()

        require_python((3, 10), (3, 12, 4), "/usr/bin/python3", stream)

        assert stream.getvalue() == ""

    def test_the_interpreter_running_these_tests_passes(self) -> None:
        """The control. Every test above drives an injected version; this one
        proves the real defaults are wired to the real interpreter."""
        require_python()


class TestItFiresBeforeTheImportThatNeedsIt:
    """Ordering is the whole fix, so it is asserted end to end.

    ``sys.version_info`` is reassignable, so a current interpreter can be made
    to report an old one. That is enough: the question is whether the guard runs
    before the local imports, not whether ``ast`` really lacks the attribute.
    """

    def _import_under_fake_version(self, module: str) -> subprocess.CompletedProcess:
        script = (
            "import sys;"
            f"sys.path.insert(0, {str(GATES_DIR)!r});"
            "sys.version_info = (3, 9, 7);"
            f"import {module}"
        )
        return subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True, check=False
        )

    @pytest.mark.parametrize(
        "module",
        [
            "py_organization_check",
            "py_silent_except_check",
            "py_git_subprocess_check",
            "py_defensive_normalization_check",
            "ruff_complexity_diff_filter",
            "pylint_diff_filter",
        ],
    )
    def test_the_gate_reports_rather_than_crashes(self, module: str) -> None:
        result = self._import_under_fake_version(module)

        assert result.returncode == EXIT_UNSUPPORTED_INTERPRETER
        assert "need Python 3.10 or newer" in result.stderr
        assert "Traceback" not in result.stderr


class TestEveryInstalledPythonGateIsGuarded:
    """A new gate must not be able to ship without this.

    Read from the installer's own table rather than a list here, so adding a
    gate to what workspaces run is what makes this test cover it.
    """

    def test_all_of_them_call_require_python(self) -> None:
        from install_workspace_hooks import MANAGED_GATES

        python_gates = [gate.script for gate in MANAGED_GATES if gate.runner == "python3"]
        assert python_gates, "the installer stopped installing python gates"

        unguarded = [
            script
            for script in python_gates
            if "require_python()" not in (GATES_DIR / script).read_text(encoding="utf-8")
        ]

        assert unguarded == []


def test_the_match_annotation_is_not_evaluated_at_import() -> None:
    """Why `py_string_vocab` carries `from __future__ import annotations`.

    ``ast.match_case`` is 3.10+, and as a bare annotation it was evaluated when
    the function was defined — which is how a type hint took down a gate. The
    guard above means no 3.9 interpreter reaches this module any more; the
    future import means the module would survive one that did.
    """
    import py_string_vocab

    annotation = py_string_vocab._match_case_literals.__annotations__["case"]

    assert annotation == "ast.match_case"
    assert isinstance(annotation, str)


def test_the_guard_module_itself_survives_an_ancient_interpreter() -> None:
    """It has to be importable by the interpreter it exists to reject, so it
    may use nothing newer than that interpreter understands."""
    source = (GATES_DIR / "interpreter.py").read_text(encoding="utf-8")

    compiled = compile(source, "interpreter.py", "exec")

    assert compiled is not None
    assert "match " not in source

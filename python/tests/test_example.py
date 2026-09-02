"""The end-to-end example, run as a test.

An example that is not executed rots into a lie within two refactors. This runs
the same script the README points at, and asserts the things it is meant to
demonstrate actually happened — that the reject edge fired, that the denied tool
was denied, that the work reached its terminal stage.

It also asserts the example uses only **public** names. That check is the real
product of this ticket: anything the example could not do without reaching into
a private module is an API gap, and the point of writing a host was to find them.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
sys.path.insert(0, str(EXAMPLES))

from draft_and_critique import run  # noqa: E402


def _is_docstring(node: ast.stmt) -> bool:
    return (
        isinstance(node, ast.Expr)  # py-org: allow-isinstance
        and isinstance(node.value, ast.Constant)  # py-org: allow-isinstance
        and isinstance(node.value.value, str)  # py-org: allow-isinstance
    )


@pytest.fixture
def result(tmp_path: Path) -> dict:
    return run(tmp_path, script={"draft": "write", "critique": "reject-then-pass"})


class TestItRuns:
    def test_reaches_the_terminal_stage(self, result: dict) -> None:
        assert result["history"][-1] == "critique:pass"

    def test_the_reject_edge_fires_and_sends_it_back(self, result: dict) -> None:
        # The edge that makes this a workflow rather than a list of steps.
        assert result["history"] == [
            "draft:pass",
            "critique:reject",
            "draft:pass",
            "critique:pass",
        ]

    def test_the_denied_tool_was_denied(self, result: dict) -> None:
        # Asked for on every stage, refused every time.
        assert len(result["denied"]) == len(result["history"])

    def test_the_agents_could_call_the_hosts_tools(self, result: dict) -> None:
        assert result["tools"] == ["read_document", "write_document"]

    def test_every_stage_left_a_run_record(self, result: dict) -> None:
        assert result["runs"] == len(result["history"])

    def test_a_rerun_is_not_decided_by_the_last_one(self, tmp_path: Path) -> None:
        # The example's fake agent keeps attempt state in the workspace. It
        # once kept it next to the script, and a file left by one run silently
        # made the next one skip its reject — an example that passed while
        # demonstrating nothing.
        first = run(tmp_path / "a", script={"draft": "write", "critique": "reject-then-pass"})
        second = run(tmp_path / "b", script={"draft": "write", "critique": "reject-then-pass"})
        assert first["history"] == second["history"]


class TestItUsesOnlyPublicApi:
    """Anything private the example needed is an API gap, not a workaround."""

    def test_no_underscore_prefixed_imports(self) -> None:
        source = (EXAMPLES / "draft_and_critique.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        private: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(  # py-org: allow-isinstance
                "lore_eden"
            ):
                private += [
                    f"{node.module}.{alias.name}"
                    for alias in node.names
                    if alias.name.startswith("_")
                ]
                if any(part.startswith("_") for part in (node.module or "").split(".")):
                    private.append(node.module or "")
        assert private == [], f"the example reached for private names: {private}"

    def test_every_imported_name_is_in_its_packages_all(self) -> None:
        # A name importable but absent from __all__ is one a host cannot
        # discover, which is the same gap in a quieter form.
        import importlib

        source = (EXAMPLES / "draft_and_critique.py").read_text(encoding="utf-8")
        missing: list[str] = []
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.ImportFrom):  # py-org: allow-isinstance
                continue
            module = node.module or ""
            if not module.startswith("lore_eden"):
                continue
            exported = getattr(importlib.import_module(module), "__all__", None)
            if exported is None:
                continue
            missing += [
                f"{module}.{alias.name}"
                for alias in node.names
                if alias.name not in exported
            ]
        assert missing == [], f"imported but not exported: {missing}"


class TestItIsNotAboutSoftware:
    """A harness that can only express its authors' job is not general."""

    VOCABULARY = ("ticket", "acceptance criteria", "pull request", "code review", "merge")

    def test_the_example_names_no_sdlc_concept(self) -> None:
        # The *code*, not the prose. The module docstring says the example uses
        # no tickets or acceptance criteria, and a check that scanned it would
        # fail on the sentence explaining the very property it is testing.
        tree = ast.parse((EXAMPLES / "draft_and_critique.py").read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not _is_docstring(node)]
        code = ast.unparse(tree).lower()
        found = [word for word in self.VOCABULARY if word in code]
        assert found == [], f"the example leaked SDLC vocabulary: {found}"

    def test_the_prompts_reaching_the_agent_name_none_either(self, tmp_path: Path) -> None:
        run(tmp_path, script={"draft": "write", "critique": "reject-then-pass"})
        prompts = list((tmp_path / ".prompts").glob("*.md"))
        assert prompts, "the runner should have written a prompt file per stage"
        for prompt in prompts:
            text = prompt.read_text(encoding="utf-8").lower()
            assert not any(word in text for word in self.VOCABULARY)


class TestItRunsAsAScript:
    def test_the_readme_command_works(self) -> None:
        # The README tells a reader to run this. If it only works as an import,
        # the README is wrong.
        completed = subprocess.run(
            [sys.executable, str(EXAMPLES / "draft_and_critique.py")],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "critique:pass" in completed.stdout

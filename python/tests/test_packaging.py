"""What a built distribution must carry, checked against the source tree.

The wheel is the thing a consumer installs, and until this ticket nothing had
ever built one. Two of these assertions exist because the obvious spelling
failed silently: `license-files = ["../LICENSE"]` was accepted by setuptools
without complaint and shipped no licence at all — metadata claiming AGPL with
nothing behind it.
"""

from __future__ import annotations

from pathlib import Path

# tomllib is 3.11+; this package's floor is 3.10.
import tomli

REPO = Path(__file__).resolve().parents[2]
PYPROJECT = REPO / "python" / "pyproject.toml"


def config() -> dict:
    return tomli.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestTheLicenceIsRealRatherThanDeclared:
    def test_the_packaged_copy_matches_the_repository_licence(self) -> None:
        """A copy, because setuptools cannot reach `../`. A copy that drifts is
        worse than no copy, so it is compared rather than trusted."""
        packaged = (REPO / "python" / "LICENSE").read_text(encoding="utf-8")
        root = (REPO / "LICENSE").read_text(encoding="utf-8")

        assert packaged == root

    def test_the_licence_file_is_declared_and_inside_the_project(self) -> None:
        declared = config()["project"]["license-files"]

        assert declared == ["LICENSE"]
        for name in declared:
            assert not name.startswith(".."), (
                f"{name} is outside the project directory; setuptools accepts it "
                "silently and ships nothing"
            )

    def test_the_expression_matches_the_licence_actually_shipped(self) -> None:
        expression = config()["project"]["license"]

        assert "AGPL-3.0" in expression
        assert "AFFERO" in (REPO / "python" / "LICENSE").read_text(encoding="utf-8").upper()


class TestTheMetadataAConsumerReads:
    def test_the_fields_that_are_blank_by_default_are_filled(self) -> None:
        """Each of these is absent unless someone writes it, and each is what a
        package index shows instead of the package."""
        project = config()["project"]

        for field in ("readme", "license", "authors", "classifiers", "urls"):
            assert project.get(field), f"[project].{field} is missing"

    def test_the_declared_python_floor_appears_in_the_classifiers(self) -> None:
        """Two statements of the same fact, and they disagree the moment one is
        updated alone."""
        project = config()["project"]
        floor = project["requires-python"].lstrip(">=")

        assert f"Programming Language :: Python :: {floor}" in project["classifiers"]

    def test_the_gates_package_is_not_in_the_wheel(self) -> None:
        """It is invoked by path from a checkout. Shipping it inside an
        importable package would imply a second, unsupported way to reach it."""
        include = config()["tool"]["setuptools"]["packages"]["find"]["include"]

        assert include == ["lore_eden*"]

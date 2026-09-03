"""This repo's `.lore-eden-gates.json` must name helpers that actually exist.

Two of the gates name a repo's own helper in their findings, and both stay off
until told which one. A config that names a module nobody has is worse than
leaving the rule off: the rule fires, and every finding points at something the
reader cannot open.

Nothing else can catch that. The gates are stdlib-only and never import the
package, so they cannot check the name they were handed; the package does not
read the config. These tests are the join — they live here because this is the
side where `lore_eden` is importable.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / ".lore-eden-gates.json"


@pytest.fixture(scope="module")
def config() -> dict[str, str]:
    assert CONFIG_PATH.is_file(), f"expected gate config at {CONFIG_PATH}"
    return json.loads(CONFIG_PATH.read_text())


def _resolve(dotted: str) -> object:
    """Import ``pkg.module.attr``, walking the tail as attributes."""
    parts = dotted.split(".")
    for split in range(len(parts) - 1, 0, -1):
        try:
            module = importlib.import_module(".".join(parts[:split]))
        except ModuleNotFoundError:
            continue
        target: object = module
        for attribute in parts[split:]:
            target = getattr(target, attribute)  # py-org: allow-dynamic (resolving a config string)
        return target
    raise ModuleNotFoundError(dotted)


def test_the_mid_dot_helper_is_configured(config: dict[str, str]) -> None:
    """Configured on purpose: this repo ships the helper, so the rule applies to it."""
    assert config.get("mid_dot_helper"), "the mid-dot rule is off — lore_eden.dot_line exists"


def test_every_name_in_the_mid_dot_helper_exists(config: dict[str, str]) -> None:
    """The value is prose naming one or more objects, e.g. `pkg.mod.Dot / mid_dot`."""
    helper = config["mid_dot_helper"]
    module_path, _, _ = helper.partition(" / ")
    package = module_path.rsplit(".", 1)[0]

    assert _resolve(module_path) is not None

    for extra in helper.split(" / ")[1:]:
        assert _resolve(f"{package}.{extra.strip()}") is not None, extra


def test_the_git_helper_is_a_callable_that_exists(config: dict[str, str]) -> None:
    helper = config["git_subprocess_helper"]

    assert callable(_resolve(helper)), f"{helper} is not callable"


def test_the_git_helper_path_is_the_file_that_defines_it(config: dict[str, str]) -> None:
    """The path exempts one file from the gate. Pointed at the wrong file it
    would both exempt an ordinary module and flag the real wrapper."""
    declared = REPO_ROOT / config["git_subprocess_helper_path"]
    assert declared.is_file(), declared

    module_name = config["git_subprocess_helper"].rsplit(".", 1)[0]
    module = importlib.import_module(module_name)

    assert module.__file__ is not None
    assert Path(module.__file__).resolve() == declared.resolve()


def test_the_config_carries_no_key_the_gates_do_not_know(config: dict[str, str]) -> None:
    """The loader rejects an unknown key, so a typo fails the gate rather than
    silently disabling a rule. Asserted here too so the failure names the key.

    The known set is read from the gates' own loader rather than restated, so
    this does not become a third place the field names have to agree.
    """
    gates_dir = REPO_ROOT / "gates" / "lore_eden_gates"
    sys.path.insert(0, str(gates_dir))
    try:
        house_rules = importlib.import_module("house_rules")
    finally:
        sys.path.remove(str(gates_dir))

    known = set(house_rules._FIELDS)

    assert known, "the gates' field set came back empty — this test proved nothing"
    assert set(config) <= known, f"unknown key(s): {sorted(set(config) - known)}"

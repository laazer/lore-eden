"""The config layer that replaced the hardcoded helper names."""

from __future__ import annotations

import json

import pytest
from house_rules import HouseRules, HouseRulesError, load_house_rules


def test_absent_config_disables_the_opt_in_rules(repo):
    rules = load_house_rules(repo.root)
    assert rules == HouseRules()
    assert not rules.mid_dot_enabled
    assert not rules.git_subprocess_enabled


def test_no_repo_yields_empty_rules():
    assert load_house_rules(None) == HouseRules()


def test_configured_helpers_enable_their_rules(repo):
    repo.write(
        ".lore-eden-gates.json",
        json.dumps(
            {
                "mid_dot_helper": "myapp.dot_line.Dot",
                "git_subprocess_helper": "myapp.git.run_git",
                "git_subprocess_helper_path": "myapp/git.py",
            }
        ),
    )
    rules = load_house_rules(repo.root)
    assert rules.mid_dot_enabled and rules.git_subprocess_enabled
    assert rules.mid_dot_helper == "myapp.dot_line.Dot"


def test_unknown_key_raises_rather_than_being_ignored(repo):
    """A typo'd key that silently disabled a gate is the failure mode this
    library exists to remove, so it must not be tolerated in its own config."""
    repo.write(".lore-eden-gates.json", json.dumps({"mid_dot_helpr": "x"}))
    with pytest.raises(HouseRulesError, match="unknown key"):
        load_house_rules(repo.root)


def test_malformed_json_raises_rather_than_defaulting(repo):
    repo.write(".lore-eden-gates.json", "{not json")
    with pytest.raises(HouseRulesError, match="cannot read"):
        load_house_rules(repo.root)


def test_non_object_toplevel_raises(repo):
    repo.write(".lore-eden-gates.json", json.dumps(["a", "b"]))
    with pytest.raises(HouseRulesError, match="JSON object"):
        load_house_rules(repo.root)


def test_non_string_value_raises(repo):
    repo.write(".lore-eden-gates.json", json.dumps({"mid_dot_helper": 3}))
    with pytest.raises(HouseRulesError, match="must be a string"):
        load_house_rules(repo.root)


def test_git_helper_without_its_path_raises(repo):
    """Without the path the gate would flag the helper's own subprocess call —
    an unfixable finding on the one file that is allowed to make it."""
    repo.write(".lore-eden-gates.json", json.dumps({"git_subprocess_helper": "myapp.git.run_git"}))
    with pytest.raises(HouseRulesError, match="git_subprocess_helper_path"):
        load_house_rules(repo.root)

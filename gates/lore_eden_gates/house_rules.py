"""Per-repository configuration for the gates that name a repo's own helpers.

Most of what these gates enforce is universal: a 1500-line module is too long
anywhere, and a silently swallowed exception reports a success the code never
had. Two rules are different. Both say "route this through the helper we
already have", and neither can say *which* helper without being told:

- the mid-dot rule, which asks for a label-joining helper instead of a third
  hand-rolled ``f"… · …"`` in one function;
- the git-subprocess rule, which asks for a wrapper that scrubs ``GIT_DIR`` and
  ``GIT_WORK_TREE`` before shelling out to ``git``/``gh``, because ``GIT_DIR``
  beats ``cwd`` and an unscrubbed call operates on the wrong repository.

A repo that has no such helper cannot act on either finding, so both rules stay
**off until configured**. That is the whole reason this module exists: the gates
were extracted from a codebase where the helper names were hardcoded, and a
hardcoded name is a message naming a module the reader's repo does not have.

Configuration lives in ``.lore-eden-gates.json`` at the repo root::

    {
      "mid_dot_helper": "myapp.dot_line.Dot / mid_dot",
      "git_subprocess_helper": "myapp.services.git_subprocess.run_git",
      "git_subprocess_helper_path": "myapp/services/git_subprocess.py"
    }

JSON rather than TOML because these gates are invoked as plain ``python3``
against arbitrary repos, and ``tomllib`` only exists from 3.11. A gate that
cannot start on the interpreter it is handed is a gate that does not run.

Absent file means "no house rules", which is a supported configuration and the
default for a repo that has not opted in. A file that exists but cannot be
parsed, or that carries a key this version does not know, raises instead: a
typo'd key that silently disables a gate is the failure this whole library is
meant to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = ".lore-eden-gates.json"


class HouseRulesError(RuntimeError):
    """The repo's gate configuration exists but could not be used."""


@dataclass(frozen=True)
class HouseRules:
    """Repo-specific helper names the shared gates point people at.

    Every field defaults to empty, and empty means the rule that needs it does
    not run. A gate must never guess a helper name.
    """

    #: How to name the label-joining helper in the mid-dot finding, e.g.
    #: ``"myapp.dot_line.Dot / mid_dot"``. Empty disables the mid-dot rule.
    mid_dot_helper: str = ""

    #: How to name the git wrapper in the git-subprocess finding, e.g.
    #: ``"myapp.services.git_subprocess.run_git"``. Empty disables that rule.
    git_subprocess_helper: str = ""

    #: Repo-relative path of the module *implementing* that wrapper, so the
    #: gate does not flag the helper's own ``subprocess`` call. Empty means the
    #: helper is not exempted, so set it whenever ``git_subprocess_helper`` is
    #: set.
    git_subprocess_helper_path: str = ""

    @property
    def mid_dot_enabled(self) -> bool:
        return bool(self.mid_dot_helper)

    @property
    def git_subprocess_enabled(self) -> bool:
        return bool(self.git_subprocess_helper)


_FIELDS = {
    "mid_dot_helper",
    "git_subprocess_helper",
    "git_subprocess_helper_path",
}


def load_house_rules(repo: Path | None) -> HouseRules:
    """Read ``.lore-eden-gates.json`` from ``repo``, or return the empty rules.

    Raises :class:`HouseRulesError` when the file exists but is unparseable,
    carries an unknown key, or gives a key a non-string value — never falls back
    to defaults on a malformed file, because a config that silently does nothing
    is indistinguishable from a gate that found nothing.
    """
    if repo is None:
        return HouseRules()
    config_path = repo / CONFIG_FILENAME
    if not config_path.is_file():
        return HouseRules()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise HouseRulesError(f"{config_path}: cannot read gate configuration: {exc}") from exc
    # Validating decoded JSON by hand is what the isinstance rule objects to, and
    # it is right: the fix is a model at the boundary. This package ships no
    # Pydantic on purpose — it must start under whatever `python3` a repo hands
    # it — so the boundary check is written out and waived per line.
    if not isinstance(raw, dict):  # py-org: allow-isinstance (no Pydantic here by design)
        raise HouseRulesError(f"{config_path}: expected a JSON object at the top level")

    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        known = ", ".join(sorted(_FIELDS))
        raise HouseRulesError(
            f"{config_path}: unknown key(s) {', '.join(unknown)}; known keys are {known}"
        )
    for key, value in raw.items():
        if not isinstance(value, str):  # py-org: allow-isinstance — see above
            raise HouseRulesError(
                f"{config_path}: `{key}` must be a string, got {type(value).__name__}"
            )

    rules = HouseRules(**raw)
    if rules.git_subprocess_helper and not rules.git_subprocess_helper_path:
        raise HouseRulesError(
            f"{config_path}: `git_subprocess_helper` is set but "
            "`git_subprocess_helper_path` is not; without it the gate flags the "
            "helper's own subprocess call"
        )
    return rules

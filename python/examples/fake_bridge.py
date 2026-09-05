"""Wiring the examples to a fake agent instead of a real CLI.

Shared by both examples, which is the only reason it exists as a module — a
real host has none of this. Its `StageRunner` uses the default
`PermissionBridge` and a `build_invocation` command, and the whole of this file
is the two lines it does not need.

The stage is recovered from the prompt file's name, which the runner writes as
``<item>-<stage>.md``. Fragile, and deliberately confined here rather than
pushed into the library: a real host knows which stage it is running because it
is the one that asked.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from lore_eden.agents import PermissionBridge

FAKE_AGENT = Path(__file__).resolve().parent / "fake_writer.py"


def stage_from_prompt_path(argv: list[str]) -> str:
    """Which stage this invocation is for, read out of the prompt file name."""
    prompt = next(part for part in argv if part.endswith(".md"))
    return prompt.rsplit("-", 1)[-1][: -len(".md")]


def make_fake_bridge_factory(
    workspace: Path, script: dict[str, str]
) -> Callable[..., PermissionBridge]:
    """A ``make_bridge`` that runs the fake agent per stage.

    ``script`` maps a stage key to a fake-agent mode. Attempt state goes in the
    workspace, never beside the script: a counter file left by one run would
    otherwise decide the next one, and an example that passes while
    demonstrating nothing is worse than one that fails.
    """

    def make_bridge(*, argv: list[str], **kwargs) -> PermissionBridge:
        stage = stage_from_prompt_path(argv)
        kwargs.pop("cwd", None)
        return PermissionBridge(
            argv=[
                sys.executable,
                str(FAKE_AGENT),
                script[stage],
                str(workspace / f".{stage}.count"),
            ],
            **kwargs,
        )

    return make_bridge

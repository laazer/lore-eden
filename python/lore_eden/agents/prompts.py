"""How a host tells an agent what to do.

The seam exists because prompt assembly is the single most domain-specific thing
in an agent harness and the one most often welded to the runner. In the source
project a `_build_prompt` method ran to about 140 lines of tickets, acceptance
criteria, plan blocks, evidence ledgers and a stage-report contract — none of
which means anything to a host doing something other than software tickets.

So the harness knows only this: something takes a context and returns text.

    class Draft:
        def build(self, context: PromptContext) -> str:
            return f"Write about {context.values['topic']}."

`PromptContext` deliberately carries a free-form `values` mapping rather than
named fields. Naming them would mean choosing a vocabulary, and every vocabulary
this could choose belongs to some particular host.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class PromptContext:
    """What the runner knows when it asks for a prompt."""

    #: The work item this run is for. Opaque to the harness.
    item_id: str = ""
    #: The stage being run.
    stage_key: str = ""
    #: Which attempt this is, from 1. A builder may say more on a retry.
    attempt: int = 1
    #: Where the agent will run.
    workspace_root: Path | None = None
    #: Whatever the host wants to pass through. The harness never reads it.
    values: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class PromptBuilder(Protocol):
    """Produces the instruction for one run."""

    def build(self, context: PromptContext) -> str: ...


@dataclass(frozen=True)
class StaticPrompt:
    """The same text every time. For a stage whose instruction does not vary."""

    text: str

    def build(self, context: PromptContext) -> str:
        return self.text


@dataclass(frozen=True)
class TemplatePrompt:
    """`str.format` over the context's values, plus its named fields.

    A missing key raises rather than rendering an empty slot. A prompt with a
    hole in it is a prompt the agent will answer anyway, badly, and the run will
    look like a model failure rather than a wiring one.
    """

    template: str

    def build(self, context: PromptContext) -> str:
        fields: dict[str, Any] = {
            "item_id": context.item_id,
            "stage_key": context.stage_key,
            "attempt": context.attempt,
            "workspace_root": context.workspace_root or "",
            **dict(context.values),
        }
        try:
            return self.template.format(**fields)
        except KeyError as exc:
            raise KeyError(
                f"Prompt template needs {exc.args[0]!r}, which the context did not carry. "
                f"Available: {sorted(fields)}"
            ) from exc


def write_prompt_file(text: str, path: Path) -> Path:
    """Write a prompt where a CLI can read it, creating parent directories.

    `claude` takes its system prompt as `--append-system-prompt-file`, and a long
    prompt on the command line runs into the argument length limit — a failure
    that appears as an exec error naming nothing useful.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path

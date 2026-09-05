"""Building the command line that starts a CLI coding agent.

This is the part a host most needs and would least enjoy rediscovering. Every
CLI spells the same six ideas differently — print versus interactive, streaming
format, model, reasoning effort, permission mode, tool allow-lists — and several
of the couplings between them are undocumented and silent when broken.

The knowledge encoded here, which cost the source project real debugging:

- **`claude -p --output-format stream-json` needs `--verbose`.** Without it the
  CLI errors out; the message names the flag, but only if you look.
- **Token-level streaming needs `--include-partial-messages`.** Without it a
  thinking block arrives once it is complete, so a long thought is one jump from
  nothing to everything.
- **`claude` reads `CLAUDE_CODE_OAUTH_TOKEN`.** A host that shells out without
  it gets "not logged in" even while the same terminal is authenticated —
  because the CLI's interactive session lives somewhere the subprocess cannot
  reach.
- **An expired session exits 0.** It prints a login message and stops, producing
  no stream. That is caught by :attr:`~lore_eden.agents.BridgeOutcome.saw_result`
  rather than here, but it is the reason the token handling matters.
- **`cursor-agent` has no `--input-format`**, so it cannot be bridged at all. It
  runs in print mode or not at all, and asking for `interactive=True` raises
  rather than silently producing a command that hangs waiting for stdin nobody
  will read.
- **Effort is not a flag everywhere.** `claude` takes `--effort`; `cursor` folds
  it into the model id; `codex` takes neither.

What is *not* here: which model to use, which tools to grant, which MCP servers
to attach. Those are the host's decisions, and they arrive as parameters.
"""

from __future__ import annotations

import os
import shlex
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class CliAdapter(str, Enum):
    """A CLI agent this module can build a command for."""

    CLAUDE = "claude"
    CURSOR = "cursor"
    CODEX = "codex"
    OPENCODE = "opencode"


class OutputFormat(str, Enum):
    STREAM_JSON = "stream-json"
    TEXT = "text"
    JSON = "json"


class UnsupportedInvocationError(ValueError):
    """The adapter cannot do what was asked — raised rather than approximated."""


#: Default executable per adapter. Overridable per call.
DEFAULT_BINARIES: dict[CliAdapter, str] = {
    CliAdapter.CLAUDE: "claude",
    CliAdapter.CURSOR: "cursor-agent",
    CliAdapter.CODEX: "codex",
    CliAdapter.OPENCODE: "opencode",
}


@dataclass(frozen=True)
class CliInvocation:
    """A command ready to hand to :class:`~lore_eden.agents.ProcessSupervisor`."""

    argv: list[str]
    adapter: CliAdapter
    cwd: str = ""
    #: Overlaid on the parent environment, never a replacement for it.
    env: dict[str, str] = field(default_factory=dict)
    #: Written to the process's stdin, for adapters that take the prompt that way.
    stdin_prompt: str | None = None
    #: True when the process holds stdin open for a bridged conversation.
    interactive: bool = False
    resume_session_id: str = ""
    #: What was actually pinned, for a caller recording the run. Empty means
    #: nothing was pinned and the CLI chose — which is unknown, not a default.
    model: str = ""
    effort: str = ""

    def as_shell(self) -> str:
        """The command as a copy-pasteable shell line, for logs and handoffs."""
        prefix = "".join(f"{k}={shlex.quote(v)} " for k, v in sorted(self.env.items()))
        return prefix + shlex.join(self.argv)


@dataclass(frozen=True)
class InvocationRequest:
    """What the caller wants run. Adapter-neutral; the builders translate."""

    adapter: CliAdapter
    workspace_root: Path
    #: The instruction. Delivered as a file, an argument or stdin per adapter.
    prompt: str = ""
    #: A file holding the system prompt. `claude` prefers this to an argument.
    prompt_file: Path | None = None
    model: str = ""
    effort: str = ""
    #: Hold stdin open so permission requests can be answered. `claude` only.
    interactive: bool = False
    output_format: OutputFormat = OutputFormat.STREAM_JSON
    #: Token-level streaming. Costs volume; only worth it if something is watching.
    partial_messages: bool = False
    resume_session_id: str = ""
    permission_mode: str = ""
    allowed_tools: Sequence[str] = ()
    disallowed_tools: Sequence[str] = ()
    #: Appended verbatim, for a flag this module does not model.
    extra_args: Sequence[str] = ()
    env: dict[str, str] = field(default_factory=dict)


def resolve_binary(adapter: CliAdapter, override: str = "") -> str:
    """The executable to run: an explicit override, then `PATH`, then the bare name.

    Falling back to the bare name rather than raising leaves the failure to the
    spawn, where the error names the command. Raising here would mean a host
    could not build an invocation for a machine other than the one it runs on —
    which is exactly what a terminal handoff does.
    """
    if override:
        return override
    name = DEFAULT_BINARIES[adapter]
    return shutil.which(name) or name


def claude_oauth_env(token: str = "", token_file: Path | None = None) -> dict[str, str]:
    """`CLAUDE_CODE_OAUTH_TOKEN`, from a literal or a file holding one.

    Empty when there is no token, so a caller can always merge the result. A
    missing token is not an error here: an interactive host may be authenticated
    another way, and guessing wrong would break it.

    Pass the result as the invocation's ``env``. Without it, a `claude`
    subprocess reports "not logged in" while the terminal that spawned it is
    signed in — and, worse, an *expired* token makes the CLI exit 0 having done
    nothing, which only :attr:`BridgeOutcome.saw_result` will catch.
    """
    if token:
        return {"CLAUDE_CODE_OAUTH_TOKEN": token}
    if token_file is not None and token_file.is_file():
        value = token_file.read_text(encoding="utf-8").strip()
        if value:
            return {"CLAUDE_CODE_OAUTH_TOKEN": value}
    return {}


def _tool_flags(request: InvocationRequest, allow: str, deny: str) -> list[str]:
    argv: list[str] = []
    if request.allowed_tools:
        argv.extend([allow, ",".join(request.allowed_tools)])
    if request.disallowed_tools:
        argv.extend([deny, ",".join(request.disallowed_tools)])
    return argv


def _claude_argv(request: InvocationRequest, binary: str) -> list[str]:
    cwd = str(request.workspace_root)
    streaming = request.output_format is OutputFormat.STREAM_JSON

    if request.interactive:
        # Bridged: the agent reads permission answers from stdin, so both
        # directions must be stream-json.
        argv = [binary, "--output-format", "stream-json", "--input-format", "stream-json"]
    else:
        argv = [binary, "-p", "--output-format", request.output_format.value]

    if streaming:
        # `-p --output-format stream-json` is rejected without --verbose, and
        # partial messages are the only stdout heartbeat print mode produces —
        # without them a long silent think looks identical to a hung process,
        # and the idle timeout fires on a working agent.
        argv.append("--verbose")
        if request.partial_messages or not request.interactive:
            argv.append("--include-partial-messages")

    if request.permission_mode:
        argv.extend(["--permission-mode", request.permission_mode])
    if request.interactive:
        argv.extend(["--permission-prompt-tool", "stdio"])

    argv.extend(["--add-dir", cwd])
    if request.prompt_file is not None:
        argv.extend(["--append-system-prompt-file", str(request.prompt_file)])
    if request.model:
        argv.extend(["--model", request.model])
    if request.effort:
        argv.extend(["--effort", request.effort])
    if request.resume_session_id:
        argv.extend(["--resume", request.resume_session_id])
    argv.extend(_tool_flags(request, "--allowedTools", "--disallowedTools"))
    argv.extend(request.extra_args)
    # Print mode takes the instruction as a trailing argument; interactive mode
    # receives it over stdin once the session is up.
    if not request.interactive and request.prompt:
        argv.append(request.prompt)
    return argv


def apply_cursor_effort(model: str, effort: str) -> str:
    """Cursor's model id carries the effort — `sonnet-4.5` becomes `sonnet-4.5-high`.

    There is no `--effort` flag to pass it separately, so a host that sets effort
    on a cursor run and expects a flag gets a run at the default effort with
    nothing saying so.
    """
    if not effort or not model:
        return model
    return model if model.endswith(f"-{effort}") else f"{model}-{effort}"


def _cursor_argv(request: InvocationRequest, binary: str) -> list[str]:
    if request.interactive:
        raise UnsupportedInvocationError(
            "cursor-agent has no --input-format, so it cannot be bridged; "
            "run it in print mode and decide permissions before launching."
        )
    argv = [binary, "agent", "-p", "--output-format", request.output_format.value]
    if request.output_format is OutputFormat.STREAM_JSON and request.partial_messages:
        argv.append("--stream-partial-output")
    argv.extend(["--workspace", str(request.workspace_root)])
    model = apply_cursor_effort(request.model, request.effort)
    if model:
        argv.extend(["--model", model])
    argv.extend(request.extra_args)
    if request.prompt:
        argv.append(request.prompt)
    return argv


def _codex_argv(request: InvocationRequest, binary: str) -> list[str]:
    if request.interactive:
        raise UnsupportedInvocationError("codex cannot be bridged; use print mode.")
    argv = [binary, "exec"]
    # `--json` is codex's stream-json: NDJSON events on stdout.
    if request.output_format is OutputFormat.STREAM_JSON:
        argv.append("--json")
    argv.extend(["--cd", str(request.workspace_root)])
    if request.model:
        argv.extend(["--model", request.model])
    argv.extend(request.extra_args)
    if request.prompt:
        argv.append(request.prompt)
    return argv


def _opencode_argv(request: InvocationRequest, binary: str) -> list[str]:
    if request.interactive:
        raise UnsupportedInvocationError("opencode cannot be bridged; use print mode.")
    argv = [binary, "run"]
    if request.output_format is OutputFormat.STREAM_JSON:
        argv.extend(["--format", "json"])
    if request.model:
        argv.extend(["--model", request.model])
    argv.extend(request.extra_args)
    if request.prompt:
        argv.append(request.prompt)
    return argv


_BUILDERS = {
    CliAdapter.CLAUDE: _claude_argv,
    CliAdapter.CURSOR: _cursor_argv,
    CliAdapter.CODEX: _codex_argv,
    CliAdapter.OPENCODE: _opencode_argv,
}


def build_invocation(request: InvocationRequest, *, binary: str = "") -> CliInvocation:
    """Turn a request into a runnable command.

    Raises :class:`UnsupportedInvocationError` when the adapter cannot do what
    was asked, rather than emitting a command that fails obscurely later.
    """
    resolved = resolve_binary(request.adapter, binary)
    argv = _BUILDERS[request.adapter](request, resolved)
    return CliInvocation(
        argv=argv,
        adapter=request.adapter,
        cwd=str(request.workspace_root),
        env=dict(request.env),
        interactive=request.interactive,
        resume_session_id=request.resume_session_id,
        model=apply_cursor_effort(request.model, request.effort)
        if request.adapter is CliAdapter.CURSOR
        else request.model,
        effort=request.effort,
    )


def environment_for(invocation: CliInvocation) -> dict[str, str] | None:
    """The full environment to spawn with, or ``None`` to inherit unchanged.

    ``None`` rather than a copy of ``os.environ`` so a run with no overlay keeps
    inheriting whatever the parent sets, including variables set after import.
    """
    if not invocation.env:
        return None
    return {**os.environ, **invocation.env}

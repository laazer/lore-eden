"""The command line, and the flag couplings that are silent when broken."""

from __future__ import annotations

from pathlib import Path

import pytest

from lore_eden.agents import (
    CliAdapter,
    InvocationRequest,
    OutputFormat,
    PromptContext,
    StaticPrompt,
    TemplatePrompt,
    UnsupportedInvocationError,
    apply_cursor_effort,
    build_invocation,
    claude_oauth_env,
    environment_for,
    resolve_binary,
    write_prompt_file,
)

WORKSPACE = Path("/tmp/workspace")


def claude(**kwargs) -> list[str]:
    request = InvocationRequest(
        adapter=CliAdapter.CLAUDE, workspace_root=WORKSPACE, **kwargs
    )
    return build_invocation(request, binary="claude").argv


class TestClaudeStreamingCouplings:
    def test_stream_json_print_mode_carries_verbose(self) -> None:
        # `claude -p --output-format stream-json` is rejected outright without
        # it, and nothing about the request says so.
        argv = claude(prompt="go")
        assert "--verbose" in argv

    def test_text_output_does_not_ask_for_verbose_or_partials(self) -> None:
        argv = claude(prompt="go", output_format=OutputFormat.TEXT)
        assert "--verbose" not in argv
        assert "--include-partial-messages" not in argv

    def test_print_mode_always_streams_partials(self) -> None:
        # Print mode's only stdout heartbeat. Without it a long silent think is
        # indistinguishable from a hung process and the idle timeout fires on a
        # working agent.
        assert "--include-partial-messages" in claude(prompt="go")

    def test_interactive_partials_are_opt_in(self) -> None:
        # A bridged run is already emitting a message per event; token-level
        # deltas are pure volume unless something is rendering them.
        assert "--include-partial-messages" not in claude(interactive=True)
        assert "--include-partial-messages" in claude(
            interactive=True, partial_messages=True
        )

    def test_interactive_speaks_stream_json_both_ways(self) -> None:
        argv = claude(interactive=True)
        assert argv[argv.index("--input-format") + 1] == "stream-json"
        assert argv[argv.index("--output-format") + 1] == "stream-json"
        assert "--permission-prompt-tool" in argv

    def test_interactive_does_not_pass_the_prompt_as_an_argument(self) -> None:
        # It arrives over stdin once the session is up; an argument would be a
        # second, conflicting instruction.
        assert "go" not in claude(interactive=True, prompt="go")


class TestClaudeFlags:
    def test_effort_is_a_flag(self) -> None:
        argv = claude(prompt="go", model="opus", effort="high")
        assert argv[argv.index("--effort") + 1] == "high"

    def test_prompt_file_rather_than_argument(self, tmp_path: Path) -> None:
        target = tmp_path / "p.md"
        argv = claude(prompt_file=target, prompt="go")
        assert argv[argv.index("--append-system-prompt-file") + 1] == str(target)

    def test_tool_lists_are_comma_joined(self) -> None:
        argv = claude(prompt="go", allowed_tools=["Read", "Grep"], disallowed_tools=["Bash"])
        assert argv[argv.index("--allowedTools") + 1] == "Read,Grep"
        assert argv[argv.index("--disallowedTools") + 1] == "Bash"

    def test_resume_carries_the_session(self) -> None:
        argv = claude(interactive=True, resume_session_id="sess-1")
        assert argv[argv.index("--resume") + 1] == "sess-1"

    def test_nothing_is_pinned_by_default(self) -> None:
        argv = claude(prompt="go")
        assert "--model" not in argv
        assert "--effort" not in argv


class TestCursor:
    def test_cannot_be_bridged(self) -> None:
        # cursor-agent has no --input-format. Raising beats emitting a command
        # that hangs waiting for stdin nobody reads.
        with pytest.raises(UnsupportedInvocationError, match="--input-format"):
            build_invocation(
                InvocationRequest(
                    adapter=CliAdapter.CURSOR, workspace_root=WORKSPACE, interactive=True
                )
            )

    def test_effort_folds_into_the_model_id(self) -> None:
        # There is no --effort flag; a host expecting one gets a run at the
        # default effort with nothing saying so.
        assert apply_cursor_effort("sonnet-4.5", "high") == "sonnet-4.5-high"
        assert apply_cursor_effort("sonnet-4.5-high", "high") == "sonnet-4.5-high"
        assert apply_cursor_effort("", "high") == ""
        assert apply_cursor_effort("sonnet-4.5", "") == "sonnet-4.5"

    def test_invocation_records_the_folded_model(self) -> None:
        invocation = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CURSOR,
                workspace_root=WORKSPACE,
                model="sonnet-4.5",
                effort="high",
            ),
            binary="cursor-agent",
        )
        assert invocation.model == "sonnet-4.5-high"
        assert "--effort" not in invocation.argv

    def test_partial_output_flag_is_cursors_own_spelling(self) -> None:
        argv = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CURSOR, workspace_root=WORKSPACE, partial_messages=True
            ),
            binary="cursor-agent",
        ).argv
        assert "--stream-partial-output" in argv
        assert "--include-partial-messages" not in argv


class TestOtherAdapters:
    def test_codex_json_is_its_stream_json(self) -> None:
        argv = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CODEX, workspace_root=WORKSPACE, prompt="go"
            ),
            binary="codex",
        ).argv
        assert "--json" in argv
        assert argv[argv.index("--cd") + 1] == str(WORKSPACE)

    def test_opencode_format_json(self) -> None:
        argv = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.OPENCODE, workspace_root=WORKSPACE, prompt="go"
            ),
            binary="opencode",
        ).argv
        assert argv[argv.index("--format") + 1] == "json"

    def test_neither_can_be_bridged(self) -> None:
        for adapter in (CliAdapter.CODEX, CliAdapter.OPENCODE):
            with pytest.raises(UnsupportedInvocationError):
                build_invocation(
                    InvocationRequest(
                        adapter=adapter, workspace_root=WORKSPACE, interactive=True
                    )
                )


class TestOAuthToken:
    def test_reads_a_literal(self) -> None:
        assert claude_oauth_env("tok") == {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}

    def test_reads_a_file_and_strips_it(self, tmp_path: Path) -> None:
        path = tmp_path / "token"
        path.write_text("  tok\n")
        assert claude_oauth_env(token_file=path) == {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}

    def test_absent_is_empty_rather_than_an_error(self, tmp_path: Path) -> None:
        # An interactive host may be authenticated another way; guessing that
        # this is broken would break it.
        assert claude_oauth_env() == {}
        assert claude_oauth_env(token_file=tmp_path / "missing") == {}
        empty = tmp_path / "empty"
        empty.write_text("\n")
        assert claude_oauth_env(token_file=empty) == {}

    def test_the_token_reaches_the_spawn_environment(self, tmp_path: Path) -> None:
        # Without this the CLI reports "not logged in" while the terminal that
        # spawned it is signed in.
        invocation = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CLAUDE,
                workspace_root=WORKSPACE,
                prompt="go",
                env=claude_oauth_env("tok"),
            ),
            binary="claude",
        )
        env = environment_for(invocation)
        assert env is not None
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "tok"
        assert "PATH" in env, "the overlay must not replace the parent environment"

    def test_no_overlay_inherits_unchanged(self) -> None:
        invocation = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CLAUDE, workspace_root=WORKSPACE, prompt="go"
            ),
            binary="claude",
        )
        assert environment_for(invocation) is None

    def test_shell_rendering_quotes_the_env_prefix(self) -> None:
        invocation = build_invocation(
            InvocationRequest(
                adapter=CliAdapter.CLAUDE,
                workspace_root=WORKSPACE,
                prompt="a b",
                env={"CLAUDE_CODE_OAUTH_TOKEN": "a b"},
            ),
            binary="claude",
        )
        rendered = invocation.as_shell()
        assert rendered.startswith("CLAUDE_CODE_OAUTH_TOKEN='a b' ")
        assert "'a b'" in rendered.split(" ", 1)[1]


class TestBinaryResolution:
    def test_override_wins(self) -> None:
        assert resolve_binary(CliAdapter.CLAUDE, "/opt/claude") == "/opt/claude"

    def test_falls_back_to_the_bare_name(self) -> None:
        # So a host can build a command for a machine other than this one —
        # which is exactly what a terminal handoff does.
        assert resolve_binary(CliAdapter.OPENCODE).endswith("opencode")


class TestPromptBuilders:
    def test_static(self) -> None:
        assert StaticPrompt("go").build(PromptContext()) == "go"

    def test_template_reads_context_values(self) -> None:
        built = TemplatePrompt("{stage_key}: {topic}").build(
            PromptContext(stage_key="draft", values={"topic": "otters"})
        )
        assert built == "draft: otters"

    def test_a_missing_key_raises_rather_than_rendering_a_hole(self) -> None:
        # A prompt with a hole gets answered anyway, badly, and the run looks
        # like a model failure rather than a wiring one.
        with pytest.raises(KeyError, match="topic"):
            TemplatePrompt("{topic}").build(PromptContext())

    def test_prompt_file_is_written_with_parents(self, tmp_path: Path) -> None:
        target = write_prompt_file("hello", tmp_path / "a" / "b" / "p.md")
        assert target.read_text(encoding="utf-8") == "hello"

    def test_a_builder_need_not_mention_any_domain(self) -> None:
        # The point of the seam: nothing here knows what a ticket is.
        text = TemplatePrompt("Write about {topic} in {words} words.").build(
            PromptContext(values={"topic": "otters", "words": 50})
        )
        assert "ticket" not in text and "stage" not in text

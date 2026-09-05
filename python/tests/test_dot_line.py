"""Tests for the display-line builders.

The rule these enforce is small and easy to regress: a segment that is empty
contributes nothing, and no separator appears next to it. Every f-string this
module replaces got that wrong at least once.
"""

from __future__ import annotations

import doctest

import pytest
from lore_eden import dot_line
from lore_eden.dot_line import (
    CMD,
    ERR,
    FAIL,
    OK,
    OUT,
    RUN,
    SEPARATOR,
    SYS,
    TOOL,
    DisplaySegments,
    Dot,
    LogLine,
    Tag,
    clip,
    kv,
    kv_space,
    mid_dot,
    shell,
)


class TestEmptySegmentsVanish:
    """The reason this module exists: an absent detail must not leave a separator."""

    @pytest.mark.parametrize("absent", ["", None])
    def test_an_absent_trailing_segment_leaves_no_separator(self, absent: object) -> None:
        assert str(Dot("$ pytest") / absent) == "$ pytest"

    @pytest.mark.parametrize("absent", ["", None])
    def test_an_absent_leading_segment_leaves_no_separator(self, absent: object) -> None:
        assert str(Dot(absent) / "session init") == "session init"

    @pytest.mark.parametrize("absent", ["", None])
    def test_an_absent_middle_segment_does_not_double_the_separator(
        self, absent: object
    ) -> None:
        assert str(Dot("a") / absent / "b") == "a · b"

    def test_none_is_dropped_rather_than_rendered_as_the_word(self) -> None:
        """The f-string failure mode: ``f"{a} · {b}"`` with b=None prints "None"."""
        assert "None" not in str(Dot("session init") / None)

    def test_a_dot_of_nothing_is_the_empty_string(self) -> None:
        assert str(Dot()) == ""
        assert str(Dot(None, "", None)) == ""

    def test_zero_is_kept_because_it_is_a_value_not_an_absence(self) -> None:
        """``0`` is falsy but meaningful — ``retries=0`` is a fact worth printing."""
        assert str(Dot("retries") / 0) == "retries · 0"
        assert str(Dot("ok") / False) == "ok · False"


class TestComposition:
    def test_segments_join_with_the_separator(self) -> None:
        assert str(Dot("a") / "b" / "c") == f"a{SEPARATOR}b{SEPARATOR}c"

    def test_composing_is_immutable(self) -> None:
        base = Dot("stage")
        derived = base / "implement"
        assert str(base) == "stage"
        assert str(derived) == "stage · implement"
        assert base is not derived

    def test_a_dot_composed_into_a_dot_flattens_instead_of_nesting_a_repr(self) -> None:
        """Without flattening this renders ``a · Dot('b')``, which is the bug."""
        assert str(Dot("a") / (Dot("b") / "c")) == "a · b · c"

    def test_rtruediv_builds_from_a_plain_string_on_the_left(self) -> None:
        assert str("prefix" / Dot("rest")) == "prefix · rest"

    def test_a_log_line_composed_into_a_dot_contributes_its_segments(self) -> None:
        assert str(Dot("outer") / (SYS / "inner")) == "outer · inner"

    def test_mid_dot_is_the_string_form(self) -> None:
        assert mid_dot("a", "", None, "b") == "a · b"

    def test_the_separator_has_one_definition(self) -> None:
        """``Dot.SEP`` is kept for call sites that reach for it; it must not drift."""
        assert Dot.SEP is SEPARATOR
        assert LogLine("T", "a", "b").text == f"a{SEPARATOR}b"


class TestAHostTypeCanCompose:
    """The protocol is the reason this dispatch is not an ``isinstance`` switch.

    A type ``lore_eden`` has never heard of composes on equal terms by
    implementing one method — which an internal type switch on ``Dot``/
    ``LogLine`` could not have allowed.
    """

    class TicketLabel:
        def __init__(self, ticket_id: str, title: str) -> None:
            self.ticket_id = ticket_id
            self.title = title

        def display_parts(self) -> tuple[str, ...]:
            return (f"#{self.ticket_id}", self.title)

    def test_a_foreign_type_contributes_its_own_segments(self) -> None:
        label = self.TicketLabel("546", "extract the harness")
        assert str(Dot("stage") / label) == "stage · #546 · extract the harness"

    def test_it_satisfies_the_declared_protocol(self) -> None:
        assert isinstance(self.TicketLabel("1", "x"), DisplaySegments)

    def test_the_library_types_satisfy_it_too(self) -> None:
        assert isinstance(Dot("a"), DisplaySegments)
        assert isinstance(SYS / "a", DisplaySegments)

    def test_a_type_without_the_method_still_falls_back_to_str(self) -> None:
        class Plain:
            def __str__(self) -> str:
                return "plain"

        assert str(Dot("a") / Plain()) == "a · plain"

    def test_a_non_callable_attribute_of_that_name_does_not_hijack_the_dispatch(self) -> None:
        """A host field coincidentally named ``display_parts`` must not crash us."""

        class Coincidence:
            display_parts = "not a method"

            def __str__(self) -> str:
                return "coincidence"

        assert str(Dot("a") / Coincidence()) == "a · coincidence"


class TestTaggedLines:
    def test_a_tag_starts_a_line(self) -> None:
        assert (SYS / "turn started").as_tuple() == ("SYS", "turn started")

    def test_a_tag_is_callable_for_a_line_with_no_chain(self) -> None:
        assert SYS("turn started", "haiku").as_tuple() == ("SYS", "turn started · haiku")

    def test_a_line_keeps_its_tag_through_composition(self) -> None:
        line = TOOL / "bash" / "exit=0"
        assert line.tag == "TOOL"
        assert line.text == "bash · exit=0"

    def test_a_line_equals_the_tuple_a_host_formatter_already_speaks(self) -> None:
        assert (SYS / "ready") == ("SYS", "ready")
        assert (SYS / "ready") != ("OUT", "ready")
        assert (SYS / "ready") != ("SYS", "different")

    def test_a_line_unpacks_as_a_pair(self) -> None:
        tag, text = SYS / "codex thread" / "abc"
        assert (tag, text) == ("SYS", "codex thread · abc")

    def test_a_line_indexes_and_measures_as_a_pair(self) -> None:
        line = SYS / "ready"
        assert len(line) == 2
        assert (line[0], line[1]) == ("SYS", "ready")

    def test_equal_lines_hash_alike_so_a_set_deduplicates_them(self) -> None:
        assert len({SYS / "ready", SYS / "ready", OUT / "ready"}) == 2

    def test_a_line_hashes_with_its_tuple_so_the_two_forms_interchange(self) -> None:
        assert hash(SYS / "ready") == hash(("SYS", "ready"))

    def test_comparing_against_an_unrelated_type_is_not_an_error(self) -> None:
        assert (SYS / "ready") != 17
        assert (SYS / "ready") != ("SYS", "ready", "extra")

    def test_the_shipped_channels_are_distinct(self) -> None:
        channels = [SYS, OUT, TOOL, RUN, CMD, ERR, OK, FAIL]
        assert len({tag.name for tag in channels}) == len(channels)

    def test_repr_shows_the_rendered_text_not_the_parts(self) -> None:
        assert repr(SYS / "a" / "b") == "LogLine('SYS', 'a · b')"


class TestBlankOutputIsDropped:
    def test_maybe_drops_blank_agent_stdout(self) -> None:
        """Agent stdout arrives as a bare newline often enough to matter."""
        assert OUT.maybe("\n") is None
        assert OUT.maybe("   ") is None
        assert OUT.maybe("") is None
        assert OUT.maybe(None) is None

    def test_maybe_strips_surrounding_whitespace_from_real_output(self) -> None:
        assert OUT.maybe("  hello\n").as_tuple() == ("OUT", "hello")

    def test_with_body_puts_stdout_under_the_headline(self) -> None:
        line = (TOOL / "bash").with_body("line one\nline two")
        assert line.as_tuple() == ("TOOL", "bash\nline one\nline two")

    def test_with_body_leaves_the_line_alone_when_there_is_no_body(self) -> None:
        headline = TOOL / "bash"
        for body in ("", "   ", None):
            assert headline.with_body(body).as_tuple() == headline.as_tuple()

    def test_with_body_clips_a_long_body(self) -> None:
        line = (TOOL / "bash").with_body("x" * 50, width=10)
        assert line.text == "bash\n" + "x" * 10


class TestFormattingHelpers:
    def test_clip_truncates_to_exactly_the_width(self) -> None:
        assert clip("abcdef", 3) == "abc"
        assert len(clip("x" * 100, 7)) == 7

    def test_clip_keeps_text_shorter_than_the_width_intact(self) -> None:
        assert clip("ab", 10) == "ab"

    def test_clip_falls_back_for_absent_or_empty_text(self) -> None:
        assert clip(None, 10, fallback="—") == "—"
        assert clip("", 10, fallback="—") == "—"

    def test_shell_prefixes_a_command(self) -> None:
        assert shell("pytest -q") == "$ pytest -q"

    def test_shell_strips_before_prefixing(self) -> None:
        assert shell("  pytest -q\n") == "$ pytest -q"

    def test_shell_names_the_absence_rather_than_printing_a_bare_dollar(self) -> None:
        """``$ `` alone tells a reader nothing about what happened."""
        assert shell(None) == "$ command"
        assert shell("") == "$ command"

    def test_kv_renders_a_pair(self) -> None:
        assert kv("exit", 0) == "exit=0"

    def test_kv_marks_an_absent_value_rather_than_trailing_the_equals(self) -> None:
        """``tokens=`` reads as a formatting bug; ``tokens=—`` reads as no data."""
        assert kv("tokens", None) == "tokens=—"
        assert kv("tokens", "") == "tokens=—"
        assert kv("tokens", None, empty="?") == "tokens=?"

    def test_kv_space_groups_pairs_into_one_segment(self) -> None:
        assert kv_space(in_=1, out=2) == "in=1 out=2"

    def test_kv_space_strips_a_trailing_underscore_from_a_keyword_key(self) -> None:
        assert kv_space(in_=1) == "in=1"
        assert kv_space(class_="x") == "class=x"

    def test_kv_space_marks_absent_values_with_a_question_mark(self) -> None:
        assert kv_space(in_=None, out=2) == "in=? out=2"

    def test_a_kv_group_composes_as_a_single_segment(self) -> None:
        assert str(Dot("tool result") / kv_space(in_=1, out=2)) == "tool result · in=1 out=2"


class TestDotEquality:
    def test_dots_with_the_same_segments_are_equal(self) -> None:
        assert Dot("a") / "b" == Dot("a", "b")

    def test_a_dot_equals_its_rendered_string(self) -> None:
        assert Dot("a") / "b" == "a · b"

    def test_empty_segments_do_not_affect_equality(self) -> None:
        assert Dot("a", "", None, "b") == Dot("a", "b")

    def test_a_dot_is_not_equal_to_a_tagged_line_that_renders_the_same(self) -> None:
        """A channel is part of the line's identity; dropping it would lose it."""
        assert Dot("ready") != (SYS / "ready")

    def test_equal_dots_hash_alike(self) -> None:
        assert hash(Dot("a", "b")) == hash(Dot("a") / "b")


def test_the_docstring_examples_run() -> None:
    results = doctest.testmod(dot_line, verbose=False)
    assert results.failed == 0
    assert results.attempted > 0, "doctest examined nothing — the examples went unchecked"


def test_the_tag_type_is_exported_for_hosts_defining_their_own_channels() -> None:
    """A host with a channel we do not ship must be able to make one."""
    audit = Tag("AUDIT")
    assert (audit / "gate" / "passed").as_tuple() == ("AUDIT", "gate · passed")

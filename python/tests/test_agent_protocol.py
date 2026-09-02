"""The stream-json wire format, as pure functions.

Two CLIs spell the same messages differently. These pin the normalization, and
two of them pin traps that are silent when got wrong.
"""

from __future__ import annotations

import pytest
from lore_eden.agents import (
    build_control_response,
    build_user_message,
    extract_permission_request,
    parse_stream_line,
    result_payload_status,
)
from lore_eden.agents.protocol import session_id_from


class TestParsing:
    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("", id="blank"),
            pytest.param("   \n", id="whitespace"),
            pytest.param("not json", id="not-json"),
            pytest.param("[1, 2]", id="json-but-not-an-object"),
            pytest.param("null", id="json-null"),
        ],
    )
    def test_noise_is_ignored_rather_than_fatal(self, line):
        # A CLI emits progress noise and partial flushes. Failing a run over one
        # would make the bridge unusable against a real agent.
        assert parse_stream_line(line) is None

    def test_a_message_decodes(self):
        assert parse_stream_line('{"type": "result"}') == {"type": "result"}


class TestPermissionRequests:
    @pytest.mark.parametrize("message_type", ["control_request", "sdk_control_request"])
    @pytest.mark.parametrize("subtype", ["permission", "can_use_tool"])
    def test_both_spellings_from_both_clis_are_recognised(self, message_type, subtype):
        # Normalizing here, once, is the difference between a bridge that works
        # with both CLIs and one with a vendor branch in every method.
        request = extract_permission_request(
            {
                "type": message_type,
                "request_id": "r1",
                "request": {"subtype": subtype, "tool_name": "Bash", "tool_input": {"a": 1}},
            }
        )

        assert request is not None
        assert request.tool_name == "Bash"
        assert request.tool_input == {"a": 1}

    def test_the_alternate_field_names_are_accepted(self):
        request = extract_permission_request(
            {
                "type": "control_request",
                "request": {"subtype": "permission", "id": "r9", "tool": "Write", "input": {"b": 2}},
            }
        )

        assert request is not None
        assert request.request_id == "r9"
        assert request.tool_name == "Write"
        assert request.tool_input == {"b": 2}

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"type": "assistant"}, id="not-a-control-request"),
            pytest.param({"type": "control_request", "request": {"subtype": "other"}}, id="other-subtype"),
            pytest.param({"type": "control_request"}, id="no-request"),
            pytest.param({}, id="empty"),
        ],
    )
    def test_other_messages_are_not_permission_requests(self, payload):
        assert extract_permission_request(payload) is None

    def test_the_raw_message_is_kept_for_a_host_that_needs_more(self):
        payload = {
            "type": "control_request",
            "request": {"subtype": "permission", "tool_name": "Bash"},
            "extra": "something this package does not model",
        }

        request = extract_permission_request(payload)

        assert request is not None
        assert request.raw is payload


class TestControlResponses:
    def test_an_approval_allows(self):
        response = build_control_response(request_id="r1", approved=True)

        assert response["response"]["request_id"] == "r1"
        assert response["response"]["response"] == {"behavior": "allow"}

    def test_an_approval_with_no_amendment_omits_updatedInput(self):
        # The trap: an empty map *overwrites* the agent's original arguments, so
        # a well-meant "no changes" silently strips the command a shell tool was
        # about to run.
        response = build_control_response(request_id="r1", approved=True, updated_input={})

        assert "updatedInput" not in response["response"]["response"]

    def test_an_amended_approval_carries_the_new_input(self):
        response = build_control_response(
            request_id="r1", approved=True, updated_input={"command": "ls"}
        )

        assert response["response"]["response"]["updatedInput"] == {"command": "ls"}

    def test_a_denial_carries_its_reason(self):
        response = build_control_response(request_id="r1", approved=False, message="not allowed")

        assert response["response"]["response"] == {
            "behavior": "deny",
            "message": "not allowed",
        }

    def test_a_denial_without_a_reason_still_says_something(self):
        # An agent handed an empty denial has nothing to report or work around.
        response = build_control_response(request_id="r1", approved=False)

        assert response["response"]["response"]["message"]

    def test_the_response_carries_the_request_id(self):
        # Without it the agent cannot match the answer to its question, and waits.
        assert build_control_response(request_id="abc", approved=True)["response"][
            "request_id"
        ] == "abc"


class TestResults:
    def test_a_successful_result_is_finished_and_not_failed(self):
        assert result_payload_status({"type": "result", "is_error": False}) == (True, False)

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({"type": "result", "is_error": True}, id="is_error"),
            pytest.param({"type": "result", "subtype": "error"}, id="error-subtype"),
        ],
    )
    def test_both_ways_of_reporting_failure_are_read(self, payload):
        assert result_payload_status(payload) == (True, True)

    def test_a_non_result_is_neither(self):
        # "Not a result" and "a result that passed" are different answers, and
        # collapsing them loses the ability to tell a stream that ended from one
        # that has not.
        assert result_payload_status({"type": "assistant"}) == (False, False)


class TestSessionAndSteering:
    def test_the_init_event_announces_the_session(self):
        assert session_id_from({"type": "system", "subtype": "init", "session_id": "s1"}) == "s1"

    def test_other_messages_announce_nothing(self):
        assert session_id_from({"type": "assistant"}) == ""

    def test_a_steer_is_shaped_as_a_user_turn(self):
        message = build_user_message("stop and summarise", session_id="s1")

        assert message["type"] == "user"
        assert message["message"] == {"role": "user", "content": "stop and summarise"}
        assert message["session_id"] == "s1"

    def test_a_steer_without_a_session_omits_the_field(self):
        assert "session_id" not in build_user_message("hello")

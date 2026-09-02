"""A fake CLI agent that speaks stream-json over stdio.

Used by the bridge tests. A real subprocess rather than a mocked one, because
the things that break a permission bridge are all at the process boundary:
whether the response reaches stdin while the child is still reading, whether a
child that exited early takes the supervisor down with it, whether a denial
actually stops the agent.

Driven by argv so one script covers every scenario:

    fake_agent.py ask <tool>      ask permission, then report what it was told
    fake_agent.py ask-many <n>    ask n times
    fake_agent.py quiet <secs>    say nothing for n seconds (idle timeout)
    fake_agent.py chatty <secs>   emit constantly for n seconds (hard cap)
    fake_agent.py fail            emit a failed result event
    fake_agent.py ok              emit a successful result event
    fake_agent.py crash           exit non-zero without a result event
"""

from __future__ import annotations

import json
import sys
import time


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def read_response() -> dict | None:
    line = sys.stdin.readline()
    if not line.strip():
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def behaviour_of(response: dict | None) -> str:
    if response is None:
        return "none"
    inner = (response.get("response") or {}).get("response") or {}
    return str(inner.get("behavior") or "none")


def ask(tool: str, request_id: str = "req-1") -> str:
    emit(
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": tool,
                "tool_input": {"command": "echo hi"},
            },
        }
    )
    return behaviour_of(read_response())


def main() -> int:
    emit({"type": "system", "subtype": "init", "session_id": "sess-42"})
    mode = sys.argv[1] if len(sys.argv) > 1 else "ok"

    if mode == "ask":
        tool = sys.argv[2] if len(sys.argv) > 2 else "Bash"
        behaviour = ask(tool)
        emit({"type": "assistant", "text": f"permission was {behaviour}"})
        emit({"type": "result", "subtype": "success", "is_error": behaviour != "allow"})
        return 0

    if mode == "ask-many":
        count = int(sys.argv[2])
        seen = [ask(f"Tool{i}", f"req-{i}") for i in range(count)]
        emit({"type": "assistant", "text": ",".join(seen)})
        emit({"type": "result", "subtype": "success", "is_error": False})
        return 0

    if mode == "quiet":
        time.sleep(float(sys.argv[2]))
        emit({"type": "result", "subtype": "success", "is_error": False})
        return 0

    if mode == "chatty":
        deadline = time.monotonic() + float(sys.argv[2])
        while time.monotonic() < deadline:
            emit({"type": "assistant", "text": "still going"})
            time.sleep(0.05)
        emit({"type": "result", "subtype": "success", "is_error": False})
        return 0

    if mode == "fail":
        emit({"type": "result", "subtype": "error", "is_error": True})
        return 0

    if mode == "crash":
        sys.stderr.write("something went wrong\n")
        return 3

    emit({"type": "result", "subtype": "success", "is_error": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

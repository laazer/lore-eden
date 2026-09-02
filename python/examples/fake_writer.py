"""A fake agent for the example, standing in for a real CLI.

CI has no authenticated `claude`, and an example that cannot run is an example
nobody trusts. This speaks the same stream-json protocol a real agent does:
announce a session, ask permission for a tool, act on the answer, report.

    fake_writer.py <mode> <state-file>

    write              ask for two tools, write, report a pass
    reject-then-pass   reject the first time, pass the second

The second mode counts its attempts in ``state-file``, because each stage runs
as a fresh process — which is also true of the real thing. The caller supplies
the path: keeping it next to this script meant a file left by one run silently
decided the next one, and the reject edge stopped firing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def ask(tool_name: str, request_id: str, tool_input: dict) -> bool:
    """Ask permission the way a real agent does, and read the answer."""
    emit(
        {
            "type": "control_request",
            "request_id": request_id,
            "request": {
                "subtype": "can_use_tool",
                "tool_name": tool_name,
                "input": tool_input,
            },
        }
    )
    line = sys.stdin.readline()
    if not line.strip():
        return False
    response = json.loads(line)
    payload = response.get("response", {})
    return payload.get("subtype") == "success" and payload.get("response", {}).get(
        "behavior"
    ) == "allow"


def say(text: str) -> None:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "write"
    counter = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    emit({"type": "system", "subtype": "init", "session_id": "sess-example"})

    # One tool the host allows, one it refuses. Both are asked for, so the deny
    # path runs in every example execution rather than only in a test.
    allowed = ask("write_document", "req-1", {"text": "Otters hold hands while they sleep."})
    refused = ask("Bash", "req-2", {"command": "rm -rf /"})
    say(f"write_document allowed={allowed} Bash allowed={refused}")

    if mode == "reject-then-pass" and counter is not None:
        attempt = int(counter.read_text()) if counter.exists() else 0
        counter.write_text(str(attempt + 1))
        if attempt == 0:
            say("STAGE-OUTCOME: reject  the opening does not say what this is about")
            emit({"type": "result", "subtype": "success", "is_error": False})
            return 0

    say("STAGE-OUTCOME: pass  done")
    emit({"type": "result", "subtype": "success", "is_error": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

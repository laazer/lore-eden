"""A fake CLI agent that reports a stage outcome, for the runner tests.

Separate from ``fake_agent.py``, which exists to exercise the permission bridge.
This one exercises the *judging* step: what a run looks like when the agent
passes, rejects, or finishes chattily having said nothing at all.

    fake_reporting_agent.py pass            work, then report a pass
    fake_reporting_agent.py reject          work, then report a reject
    fake_reporting_agent.py silent-success  a full stream, no verdict, exit 0
    fake_reporting_agent.py expired         no stream at all, exit 0
    fake_reporting_agent.py crash           exit 3

``silent-success`` is the interesting one. It is a perfectly clean run by every
signal a harness has — exit 0, a result event saying success, no timeout — that
never claims the work was done. A harness reading the exit code advances on it.
"""

from __future__ import annotations

import json
import sys


def emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def say(text: str) -> None:
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    # The report is read from stdout as a whole, so it also goes out plainly —
    # a real CLI's text lands there too.
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pass"

    if mode == "expired":
        sys.stderr.write("Invalid API key · Please run /login\n")
        return 0

    if mode == "crash":
        sys.stderr.write("something went wrong\n")
        return 3

    emit({"type": "system", "subtype": "init", "session_id": "sess-fake"})
    say("Working on it.")

    if mode == "pass":
        say("STAGE-OUTCOME: pass  drafted three paragraphs")
    elif mode == "reject":
        say("STAGE-OUTCOME: reject  the opening does not say what this is about")
    elif mode == "silent-success":
        say("All done, I think that covers everything you asked for.")

    emit({"type": "result", "subtype": "success", "is_error": False})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

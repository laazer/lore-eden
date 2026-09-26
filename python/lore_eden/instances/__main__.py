"""``python -m lore_eden.instances`` — find instances from a shell.

Discovery only: ``list``, ``url``, ``logs`` and ``stop``. Launching needs a
host's templates, so a host exposes that through its own API or CLI.

``url`` is the piece scripts use to connect::

    API_TARGET="$(python -m lore_eden.instances url --project shop main)"

It exits 1, printing nothing to stdout, when no live instance matches — so a
caller's fallback (``|| echo http://127.0.0.1:8000``) is taken rather than an
empty string being used as a URL.
"""

from __future__ import annotations

import argparse
import sys

from lore_eden.instances.manager import InstanceManager, NotManagedError
from lore_eden.instances.models import InstanceState
from lore_eden.instances.registry import FileInstanceRegistry
from lore_eden.instances.templates import TemplateCatalog


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m lore_eden.instances")
    parser.add_argument("--project", default=None, help="limit to one project")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="every instance, with its state and URL")
    url = sub.add_parser("url", help="print the URL of a live instance")
    url.add_argument("ref", help="instance id, or name within --project")
    logs = sub.add_parser("logs", help="print the tail of an instance's log")
    logs.add_argument("ref")
    logs.add_argument("--tail", type=int, default=50)
    stop = sub.add_parser("stop", help="stop a launched instance, or dismiss an exited one")
    stop.add_argument("ref")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manager = InstanceManager(FileInstanceRegistry(), TemplateCatalog())
    if args.command == "list":
        listing = manager.list(args.project)
        for view in listing.instances:
            print(f"{view.id}\t{view.project}\t{view.name}\t{view.kind.value}\t{view.state.value}\t{view.url}")
        for bad in listing.unreadable:
            print(f"unreadable record {bad.path}: {bad.error}", file=sys.stderr)
        return 1 if listing.unreadable else 0
    record = manager.registry.resolve(args.project, args.ref)
    if record is None:
        print(f"no instance matching {args.ref!r}", file=sys.stderr)
        return 1
    if args.command == "url":
        view = manager.refresh(record)
        if view.state == InstanceState.EXITED:
            print(f"{view.id} is not running: {view.last_error}", file=sys.stderr)
            return 1
        print(view.url)
        return 0
    if args.command == "logs":
        print("\n".join(manager.logs(record.id, tail=args.tail).lines))
        return 0
    try:
        manager.stop(record.id)
    except NotManagedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

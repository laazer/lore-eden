"""Choosing a port nobody is using.

Binding port 0 and reading back what the kernel chose is the usual trick, and
it is wrong here: the port is released the moment the probe socket closes, and
the process that will actually listen on it starts seconds later. Two launches
in that window get the same port.

So a port is chosen from a range, skipping every port the registry has already
handed to a live instance — the registry's lock makes that check-and-claim
atomic between launchers — and then confirmed free with a bind. The bind
catches what the registry cannot know about: a server someone started by hand.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable

DEFAULT_PORT_RANGE = (8100, 8999)


#: Bind addresses meaning "every interface". Fine to listen on, but not a
#: place anyone can connect to: browsers refuse to navigate to 0.0.0.0, and an
#: embed policy that allows only loopback rightly refuses it too.
_WILDCARD_TO_LOOPBACK = {"0.0.0.0": "127.0.0.1", "::": "::1"}


def connectable_host(host: str) -> str:
    """The address a client on this machine should connect to for ``host``.

    A wildcard bind is reached on loopback; any other host already names a
    real address and is returned unchanged.
    """
    return _WILDCARD_TO_LOOPBACK.get(host, host)


def instance_url(host: str, port: int) -> str:
    """The URL to advertise for a server bound to ``host``:``port``.

    The bind host is kept in the record as ``host``; this is what a person or
    tool should open. IPv6 literals are bracketed, as a URL requires.
    """
    target = connectable_host(host)
    try:
        if ipaddress.ip_address(target).version == 6:
            target = f"[{target}]"
    except ValueError:  # silent-ok: a hostname, not an IP literal; used as given
        pass
    return f"http://{target}:{port}"


class NoFreePortError(RuntimeError):
    """Every port in the range is claimed or bound."""


def port_is_free(host: str, port: int) -> bool:
    """Whether ``port`` can be bound on ``host`` right now.

    ``SO_REUSEADDR`` is deliberately not set: with it, a port in TIME_WAIT
    reads as free on Linux, which is correct, but on macOS it also lets the
    probe bind beside a live listener, which is not.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def allocate_port(
    host: str,
    *,
    port_range: tuple[int, int] = DEFAULT_PORT_RANGE,
    claimed: Iterable[int] = (),
) -> int:
    """The lowest port in ``port_range`` (inclusive) not claimed and not bound.

    Lowest rather than random so the same instance tends to land on the same
    port across restarts, which is what a bookmarked URL wants.
    """
    low, high = port_range
    if low > high:
        raise ValueError(f"empty port range {low}-{high}")
    taken = set(claimed)
    for port in range(low, high + 1):
        if port not in taken and port_is_free(host, port):
            return port
    raise NoFreePortError(f"no free port on {host} in {low}-{high}")

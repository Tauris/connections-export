"""A bare `connections-export` opens the console, whatever is on 8000.

Someone who has just installed this types the command with no arguments.
If something else already holds port 8000 -- another dev server, a
previous run -- the answer cannot be a bind error: the fix would be
`serve --port 8137`, and knowing that is a lot to ask of a first minute.

`_free_port` exists for exactly this and did not work on Windows, which
is where most of these users are. `SO_REUSEADDR` does not mean the same
thing on both platforms: on Unix it permits rebinding a socket in
TIME_WAIT, while on Windows it permits binding a port another socket is
*actively* using. So the probe reported a busy port as free, and uvicorn
then failed to bind it for real.
"""

from __future__ import annotations

import socket

from connections_export.cli import _free_port

HOST = "127.0.0.1"


def _occupied() -> socket.socket:
    """A socket genuinely holding a port, bound the way a server binds."""
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind((HOST, 0))
    held.listen(1)
    return held


def test_a_port_someone_else_is_using_is_not_offered():
    """On Unix this passes either way -- `SO_REUSEADDR` does not allow
    stealing an active binding there. It is the Windows cells of the matrix
    that this is really for, which is why it must keep running there."""
    held = _occupied()
    busy = held.getsockname()[1]
    try:
        assert _free_port(HOST, busy) != busy
    finally:
        held.close()


def test_the_probe_asks_for_an_exclusive_binding():
    """Stated directly, because the behaviour above cannot fail on the
    platform this is usually run on. The probe must not ask for any option
    that would let it share a port -- that is the whole question it exists
    to answer."""
    asked = []
    real_socket = socket.socket

    class _Recording(real_socket):  # type: ignore[misc,valid-type]
        def setsockopt(self, level, option, value):
            asked.append((level, option, value))
            return super().setsockopt(level, option, value)

    socket.socket = _Recording  # type: ignore[misc]
    try:
        _free_port(HOST, 0)
    finally:
        socket.socket = real_socket  # type: ignore[misc]

    reuse = [a for a in asked if a[1] == socket.SO_REUSEADDR and a[2]]
    assert reuse == [], (
        "the probe enabled SO_REUSEADDR, which on Windows means it can bind a "
        "port another process is actively using -- so it reports busy ports free"
    )


def test_a_free_port_is_returned_unchanged():
    """It moves only when it has to. Someone reading the printed URL should
    see 8000 in the ordinary case."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind((HOST, 0))
    free = probe.getsockname()[1]
    probe.close()

    assert _free_port(HOST, free) == free

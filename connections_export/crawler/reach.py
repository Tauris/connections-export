"""Whether the server an archive came from can be reached, asked before a
repair starts.

A repair fetches what the original capture missed, so it needs that server.
One request settles it before anything is written: any answer at all means
the server is there; no answer means the repair cannot run now, and the
person is told so in words, with nothing in the archive changed.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from connections_export.http.results import Fetched


def server_problem(client, base_url: str) -> str | None:
    """None when `base_url` answers; otherwise a sentence saying why a repair
    cannot run, for the person who asked for it."""
    host = urlsplit(base_url).hostname or base_url
    try:
        answer = client.get(base_url.rstrip("/") + "/")
    except Exception:  # noqa: BLE001 - any way of not answering is not answering
        answer = None
    status = answer.status if isinstance(answer, Fetched) else getattr(answer, "status", None)
    if status in (401, 403):
        return (
            f"Repair needs to sign in to {host}, the server this archive was captured "
            f"from, and signing in there did not work (HTTP {status}). Nothing in the "
            "archive was changed. Check that this computer is signed in with an account "
            "that can open it, then try again."
        )
    if status is None or status >= 500:
        return (
            f"Repair needs the server this archive was captured from, {host}, to be "
            "available, and it cannot be contacted right now. Nothing in the archive was "
            f"changed. Try again when {host} can be reached from this computer."
        )
    return None

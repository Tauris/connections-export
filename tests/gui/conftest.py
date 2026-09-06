"""Shared helpers for the console's HTTP tests.

`read_sse` is here because five test files had each written their own SSE
reader, and they did not agree. Three scan line by line and pick out the
ones beginning `data: `; two split the stream into blocks and require the
BLOCK to begin `data: `.

An SSE event is a block of `field: value` lines, so the moment the stream
started carrying `id:` -- what a browser hands back to resume a dropped
connection -- the three line-based readers carried on working and the two
block-based ones read every run as empty. Same stream, same change, two
different answers, and the ones that kept working made the ones that broke
look like a product bug.

The block-based readers use this. The line-based ones are correct as they
stand and are left alone; what they are not is a second opinion.
"""

from __future__ import annotations

import json
from typing import Any


async def read_sse(
    response,
    collected: list[dict[str, Any]],
    *,
    read_limit: int | None = None,
    ids: list[str] | None = None,
) -> None:
    """Read an SSE response into `collected`, the way a browser does.

    `ids` collects each event's `id:` -- the value a reconnecting EventSource
    sends back in `Last-Event-ID`. `read_limit` stops early and leaves the
    connection open, which is how a mid-stream disconnect is reproduced: the
    caller exits its `async with` block, and that is what actually drops it.
    """
    buffer = ""
    async for chunk in response.aiter_text():
        buffer += chunk
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            payload = None
            for line in raw.split("\n"):
                field, _, value = line.partition(": ")
                if field == "data":
                    payload = json.loads(value)
                elif field == "id" and ids is not None:
                    ids.append(value)
            if payload is None:
                continue
            collected.append(payload)
            if read_limit is not None and len(collected) >= read_limit:
                return

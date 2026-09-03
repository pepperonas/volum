"""Server-sent events over a thread boundary.

Jobs and installs run on worker threads and report through listener callbacks.
A client on the event loop wants those as a stream. This module is the bridge:
each connection gets its own queue, the listener posts into it from whatever
thread it is on, and an async generator drains it as ``data:`` lines.

Each event carries the full record, not a delta. That makes the stream safe to
join late and safe to miss frames in — the newest event is always the whole
truth — and a ``seq`` lets the server drop anything that would go backwards
between the initial snapshot and the first live update.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import Generic, TypeVar

from fastapi import Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

#: Idle gap after which a comment line is sent so proxies and browsers keep the
#: connection open. Harmless for loopback; costs nothing.
HEARTBEAT_SECONDS = 15.0

Subscribe = Callable[[Callable[[T], None]], Callable[[], None]]


class EventSource(Generic[T]):
    """One subscription, delivered to one asyncio queue."""

    def __init__(
        self,
        subscribe: Subscribe[T],
        *,
        accept: Callable[[T], bool],
        sequence: Callable[[T], int],
        is_final: Callable[[T], bool],
    ) -> None:
        self._subscribe = subscribe
        self._accept = accept
        self._sequence = sequence
        self._is_final = is_final

    async def stream(self, request: Request, snapshot: T | None) -> AsyncIterator[bytes]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[T] = asyncio.Queue()

        def listener(item: T) -> None:
            if not self._accept(item):
                return
            # Copy *here*, on the producer's thread, while the object still
            # says what it said when the notification fired. Queueing the live
            # record itself let the pipeline thread append the next stage
            # before the loop serialised the previous one — two frames with the
            # same state, one message gone (seen live: seq 7 → 9).
            loop.call_soon_threadsafe(queue.put_nowait, item.model_copy(deep=True))

        # Subscribe before reading the snapshot: an update between the two is
        # then queued rather than lost, and the sequence check below drops any
        # that turns out to be older than the snapshot.
        unsubscribe = self._subscribe(listener)
        last_seq = -1
        try:
            if snapshot is not None:
                last_seq = self._sequence(snapshot)
                yield _frame(snapshot)
                if self._is_final(snapshot):
                    return
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield b": keep-alive\n\n"
                    continue
                seq = self._sequence(item)
                if seq <= last_seq:
                    continue
                last_seq = seq
                yield _frame(item)
                if self._is_final(item):
                    return
        finally:
            with contextlib.suppress(Exception):
                unsubscribe()

    def response(self, request: Request, snapshot: T | None) -> StreamingResponse:
        return StreamingResponse(
            self.stream(request, snapshot),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )


def _frame(item: BaseModel) -> bytes:
    return f"data: {item.model_dump_json()}\n\n".encode()

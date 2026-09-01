"""In-process SSE broker.

Background worker threads (engine bridge, agent passes) publish events; async
SSE endpoints subscribe. `/api/runs/{id}/events` carries that run's stage
events plus new-post notifications (posts are broadcast to every open stream
so feeds can refresh live, per SPEC-APP section 7).
"""

from __future__ import annotations

import asyncio
import threading


class Broker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        # sub id -> (loop, queue, run_id filter or None)
        self._subs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue, int | None]] = {}
        self._next_id = 1

    def subscribe(self, run_id: int | None = None):
        """Call from async context. Returns (sub_id, queue)."""
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subs[sub_id] = (loop, q, run_id)
        return sub_id, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._lock:
            self._subs.pop(sub_id, None)

    def publish(self, event: str, data: dict, run_id: int | None = None) -> None:
        """Thread-safe. run_id given -> only that run's subscribers;
        run_id None -> broadcast (used for post notifications)."""
        item = {"event": event, "data": data}
        with self._lock:
            targets = [
                (loop, q) for (loop, q, filt) in self._subs.values()
                if run_id is None or filt is None or filt == run_id
            ]
        for loop, q in targets:
            try:
                loop.call_soon_threadsafe(q.put_nowait, item)
            except RuntimeError:
                pass  # subscriber's loop already closed


broker = Broker()

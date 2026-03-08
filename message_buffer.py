"""In-memory message buffer — single-consumer getUpdates architecture.

The inbound loop is the sole getUpdates consumer. It dispatches messages into
per-topic and DM buffers. Tools (check_replies, ask, andon) read from buffers
instead of calling getUpdates directly.
"""
import asyncio
from collections import defaultdict


class MessageBuffer:
    """Thread-safe message buffer keyed by topic (tmux_target) or 'dm'.

    Messages are consumed on read — once read, they're gone.
    """

    DM_KEY = "__dm__"

    def __init__(self):
        self._buffers: dict[str, list[dict]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._events: dict[str, asyncio.Event] = defaultdict(asyncio.Event)

    async def append(self, key: str, message: dict) -> None:
        """Add a message to a buffer and signal waiters."""
        async with self._lock:
            self._buffers[key].append(message)
            self._events[key].set()

    async def consume(self, key: str) -> list[dict]:
        """Consume and return all messages for a key. Returns [] if empty."""
        async with self._lock:
            messages = self._buffers.pop(key, [])
            if key in self._events:
                self._events[key].clear()
            return messages

    async def wait_for_messages(self, key: str, timeout: float) -> list[dict]:
        """Wait up to timeout seconds for messages, then consume them.

        Returns messages if any arrive, or [] on timeout.
        """
        event = self._events[key]
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
        return await self.consume(key)

    async def peek(self, key: str) -> list[dict]:
        """Peek at messages without consuming. For testing/debugging."""
        async with self._lock:
            return list(self._buffers.get(key, []))

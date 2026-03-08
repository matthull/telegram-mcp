"""Tests for MessageBuffer — T007 single-consumer getUpdates architecture."""
import asyncio
import pytest

from message_buffer import MessageBuffer


class TestMessageBufferBasics:
    """Loop 1: Buffer append, consume, and key isolation."""

    @pytest.mark.anyio
    async def test_append_and_consume(self):
        buf = MessageBuffer()
        await buf.append("topic:a", {"text": "hello"})
        messages = await buf.consume("topic:a")
        assert len(messages) == 1
        assert messages[0]["text"] == "hello"

    @pytest.mark.anyio
    async def test_consume_empties_buffer(self):
        buf = MessageBuffer()
        await buf.append("topic:a", {"text": "hello"})
        await buf.consume("topic:a")
        messages = await buf.consume("topic:a")
        assert messages == []

    @pytest.mark.anyio
    async def test_consume_empty_key_returns_empty(self):
        buf = MessageBuffer()
        messages = await buf.consume("nonexistent")
        assert messages == []

    @pytest.mark.anyio
    async def test_multiple_messages_same_key(self):
        buf = MessageBuffer()
        await buf.append("topic:a", {"text": "one"})
        await buf.append("topic:a", {"text": "two"})
        messages = await buf.consume("topic:a")
        assert len(messages) == 2
        assert messages[0]["text"] == "one"
        assert messages[1]["text"] == "two"

    @pytest.mark.anyio
    async def test_key_isolation(self):
        """Messages for different keys don't mix."""
        buf = MessageBuffer()
        await buf.append("topic:a", {"text": "for a"})
        await buf.append("topic:b", {"text": "for b"})
        a_msgs = await buf.consume("topic:a")
        b_msgs = await buf.consume("topic:b")
        assert len(a_msgs) == 1
        assert a_msgs[0]["text"] == "for a"
        assert len(b_msgs) == 1
        assert b_msgs[0]["text"] == "for b"

    @pytest.mark.anyio
    async def test_dm_buffer_key(self):
        buf = MessageBuffer()
        await buf.append(MessageBuffer.DM_KEY, {"text": "dm msg"})
        messages = await buf.consume(MessageBuffer.DM_KEY)
        assert len(messages) == 1
        assert messages[0]["text"] == "dm msg"


class TestMessageBufferWait:
    """Loop 1: wait_for_messages with timeout."""

    @pytest.mark.anyio
    async def test_wait_returns_on_message(self):
        buf = MessageBuffer()

        async def delayed_append():
            await asyncio.sleep(0.05)
            await buf.append("topic:a", {"text": "arrived"})

        task = asyncio.create_task(delayed_append())
        messages = await buf.wait_for_messages("topic:a", timeout=2.0)
        await task
        assert len(messages) == 1
        assert messages[0]["text"] == "arrived"

    @pytest.mark.anyio
    async def test_wait_returns_empty_on_timeout(self):
        buf = MessageBuffer()
        messages = await buf.wait_for_messages("topic:a", timeout=0.05)
        assert messages == []

    @pytest.mark.anyio
    async def test_wait_consumes_messages(self):
        """After wait_for_messages returns, buffer should be empty."""
        buf = MessageBuffer()
        await buf.append("topic:a", {"text": "hello"})
        messages = await buf.wait_for_messages("topic:a", timeout=0.1)
        assert len(messages) == 1
        # Second read should be empty
        messages2 = await buf.consume("topic:a")
        assert messages2 == []


class TestMessageBufferConcurrency:
    """Loop 2: Concurrent access patterns."""

    @pytest.mark.anyio
    async def test_concurrent_append_and_consume(self):
        """Multiple topics receiving messages concurrently."""
        buf = MessageBuffer()
        topics = [f"topic:{i}" for i in range(5)]

        async def producer(key: str, count: int):
            for j in range(count):
                await buf.append(key, {"text": f"{key}-{j}"})

        # Produce messages to all topics concurrently
        await asyncio.gather(*[producer(t, 10) for t in topics])

        # Each topic should have exactly 10 messages
        for t in topics:
            messages = await buf.consume(t)
            assert len(messages) == 10

    @pytest.mark.anyio
    async def test_concurrent_wait_different_topics(self):
        """Two waiters on different topics both get their messages."""
        buf = MessageBuffer()

        async def delayed_append(key: str, delay: float):
            await asyncio.sleep(delay)
            await buf.append(key, {"text": f"for {key}"})

        t1 = asyncio.create_task(delayed_append("topic:a", 0.05))
        t2 = asyncio.create_task(delayed_append("topic:b", 0.05))

        results = await asyncio.gather(
            buf.wait_for_messages("topic:a", timeout=2.0),
            buf.wait_for_messages("topic:b", timeout=2.0),
        )
        await t1
        await t2

        assert len(results[0]) == 1
        assert results[0][0]["text"] == "for topic:a"
        assert len(results[1]) == 1
        assert results[1][0]["text"] == "for topic:b"

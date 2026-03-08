"""Tests for inbound polling loop — T005 + T007 buffer integration."""
import os
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummy_hash")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token")

from topic_registry import TopicRegistry
from message_buffer import MessageBuffer
from inbound_loop import poll_once, dispatch_to_tmux, run_inbound_loop


def _mock_httpx_post(json_data):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


@pytest.fixture
def registry(tmp_path):
    reg = TopicRegistry(str(tmp_path / "registry.json"))
    reg.register("village:chart", 42)
    reg.register("musashi:code", 99)
    return reg


@pytest.fixture
def buffer():
    return MessageBuffer()


class TestPollOnceDispatch:
    """Messages with known topic_id dispatch correct tmux send-keys."""

    @pytest.mark.anyio
    async def test_dispatches_to_correct_tmux_target(self, registry, buffer):
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {
                        "text": "hello from topic",
                        "message_thread_id": 42,
                        "chat": {"id": -100123},
                    },
                }
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock) as mock_dispatch:
            new_id = await poll_once("token", -100123, registry, 0, buffer)

        mock_dispatch.assert_called_once_with("village:chart", "hello from topic")
        assert new_id == 100

    @pytest.mark.anyio
    async def test_unknown_topic_logs_warning(self, registry, buffer, caplog):
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 200,
                    "message": {
                        "text": "unknown topic msg",
                        "message_thread_id": 999,
                        "chat": {"id": -100123},
                    },
                }
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        import logging
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock) as mock_dispatch, \
             caplog.at_level(logging.WARNING, logger="telegram_mcp.inbound"):
            new_id = await poll_once("token", -100123, registry, 0, buffer)

        mock_dispatch.assert_not_called()
        assert "Unrouted" in caplog.text
        assert new_id == 200

    @pytest.mark.anyio
    async def test_buffers_topic_messages(self, registry, buffer):
        """Messages should be buffered by tmux_target for tool consumption."""
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 300,
                    "message": {
                        "text": "buffered msg",
                        "message_thread_id": 42,
                        "chat": {"id": -100123},
                        "date": 1234567,
                    },
                }
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock):
            await poll_once("token", -100123, registry, 0, buffer)

        messages = await buffer.consume("village:chart")
        assert len(messages) == 1
        assert messages[0]["text"] == "buffered msg"
        assert messages[0]["update_id"] == 300

    @pytest.mark.anyio
    async def test_buffers_dm_messages(self, registry, buffer):
        """Non-forum messages should be buffered as DMs."""
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 400,
                    "message": {
                        "text": "dm message",
                        "chat": {"id": 99999},
                        "date": 1234567,
                    },
                }
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock) as mock_dispatch:
            new_id = await poll_once("token", -100123, registry, 0, buffer)

        mock_dispatch.assert_not_called()
        dm_messages = await buffer.consume(MessageBuffer.DM_KEY)
        assert len(dm_messages) == 1
        assert dm_messages[0]["text"] == "dm message"
        assert new_id == 400

    @pytest.mark.anyio
    async def test_ignores_messages_from_other_chats_without_buffer(self, registry):
        """Without a buffer, non-forum messages are silently ignored."""
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 400,
                    "message": {
                        "text": "dm message",
                        "chat": {"id": 99999},
                    },
                }
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock) as mock_dispatch:
            new_id = await poll_once("token", -100123, registry, 0)

        mock_dispatch.assert_not_called()
        assert new_id == 400

    @pytest.mark.anyio
    async def test_updates_last_update_id(self, registry, buffer):
        api_response = {
            "ok": True,
            "result": [
                {"update_id": 500, "message": {"text": "a", "message_thread_id": 42, "chat": {"id": -100123}}},
                {"update_id": 501, "message": {"text": "b", "message_thread_id": 99, "chat": {"id": -100123}}},
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock):
            new_id = await poll_once("token", -100123, registry, 0, buffer)

        assert new_id == 501

    @pytest.mark.anyio
    async def test_no_results_returns_same_id(self, registry):
        mock_client = _mock_httpx_post({"ok": True, "result": []})
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client):
            new_id = await poll_once("token", -100123, registry, 50)

        assert new_id == 50


class TestDispatchToTmux:
    """Verify tmux send-keys invocation."""

    @pytest.mark.anyio
    async def test_single_line_dispatch(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("inbound_loop.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await dispatch_to_tmux("village:chart", "hello")

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "tmux-claude-send"
        assert args[1] == "village:chart.0"
        assert "Telegram message from operator: hello" in args[2]

    @pytest.mark.anyio
    async def test_multiline_dispatch_single_call(self):
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("inbound_loop.asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await dispatch_to_tmux("village:chart", "line1\nline2\nline3")

        # tmux-claude-send handles multi-line — single call
        mock_exec.assert_called_once()
        assert "line1\nline2\nline3" in mock_exec.call_args[0][2]


class TestRunInboundLoop:
    """Verify loop lifecycle."""

    @pytest.mark.anyio
    async def test_loop_cancellation(self, registry, buffer):
        """Loop should stop cleanly on cancellation."""
        mock_client = _mock_httpx_post({"ok": True, "result": []})
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.asyncio.sleep", new_callable=AsyncMock):
            task = asyncio.create_task(
                run_inbound_loop("token", -100123, registry, buffer, poll_interval=0.01)
            )
            # Let it run briefly
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    @pytest.mark.anyio
    async def test_loop_recovers_from_poll_error(self, registry, buffer):
        """Loop should continue after a poll error."""
        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                raise asyncio.CancelledError()

        with patch("inbound_loop.poll_once", new_callable=AsyncMock, side_effect=[Exception("network"), 0, 0]), \
             patch("inbound_loop.asyncio.sleep", side_effect=mock_sleep):
            # run_inbound_loop catches CancelledError internally and returns
            await run_inbound_loop("token", -100123, registry, buffer, poll_interval=0.01)

        # Should have attempted at least 2 polls (one error, one success)
        assert call_count >= 2

    @pytest.mark.anyio
    async def test_loop_persists_last_update_id(self, registry, buffer):
        """Loop should persist last_update_id to registry after each poll."""
        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError()

        api_response = {
            "ok": True,
            "result": [
                {"update_id": 777, "message": {"text": "a", "message_thread_id": 42, "chat": {"id": -100123}}},
            ],
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client), \
             patch("inbound_loop.dispatch_to_tmux", new_callable=AsyncMock), \
             patch("inbound_loop.asyncio.sleep", side_effect=mock_sleep):
            await run_inbound_loop("token", -100123, registry, buffer, poll_interval=0.01)

        assert registry.get_last_update_id() == 777

    @pytest.mark.anyio
    async def test_loop_loads_persisted_last_update_id(self, registry, buffer):
        """Loop should start from persisted last_update_id."""
        registry.set_last_update_id(500)

        call_count = 0

        async def mock_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise asyncio.CancelledError()

        mock_client = _mock_httpx_post({"ok": True, "result": []})
        with patch("inbound_loop.httpx.AsyncClient", return_value=mock_client) as _, \
             patch("inbound_loop.asyncio.sleep", side_effect=mock_sleep):
            await run_inbound_loop("token", -100123, registry, buffer, poll_interval=0.01)

        # Verify the offset used in the API call was 501 (500 + 1)
        post_call = mock_client.post.call_args
        params = post_call[1]["json"]
        assert params["offset"] == 501

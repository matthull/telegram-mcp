"""Tests for ask and andon with tmux_target — T004."""
import os
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummy_hash")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token")
os.environ.setdefault("NOTIFY_CHAT_ID", "99999")

import main


def _mock_httpx_post(json_data):
    """Create a mock for httpx.AsyncClient().post that returns json_data."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


def _mock_httpx_post_sequence(responses):
    """Create a mock that returns different responses on successive calls."""
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_resps = []
    for json_data in responses:
        r = MagicMock()
        r.json.return_value = json_data
        r.raise_for_status = MagicMock()
        mock_resps.append(r)

    mock_client.post = AsyncMock(side_effect=mock_resps)
    return mock_client


class TestAskWithoutTmuxTarget:
    """Backward compatibility: ask without tmux_target uses DM."""

    @pytest.mark.anyio
    async def test_ask_dm_sends_to_notify_chat_id(self):
        # Response sequence: send message, baseline poll, poll with reply
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [{"update_id": 500, "message": {"text": "yes"}}]},  # reply
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        main._last_seen_message_id = 0
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await main.ask("question?", timeout_seconds=60, poll_interval=5)

        assert "yes" in result
        # First call should be sendMessage to NOTIFY_CHAT_ID
        first_call = mock_client.post.call_args_list[0]
        assert "sendMessage" in first_call[0][0]

    @pytest.mark.anyio
    async def test_ask_dm_timeout(self):
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": []},  # poll 1 - empty
            {"ok": True, "result": []},  # poll 2 - empty
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        main._last_seen_message_id = 0
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await main.ask("question?", timeout_seconds=10, poll_interval=5)

        assert "timeout" in result.lower()


class TestAskWithTmuxTarget:
    """ask with tmux_target routes through forum topics."""

    @pytest.mark.anyio
    async def test_ask_topic_sends_to_forum_and_polls_topic(self):
        # Response sequence: send message, baseline poll, poll with topic reply
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send to topic
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [
                {"update_id": 600, "message": {
                    "text": "topic answer",
                    "message_thread_id": 42,
                    "chat": {"id": -100123}
                }}
            ]},  # reply in topic
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry, \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_registry.get_last_seen.return_value = 0
            result = await main.ask("question?", tmux_target="village:chart",
                                    timeout_seconds=60, poll_interval=5)

        assert "topic answer" in result

    @pytest.mark.anyio
    async def test_ask_topic_filters_by_thread_id(self):
        """Only messages matching the topic's thread_id should be returned."""
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [
                {"update_id": 700, "message": {
                    "text": "wrong topic",
                    "message_thread_id": 99,
                    "chat": {"id": -100123}
                }},
                {"update_id": 701, "message": {
                    "text": "right topic",
                    "message_thread_id": 42,
                    "chat": {"id": -100123}
                }},
            ]},
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry, \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_registry.get_last_seen.return_value = 0
            result = await main.ask("question?", tmux_target="village:chart",
                                    timeout_seconds=60, poll_interval=5)

        assert "right topic" in result
        assert "wrong topic" not in result

    @pytest.mark.anyio
    async def test_ask_topic_error_when_no_forum_group(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", None):
            result = await main.ask("question?", tmux_target="village:chart")

        assert "TELEGRAM_FORUM_GROUP_ID" in result
        assert "Error" in result

    @pytest.mark.anyio
    async def test_ask_topic_updates_per_topic_last_seen(self):
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [
                {"update_id": 800, "message": {
                    "text": "answer",
                    "message_thread_id": 42,
                    "chat": {"id": -100123}
                }}
            ]},
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry, \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_registry.get_last_seen.return_value = 0
            await main.ask("question?", tmux_target="village:chart",
                          timeout_seconds=60, poll_interval=5)

        mock_registry.set_last_seen.assert_called_with("village:chart", 800)


class TestBufferBasedPolling:
    """T007: When inbound loop is active, ask/check_replies read from buffer."""

    @pytest.mark.anyio
    async def test_ask_reads_from_buffer_when_inbound_active(self):
        """With inbound loop active, ask should read from message buffer."""
        from message_buffer import MessageBuffer

        async def fake_poll_reply(key, timeout_seconds, poll_interval):
            return "Matt replied:\nbuffered reply"

        mock_client = _mock_httpx_post_sequence([
            {"ok": True, "result": {"message_id": 1}},  # send
        ])
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "_inbound_loop_active", True), \
             patch("main._poll_for_reply_buffer", side_effect=fake_poll_reply):
            result = await main.ask("question?", tmux_target="village:chart",
                                    timeout_seconds=60, poll_interval=5)

        assert "buffered reply" in result

    @pytest.mark.anyio
    async def test_ask_falls_back_to_direct_when_no_inbound(self):
        """Without inbound loop, ask should call getUpdates directly."""
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [
                {"update_id": 900, "message": {
                    "text": "direct reply",
                    "message_thread_id": 42,
                    "chat": {"id": -100123}
                }}
            ]},
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "_inbound_loop_active", False), \
             patch.object(main, "topic_registry") as mock_registry, \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_registry.get_last_seen.return_value = 0
            result = await main.ask("question?", tmux_target="village:chart",
                                    timeout_seconds=60, poll_interval=5)

        assert "direct reply" in result

    @pytest.mark.anyio
    async def test_ask_dm_reads_from_buffer_when_inbound_active(self):
        """DM path should also use buffer when inbound loop is active."""
        from message_buffer import MessageBuffer

        async def fake_poll_reply(key, timeout_seconds, poll_interval):
            assert key == MessageBuffer.DM_KEY
            return "Matt replied:\ndm buffered"

        mock_client = _mock_httpx_post_sequence([
            {"ok": True, "result": {"message_id": 1}},  # send
        ])
        main._last_seen_message_id = 0
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch.object(main, "_inbound_loop_active", True), \
             patch("main._poll_for_reply_buffer", side_effect=fake_poll_reply):
            result = await main.ask("question?", timeout_seconds=60, poll_interval=5)

        assert "dm buffered" in result


class TestAndonWithTmuxTarget:
    """andon with tmux_target routes through forum topics."""

    @pytest.mark.anyio
    async def test_andon_topic_sends_to_forum(self):
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [
                {"update_id": 1000, "message": {
                    "text": "on it",
                    "message_thread_id": 42,
                    "chat": {"id": -100123}
                }}
            ]},
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry, \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_registry.get_last_seen.return_value = 0
            result = await main.andon("blocker!", tmux_target="village:chart",
                                      timeout_seconds=60, poll_interval=5)

        assert "on it" in result
        # Verify urgent formatting was sent
        send_call = mock_client.post.call_args_list[0]
        payload = send_call[1]["json"]
        assert "ANDON" in payload["text"]
        assert payload["message_thread_id"] == 42

    @pytest.mark.anyio
    async def test_andon_topic_error_when_no_forum_group(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", None):
            result = await main.andon("blocker!", tmux_target="village:chart")

        assert "TELEGRAM_FORUM_GROUP_ID" in result

    @pytest.mark.anyio
    async def test_andon_dm_backward_compat(self):
        responses = [
            {"ok": True, "result": {"message_id": 1}},  # send
            {"ok": True, "result": []},  # baseline
            {"ok": True, "result": [{"update_id": 1100, "message": {"text": "ack"}}]},
        ]
        mock_client = _mock_httpx_post_sequence(responses)
        main._last_seen_message_id = 0
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await main.andon("blocker!", timeout_seconds=60, poll_interval=5)

        assert "ack" in result

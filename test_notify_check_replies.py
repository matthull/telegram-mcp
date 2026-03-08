"""Tests for notify and check_replies with tmux_target — T003."""
import os
import json
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummy_hash")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token")
os.environ.setdefault("NOTIFY_CHAT_ID", "99999")

import main
from topic_registry import TopicRegistry


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


class TestNotifyWithoutTmuxTarget:
    """Backward compatibility: notify without tmux_target sends to DM."""

    @pytest.mark.anyio
    async def test_notify_dm_sends_to_notify_chat_id(self):
        mock_client = _mock_httpx_post({"ok": True, "result": {"message_id": 1}})
        with patch("main.httpx.AsyncClient", return_value=mock_client):
            result = await main.notify("hello")

        assert result == "Message sent."
        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["chat_id"] == int(os.environ["NOTIFY_CHAT_ID"])
        assert payload["text"] == "hello"

    @pytest.mark.anyio
    async def test_notify_dm_no_thread_id_in_payload(self):
        mock_client = _mock_httpx_post({"ok": True, "result": {"message_id": 1}})
        with patch("main.httpx.AsyncClient", return_value=mock_client):
            await main.notify("hello")

        payload = mock_client.post.call_args[1]["json"]
        assert "message_thread_id" not in payload


class TestNotifyWithTmuxTarget:
    """notify with tmux_target routes through forum topics."""

    @pytest.mark.anyio
    async def test_notify_topic_sends_to_forum_group(self):
        mock_client = _mock_httpx_post({"ok": True, "result": {"message_id": 1}})
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.resolve_topic", new_callable=AsyncMock, return_value=42), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123):
            result = await main.notify("hello", tmux_target="village:chart")

        assert result == "Message sent."
        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["chat_id"] == -100123
        assert payload["message_thread_id"] == 42

    @pytest.mark.anyio
    async def test_notify_topic_error_when_no_forum_group(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", None):
            result = await main.notify("hello", tmux_target="village:chart")

        assert "TELEGRAM_FORUM_GROUP_ID" in result
        assert "Error" in result


class TestCheckRepliesWithoutTmuxTarget:
    """Backward compatibility: check_replies without tmux_target uses DM tracking."""

    @pytest.mark.anyio
    async def test_check_replies_dm_returns_messages(self):
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 100,
                    "message": {"text": "hey there", "date": 1234567}
                }
            ]
        }
        mock_client = _mock_httpx_post(api_response)
        # Reset global tracking
        main._last_seen_message_id = 0
        with patch("main.httpx.AsyncClient", return_value=mock_client):
            result = await main.check_replies()

        assert "hey there" in result

    @pytest.mark.anyio
    async def test_check_replies_dm_no_messages(self):
        mock_client = _mock_httpx_post({"ok": True, "result": []})
        with patch("main.httpx.AsyncClient", return_value=mock_client):
            result = await main.check_replies()

        assert result == "No new replies."


class TestCheckRepliesWithTmuxTarget:
    """check_replies with tmux_target uses per-topic tracking."""

    @pytest.mark.anyio
    async def test_check_replies_topic_filters_by_thread_id(self):
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 200,
                    "message": {
                        "text": "topic reply",
                        "date": 1234567,
                        "message_thread_id": 42,
                        "chat": {"id": -100123}
                    }
                },
                {
                    "update_id": 201,
                    "message": {
                        "text": "other topic",
                        "date": 1234568,
                        "message_thread_id": 99,
                        "chat": {"id": -100123}
                    }
                },
            ]
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry:
            mock_registry.get_topic_id.return_value = 42
            mock_registry.get_last_seen.return_value = 0
            result = await main.check_replies(tmux_target="village:chart")

        assert "topic reply" in result
        assert "other topic" not in result

    @pytest.mark.anyio
    async def test_check_replies_topic_error_when_no_forum_group(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", None):
            result = await main.check_replies(tmux_target="village:chart")

        assert "TELEGRAM_FORUM_GROUP_ID" in result

    @pytest.mark.anyio
    async def test_check_replies_topic_updates_per_topic_last_seen(self):
        api_response = {
            "ok": True,
            "result": [
                {
                    "update_id": 300,
                    "message": {
                        "text": "reply",
                        "date": 1234567,
                        "message_thread_id": 42,
                        "chat": {"id": -100123}
                    }
                }
            ]
        }
        mock_client = _mock_httpx_post(api_response)
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry:
            mock_registry.get_topic_id.return_value = 42
            mock_registry.get_last_seen.return_value = 0
            await main.check_replies(tmux_target="village:chart")

        mock_registry.set_last_seen.assert_called_with("village:chart", 300)

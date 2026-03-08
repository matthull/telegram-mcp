"""Tests for forum Bot API helpers — T002."""
import os
import json
import tempfile
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "dummy_hash"
os.environ["TELEGRAM_BOT_TOKEN"] = "test_bot_token"

from topic_registry import TopicRegistry
from forum_helpers import (
    bot_send_message,
    create_forum_topic,
    resolve_topic,
)


@pytest.fixture
def registry(tmp_path):
    return TopicRegistry(str(tmp_path / "registry.json"))


def _mock_response(json_data, status_code=200):
    """Create a mock httpx response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


class TestBotSendMessage:
    """Verify _bot_send_message handles message_thread_id correctly."""

    @pytest.mark.anyio
    async def test_send_without_thread_id(self):
        mock_resp = _mock_response({"ok": True, "result": {"message_id": 1}})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            result = await bot_send_message("test_token", 12345, "hello")

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["chat_id"] == 12345
        assert payload["text"] == "hello"
        assert "message_thread_id" not in payload

    @pytest.mark.anyio
    async def test_send_with_thread_id(self):
        mock_resp = _mock_response({"ok": True, "result": {"message_id": 1}})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            result = await bot_send_message("test_token", 12345, "hello", message_thread_id=42)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert payload["message_thread_id"] == 42

    @pytest.mark.anyio
    async def test_send_returns_api_response(self):
        expected = {"ok": True, "result": {"message_id": 99}}
        mock_resp = _mock_response(expected)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            result = await bot_send_message("test_token", 12345, "hello")

        assert result == expected


class TestCreateForumTopic:
    """Verify topic creation calls correct Bot API endpoint."""

    @pytest.mark.anyio
    async def test_creates_topic_and_returns_thread_id(self):
        api_response = {
            "ok": True,
            "result": {"message_thread_id": 42, "name": "village:chart"}
        }
        mock_resp = _mock_response(api_response)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            thread_id = await create_forum_topic("test_token", -100123, "village:chart")

        assert thread_id == 42
        call_args = mock_client.post.call_args
        assert "createForumTopic" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == -100123
        assert payload["name"] == "village:chart"

    @pytest.mark.anyio
    async def test_api_failure_raises(self):
        mock_resp = _mock_response({}, status_code=400)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_resp)

        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(Exception):
                await create_forum_topic("test_token", -100123, "village:chart")


class TestResolveTopicLazyCreation:
    """Verify lazy topic creation — registry lookup, then create if absent."""

    @pytest.mark.anyio
    async def test_returns_cached_topic_without_api_call(self, registry):
        registry.register("village:chart", 42)

        with patch("forum_helpers.create_forum_topic") as mock_create:
            thread_id = await resolve_topic("test_token", -100123, "village:chart", registry)

        assert thread_id == 42
        mock_create.assert_not_called()

    @pytest.mark.anyio
    async def test_creates_topic_when_not_cached(self, registry):
        with patch("forum_helpers.create_forum_topic", new_callable=AsyncMock, return_value=99) as mock_create:
            thread_id = await resolve_topic("test_token", -100123, "musashi:code", registry)

        assert thread_id == 99
        mock_create.assert_called_once_with("test_token", -100123, "musashi:code")
        # Should be cached now
        assert registry.get_topic_id("musashi:code") == 99

    @pytest.mark.anyio
    async def test_second_call_uses_cache(self, registry):
        with patch("forum_helpers.create_forum_topic", new_callable=AsyncMock, return_value=99):
            await resolve_topic("test_token", -100123, "musashi:code", registry)

        with patch("forum_helpers.create_forum_topic") as mock_create:
            thread_id = await resolve_topic("test_token", -100123, "musashi:code", registry)

        assert thread_id == 99
        mock_create.assert_not_called()

    @pytest.mark.anyio
    async def test_registry_persistence_on_creation(self, registry, tmp_path):
        with patch("forum_helpers.create_forum_topic", new_callable=AsyncMock, return_value=55):
            await resolve_topic("test_token", -100123, "test:target", registry)

        # Load fresh registry from same file — should see the cached topic
        reg2 = TopicRegistry(str(tmp_path / "registry.json"))
        assert reg2.get_topic_id("test:target") == 55

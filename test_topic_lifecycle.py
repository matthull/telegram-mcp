"""Tests for topic lifecycle tools — T006."""
import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

os.environ.setdefault("TELEGRAM_API_ID", "12345")
os.environ.setdefault("TELEGRAM_API_HASH", "dummy_hash")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test_bot_token")
os.environ.setdefault("NOTIFY_CHAT_ID", "99999")

import main
from topic_registry import TopicRegistry
from forum_helpers import close_forum_topic, reopen_forum_topic, resolve_topic


def _mock_httpx_post(json_data):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestCloseForumTopicHelper:
    """Bot API helper for closing topics."""

    @pytest.mark.anyio
    async def test_calls_correct_api_endpoint(self):
        mock_client = _mock_httpx_post({"ok": True})
        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            await close_forum_topic("token", -100123, 42)

        call_args = mock_client.post.call_args
        assert "closeForumTopic" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == -100123
        assert payload["message_thread_id"] == 42


class TestReopenForumTopicHelper:
    """Bot API helper for reopening topics."""

    @pytest.mark.anyio
    async def test_calls_correct_api_endpoint(self):
        mock_client = _mock_httpx_post({"ok": True})
        with patch("forum_helpers.httpx.AsyncClient", return_value=mock_client):
            await reopen_forum_topic("token", -100123, 42)

        call_args = mock_client.post.call_args
        assert "reopenForumTopic" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["chat_id"] == -100123
        assert payload["message_thread_id"] == 42


class TestCloseTopicMCPTool:
    """close_topic MCP tool."""

    @pytest.mark.anyio
    async def test_closes_topic_and_marks_registry(self):
        mock_client = _mock_httpx_post({"ok": True})
        with patch("main.httpx.AsyncClient", return_value=mock_client), \
             patch("main.close_forum_topic", new_callable=AsyncMock) as mock_close, \
             patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry:
            mock_registry.get_topic_id.return_value = 42
            mock_registry.is_closed.return_value = False
            result = await main.close_topic("village:chart")

        assert "closed" in result.lower()
        mock_close.assert_called_once_with("test_bot_token", -100123, 42)
        mock_registry.set_closed.assert_called_once_with("village:chart")

    @pytest.mark.anyio
    async def test_error_when_no_forum_group(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", None):
            result = await main.close_topic("village:chart")

        assert "TELEGRAM_FORUM_GROUP_ID" in result

    @pytest.mark.anyio
    async def test_error_when_unknown_tmux_target(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry:
            mock_registry.get_topic_id.return_value = None
            result = await main.close_topic("unknown:target")

        assert "Error" in result
        assert "unknown:target" in result

    @pytest.mark.anyio
    async def test_already_closed_is_noop(self):
        with patch.object(main, "TELEGRAM_FORUM_GROUP_ID", -100123), \
             patch.object(main, "topic_registry") as mock_registry:
            mock_registry.get_topic_id.return_value = 42
            mock_registry.is_closed.return_value = True
            result = await main.close_topic("village:chart")

        assert "already closed" in result.lower()


class TestListActiveTopicsMCPTool:
    """list_active_topics MCP tool."""

    @pytest.mark.anyio
    async def test_lists_topics_with_status(self):
        with patch.object(main, "topic_registry") as mock_registry:
            mock_registry.list_topics.return_value = [
                {"tmux_target": "village:chart", "topic_id": 42, "status": "open"},
                {"tmux_target": "musashi:code", "topic_id": 99, "status": "closed"},
            ]
            result = await main.list_active_topics()

        assert "village:chart" in result
        assert "musashi:code" in result
        assert "open" in result
        assert "closed" in result

    @pytest.mark.anyio
    async def test_empty_registry(self):
        with patch.object(main, "topic_registry") as mock_registry:
            mock_registry.list_topics.return_value = []
            result = await main.list_active_topics()

        assert "No topics" in result


class TestAutoReopenOnOutbound:
    """Verify resolve_topic auto-reopens closed topics."""

    @pytest.mark.anyio
    async def test_reopens_closed_topic_on_resolve(self, tmp_path):
        registry = TopicRegistry(str(tmp_path / "reg.json"))
        registry.register("village:chart", 42)
        registry.set_closed("village:chart")

        with patch("forum_helpers.reopen_forum_topic", new_callable=AsyncMock) as mock_reopen:
            topic_id = await resolve_topic("token", -100123, "village:chart", registry)

        assert topic_id == 42
        mock_reopen.assert_called_once_with("token", -100123, 42)
        assert not registry.is_closed("village:chart")

    @pytest.mark.anyio
    async def test_does_not_reopen_open_topic(self, tmp_path):
        registry = TopicRegistry(str(tmp_path / "reg.json"))
        registry.register("village:chart", 42)

        with patch("forum_helpers.reopen_forum_topic", new_callable=AsyncMock) as mock_reopen:
            topic_id = await resolve_topic("token", -100123, "village:chart", registry)

        assert topic_id == 42
        mock_reopen.assert_not_called()


class TestRegistryClosedState:
    """Verify registry tracks closed state correctly."""

    def test_closed_state_persistence(self, tmp_path):
        reg = TopicRegistry(str(tmp_path / "reg.json"))
        reg.register("village:chart", 42)
        reg.set_closed("village:chart")

        reg2 = TopicRegistry(str(tmp_path / "reg.json"))
        assert reg2.is_closed("village:chart")

    def test_set_open_clears_closed(self, tmp_path):
        reg = TopicRegistry(str(tmp_path / "reg.json"))
        reg.register("village:chart", 42)
        reg.set_closed("village:chart")
        reg.set_open("village:chart")
        assert not reg.is_closed("village:chart")

    def test_list_topics_includes_status(self, tmp_path):
        reg = TopicRegistry(str(tmp_path / "reg.json"))
        reg.register("village:chart", 42)
        reg.register("musashi:code", 99)
        reg.set_closed("musashi:code")

        topics = reg.list_topics()
        by_target = {t["tmux_target"]: t for t in topics}
        assert by_target["village:chart"]["status"] == "open"
        assert by_target["musashi:code"]["status"] == "closed"

    def test_full_lifecycle(self, tmp_path):
        """create → close → reopen cycle preserves data."""
        reg = TopicRegistry(str(tmp_path / "reg.json"))
        reg.register("village:chart", 42)
        assert not reg.is_closed("village:chart")

        reg.set_closed("village:chart")
        assert reg.is_closed("village:chart")
        assert reg.get_topic_id("village:chart") == 42  # still registered

        reg.set_open("village:chart")
        assert not reg.is_closed("village:chart")
        assert reg.get_topic_id("village:chart") == 42

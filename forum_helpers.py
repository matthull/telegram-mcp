"""Forum Bot API helpers — topic creation, message routing, lazy resolution."""
import httpx
from topic_registry import TopicRegistry


async def bot_send_message(
    bot_token: str,
    chat_id: int,
    text: str,
    message_thread_id: int | None = None,
) -> dict:
    """Send a message via Bot HTTP API. Optionally target a forum topic.

    Args:
        bot_token: Telegram bot token.
        chat_id: Chat ID to send to.
        text: Message text.
        message_thread_id: Forum topic thread ID (omit for general/DM).
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if message_thread_id is not None:
        payload["message_thread_id"] = message_thread_id
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()


async def create_forum_topic(bot_token: str, chat_id: int, name: str) -> int:
    """Create a forum topic via Bot API and return the message_thread_id.

    Args:
        bot_token: Telegram bot token.
        chat_id: Forum supergroup chat ID.
        name: Topic name (1-128 chars, typically tmux_target format).

    Returns:
        The message_thread_id of the created topic.
    """
    url = f"https://api.telegram.org/bot{bot_token}/createForumTopic"
    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json={"chat_id": chat_id, "name": name})
        resp.raise_for_status()
        data = resp.json()
    return data["result"]["message_thread_id"]


async def close_forum_topic(bot_token: str, chat_id: int, message_thread_id: int) -> None:
    """Close (archive) a forum topic via Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/closeForumTopic"
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            url, json={"chat_id": chat_id, "message_thread_id": message_thread_id}
        )
        resp.raise_for_status()


async def reopen_forum_topic(bot_token: str, chat_id: int, message_thread_id: int) -> None:
    """Reopen a closed forum topic via Bot API."""
    url = f"https://api.telegram.org/bot{bot_token}/reopenForumTopic"
    async with httpx.AsyncClient() as http:
        resp = await http.post(
            url, json={"chat_id": chat_id, "message_thread_id": message_thread_id}
        )
        resp.raise_for_status()


async def resolve_topic(
    bot_token: str,
    forum_group_id: int,
    tmux_target: str,
    registry: TopicRegistry,
) -> int:
    """Resolve a tmux_target to a message_thread_id, creating the topic if needed.

    Lazy creation: checks registry first, only calls Bot API when topic doesn't exist.

    Args:
        bot_token: Telegram bot token.
        forum_group_id: Forum supergroup chat ID.
        tmux_target: The tmux target identifier (e.g., "village:chart").
        registry: TopicRegistry instance for caching.

    Returns:
        The message_thread_id for the topic.
    """
    topic_id = registry.get_topic_id(tmux_target)
    if topic_id is not None:
        # Auto-reopen if closed
        if registry.is_closed(tmux_target):
            await reopen_forum_topic(bot_token, forum_group_id, topic_id)
            registry.set_open(tmux_target)
        return topic_id

    topic_id = await create_forum_topic(bot_token, forum_group_id, tmux_target)
    registry.register(tmux_target, topic_id)
    return topic_id

"""Inbound polling loop — sole getUpdates consumer, populates message buffer."""
import asyncio
import logging

import httpx

from message_buffer import MessageBuffer
from topic_registry import TopicRegistry

logger = logging.getLogger("telegram_mcp.inbound")

# Default polling interval (seconds)
POLL_INTERVAL = 5


async def dispatch_to_tmux(tmux_target: str, text: str) -> None:
    """Send text to a tmux pane via tmux send-keys.

    Multi-line messages are written to a temp approach using literal newlines
    escaped for tmux send-keys.
    """
    prefixed = f"Telegram message from operator: {text}"
    cmd = ["tmux-claude-send", f"{tmux_target}.0", prefixed]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.warning(
                "tmux-claude-send failed for %s: %s",
                tmux_target,
                stderr.decode().strip(),
            )
    except FileNotFoundError:
        logger.error("tmux-claude-send not found — cannot dispatch to %s", tmux_target)


async def poll_once(
    bot_token: str,
    forum_group_id: int,
    registry: TopicRegistry,
    last_update_id: int,
    message_buffer: MessageBuffer | None = None,
) -> int:
    """Poll getUpdates once, buffer messages, and dispatch to tmux. Returns new last_update_id."""
    url = f"https://api.telegram.org/bot{bot_token}/getUpdates"
    params = {
        "allowed_updates": ["message"],
        "limit": 50,
    }
    if last_update_id > 0:
        params["offset"] = last_update_id + 1

    async with httpx.AsyncClient() as http:
        resp = await http.post(url, json=params)
        resp.raise_for_status()
        data = resp.json()

    max_update_id = last_update_id
    for update in data.get("result", []):
        update_id = update.get("update_id", 0)
        if update_id > max_update_id:
            max_update_id = update_id

        msg = update.get("message", {})
        chat = msg.get("chat", {})
        chat_id = chat.get("id")

        text = msg.get("text", "")
        date = msg.get("date", "")

        # Messages from the forum group → buffer by topic + dispatch to tmux
        if chat_id == forum_group_id:
            thread_id = msg.get("message_thread_id")
            if not text or not thread_id:
                continue

            # Buffer the message for tools (check_replies, ask, andon)
            tmux_target = registry.get_tmux_target(thread_id)
            if tmux_target is not None and message_buffer is not None:
                await message_buffer.append(tmux_target, {
                    "update_id": update_id,
                    "text": text,
                    "date": date,
                    "message_thread_id": thread_id,
                })
                # Also dispatch to tmux
                await dispatch_to_tmux(tmux_target, text)
            elif tmux_target is None:
                logger.warning(
                    "Unrouted message in topic %d: %s",
                    thread_id,
                    text[:80],
                )
        else:
            # DM or other chat — buffer as DM for tools
            if text and message_buffer is not None:
                await message_buffer.append(MessageBuffer.DM_KEY, {
                    "update_id": update_id,
                    "text": text,
                    "date": date,
                })

    return max_update_id


async def run_inbound_loop(
    bot_token: str,
    forum_group_id: int,
    registry: TopicRegistry,
    message_buffer: MessageBuffer,
    poll_interval: float = POLL_INTERVAL,
) -> None:
    """Run the inbound polling loop until cancelled.

    Persists last_update_id to the registry on each successful poll.
    """
    last_update_id = registry.get_last_update_id()
    logger.info("Inbound polling loop started for forum group %d (offset=%d)", forum_group_id, last_update_id)
    try:
        while True:
            try:
                last_update_id = await poll_once(
                    bot_token, forum_group_id, registry, last_update_id, message_buffer
                )
                registry.set_last_update_id(last_update_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in inbound polling loop")
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        logger.info("Inbound polling loop stopped")

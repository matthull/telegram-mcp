"""Topic registry — maps tmux targets to Telegram forum topic IDs with JSON persistence."""
import json
import os
import tempfile


class TopicRegistry:
    """In-memory registry of tmux_target → message_thread_id mappings.

    Persists to a JSON file on every mutation. Loads from file on init.
    Also tracks per-topic last-seen message IDs for independent polling.
    """

    def __init__(self, registry_path: str):
        self._path = registry_path
        # topics: {tmux_target: topic_id}
        self._topics: dict[str, int] = {}
        # last_seen: {tmux_target: last_seen_update_id}
        self._last_seen: dict[str, int] = {}
        # closed: set of tmux_targets whose topics are closed/archived
        self._closed: set[str] = set()
        # last_update_id: global getUpdates offset for inbound loop persistence
        self._last_update_id: int = 0
        self._load()

    def _load(self):
        """Load registry from JSON file if it exists."""
        if os.path.exists(self._path):
            try:
                with open(self._path) as f:
                    data = json.load(f)
                self._topics = data.get("topics", {})
                self._last_seen = {k: v for k, v in data.get("last_seen", {}).items()}
                self._closed = set(data.get("closed", []))
                self._last_update_id = data.get("last_update_id", 0)
            except (json.JSONDecodeError, OSError):
                self._topics = {}
                self._last_seen = {}
                self._closed = set()
                self._last_update_id = 0

    def _save(self):
        """Persist registry to JSON file atomically (temp file + rename)."""
        dir_path = os.path.dirname(self._path) or "."
        os.makedirs(dir_path, exist_ok=True)
        data = {
            "topics": self._topics,
            "last_seen": self._last_seen,
            "closed": sorted(self._closed),
            "last_update_id": self._last_update_id,
        }
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.rename(tmp_path, self._path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def register(self, tmux_target: str, topic_id: int):
        """Add or update a tmux_target → topic_id mapping."""
        self._topics[tmux_target] = topic_id
        self._save()

    def get_topic_id(self, tmux_target: str) -> int | None:
        """Get the topic_id for a tmux_target, or None if not registered."""
        return self._topics.get(tmux_target)

    def get_tmux_target(self, topic_id: int) -> str | None:
        """Reverse lookup: get tmux_target for a given topic_id."""
        for target, tid in self._topics.items():
            if tid == topic_id:
                return target
        return None

    def remove(self, tmux_target: str):
        """Remove a mapping. No-op if not present."""
        if tmux_target in self._topics:
            del self._topics[tmux_target]
            self._last_seen.pop(tmux_target, None)
            self._save()

    def list_topics(self) -> list[dict]:
        """List all registered mappings with open/closed status."""
        return [
            {
                "tmux_target": target,
                "topic_id": tid,
                "status": "closed" if target in self._closed else "open",
            }
            for target, tid in self._topics.items()
        ]

    def is_closed(self, tmux_target: str) -> bool:
        """Check if a topic is marked as closed."""
        return tmux_target in self._closed

    def set_closed(self, tmux_target: str):
        """Mark a topic as closed."""
        self._closed.add(tmux_target)
        self._save()

    def set_open(self, tmux_target: str):
        """Mark a topic as open (remove from closed set)."""
        if tmux_target in self._closed:
            self._closed.discard(tmux_target)
            self._save()

    def get_last_seen(self, tmux_target: str) -> int:
        """Get the last-seen update_id for a topic. Returns 0 if not tracked."""
        return self._last_seen.get(tmux_target, 0)

    def set_last_seen(self, tmux_target: str, update_id: int):
        """Set the last-seen update_id for a topic."""
        self._last_seen[tmux_target] = update_id
        self._save()

    def get_last_update_id(self) -> int:
        """Get the global last_update_id for the inbound polling loop."""
        return self._last_update_id

    def set_last_update_id(self, update_id: int):
        """Set the global last_update_id for the inbound polling loop."""
        self._last_update_id = update_id
        self._save()

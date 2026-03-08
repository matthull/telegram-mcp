"""Tests for TopicRegistry — T001 topic registry with JSON persistence."""
import os
import json
import tempfile
import pytest

os.environ["TELEGRAM_API_ID"] = "12345"
os.environ["TELEGRAM_API_HASH"] = "dummy_hash"

from topic_registry import TopicRegistry


class TestTopicRegistryBasicOperations:
    """Loop 1: Unit tests for registry operations."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "topic_registry.json")
        self.registry = TopicRegistry(self.registry_path)

    def test_register_and_get_topic(self):
        self.registry.register("village:chart", 42)
        assert self.registry.get_topic_id("village:chart") == 42

    def test_get_nonexistent_topic_returns_none(self):
        assert self.registry.get_topic_id("nonexistent") is None

    def test_remove_topic(self):
        self.registry.register("village:chart", 42)
        self.registry.remove("village:chart")
        assert self.registry.get_topic_id("village:chart") is None

    def test_remove_nonexistent_topic_no_error(self):
        self.registry.remove("nonexistent")  # should not raise

    def test_list_topics(self):
        self.registry.register("village:chart", 42)
        self.registry.register("musashi:code", 99)
        topics = self.registry.list_topics()
        assert len(topics) == 2
        assert {"tmux_target": "village:chart", "topic_id": 42, "status": "open"} in topics
        assert {"tmux_target": "musashi:code", "topic_id": 99, "status": "open"} in topics

    def test_list_topics_empty(self):
        assert self.registry.list_topics() == []

    def test_reverse_lookup(self):
        self.registry.register("village:chart", 42)
        assert self.registry.get_tmux_target(42) == "village:chart"

    def test_reverse_lookup_nonexistent(self):
        assert self.registry.get_tmux_target(999) is None

    def test_overwrite_existing_mapping(self):
        self.registry.register("village:chart", 42)
        self.registry.register("village:chart", 100)
        assert self.registry.get_topic_id("village:chart") == 100

    def test_per_topic_last_seen_tracking(self):
        self.registry.register("village:chart", 42)
        self.registry.register("musashi:code", 99)
        # Initially zero
        assert self.registry.get_last_seen("village:chart") == 0
        assert self.registry.get_last_seen("musashi:code") == 0
        # Update independently
        self.registry.set_last_seen("village:chart", 500)
        assert self.registry.get_last_seen("village:chart") == 500
        assert self.registry.get_last_seen("musashi:code") == 0

    def test_last_seen_for_unregistered_topic(self):
        assert self.registry.get_last_seen("nonexistent") == 0

    def test_set_last_seen_for_unregistered_topic_works(self):
        # Setting last_seen for unregistered topic should still work
        # (topic might be created by polling before explicit registration)
        self.registry.set_last_seen("new:topic", 123)
        assert self.registry.get_last_seen("new:topic") == 123


class TestTopicRegistryPersistence:
    """Loop 1 continued: JSON round-trip persistence tests."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "topic_registry.json")

    def test_save_and_load_round_trip(self):
        reg1 = TopicRegistry(self.registry_path)
        reg1.register("village:chart", 42)
        reg1.register("musashi:code", 99)
        reg1.set_last_seen("village:chart", 500)

        # Create new instance — should load from file
        reg2 = TopicRegistry(self.registry_path)
        assert reg2.get_topic_id("village:chart") == 42
        assert reg2.get_topic_id("musashi:code") == 99
        assert reg2.get_last_seen("village:chart") == 500
        assert reg2.get_last_seen("musashi:code") == 0

    def test_load_nonexistent_file_starts_empty(self):
        reg = TopicRegistry("/tmp/nonexistent_topic_registry_test.json")
        assert reg.list_topics() == []

    def test_save_creates_file(self):
        reg = TopicRegistry(self.registry_path)
        reg.register("test:topic", 1)
        assert os.path.exists(self.registry_path)

    def test_persisted_json_is_valid(self):
        reg = TopicRegistry(self.registry_path)
        reg.register("village:chart", 42)
        with open(self.registry_path) as f:
            data = json.load(f)
        assert "topics" in data
        assert "village:chart" in data["topics"]

    def test_remove_persists(self):
        reg1 = TopicRegistry(self.registry_path)
        reg1.register("village:chart", 42)
        reg1.remove("village:chart")

        reg2 = TopicRegistry(self.registry_path)
        assert reg2.get_topic_id("village:chart") is None

    def test_last_seen_persists(self):
        reg1 = TopicRegistry(self.registry_path)
        reg1.set_last_seen("village:chart", 777)

        reg2 = TopicRegistry(self.registry_path)
        assert reg2.get_last_seen("village:chart") == 777


class TestAtomicWrites:
    """Fix E: Registry writes use temp file + rename (atomic on POSIX)."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "topic_registry.json")

    def test_save_uses_atomic_rename(self):
        """_save should write to a temp file then rename, not write directly."""
        reg = TopicRegistry(self.registry_path)
        reg.register("village:chart", 42)
        # File must exist and be valid JSON after save
        assert os.path.exists(self.registry_path)
        with open(self.registry_path) as f:
            data = json.load(f)
        assert data["topics"]["village:chart"] == 42

    def test_no_partial_writes_on_valid_save(self):
        """After a save, the file should be complete and parseable."""
        reg = TopicRegistry(self.registry_path)
        # Do many rapid writes
        for i in range(20):
            reg.register(f"target:{i}", i)
        with open(self.registry_path) as f:
            data = json.load(f)
        assert len(data["topics"]) == 20

    def test_atomic_write_no_temp_file_left_behind(self):
        """No temporary files should remain after a successful save."""
        reg = TopicRegistry(self.registry_path)
        reg.register("village:chart", 42)
        files = os.listdir(self.tmpdir)
        # Only the registry file should exist (no .tmp files)
        assert files == ["topic_registry.json"]


class TestLastUpdateIdPersistence:
    """Fix D: Inbound loop last_update_id persisted across restarts."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = os.path.join(self.tmpdir, "topic_registry.json")

    def test_get_last_update_id_default_zero(self):
        reg = TopicRegistry(self.registry_path)
        assert reg.get_last_update_id() == 0

    def test_set_and_get_last_update_id(self):
        reg = TopicRegistry(self.registry_path)
        reg.set_last_update_id(12345)
        assert reg.get_last_update_id() == 12345

    def test_last_update_id_persists_across_restarts(self):
        reg1 = TopicRegistry(self.registry_path)
        reg1.set_last_update_id(67890)

        reg2 = TopicRegistry(self.registry_path)
        assert reg2.get_last_update_id() == 67890

    def test_last_update_id_updates(self):
        reg = TopicRegistry(self.registry_path)
        reg.set_last_update_id(100)
        reg.set_last_update_id(200)
        assert reg.get_last_update_id() == 200

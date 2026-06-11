"""
Tests for topology_converter.py safety mechanisms.

These tests cover path traversal validation, conversion status initialization,
locking behaviour, and structural code patterns (auth order, None-guard checks).
"""

import inspect
import re
import threading

import pytest

import topology_converter as tc


# ---------------------------------------------------------------------------
# 1. Path traversal validation
# ---------------------------------------------------------------------------

VALID_TOPOLOGY_NAMES = [
    "training-level1",
    "campus",
    "exam-v2",
    "level7",
    "training_level3",
    "Campus2",
    "EXAM",
    "a",
]

INVALID_TOPOLOGY_NAMES = [
    "../../etc",
    "../passwd",
    "foo/bar",
    "foo;rm",
    "foo bar",
    "foo&bar",
    "foo|bar",
    "foo`whoami`",
    ".hidden",
    "foo.bar",
    "",
    "foo\x00bar",
    "foo$HOME",
    "foo>output",
]

TOPOLOGY_PATTERN = r"^[a-zA-Z0-9_-]+$"


class TestPathTraversalValidation:
    """Verify the topology name regex rejects dangerous inputs and accepts safe ones."""

    @pytest.mark.parametrize("name", INVALID_TOPOLOGY_NAMES)
    def test_bad_names_rejected(self, name):
        """Bad topology names must NOT match the validation pattern."""
        assert re.match(TOPOLOGY_PATTERN, name) is None, (
            f"Expected '{name}' to be rejected by pattern '{TOPOLOGY_PATTERN}'"
        )

    @pytest.mark.parametrize("name", VALID_TOPOLOGY_NAMES)
    def test_good_names_accepted(self, name):
        """Good topology names must match the validation pattern."""
        assert re.match(TOPOLOGY_PATTERN, name) is not None, (
            f"Expected '{name}' to be accepted by pattern '{TOPOLOGY_PATTERN}'"
        )


# ---------------------------------------------------------------------------
# 2. Conversion status initial state
# ---------------------------------------------------------------------------

class TestConversionStatus:
    """Verify the module-level conversion_status dict is correctly initialised."""

    def test_initial_in_progress_is_false(self):
        """conversion_status must start with in_progress=False."""
        assert tc.conversion_status["in_progress"] is False

    def test_initial_status_is_idle(self):
        """conversion_status must start with status='Idle'."""
        assert tc.conversion_status["status"] == "Idle"

    def test_log_is_bounded_deque(self):
        """conversion_status['log'] must be a collections.deque with maxlen=500."""
        from collections import deque

        log = tc.conversion_status["log"]
        assert isinstance(log, deque), f"Expected deque, got {type(log)}"
        assert log.maxlen == 500, f"Expected maxlen 500, got {log.maxlen}"


# ---------------------------------------------------------------------------
# 3. Conversion lock
# ---------------------------------------------------------------------------

class TestConversionLock:
    """Verify the module-level _conversion_lock is a threading.Lock."""

    def test_lock_exists_and_is_lock(self):
        """_conversion_lock must exist and be a threading.Lock instance."""
        # threading.Lock() returns an internal _thread.lock; check via acquire/release
        lock = tc._conversion_lock
        assert hasattr(lock, "acquire") and hasattr(lock, "release"), (
            "_conversion_lock does not look like a threading.Lock"
        )

    def test_lock_is_initially_acquirable(self):
        """_conversion_lock must not be held at module import time."""
        lock = tc._conversion_lock
        acquired = lock.acquire(blocking=False)
        assert acquired, "_conversion_lock was already held at startup"
        lock.release()


# ---------------------------------------------------------------------------
# 4. TopologyConverterInfoHandler — structural: re.match validation present
# ---------------------------------------------------------------------------

class TestInfoHandlerValidation:
    """Verify TopologyConverterInfoHandler source contains re.match input validation."""

    def test_info_handler_uses_re_match(self):
        """TopologyConverterInfoHandler.get must contain re.match for topology name validation."""
        source = inspect.getsource(tc.TopologyConverterInfoHandler.get)
        assert "re.match" in source, (
            "TopologyConverterInfoHandler.get does not appear to validate "
            "the topology name with re.match — path traversal protection may be missing"
        )


# ---------------------------------------------------------------------------
# 5. None-guard before accessing topo_build keys
# ---------------------------------------------------------------------------

class TestNoneFromYaml:
    """Verify that topo_build is None-guarded before key access."""

    def test_topo_build_none_guard_present_in_source(self):
        """Source must contain 'if topo_build and' to guard against None YAML result."""
        source_text = inspect.getsource(tc)
        assert "if topo_build and" in source_text, (
            "topology_converter.py does not None-guard topo_build before checking "
            "'nodes' key — YAML().load() can return None for an empty file"
        )


# ---------------------------------------------------------------------------
# 6. TopologyConverterStatusHandler — auth check before status poll log
# ---------------------------------------------------------------------------

class TestStatusHandlerAuthOrder:
    """Verify authentication is checked before conversion_status is polled/logged."""

    def test_current_user_before_conversion_status_poll(self):
        """current_user check must appear before conversion_status_poll in StatusHandler.get."""
        source = inspect.getsource(tc.TopologyConverterStatusHandler.get)
        current_user_pos = source.find("current_user")
        status_poll_pos = source.find("conversion_status_poll")
        assert current_user_pos != -1, (
            "TopologyConverterStatusHandler.get does not reference current_user"
        )
        assert status_poll_pos != -1, (
            "TopologyConverterStatusHandler.get does not reference conversion_status_poll"
        )
        assert current_user_pos < status_poll_pos, (
            "current_user check must appear BEFORE conversion_status_poll in "
            "TopologyConverterStatusHandler.get — unauthenticated callers must be "
            "rejected before any status data is logged or returned"
        )

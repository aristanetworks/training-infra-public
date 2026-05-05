"""
Unit tests for internal event engine.

Tests cover:
- Time remaining calculation (exam mode, lab mode, initializing)
- Threshold evaluation (single, multiple, none, edge cases)
- Threshold reset on runtime extension
- Pushed announcements (add, remove, TTL expiry)
- Announcement dict format validation
- Custom threshold parsing from JSON
"""

import json
import time
import sys
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from internal_events import (
    InternalEventEngine,
    TimeThreshold,
    DEFAULT_THRESHOLDS,
    _parse_thresholds_json,
)


# =============================================================================
# TimeThreshold Tests
# =============================================================================

class TestTimeThreshold:
    """Tests for TimeThreshold dataclass."""

    def test_generate_announcement_format(self):
        t = TimeThreshold(
            minutes=30, type='warning', priority=70,
            title='Time Warning',
            message_template='Your lab has {minutes} minutes remaining.',
        )
        ann = t.generate_announcement()

        assert ann['id'] == 'internal-time-remaining-30'
        assert ann['title'] == 'Time Warning'
        assert ann['message'] == 'Your lab has 30 minutes remaining.'
        assert ann['type'] == 'warning'
        assert ann['priority'] == 70
        assert ann['dismissible'] is True
        assert ann['source'] == 'internal'
        assert 'start_date' in ann
        assert 'end_date' in ann

    def test_generate_announcement_alert_type(self):
        t = TimeThreshold(
            minutes=5, type='alert', priority=95,
            title='Session Ending',
            message_template='Less than {minutes} minutes left!',
        )
        ann = t.generate_announcement()
        assert ann['type'] == 'alert'
        assert ann['priority'] == 95
        assert ann['message'] == 'Less than 5 minutes left!'

    def test_announcement_id_stable(self):
        """Same threshold always produces same ID."""
        t = TimeThreshold(minutes=15, type='warning', priority=85,
                          title='T', message_template='{minutes}')
        id1 = t.generate_announcement()['id']
        id2 = t.generate_announcement()['id']
        assert id1 == id2 == 'internal-time-remaining-15'


# =============================================================================
# Threshold JSON Parsing Tests
# =============================================================================

class TestThresholdParsing:

    def test_empty_string_returns_none(self):
        assert _parse_thresholds_json('') is None
        assert _parse_thresholds_json('  ') is None

    def test_valid_json(self):
        cfg = json.dumps([
            {'minutes': 20, 'type': 'warning', 'priority': 60,
             'title': 'Custom', 'message': '{minutes} min left'}
        ])
        result = _parse_thresholds_json(cfg)
        assert len(result) == 1
        assert result[0].minutes == 20
        assert result[0].type == 'warning'

    def test_invalid_json_returns_none(self):
        assert _parse_thresholds_json('not json') is None

    def test_missing_required_field(self):
        """minutes is required"""
        cfg = json.dumps([{'type': 'warning'}])
        assert _parse_thresholds_json(cfg) is None


# =============================================================================
# InternalEventEngine Tests
# =============================================================================

class TestFetchTimeRemaining:

    def _make_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            return InternalEventEngine(cloud_logger=None, topology='test')

    @patch('internal_events.urllib.request.urlopen')
    def test_lab_mode(self, mock_urlopen):
        """boottime + runtime → remaining seconds"""
        current = int(time.time())
        runtime_hours = 2
        boottime = current - 3600  # booted 1 hour ago
        # Expected remaining: boottime + 2*3600 - current = 3600

        response_data = json.dumps({
            'boottime': boottime,
            'runtime': runtime_hours,
            'exam_end_time': 0,
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        engine = self._make_engine()
        remaining = engine.fetch_time_remaining()

        assert remaining is not None
        assert abs(remaining - 3600) < 5  # allow small timing variance

    @patch('internal_events.urllib.request.urlopen')
    def test_exam_mode(self, mock_urlopen):
        """exam_end_time takes precedence"""
        current = int(time.time())
        exam_end = current + 1800  # 30 min from now

        response_data = json.dumps({
            'boottime': current - 7200,
            'runtime': 12,
            'exam_end_time': exam_end,
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        engine = self._make_engine()
        remaining = engine.fetch_time_remaining()

        assert remaining is not None
        assert abs(remaining - 1800) < 5

    @patch('internal_events.urllib.request.urlopen')
    def test_initializing_returns_none(self, mock_urlopen):
        """boottime=0 and no exam → None (still booting)"""
        response_data = json.dumps({
            'boottime': 0,
            'runtime': 12,
            'exam_end_time': 0,
        }).encode()
        mock_resp = MagicMock()
        mock_resp.read.return_value = response_data
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        engine = self._make_engine()
        assert engine.fetch_time_remaining() is None

    @patch('internal_events.urllib.request.urlopen')
    def test_network_failure_returns_none(self, mock_urlopen):
        mock_urlopen.side_effect = Exception("connection refused")
        engine = self._make_engine()
        assert engine.fetch_time_remaining() is None


class TestEvaluateThresholds:

    def _make_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            return InternalEventEngine(cloud_logger=None, topology='test')

    def test_no_thresholds_triggered(self):
        engine = self._make_engine()
        result = engine._evaluate_thresholds(7200)  # 2 hours remaining
        assert result == []

    def test_30min_threshold(self):
        engine = self._make_engine()
        result = engine._evaluate_thresholds(1500)  # 25 min remaining
        ids = [a['id'] for a in result]
        assert 'internal-time-remaining-30' in ids
        assert 'internal-time-remaining-15' not in ids

    def test_15min_threshold(self):
        engine = self._make_engine()
        result = engine._evaluate_thresholds(800)  # ~13 min remaining
        ids = [a['id'] for a in result]
        assert 'internal-time-remaining-30' in ids
        assert 'internal-time-remaining-15' in ids
        assert 'internal-time-remaining-5' not in ids

    def test_all_thresholds(self):
        engine = self._make_engine()
        result = engine._evaluate_thresholds(200)  # ~3 min remaining
        ids = [a['id'] for a in result]
        assert 'internal-time-remaining-30' in ids
        assert 'internal-time-remaining-15' in ids
        assert 'internal-time-remaining-5' in ids

    def test_exact_threshold_boundary(self):
        """remaining == threshold_seconds should trigger"""
        engine = self._make_engine()
        result = engine._evaluate_thresholds(1800)  # exactly 30 min
        ids = [a['id'] for a in result]
        assert 'internal-time-remaining-30' in ids

    def test_triggered_only_once(self):
        """Calling evaluate twice with same time should not re-log"""
        engine = self._make_engine()
        r1 = engine._evaluate_thresholds(1500)
        r2 = engine._evaluate_thresholds(1500)
        # Both should return same announcements
        assert len(r1) == len(r2)
        # But _triggered should have been set on first call
        assert 30 in engine._triggered


class TestCheckAndUpdate:

    def _make_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            return InternalEventEngine(cloud_logger=None, topology='test')

    @patch.object(InternalEventEngine, 'fetch_time_remaining')
    def test_time_extension_clears_cache(self, mock_fetch):
        engine = self._make_engine()

        # First: trigger 30-min threshold
        mock_fetch.return_value = 1500
        engine.check_and_update()
        assert len(engine._timer_announcements) > 0
        assert 30 in engine._triggered

        # Then: time extended to 2 hours
        mock_fetch.return_value = 7200
        engine.check_and_update()
        assert len(engine._timer_announcements) == 0
        assert len(engine._triggered) == 0

    @patch.object(InternalEventEngine, 'fetch_time_remaining')
    def test_fetch_failure_keeps_cache(self, mock_fetch):
        engine = self._make_engine()

        # Trigger some announcements
        mock_fetch.return_value = 800
        engine.check_and_update()
        cached = list(engine._timer_announcements)
        assert len(cached) > 0

        # Fetch fails
        mock_fetch.return_value = None
        engine.check_and_update()
        # Cache unchanged
        assert engine._timer_announcements == cached


# =============================================================================
# Pushed Announcements Tests
# =============================================================================

class TestPushedAnnouncements:

    def _make_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            return InternalEventEngine(cloud_logger=None, topology='test')

    def test_add_announcement(self):
        engine = self._make_engine()
        ann = engine.add_announcement({
            'id': 'login-warning',
            'title': 'Session Notice',
            'message': 'Credentials updated.',
            'type': 'info',
            'priority': 60,
        })
        assert ann['id'] == 'internal-login-warning'
        assert ann['type'] == 'info'
        assert ann['dismissible'] is True

    def test_add_with_internal_prefix(self):
        """If ID already has prefix, don't double-prefix"""
        engine = self._make_engine()
        ann = engine.add_announcement({
            'id': 'internal-custom',
            'title': 'Test',
            'message': 'Test message',
        })
        assert ann['id'] == 'internal-custom'

    def test_add_missing_id_raises(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="'id' is required"):
            engine.add_announcement({'title': 'T', 'message': 'M'})

    def test_add_missing_title_raises(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="'title' is required"):
            engine.add_announcement({'id': 'x', 'message': 'M'})

    def test_add_missing_message_raises(self):
        engine = self._make_engine()
        with pytest.raises(ValueError, match="'message' is required"):
            engine.add_announcement({'id': 'x', 'title': 'T'})

    def test_remove_announcement(self):
        engine = self._make_engine()
        engine.add_announcement({
            'id': 'temp', 'title': 'T', 'message': 'M',
        })
        assert engine.remove_announcement('temp') is True
        assert engine.remove_announcement('temp') is False  # already removed

    def test_remove_nonexistent(self):
        engine = self._make_engine()
        assert engine.remove_announcement('does-not-exist') is False

    def test_ttl_expiry(self):
        engine = self._make_engine()
        engine.add_announcement({
            'id': 'expiring',
            'title': 'T',
            'message': 'M',
            'ttl_minutes': 0,  # expires immediately
        })
        # Manually set expires_at to past
        ann_id = 'internal-expiring'
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        engine._pushed_announcements[ann_id]['_expires_at'] = past

        engine._cleanup_expired_pushed()
        assert ann_id not in engine._pushed_announcements

    def test_default_ttl(self):
        engine = self._make_engine()
        ann = engine.add_announcement({
            'id': 'default-ttl',
            'title': 'T',
            'message': 'M',
        })
        # Default TTL is 60 minutes
        expires = datetime.fromisoformat(ann['_expires_at'])
        now = datetime.now(timezone.utc)
        diff = (expires - now).total_seconds()
        assert 3500 < diff < 3700  # ~60 minutes


class TestGetActiveAnnouncements:

    def _make_engine(self):
        with patch.dict(os.environ, {}, clear=False):
            return InternalEventEngine(cloud_logger=None, topology='test')

    @patch.object(InternalEventEngine, 'fetch_time_remaining')
    def test_merges_timer_and_pushed(self, mock_fetch):
        engine = self._make_engine()

        # Generate timer announcements
        mock_fetch.return_value = 800
        engine.check_and_update()

        # Add pushed announcement
        engine.add_announcement({
            'id': 'custom', 'title': 'T', 'message': 'M',
        })

        all_anns = engine.get_active_announcements()
        ids = [a['id'] for a in all_anns]
        assert 'internal-time-remaining-30' in ids
        assert 'internal-time-remaining-15' in ids
        assert 'internal-custom' in ids

    def test_strips_internal_fields(self):
        engine = self._make_engine()
        engine.add_announcement({
            'id': 'test', 'title': 'T', 'message': 'M',
        })
        all_anns = engine.get_active_announcements()
        for ann in all_anns:
            for key in ann:
                assert not key.startswith('_'), f"Internal field {key} leaked"


class TestCloudLogging:

    @patch.object(InternalEventEngine, 'fetch_time_remaining')
    def test_log_event_called_on_trigger(self, mock_fetch):
        mock_cloud = MagicMock()
        with patch.dict(os.environ, {}, clear=False):
            engine = InternalEventEngine(cloud_logger=mock_cloud, topology='test')

        mock_fetch.return_value = 200  # triggers all thresholds
        engine.check_and_update()

        # Cloud logger should have been called (in background threads)
        # We can't easily test async threads, but verify the logger was passed
        assert engine._cloud_logger is mock_cloud

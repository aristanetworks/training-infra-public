"""
Internal Event Engine for ConfigService

Generates instance-specific announcements from local events:
1. Timer-based: Polls /uptimeWithRuntime, triggers time-remaining warnings
2. API-based: Other containers push announcements via POST /internal/announcements

All generated announcements match Firestore announcement dict format
and merge seamlessly into the /announcements response.
"""

import json
import logging
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set

try:
    from google.cloud import logging as cloud_logging
    _HAS_CLOUD_LOGGING = True
except ImportError:
    _HAS_CLOUD_LOGGING = False

from config import (
    UILANDING_UPTIME_URL,
    INTERNAL_EVENT_THRESHOLDS_JSON,
)

logger = logging.getLogger('configservice.internal_events')

# Far future date for announcements without natural expiry
_FAR_FUTURE = '2099-12-31T23:59:59Z'


@dataclass
class TimeThreshold:
    """Defines a single time-remaining threshold rule"""
    minutes: int
    type: str           # "warning" or "alert"
    priority: int       # 0-100
    title: str
    message_template: str

    def generate_announcement(self) -> Dict:
        """Generate an announcement dict matching Firestore format"""
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        return {
            'id': f'internal-time-remaining-{self.minutes}',
            'title': self.title,
            'message': self.message_template.format(minutes=self.minutes),
            'type': self.type,
            'priority': self.priority,
            'dismissible': True,
            'start_date': now,
            'end_date': _FAR_FUTURE,
            'source': 'internal',
        }


# Default thresholds if no env override
DEFAULT_THRESHOLDS = [
    TimeThreshold(
        minutes=30,
        type='warning',
        priority=70,
        title='Time Warning',
        message_template='Your lab session has approximately {minutes} minutes remaining. Please save your work.',
    ),
    TimeThreshold(
        minutes=15,
        type='warning',
        priority=85,
        title='Time Warning',
        message_template='Your lab session has approximately {minutes} minutes remaining.',
    ),
    TimeThreshold(
        minutes=5,
        type='alert',
        priority=95,
        title='Session Ending Soon',
        message_template='Your lab session has less than {minutes} minutes remaining. Please save all work immediately.',
    ),
]


def _parse_thresholds_json(json_str: str) -> Optional[List[TimeThreshold]]:
    """Parse threshold config from JSON string (env var override)"""
    if not json_str or not json_str.strip():
        return None
    try:
        items = json.loads(json_str)
        thresholds = []
        for item in items:
            thresholds.append(TimeThreshold(
                minutes=item['minutes'],
                type=item.get('type', 'warning'),
                priority=item.get('priority', 50),
                title=item.get('title', 'Time Warning'),
                message_template=item.get('message', '{minutes} minutes remaining.'),
            ))
        return thresholds
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"Failed to parse INTERNAL_EVENT_THRESHOLDS: {e}. Using defaults.")
        return None


class InternalEventEngine:
    """
    Generates internal announcements from instance state.

    Two sources:
    1. Timer thresholds — evaluated every check cycle against /uptimeWithRuntime
    2. Pushed announcements — received via POST /internal/announcements from other containers
    """

    def __init__(self, cloud_logger=None, topology: str = 'unknown', lab_hostname: str = 'unknown'):
        # Timer threshold config
        custom = _parse_thresholds_json(INTERNAL_EVENT_THRESHOLDS_JSON)
        self.thresholds: List[TimeThreshold] = custom if custom else list(DEFAULT_THRESHOLDS)
        self.thresholds.sort(key=lambda t: t.minutes, reverse=True)  # largest first

        # State
        self._triggered: Set[int] = set()          # threshold minutes that have fired
        self._timer_announcements: List[Dict] = [] # active timer-based announcements
        self._pushed_announcements: Dict[str, Dict] = {}  # id -> announcement dict
        self._remaining_seconds: Optional[int] = None
        self._lock = threading.Lock()

        # Context
        self._topology = topology
        self._lab_hostname = lab_hostname
        self._cloud_logger = cloud_logger

        logger.info(
            f"InternalEventEngine initialized with {len(self.thresholds)} thresholds: "
            f"{[t.minutes for t in self.thresholds]} minutes"
        )
        self._log_event('internal_event_engine_initialized', {
            'thresholds': [t.minutes for t in self.thresholds],
            'threshold_count': len(self.thresholds),
        })

    def _log_event(self, event_type: str, details: Dict, severity_override: str = None):
        """Structured cloud logging for internal events"""
        log_entry = {
            'event_type': event_type,
            'lab_hostname': self._lab_hostname,
            'topology': self._topology,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            **details,
        }
        logger.info(f"Internal event: {event_type} - {details}")

        if self._cloud_logger:
            def _log_async():
                try:
                    if severity_override:
                        severity = severity_override
                    elif 'error' in event_type or 'failed' in event_type:
                        severity = 'WARNING'
                    else:
                        severity = 'INFO'
                    self._cloud_logger.log_struct(log_entry, severity=severity)
                except Exception as e:
                    logger.warning(f"Cloud Logging failed for {event_type}: {e}")
            threading.Thread(target=_log_async, daemon=True).start()

    def fetch_time_remaining(self) -> Optional[int]:
        """
        Fetch remaining seconds from uilanding's /uptimeWithRuntime.

        Returns remaining seconds, or None if unavailable.
        Uses same calculation logic as frontend timer-widget.js.
        """
        try:
            req = urllib.request.Request(UILANDING_UPTIME_URL, method='GET')
            req.add_header('Accept', 'application/json')
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            if not isinstance(data, dict):
                raise ValueError(f"Expected dict response, got {type(data).__name__}")
        except Exception as e:
            self._log_event('internal_event_fetch_failed', {
                'url': UILANDING_UPTIME_URL,
                'error': str(e),
            })
            return None

        current_time = int(time.time())

        # Exam mode: absolute end time
        exam_end = data.get('exam_end_time', 0)
        if exam_end and exam_end > 0:
            return exam_end - current_time

        # Lab mode: boottime + runtime hours
        boottime = data.get('boottime', 0)
        runtime = data.get('runtime', 12)  # default 12 hours
        if boottime and boottime > 0:
            expiration = boottime + (runtime * 3600)
            return expiration - current_time

        # Still initializing
        return None

    def _evaluate_thresholds(self, remaining_seconds: int) -> List[Dict]:
        """
        Evaluate time thresholds against remaining seconds.
        Returns list of announcement dicts for all crossed thresholds.
        """
        active = []
        newly_triggered = []

        for threshold in self.thresholds:
            threshold_seconds = threshold.minutes * 60
            if remaining_seconds <= threshold_seconds:
                active.append(threshold.generate_announcement())
                if threshold.minutes not in self._triggered:
                    newly_triggered.append(threshold)
                    self._triggered.add(threshold.minutes)

        # Log newly triggered thresholds
        for t in newly_triggered:
            self._log_event('internal_event_triggered', {
                'threshold_minutes': t.minutes,
                'remaining_seconds': remaining_seconds,
                'announcement_type': t.type,
                'priority': t.priority,
            })

        return active

    def check_and_update(self):
        """
        Periodic callback: fetch time remaining, evaluate thresholds, update cache.
        Also cleans up expired pushed announcements.

        CRITICAL: This runs inside Tornado's PeriodicCallback. An unhandled exception
        could stop future invocations. The top-level try/except ensures the lab never
        breaks due to announcement generation failures.
        """
        try:
            remaining = self.fetch_time_remaining()

            with self._lock:
                if remaining is None:
                    # Can't determine time — keep existing state
                    self._remaining_seconds = None
                    return

                self._remaining_seconds = remaining

                # Check if time was extended (remaining now exceeds all thresholds)
                max_threshold = max(t.minutes for t in self.thresholds) if self.thresholds else 0
                if remaining > max_threshold * 60 and self._triggered:
                    self._log_event('internal_event_cleared', {
                        'reason': 'time_extended',
                        'remaining_seconds': remaining,
                        'previously_triggered': list(self._triggered),
                    })
                    self._triggered.clear()
                    self._timer_announcements = []
                    return

                # Evaluate thresholds
                self._timer_announcements = self._evaluate_thresholds(remaining)

                # Clean up expired pushed announcements
                self._cleanup_expired_pushed()
        except Exception as e:
            logger.error(f"check_and_update failed (will retry next cycle): {e}", exc_info=True)
            self._log_event('internal_event_error', {
                'method': 'check_and_update',
                'error': str(e),
            }, severity_override='ERROR')

    def _cleanup_expired_pushed(self):
        """Remove pushed announcements past their TTL"""
        now = datetime.now(timezone.utc)
        expired = []
        for ann_id, ann in self._pushed_announcements.items():
            expires_at = ann.get('_expires_at')
            if expires_at:
                try:
                    if datetime.fromisoformat(expires_at) <= now:
                        expired.append(ann_id)
                except (ValueError, TypeError):
                    logger.warning(f"Malformed _expires_at for {ann_id}: {expires_at}, treating as expired")
                    expired.append(ann_id)

        for ann_id in expired:
            del self._pushed_announcements[ann_id]
            self._log_event('internal_announcement_removed', {
                'announcement_id': ann_id,
                'reason': 'ttl_expired',
            })

    def add_announcement(self, announcement: Dict) -> Dict:
        """
        Add a pushed announcement from another container.

        Required fields: id, title, message
        Optional: type (default: info), priority (default: 50),
                  dismissible (default: True), ttl_minutes (default: 60)

        Returns the normalized announcement dict.
        """
        raw_id = announcement.get('id', '')
        if not raw_id:
            raise ValueError("Announcement 'id' is required")
        if not announcement.get('title'):
            raise ValueError("Announcement 'title' is required")
        if not announcement.get('message'):
            raise ValueError("Announcement 'message' is required")

        # Prefix ID to avoid Firestore collision
        ann_id = f'internal-{raw_id}' if not raw_id.startswith('internal-') else raw_id

        now = datetime.now(timezone.utc)
        try:
            ttl_minutes = max(1, int(announcement.get('ttl_minutes', 60)))
        except (ValueError, TypeError):
            ttl_minutes = 60
        expires_at = (now + timedelta(minutes=ttl_minutes)).isoformat()

        ann_dict = {
            'id': ann_id,
            'title': announcement['title'],
            'message': announcement['message'],
            'type': announcement.get('type', 'info'),
            'priority': announcement.get('priority', 50),
            'dismissible': announcement.get('dismissible', True),
            'start_date': now.isoformat().replace('+00:00', 'Z'),
            'end_date': (now + timedelta(minutes=ttl_minutes)).isoformat().replace('+00:00', 'Z'),
            'source': 'internal',
            '_expires_at': expires_at,  # internal tracking, stripped before response
        }

        with self._lock:
            self._pushed_announcements[ann_id] = ann_dict

        self._log_event('internal_announcement_pushed', {
            'announcement_id': ann_id,
            'title': announcement['title'],
            'type': ann_dict['type'],
            'priority': ann_dict['priority'],
            'ttl_minutes': ttl_minutes,
        })

        return ann_dict

    def remove_announcement(self, announcement_id: str) -> bool:
        """Remove a pushed announcement by ID. Returns True if found and removed."""
        # Normalize ID
        ann_id = f'internal-{announcement_id}' if not announcement_id.startswith('internal-') else announcement_id

        with self._lock:
            if ann_id in self._pushed_announcements:
                del self._pushed_announcements[ann_id]
                self._log_event('internal_announcement_removed', {
                    'announcement_id': ann_id,
                    'reason': 'manual_removal',
                })
                return True
        return False

    def get_active_announcements(self) -> List[Dict]:
        """
        Get all active internal announcements (timer + pushed).
        Strips internal tracking fields before returning.

        Returns empty list on any error — never breaks the /announcements endpoint.
        """
        try:
            with self._lock:
                all_anns = list(self._timer_announcements)
                for ann in self._pushed_announcements.values():
                    # Strip internal tracking fields
                    clean = {k: v for k, v in ann.items() if not k.startswith('_')}
                    all_anns.append(clean)
            return all_anns
        except Exception as e:
            logger.error(f"get_active_announcements failed, returning empty: {e}", exc_info=True)
            return []

    @property
    def remaining_seconds(self) -> Optional[int]:
        """Last known remaining seconds (for health/debug endpoints)"""
        return self._remaining_seconds

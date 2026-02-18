"""
Announcement data model and validation
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, time
from typing import Dict, List, Optional
from enum import Enum
from zoneinfo import ZoneInfo

logger = logging.getLogger('configservice')


class AnnouncementType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"
    SUCCESS = "success"


@dataclass
class Announcement:
    """Represents an announcement with time-based activation and optional recurring daily window"""

    id: str
    title: str
    message: str
    type: AnnouncementType
    priority: int
    dismissible: bool
    start_date: datetime
    end_date: datetime
    recurring: bool = False
    active_time_start: Optional[str] = None  # HH:MM format
    active_time_end: Optional[str] = None    # HH:MM format
    ann_timezone: str = "America/New_York"    # IANA timezone
    audience: str = "all"                     # "all", "arista", or "external"

    @classmethod
    def from_dict(cls, data: Dict) -> Optional['Announcement']:
        """
        Create Announcement from Firestore document data.

        Args:
            data: Dictionary with announcement fields

        Returns:
            Announcement instance or None if parsing fails
        """
        try:
            # Parse dates - handle both Z suffix and +00:00 format
            start_str = data['start_date']
            end_str = data['end_date']

            if start_str.endswith('Z'):
                start_str = start_str[:-1] + '+00:00'
            if end_str.endswith('Z'):
                end_str = end_str[:-1] + '+00:00'

            return cls(
                id=data['id'],
                title=data['title'],
                message=data['message'],
                type=AnnouncementType(data.get('type', 'info')),
                priority=int(data.get('priority', 50)),
                dismissible=bool(data.get('dismissible', True)),
                start_date=datetime.fromisoformat(start_str),
                end_date=datetime.fromisoformat(end_str),
                recurring=bool(data.get('recurring', False)),
                active_time_start=data.get('active_time_start'),
                active_time_end=data.get('active_time_end'),
                ann_timezone=data.get('timezone', 'America/New_York'),
                audience=data.get('audience', 'all')
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse announcement: {e}, data: {data}")
            return None

    def _is_in_daily_window(self, now: datetime) -> bool:
        """
        Check if the current time falls within the daily active window.
        Supports overnight windows (e.g., 20:00 - 08:00).

        Args:
            now: Current time in UTC

        Returns:
            True if within the daily active window
        """
        if not self.active_time_start or not self.active_time_end:
            return True

        try:
            tz = ZoneInfo(self.ann_timezone)
        except (KeyError, Exception):
            logger.warning(f"Invalid timezone '{self.ann_timezone}', falling back to America/New_York")
            tz = ZoneInfo("America/New_York")

        # Convert UTC now to the announcement's timezone
        local_now = now.astimezone(tz)
        current_time = local_now.time()

        # Parse start and end times
        start_parts = self.active_time_start.split(':')
        end_parts = self.active_time_end.split(':')
        start_time = time(int(start_parts[0]), int(start_parts[1]))
        end_time = time(int(end_parts[0]), int(end_parts[1]))

        if start_time <= end_time:
            # Same-day window (e.g., 09:00 - 17:00)
            return start_time <= current_time <= end_time
        else:
            # Overnight window (e.g., 20:00 - 08:00)
            return current_time >= start_time or current_time <= end_time

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """
        Check if announcement is currently active based on dates and optional daily window.

        Args:
            now: Current time (defaults to UTC now)

        Returns:
            True if announcement is within active window
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # First check the overall date range
        if not (self.start_date <= now <= self.end_date):
            return False

        # If recurring, also check the daily time window
        if self.recurring:
            return self._is_in_daily_window(now)

        return True

    def is_for_audience(self, user_is_arista: bool) -> bool:
        """
        Check if announcement should be shown to this user type.

        Args:
            user_is_arista: True if user is an Arista employee

        Returns:
            True if announcement should be shown
        """
        if self.audience == 'all':
            return True
        if self.audience == 'arista':
            return user_is_arista
        if self.audience == 'external':
            return not user_is_arista
        return True

    def to_dict(self) -> Dict:
        """
        Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation of announcement
        """
        result = {
            'id': self.id,
            'title': self.title,
            'message': self.message,
            'type': self.type.value,
            'priority': self.priority,
            'dismissible': self.dismissible,
            'start_date': self.start_date.isoformat().replace('+00:00', 'Z'),
            'end_date': self.end_date.isoformat().replace('+00:00', 'Z')
        }

        if self.recurring:
            result['recurring'] = True
            result['active_time_start'] = self.active_time_start
            result['active_time_end'] = self.active_time_end
            result['timezone'] = self.ann_timezone

        if self.audience != 'all':
            result['audience'] = self.audience

        return result


def filter_active_announcements(announcements: List[Dict], user_is_arista: Optional[bool] = None) -> List[Dict]:
    """
    Filter and sort announcements by active status, audience, and priority.

    Args:
        announcements: List of announcement dictionaries from Firestore
        user_is_arista: If provided, filter by audience (True=Arista, False=external).
                        If None, no audience filtering is applied.

    Returns:
        List of active announcements sorted by priority (highest first)
    """
    now = datetime.now(timezone.utc)
    active = []

    for ann_data in announcements:
        ann = Announcement.from_dict(ann_data)
        if ann and ann.is_active(now):
            # Apply audience filter if user type is known
            if user_is_arista is not None and not ann.is_for_audience(user_is_arista):
                continue
            active.append(ann.to_dict())

    # Sort by priority (highest first)
    active.sort(key=lambda x: x['priority'], reverse=True)
    return active

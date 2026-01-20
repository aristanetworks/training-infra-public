"""
Announcement data model and validation
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional
from enum import Enum

logger = logging.getLogger('configservice')


class AnnouncementType(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"
    SUCCESS = "success"


@dataclass
class Announcement:
    """Represents an announcement with time-based activation"""

    id: str
    title: str
    message: str
    type: AnnouncementType
    priority: int
    dismissible: bool
    start_date: datetime
    end_date: datetime

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
                end_date=datetime.fromisoformat(end_str)
            )
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse announcement: {e}, data: {data}")
            return None

    def is_active(self, now: Optional[datetime] = None) -> bool:
        """
        Check if announcement is currently active based on dates.

        Args:
            now: Current time (defaults to UTC now)

        Returns:
            True if announcement is within active window
        """
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        return self.start_date <= now <= self.end_date

    def to_dict(self) -> Dict:
        """
        Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation of announcement
        """
        return {
            'id': self.id,
            'title': self.title,
            'message': self.message,
            'type': self.type.value,
            'priority': self.priority,
            'dismissible': self.dismissible,
            'start_date': self.start_date.isoformat().replace('+00:00', 'Z'),
            'end_date': self.end_date.isoformat().replace('+00:00', 'Z')
        }


def filter_active_announcements(announcements: List[Dict]) -> List[Dict]:
    """
    Filter and sort announcements by active status and priority.

    Args:
        announcements: List of announcement dictionaries from Firestore

    Returns:
        List of active announcements sorted by priority (highest first)
    """
    now = datetime.now(timezone.utc)
    active = []

    for ann_data in announcements:
        ann = Announcement.from_dict(ann_data)
        if ann and ann.is_active(now):
            active.append(ann.to_dict())

    # Sort by priority (highest first)
    active.sort(key=lambda x: x['priority'], reverse=True)
    return active

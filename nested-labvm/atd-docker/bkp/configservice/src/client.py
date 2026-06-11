"""
Config Service Client - Helper for querying features and announcements from other services.

This client can be copied into other service containers that need to query
the configservice for feature flags or announcements.

Usage:
    from client import ConfigClient

    client = ConfigClient()

    # Check single feature
    if client.is_enabled('dark_mode'):
        enable_dark_mode()

    # Get all features
    all_features = client.get_features()

    # Check if feature was disabled due to missing dependencies
    if client.is_disabled_by_dependency('advanced_capture'):
        print("Enable base_capture first!")

    # Get announcements
    announcements = client.get_announcements()
"""

import json
import logging
import urllib.request
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default service URL (container name on Docker network)
DEFAULT_SERVICE_URL = 'http://atd-configservice:50011'


class ConfigClient:
    """Client for the Config Service (features and announcements)."""

    def __init__(self, service_url: str = DEFAULT_SERVICE_URL, timeout: float = 5.0):
        """
        Initialize config service client.

        Args:
            service_url: Base URL of the config service
            timeout: Request timeout in seconds
        """
        self.service_url = service_url.rstrip('/')
        self.timeout = timeout
        self._features_cache: Optional[Dict] = None
        self._announcements_cache: Optional[Dict] = None

    def _fetch_features(self) -> Dict:
        """Fetch features from service (cached after first call)."""
        if self._features_cache is not None:
            return self._features_cache

        try:
            url = f"{self.service_url}/features"
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                self._features_cache = data
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch features: {e}")
            return {
                'enabled_features': [],
                'requested_features': [],
                'dependency_resolution': None,
                'source': 'error'
            }

    def _fetch_announcements(self) -> Dict:
        """Fetch announcements from service (cached after first call)."""
        if self._announcements_cache is not None:
            return self._announcements_cache

        try:
            url = f"{self.service_url}/announcements"
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                self._announcements_cache = data
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch announcements: {e}")
            return {
                'active_announcements': [],
                'source': 'error'
            }

    # -------------------------------------------------------------------------
    # Feature Methods
    # -------------------------------------------------------------------------

    def get_features(self) -> Dict:
        """
        Get full feature state.

        Returns:
            Dict with keys: enabled_features, requested_features, global_features,
                          topology_features, dependency_resolution, topology,
                          fetched_at, source
        """
        return self._fetch_features()

    def is_enabled(self, feature_id: str) -> bool:
        """
        Check if a specific feature is enabled.

        Note: This checks the final enabled_features list, which accounts
        for dependency resolution. A feature may be requested but not enabled
        if its dependencies are not met.

        Args:
            feature_id: The feature identifier to check

        Returns:
            True if feature is enabled, False otherwise
        """
        data = self._fetch_features()
        return feature_id in data.get('enabled_features', [])

    def is_requested(self, feature_id: str) -> bool:
        """
        Check if a feature was requested (before dependency resolution).

        Args:
            feature_id: The feature identifier to check

        Returns:
            True if feature was requested, False otherwise
        """
        data = self._fetch_features()
        return feature_id in data.get('requested_features', [])

    def is_disabled_by_dependency(self, feature_id: str) -> bool:
        """
        Check if a feature is disabled due to missing dependencies.

        Args:
            feature_id: The feature identifier to check

        Returns:
            True if feature is disabled due to dependency issues
        """
        data = self._fetch_features()
        resolution = data.get('dependency_resolution')
        if not resolution:
            return False
        return feature_id in resolution.get('disabled_missing_deps', {})

    def get_missing_dependencies(self, feature_id: str) -> List[str]:
        """
        Get list of missing dependencies for a feature.

        Args:
            feature_id: The feature identifier to check

        Returns:
            List of missing dependency feature IDs, or empty list
        """
        data = self._fetch_features()
        resolution = data.get('dependency_resolution')
        if not resolution:
            return []
        return resolution.get('disabled_missing_deps', {}).get(feature_id, [])

    def filter_enabled(self, feature_ids: List[str]) -> List[str]:
        """
        Filter a list of feature IDs to only those that are enabled.

        Args:
            feature_ids: List of feature identifiers to check

        Returns:
            List of feature IDs that are enabled
        """
        data = self._fetch_features()
        enabled_set: Set[str] = set(data.get('enabled_features', []))
        return [f for f in feature_ids if f in enabled_set]

    def get_topology(self) -> str:
        """
        Get the current topology name.

        Returns:
            The topology identifier (e.g., 'training-level7-cl')
        """
        data = self._fetch_features()
        return data.get('topology', 'unknown')

    def get_source(self) -> str:
        """
        Get the data source indicator.

        Returns:
            Source string: 'firestore', 'cache', 'empty_fallback', or 'error'
        """
        data = self._fetch_features()
        return data.get('source', 'unknown')

    # -------------------------------------------------------------------------
    # Announcement Methods
    # -------------------------------------------------------------------------

    def get_announcements(self) -> Dict:
        """
        Get full announcement state.

        Returns:
            Dict with keys: active_announcements, global_announcements,
                          topology_announcements, topology, fetched_at, source
        """
        return self._fetch_announcements()

    def get_active_announcements(self) -> List[Dict]:
        """
        Get list of currently active announcements.

        Returns:
            List of announcement dicts, sorted by priority (highest first)
        """
        data = self._fetch_announcements()
        return data.get('active_announcements', [])

    def has_announcements(self) -> bool:
        """
        Check if there are any active announcements.

        Returns:
            True if there are active announcements
        """
        return len(self.get_active_announcements()) > 0

    def get_announcements_by_type(self, ann_type: str) -> List[Dict]:
        """
        Get announcements filtered by type.

        Args:
            ann_type: One of 'info', 'warning', 'alert', 'success'

        Returns:
            List of matching announcements
        """
        announcements = self.get_active_announcements()
        return [a for a in announcements if a.get('type') == ann_type]

    def get_alerts(self) -> List[Dict]:
        """Get only alert-type announcements."""
        return self.get_announcements_by_type('alert')

    def get_warnings(self) -> List[Dict]:
        """Get only warning-type announcements."""
        return self.get_announcements_by_type('warning')

    # -------------------------------------------------------------------------
    # Cache Management
    # -------------------------------------------------------------------------

    def clear_cache(self) -> None:
        """Clear all local caches (force refresh on next call)."""
        self._features_cache = None
        self._announcements_cache = None

    def clear_features_cache(self) -> None:
        """Clear features cache only."""
        self._features_cache = None

    def clear_announcements_cache(self) -> None:
        """Clear announcements cache only."""
        self._announcements_cache = None


# Backward compatibility alias
FeatureFlags = ConfigClient

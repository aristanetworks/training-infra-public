"""
Feature Flags Client - Helper for querying feature flags from other services

Usage:
    from feature_flags_client import FeatureFlags

    ff = FeatureFlags()

    # Check single feature
    if ff.is_enabled('feature-dark-mode'):
        enable_dark_mode()

    # Get all features
    all_features = ff.get_all()

    # Batch check - returns list of enabled features from input
    enabled = ff.filter_enabled(['feature-a', 'feature-b', 'feature-c'])
"""

import json
import logging
import urllib.request
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Default service URL (container name on Docker network)
DEFAULT_SERVICE_URL = 'http://atd-featureflags:50011'


class FeatureFlags:
    """Client for feature flags service"""

    def __init__(self, service_url: str = DEFAULT_SERVICE_URL, timeout: float = 5.0):
        """
        Initialize feature flags client.

        Args:
            service_url: Base URL of the feature flags service
            timeout: Request timeout in seconds
        """
        self.service_url = service_url.rstrip('/')
        self.timeout = timeout
        self._cache: Optional[Dict] = None

    def _fetch(self) -> Dict:
        """Fetch features from service (cached after first call)"""
        if self._cache is not None:
            return self._cache

        try:
            url = f"{self.service_url}/features"
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                data = json.loads(response.read().decode('utf-8'))
                self._cache = data
                return data
        except Exception as e:
            logger.warning(f"Failed to fetch features: {e}")
            return {'enabled_features': [], 'source': 'error'}

    def get_all(self) -> Dict:
        """
        Get full feature state.

        Returns:
            Dict with keys: enabled_features, global_features, topology_features,
                          topology, fetched_at, source
        """
        return self._fetch()

    def is_enabled(self, feature_id: str) -> bool:
        """
        Check if a specific feature is enabled.

        Args:
            feature_id: The feature identifier to check

        Returns:
            True if feature is enabled, False otherwise
        """
        data = self._fetch()
        return feature_id in data.get('enabled_features', [])

    def filter_enabled(self, feature_ids: List[str]) -> List[str]:
        """
        Filter a list of feature IDs to only those that are enabled.

        Args:
            feature_ids: List of feature identifiers to check

        Returns:
            List of feature IDs that are enabled
        """
        data = self._fetch()
        enabled_set: Set[str] = set(data.get('enabled_features', []))
        return [f for f in feature_ids if f in enabled_set]

    def get_topology(self) -> str:
        """
        Get the current topology name.

        Returns:
            The topology identifier (e.g., 'training-level7-cl')
        """
        data = self._fetch()
        return data.get('topology', 'unknown')

    def get_source(self) -> str:
        """
        Get the data source indicator.

        Returns:
            Source string: 'firestore', 'cache', 'empty_fallback', or 'error'
        """
        data = self._fetch()
        return data.get('source', 'unknown')

    def clear_cache(self) -> None:
        """Clear local cache (force refresh on next call)"""
        self._cache = None

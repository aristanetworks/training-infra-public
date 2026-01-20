"""
Firestore clients for feature flags and announcements retrieval
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set

from google.cloud import firestore
from google.api_core import exceptions as gcp_exceptions

from config import (
    FIRESTORE_FEATURES_COLLECTION,
    FIRESTORE_ANNOUNCEMENTS_COLLECTION,
    FIRESTORE_GLOBAL_DOC,
    FIRESTORE_TOPOLOGIES_DOC,
    FIRESTORE_MAX_RETRIES,
    FIRESTORE_RETRY_DELAY_SECONDS,
)
from models.announcement import filter_active_announcements

logger = logging.getLogger('configservice')


class BaseFirestoreClient:
    """Base class for Firestore clients with shared connection logic"""

    _shared_db: Optional[firestore.Client] = None

    @classmethod
    def _get_client(cls) -> firestore.Client:
        """Get or create Firestore client (lazy initialization, shared)"""
        if cls._shared_db is None:
            cls._shared_db = firestore.Client()
        return cls._shared_db


class FeatureFlagClient(BaseFirestoreClient):
    """Client for fetching feature flags from Firestore"""

    def fetch_all_features(self, topology: str) -> Dict:
        """
        Fetch all enabled features (global + topology-specific).

        Args:
            topology: The topology name (e.g., "training-level7-cl")

        Returns:
            Dictionary containing enabled features and metadata

        Raises:
            RuntimeError: If Firestore is unreachable after retries
        """
        last_error = None

        for attempt in range(FIRESTORE_MAX_RETRIES):
            try:
                db = self._get_client()
                collection = db.collection(FIRESTORE_FEATURES_COLLECTION)

                # Fetch both documents
                global_doc = collection.document(FIRESTORE_GLOBAL_DOC).get()
                topo_doc = collection.document(FIRESTORE_TOPOLOGIES_DOC).get()

                # Extract global features
                global_features: List[str] = []
                if global_doc.exists:
                    global_data = global_doc.to_dict()
                    global_features = global_data.get('enabled_features', [])

                # Extract topology-specific features
                topology_features: List[str] = []
                if topo_doc.exists:
                    topo_data = topo_doc.to_dict()
                    topology_features = topo_data.get(topology, [])

                # Merge and deduplicate
                all_features: Set[str] = set(global_features) | set(topology_features)

                return {
                    'enabled_features': sorted(list(all_features)),
                    'global_features': global_features,
                    'topology_features': topology_features,
                    'topology': topology,
                    'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'source': 'firestore'
                }

            except (gcp_exceptions.GoogleAPIError, Exception) as e:
                last_error = e
                logger.warning(
                    f"Firestore features fetch attempt {attempt + 1}/{FIRESTORE_MAX_RETRIES} failed: {e}"
                )
                if attempt < FIRESTORE_MAX_RETRIES - 1:
                    time.sleep(FIRESTORE_RETRY_DELAY_SECONDS)

        raise RuntimeError(
            f"Failed to fetch features after {FIRESTORE_MAX_RETRIES} attempts: {last_error}"
        )


class AnnouncementClient(BaseFirestoreClient):
    """Client for fetching announcements from Firestore"""

    def fetch_all_announcements(self, topology: str) -> Dict:
        """
        Fetch all active announcements (global + topology-specific).
        Filters by current time to only return active announcements.

        Args:
            topology: The topology name (e.g., "training-level7-cl")

        Returns:
            Dictionary containing active announcements and metadata

        Raises:
            RuntimeError: If Firestore is unreachable after retries
        """
        last_error = None

        for attempt in range(FIRESTORE_MAX_RETRIES):
            try:
                db = self._get_client()
                collection = db.collection(FIRESTORE_ANNOUNCEMENTS_COLLECTION)

                # Fetch both documents
                global_doc = collection.document(FIRESTORE_GLOBAL_DOC).get()
                topo_doc = collection.document(FIRESTORE_TOPOLOGIES_DOC).get()

                # Extract global announcements
                global_announcements: List[Dict] = []
                if global_doc.exists:
                    global_data = global_doc.to_dict()
                    global_announcements = global_data.get('announcements', [])

                # Extract topology-specific announcements
                topology_announcements: List[Dict] = []
                if topo_doc.exists:
                    topo_data = topo_doc.to_dict()
                    topology_announcements = topo_data.get(topology, [])

                # Filter to active only
                active_global = filter_active_announcements(global_announcements)
                active_topology = filter_active_announcements(topology_announcements)

                # Combine and re-sort by priority
                all_active = active_global + active_topology
                all_active.sort(key=lambda x: x['priority'], reverse=True)

                # Deduplicate by ID (topology-specific takes precedence for same ID)
                seen_ids: Set[str] = set()
                deduped = []
                # Process topology first so they take precedence
                for ann in active_topology:
                    if ann['id'] not in seen_ids:
                        seen_ids.add(ann['id'])
                        deduped.append(ann)
                for ann in active_global:
                    if ann['id'] not in seen_ids:
                        seen_ids.add(ann['id'])
                        deduped.append(ann)

                # Re-sort deduped by priority
                deduped.sort(key=lambda x: x['priority'], reverse=True)

                return {
                    'active_announcements': deduped,
                    'global_announcements': active_global,
                    'topology_announcements': active_topology,
                    'topology': topology,
                    'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'source': 'firestore'
                }

            except (gcp_exceptions.GoogleAPIError, Exception) as e:
                last_error = e
                logger.warning(
                    f"Firestore announcements fetch attempt {attempt + 1}/{FIRESTORE_MAX_RETRIES} failed: {e}"
                )
                if attempt < FIRESTORE_MAX_RETRIES - 1:
                    time.sleep(FIRESTORE_RETRY_DELAY_SECONDS)

        raise RuntimeError(
            f"Failed to fetch announcements after {FIRESTORE_MAX_RETRIES} attempts: {last_error}"
        )

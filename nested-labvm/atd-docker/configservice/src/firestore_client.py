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
from models.feature import DependencyResolver, parse_feature_definitions

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

    def fetch_all_features(self, topology: str, resolve_dependencies: bool = True) -> Dict:
        """
        Fetch all enabled features (global + topology-specific).

        Features are resolved based on their dependencies - a feature is only
        truly enabled if all its dependencies are also enabled.

        Args:
            topology: The topology name (e.g., "training-level7-cl")
            resolve_dependencies: If True, check dependencies and only return
                                  features whose dependencies are satisfied.
                                  If False, return raw enabled features list.

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

                # Extract global data
                global_data = {}
                global_features: List[str] = []
                feature_definitions_raw: Dict = {}
                if global_doc.exists:
                    global_data = global_doc.to_dict()
                    global_features = global_data.get('enabled_features', [])
                    feature_definitions_raw = global_data.get('feature_definitions', {})

                # Extract topology-specific features
                topology_features: List[str] = []
                topology_config: Dict = {}
                if topo_doc.exists:
                    topo_data = topo_doc.to_dict()
                    topo_entry = topo_data.get(topology, {})
                    # Handle both legacy (list) and new (dict) formats
                    if isinstance(topo_entry, list):
                        topology_features = topo_entry
                    elif isinstance(topo_entry, dict):
                        topology_features = topo_entry.get('enabled_features', [])
                        topology_config = topo_entry

                # Merge requested features (before dependency resolution)
                requested_features: Set[str] = set(global_features) | set(topology_features)

                # Parse feature definitions
                feature_definitions = parse_feature_definitions(feature_definitions_raw)

                # Build response
                response = {
                    'topology': topology,
                    'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                    'source': 'firestore',
                    'global_features': global_features,
                    'topology_features': topology_features,
                    'requested_features': sorted(list(requested_features)),
                }

                if resolve_dependencies and feature_definitions:
                    # Resolve dependencies
                    resolver = DependencyResolver(feature_definitions)
                    resolution = resolver.resolve_enabled_features(
                        requested_features,
                        feature_definitions
                    )

                    response['enabled_features'] = resolution['enabled']
                    response['dependency_resolution'] = {
                        'disabled_missing_deps': resolution['disabled_missing_deps'],
                        'disabled_circular': resolution['disabled_circular'],
                        'dependency_chain': resolution['dependency_chain'],
                        'circular_dependencies': resolution['circular_dependencies'],
                    }

                    # Log warnings for dependency issues
                    if resolution['disabled_missing_deps']:
                        for feat, missing in resolution['disabled_missing_deps'].items():
                            logger.warning(
                                f"Feature '{feat}' disabled due to missing dependencies: {missing}"
                            )
                    if resolution['disabled_circular']:
                        logger.warning(
                            f"Features disabled due to circular dependencies: {resolution['disabled_circular']}"
                        )
                else:
                    # No dependency resolution - return all requested features
                    response['enabled_features'] = sorted(list(requested_features))
                    response['dependency_resolution'] = None

                return response

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

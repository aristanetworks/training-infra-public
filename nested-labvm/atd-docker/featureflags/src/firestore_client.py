"""
Firestore client for feature flag retrieval
"""

import logging
import time
from datetime import datetime
from typing import Dict, List, Optional, Set

from google.cloud import firestore
from google.api_core import exceptions as gcp_exceptions

from config import (
    FIRESTORE_COLLECTION,
    FIRESTORE_GLOBAL_DOC,
    FIRESTORE_TOPOLOGIES_DOC,
    FIRESTORE_MAX_RETRIES,
    FIRESTORE_RETRY_DELAY_SECONDS,
)

logger = logging.getLogger('featureflags')


class FeatureFlagClient:
    """Client for fetching feature flags from Firestore"""

    def __init__(self):
        self._db: Optional[firestore.Client] = None

    def _get_client(self) -> firestore.Client:
        """Get or create Firestore client (lazy initialization)"""
        if self._db is None:
            # Uses GOOGLE_APPLICATION_CREDENTIALS environment variable
            self._db = firestore.Client()
        return self._db

    def fetch_all_features(self, topology: str) -> Dict:
        """
        Fetch all enabled features (global + topology-specific).

        Args:
            topology: The topology name (e.g., "training-level7-cl")

        Returns:
            {
                "enabled_features": ["feature-a", "feature-b", ...],
                "global_features": ["feature-a", ...],
                "topology_features": ["feature-b", ...],
                "topology": "training-level7-cl",
                "fetched_at": "2026-01-19T10:30:00Z",
                "source": "firestore"
            }

        Raises:
            RuntimeError: If Firestore is unreachable after retries
        """
        last_error = None

        for attempt in range(FIRESTORE_MAX_RETRIES):
            try:
                db = self._get_client()
                collection = db.collection(FIRESTORE_COLLECTION)

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
                    'fetched_at': datetime.utcnow().isoformat() + 'Z',
                    'source': 'firestore'
                }

            except (gcp_exceptions.GoogleAPIError, Exception) as e:
                last_error = e
                logger.warning(
                    f"Firestore fetch attempt {attempt + 1}/{FIRESTORE_MAX_RETRIES} failed: {e}"
                )
                if attempt < FIRESTORE_MAX_RETRIES - 1:
                    time.sleep(FIRESTORE_RETRY_DELAY_SECONDS)

        raise RuntimeError(
            f"Failed to fetch features after {FIRESTORE_MAX_RETRIES} attempts: {last_error}"
        )

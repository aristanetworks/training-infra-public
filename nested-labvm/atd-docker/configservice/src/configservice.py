#!/usr/bin/env python3
"""
Config Service for ATL Labs

Provides REST API for querying feature flags and announcements.
Fetches from Firestore once at boot, caches locally for lab lifetime.

Endpoints:
- GET /health                  - Health check
- GET /features                - Get all enabled features for this lab
- GET /features/{id}           - Check if specific feature is enabled
- POST /refresh                - Force refresh features from Firestore
- GET /announcements           - Get all active announcements for this lab
- GET /announcements/{id}      - Get specific announcement by ID
- POST /announcements/refresh  - Force refresh announcements from Firestore
- GET /config                  - Get combined config (features + announcements)
- POST /refresh/all            - Force refresh both features and announcements
- POST /internal/announcements - Push internal announcement from other containers
- DELETE /internal/announcements/{id} - Remove internal announcement
- GET /internal/announcements  - List active internal announcements
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Dict, Optional

import tornado.ioloop
import tornado.web
from ruamel.yaml import YAML

try:
    from google.cloud import logging as cloud_logging
    _HAS_CLOUD_LOGGING = True
except ImportError:
    _HAS_CLOUD_LOGGING = False

from config import (
    SERVICE_PORT,
    SERVICE_NAME,
    SERVICE_VERSION,
    ACCESS_INFO_PATH,
    FEATURE_CACHE_PATH,
    ANNOUNCEMENT_CACHE_PATH,
    SERVICE_ACCOUNT_PATH,
    INTERNAL_EVENT_CHECK_INTERVAL,
)
from firestore_client import FeatureFlagClient, AnnouncementClient
from internal_events import InternalEventEngine
from models.announcement import filter_active_announcements

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('configservice')

# Initialize Google Cloud Logging (optional dependency)
_cloud_logger = None
if _HAS_CLOUD_LOGGING:
    try:
        cloud_logging_client = cloud_logging.Client()
        _cloud_logger = cloud_logging_client.logger('configservice')
        logger.info("Google Cloud Logging initialized successfully")
    except Exception as e:
        logger.warning(f"Failed to initialize Google Cloud Logging: {e}. Falling back to standard logging.")

# Global state
_feature_cache: Optional[Dict] = None
_announcement_cache: Optional[Dict] = None
_topology: Optional[str] = None
_lab_hostname: Optional[str] = None
_internal_engine: Optional[InternalEventEngine] = None


def get_topology() -> str:
    """Get topology from ACCESS_INFO.yaml"""
    global _topology
    if _topology is None:
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            _topology = access_info.get('topology', 'unknown')
    return _topology


def get_lab_hostname() -> str:
    """Get lab hostname from ACCESS_INFO.yaml (cached after first read)"""
    global _lab_hostname
    if _lab_hostname is None:
        try:
            yaml = YAML()
            with open(ACCESS_INFO_PATH, 'r') as f:
                access_info = yaml.load(f)
                _lab_hostname = access_info.get('name', 'unknown')
        except Exception as e:
            logger.warning(f"Failed to read lab hostname from ACCESS_INFO.yaml: {e}")
            _lab_hostname = 'unknown'
    return _lab_hostname


def get_user_email() -> Optional[str]:
    """Get user email from ACCESS_INFO.yaml customer_details"""
    try:
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            customer_details = access_info.get('customer_details', {})
            email = customer_details.get('exam_taker_email')
            return email
    except Exception as e:
        logger.warning(f"Failed to read user email from ACCESS_INFO.yaml: {e}")
        return None


def is_arista_user() -> bool:
    """
    Check if the current user is an Arista employee.
    If email is not found, assume Arista user (default allow).
    """
    email = get_user_email()

    if email is None:
        logger.info("User email not found in ACCESS_INFO.yaml - treating as Arista user (default allow)")
        return True

    is_arista = email.lower().endswith('@arista.com')
    logger.info(f"User email: {email}, is_arista: {is_arista}")
    return is_arista


def filter_features_by_arista_only(features: Dict, feature_definitions: Dict) -> Dict:
    """
    Filter enabled features based on arista_only rollout flag.

    Args:
        features: Feature data dict with 'enabled_features' list
        feature_definitions: Feature definitions dict from Firestore

    Returns:
        Updated features dict with filtered enabled_features list
    """
    if not feature_definitions:
        return features

    user_is_arista = is_arista_user()
    enabled_features = features.get('enabled_features', [])
    original_count = len(enabled_features)

    # Filter features
    filtered_features = []
    for feature_id in enabled_features:
        definition = feature_definitions.get(feature_id, {})
        rollout = definition.get('rollout', {})
        arista_only = rollout.get('arista_only', False)

        # If feature requires Arista user and user is not Arista, skip it
        if arista_only and not user_is_arista:
            logger.info(f"Filtering out feature '{feature_id}' (arista_only=true, user is not Arista)")
            continue

        filtered_features.append(feature_id)

    # Update features dict
    features['enabled_features'] = filtered_features
    features['filtered_by_arista_only'] = original_count != len(filtered_features)
    features['user_is_arista'] = user_is_arista

    if original_count != len(filtered_features):
        logger.info(f"Filtered {original_count - len(filtered_features)} features due to arista_only restriction")

    return features


# =============================================================================
# Feature Flag Functions
# =============================================================================

def load_feature_cache_from_file() -> Optional[Dict]:
    """Load cached features from local file"""
    try:
        if os.path.exists(FEATURE_CACHE_PATH):
            yaml = YAML()
            with open(FEATURE_CACHE_PATH, 'r') as f:
                data = yaml.load(f)
                if data:
                    logger.info(f"Loaded feature cache from {FEATURE_CACHE_PATH}")
                    data['source'] = 'cache'
                    return data
    except Exception as e:
        logger.warning(f"Failed to load feature cache file: {e}")
    return None


def save_feature_cache_to_file(data: Dict) -> bool:
    """Persist features to local cache file"""
    try:
        yaml = YAML()
        yaml.default_flow_style = False
        with open(FEATURE_CACHE_PATH, 'w') as f:
            yaml.dump(data, f)
        logger.info(f"Saved feature cache to {FEATURE_CACHE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save feature cache file: {e}")
        return False


def fetch_and_cache_features() -> Dict:
    """
    Fetch features from Firestore and cache locally.
    Applies arista_only filtering based on user email.
    Falls back to cached file if Firestore is unreachable.
    """
    global _feature_cache

    topology = get_topology()
    client = FeatureFlagClient()

    try:
        data = client.fetch_all_features(topology)

        # Apply arista_only filtering
        feature_definitions = data.get('feature_definitions', {})
        data = filter_features_by_arista_only(data, feature_definitions)

        save_feature_cache_to_file(data)
        _feature_cache = data
        logger.info(f"Fetched {len(data['enabled_features'])} features from Firestore (after arista_only filtering)")
        return data

    except Exception as e:
        logger.error(f"Firestore features fetch failed: {e}")

        cached = load_feature_cache_from_file()
        if cached:
            # Apply arista_only filtering to cached data too
            feature_definitions = cached.get('feature_definitions', {})
            cached = filter_features_by_arista_only(cached, feature_definitions)
            _feature_cache = cached
            logger.warning("Using cached features due to Firestore failure (with arista_only filtering)")
            return cached

        logger.error("No cached features available, returning empty set")
        _feature_cache = {
            'enabled_features': [],
            'global_features': [],
            'topology_features': [],
            'requested_features': [],
            'dependency_resolution': None,
            'topology': topology,
            'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'source': 'empty_fallback',
            'user_is_arista': is_arista_user()
        }
        return _feature_cache


def get_features() -> Dict:
    """Get current feature state (from memory cache)"""
    global _feature_cache
    if _feature_cache is None:
        return fetch_and_cache_features()
    return _feature_cache


# =============================================================================
# Announcement Functions
# =============================================================================

def load_announcement_cache_from_file() -> Optional[Dict]:
    """Load cached announcements from local file"""
    try:
        if os.path.exists(ANNOUNCEMENT_CACHE_PATH):
            yaml = YAML()
            with open(ANNOUNCEMENT_CACHE_PATH, 'r') as f:
                data = yaml.load(f)
                if data:
                    logger.info(f"Loaded announcement cache from {ANNOUNCEMENT_CACHE_PATH}")
                    data['source'] = 'cache'
                    # Re-filter for active (in case time has passed since caching)
                    all_announcements = data.get('active_announcements', [])
                    data['active_announcements'] = filter_active_announcements(all_announcements)
                    return data
    except Exception as e:
        logger.warning(f"Failed to load announcement cache file: {e}")
    return None


def save_announcement_cache_to_file(data: Dict) -> bool:
    """Persist announcements to local cache file"""
    try:
        yaml = YAML()
        yaml.default_flow_style = False
        with open(ANNOUNCEMENT_CACHE_PATH, 'w') as f:
            yaml.dump(data, f)
        logger.info(f"Saved announcement cache to {ANNOUNCEMENT_CACHE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save announcement cache file: {e}")
        return False


def fetch_and_cache_announcements() -> Dict:
    """
    Fetch announcements from Firestore and cache locally.
    Falls back to cached file if Firestore is unreachable.
    """
    global _announcement_cache

    topology = get_topology()
    client = AnnouncementClient()
    user_arista = is_arista_user()

    try:
        data = client.fetch_all_announcements(topology, user_is_arista=user_arista)
        save_announcement_cache_to_file(data)
        _announcement_cache = data
        logger.info(f"Fetched {len(data['active_announcements'])} active announcements from Firestore")
        return data

    except Exception as e:
        logger.error(f"Firestore announcements fetch failed: {e}")

        cached = load_announcement_cache_from_file()
        if cached:
            _announcement_cache = cached
            logger.warning("Using cached announcements due to Firestore failure")
            return cached

        logger.error("No cached announcements available, returning empty set")
        _announcement_cache = {
            'active_announcements': [],
            'global_announcements': [],
            'topology_announcements': [],
            'topology': topology,
            'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'source': 'empty_fallback'
        }
        return _announcement_cache


def get_announcements() -> Dict:
    """Get current announcement state, merging Firestore and internal event announcements.

    CRITICAL: This function must NEVER throw. The /announcements endpoint depends on it.
    If internal event merging fails, fall back to Firestore-only data.
    """
    global _announcement_cache, _internal_engine

    base = _announcement_cache if _announcement_cache else fetch_and_cache_announcements()

    if not _internal_engine:
        return base

    try:
        internal_anns = _internal_engine.get_active_announcements()
        if not internal_anns:
            return base

        # Merge: internal announcements take precedence in dedup, sort by priority
        seen_ids = set()
        merged = []
        for ann in internal_anns + base.get('active_announcements', []):
            ann_id = ann.get('id')
            if ann_id and ann_id not in seen_ids:
                seen_ids.add(ann_id)
                merged.append(ann)
        merged.sort(key=lambda x: x.get('priority', 0), reverse=True)

        result = dict(base)
        result['active_announcements'] = merged
        result['internal_announcements'] = internal_anns
        return result
    except Exception as e:
        logger.error(f"Internal event merge failed, returning Firestore-only data: {e}")
        return base


# =============================================================================
# HTTP Handlers
# =============================================================================

class HealthHandler(tornado.web.RequestHandler):
    """Health check endpoint"""

    def get(self):
        self.write({
            'status': 'ok',
            'service': SERVICE_NAME,
            'version': SERVICE_VERSION
        })


class FeaturesHandler(tornado.web.RequestHandler):
    """Get all enabled features"""

    def get(self):
        features = get_features()
        self.write(features)


class FeatureCheckHandler(tornado.web.RequestHandler):
    """Check if specific feature is enabled"""

    def get(self, feature_id: str):
        features = get_features()
        enabled = feature_id in features.get('enabled_features', [])
        self.write({
            'feature_id': feature_id,
            'enabled': enabled,
            'topology': features.get('topology', 'unknown')
        })


class RefreshHandler(tornado.web.RequestHandler):
    """Force refresh features from Firestore"""

    def post(self):
        try:
            data = fetch_and_cache_features()
            self.write({
                'status': 'refreshed',
                'features_count': len(data.get('enabled_features', [])),
                'source': data.get('source', 'unknown')
            })
        except Exception as e:
            self.set_status(500)
            self.write({
                'status': 'error',
                'message': str(e)
            })


class AnnouncementsHandler(tornado.web.RequestHandler):
    """Get all active announcements"""

    def get(self):
        announcements = get_announcements()

        # Log announcement fetch with structured data to Google Cloud Logging (async, non-blocking)
        if _cloud_logger:
            def log_async():
                try:
                    active_announcements = announcements.get('active_announcements', [])
                    announcement_ids = [ann.get('id', 'unknown') for ann in active_announcements]

                    log_entry = {
                        'event_type': 'announcement_fetch',
                        'lab_hostname': get_lab_hostname(),
                        'topology': get_topology(),
                        'user_email': get_user_email(),
                        'announcements_count': len(active_announcements),
                        'announcement_ids': announcement_ids,
                        'source': announcements.get('source', 'unknown'),
                        'timestamp': datetime.now(timezone.utc).isoformat()
                    }

                    _cloud_logger.log_struct(log_entry, severity='INFO')
                except Exception as e:
                    logger.warning(f"Failed to log announcement fetch to Cloud Logging: {e}")

            # Run logging in background thread so it doesn't block the HTTP response
            threading.Thread(target=log_async, daemon=True).start()

        self.write(announcements)


class AnnouncementCheckHandler(tornado.web.RequestHandler):
    """Get specific announcement by ID"""

    def get(self, announcement_id: str):
        announcements = get_announcements()
        active = announcements.get('active_announcements', [])

        announcement = next(
            (a for a in active if a['id'] == announcement_id),
            None
        )

        self.write({
            'announcement_id': announcement_id,
            'active': announcement is not None,
            'announcement': announcement,
            'topology': announcements.get('topology', 'unknown')
        })


class AnnouncementRefreshHandler(tornado.web.RequestHandler):
    """Force refresh announcements from Firestore"""

    def post(self):
        try:
            data = fetch_and_cache_announcements()
            self.write({
                'status': 'refreshed',
                'announcements_count': len(data.get('active_announcements', [])),
                'source': data.get('source', 'unknown')
            })
        except Exception as e:
            self.set_status(500)
            self.write({
                'status': 'error',
                'message': str(e)
            })


class ConfigHandler(tornado.web.RequestHandler):
    """Get combined configuration (features + announcements)"""

    def get(self):
        features = get_features()
        announcements = get_announcements()
        self.write({
            'features': features,
            'announcements': announcements,
            'topology': features.get('topology', 'unknown'),
            'fetched_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
        })


class RefreshAllHandler(tornado.web.RequestHandler):
    """Force refresh both features and announcements"""

    def post(self):
        try:
            features = fetch_and_cache_features()
            announcements = fetch_and_cache_announcements()
            self.write({
                'status': 'refreshed',
                'features_count': len(features.get('enabled_features', [])),
                'announcements_count': len(announcements.get('active_announcements', [])),
                'features_source': features.get('source', 'unknown'),
                'announcements_source': announcements.get('source', 'unknown')
            })
        except Exception as e:
            self.set_status(500)
            self.write({
                'status': 'error',
                'message': str(e)
            })


class InternalAnnouncementHandler(tornado.web.RequestHandler):
    """API for other containers to push/list/remove internal announcements"""

    def get(self):
        """List all active internal announcements"""
        if not _internal_engine:
            self.write({'internal_announcements': [], 'status': 'engine_not_initialized'})
            return
        anns = _internal_engine.get_active_announcements()
        self.write({
            'internal_announcements': anns,
            'count': len(anns),
            'remaining_seconds': _internal_engine.remaining_seconds,
        })

    def post(self):
        """Push an internal announcement. Body: {id, title, message, type?, priority?, ttl_minutes?}"""
        if not _internal_engine:
            self.set_status(503)
            self.write({'status': 'error', 'message': 'Internal event engine not initialized'})
            return
        try:
            body = json.loads(self.request.body)
            ann = _internal_engine.add_announcement(body)
            self.write({'status': 'created', 'announcement': ann})
        except (json.JSONDecodeError, ValueError) as e:
            self.set_status(400)
            self.write({'status': 'error', 'message': str(e)})
        except Exception as e:
            logger.error(f"InternalAnnouncementHandler.post failed: {e}")
            self.set_status(500)
            self.write({'status': 'error', 'message': 'Internal server error'})

    def delete(self, announcement_id: str = None):
        """Remove a pushed internal announcement by ID"""
        if not _internal_engine:
            self.set_status(503)
            self.write({'status': 'error', 'message': 'Internal event engine not initialized'})
            return
        if not announcement_id:
            self.set_status(400)
            self.write({'status': 'error', 'message': 'announcement_id required'})
            return
        try:
            removed = _internal_engine.remove_announcement(announcement_id)
            if removed:
                self.write({'status': 'removed', 'announcement_id': announcement_id})
            else:
                self.set_status(404)
                self.write({'status': 'not_found', 'announcement_id': announcement_id})
        except Exception as e:
            logger.error(f"InternalAnnouncementHandler.delete failed for {announcement_id}: {e}")
            self.set_status(500)
            self.write({'status': 'error', 'message': 'Internal server error'})


def make_app():
    """Create Tornado application with all routes"""
    return tornado.web.Application([
        # Health
        (r'/health', HealthHandler),

        # Features (backward compatible)
        (r'/features', FeaturesHandler),
        (r'/features/(.+)', FeatureCheckHandler),
        (r'/refresh', RefreshHandler),

        # Announcements
        (r'/announcements', AnnouncementsHandler),
        (r'/announcements/refresh', AnnouncementRefreshHandler),
        (r'/announcements/(.+)', AnnouncementCheckHandler),

        # Internal events API
        (r'/internal/announcements', InternalAnnouncementHandler),
        (r'/internal/announcements/(.+)', InternalAnnouncementHandler),

        # Combined
        (r'/config', ConfigHandler),
        (r'/refresh/all', RefreshAllHandler),
    ])


def pS(mtype):
    """Print formatted log message"""
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


def auto_refresh_cache():
    """Automatically refresh cache from Firestore every 5 minutes"""
    try:
        logger.info("Auto-refresh: Fetching latest data from Firestore...")
        fetch_and_cache_features()
        fetch_and_cache_announcements()
        logger.info("Auto-refresh: Cache updated successfully")
    except Exception as e:
        logger.error(f"Auto-refresh failed: {e}")


if __name__ == "__main__":
    # Set credentials path if file exists
    if os.path.exists(SERVICE_ACCOUNT_PATH):
        os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = SERVICE_ACCOUNT_PATH
        pS(f"Using service account: {SERVICE_ACCOUNT_PATH}")
    else:
        pS(f"WARNING: Service account not found at {SERVICE_ACCOUNT_PATH}")

    # Fetch features at startup
    pS("Fetching feature flags from Firestore...")
    fetch_and_cache_features()

    # Fetch announcements at startup
    pS("Fetching announcements from Firestore...")
    fetch_and_cache_announcements()

    # Initialize internal event engine (non-fatal — service runs without it if init fails)
    try:
        topology = get_topology()
        lab_hostname = get_lab_hostname()
        _internal_engine = InternalEventEngine(cloud_logger=_cloud_logger, topology=topology, lab_hostname=lab_hostname)
        pS("Internal event engine initialized")
        _internal_engine.check_and_update()
    except Exception as e:
        logger.error(f"Failed to initialize internal event engine: {e}. Service will run without internal events.")
        _internal_engine = None

    # Start server
    app = make_app()
    app.listen(SERVICE_PORT)
    pS(f'*** Config Service v{SERVICE_VERSION} Started on port {SERVICE_PORT} ***')

    # Schedule automatic cache refresh every 5 minutes (300000 ms)
    refresh_interval = 5 * 60 * 1000  # 5 minutes in milliseconds
    tornado.ioloop.PeriodicCallback(auto_refresh_cache, refresh_interval).start()
    logger.info(f"Auto-refresh enabled: Will refresh cache every 5 minutes")

    # Schedule internal event check (faster interval for time-sensitive warnings)
    if _internal_engine:
        def _safe_internal_check():
            try:
                _internal_engine.check_and_update()
            except Exception as e:
                logger.error(f"Internal event check failed (will retry next cycle): {e}")

        internal_interval = INTERNAL_EVENT_CHECK_INTERVAL * 1000  # seconds to ms
        tornado.ioloop.PeriodicCallback(_safe_internal_check, internal_interval).start()
        logger.info(f"Internal event check enabled: every {INTERNAL_EVENT_CHECK_INTERVAL} seconds")

    try:
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        pS("*** Service Stopped ***")

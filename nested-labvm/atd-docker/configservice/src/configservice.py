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
"""

import logging
import os
from datetime import datetime, timezone
from typing import Dict, Optional

import tornado.ioloop
import tornado.web
from ruamel.yaml import YAML

from config import (
    SERVICE_PORT,
    SERVICE_NAME,
    SERVICE_VERSION,
    ACCESS_INFO_PATH,
    FEATURE_CACHE_PATH,
    ANNOUNCEMENT_CACHE_PATH,
    SERVICE_ACCOUNT_PATH
)
from firestore_client import FeatureFlagClient, AnnouncementClient
from models.announcement import filter_active_announcements

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('configservice')

# Global state
_feature_cache: Optional[Dict] = None
_announcement_cache: Optional[Dict] = None
_topology: Optional[str] = None


def get_topology() -> str:
    """Get topology from ACCESS_INFO.yaml"""
    global _topology
    if _topology is None:
        yaml = YAML()
        with open(ACCESS_INFO_PATH, 'r') as f:
            access_info = yaml.load(f)
            _topology = access_info.get('topology', 'unknown')
    return _topology


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
    Falls back to cached file if Firestore is unreachable.
    """
    global _feature_cache

    topology = get_topology()
    client = FeatureFlagClient()

    try:
        data = client.fetch_all_features(topology)
        save_feature_cache_to_file(data)
        _feature_cache = data
        logger.info(f"Fetched {len(data['enabled_features'])} features from Firestore")
        return data

    except Exception as e:
        logger.error(f"Firestore features fetch failed: {e}")

        cached = load_feature_cache_from_file()
        if cached:
            _feature_cache = cached
            logger.warning("Using cached features due to Firestore failure")
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
            'source': 'empty_fallback'
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

    try:
        data = client.fetch_all_announcements(topology)
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
    """Get current announcement state (from memory cache)"""
    global _announcement_cache
    if _announcement_cache is None:
        return fetch_and_cache_announcements()
    return _announcement_cache


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

        # Combined
        (r'/config', ConfigHandler),
        (r'/refresh/all', RefreshAllHandler),
    ])


def pS(mtype):
    """Print formatted log message"""
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


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

    # Start server
    app = make_app()
    app.listen(SERVICE_PORT)
    pS(f'*** Config Service v{SERVICE_VERSION} Started on port {SERVICE_PORT} ***')

    try:
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        pS("*** Service Stopped ***")

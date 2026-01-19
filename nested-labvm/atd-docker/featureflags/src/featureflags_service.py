#!/usr/bin/env python3
"""
Feature Flags Service for ATL Labs

Provides REST API for querying enabled feature flags.
Fetches from Firestore once at boot, caches locally for lab lifetime.

Endpoints:
- GET /health           - Health check
- GET /features         - Get all enabled features for this lab
- GET /features/{id}    - Check if specific feature is enabled
- POST /refresh         - Force refresh from Firestore (admin use)
"""

import logging
import os
from datetime import datetime
from typing import Dict, Optional

import tornado.ioloop
import tornado.web
from ruamel.yaml import YAML

from config import (
    SERVICE_PORT,
    ACCESS_INFO_PATH,
    FEATURE_CACHE_PATH,
    SERVICE_ACCOUNT_PATH
)
from firestore_client import FeatureFlagClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('featureflags')

# Global state
_feature_cache: Optional[Dict] = None
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


def load_cache_from_file() -> Optional[Dict]:
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
        logger.warning(f"Failed to load cache file: {e}")
    return None


def save_cache_to_file(data: Dict) -> bool:
    """Persist features to local cache file"""
    try:
        yaml = YAML()
        yaml.default_flow_style = False
        with open(FEATURE_CACHE_PATH, 'w') as f:
            yaml.dump(data, f)
        logger.info(f"Saved feature cache to {FEATURE_CACHE_PATH}")
        return True
    except Exception as e:
        logger.error(f"Failed to save cache file: {e}")
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
        # Try to fetch from Firestore
        data = client.fetch_all_features(topology)
        save_cache_to_file(data)
        _feature_cache = data
        logger.info(f"Fetched {len(data['enabled_features'])} features from Firestore")
        return data

    except Exception as e:
        logger.error(f"Firestore fetch failed: {e}")

        # Fall back to cache file
        cached = load_cache_from_file()
        if cached:
            _feature_cache = cached
            logger.warning("Using cached features due to Firestore failure")
            return cached

        # No cache available - return empty state
        logger.error("No cached features available, returning empty set")
        _feature_cache = {
            'enabled_features': [],
            'global_features': [],
            'topology_features': [],
            'topology': topology,
            'fetched_at': datetime.utcnow().isoformat() + 'Z',
            'source': 'empty_fallback'
        }
        return _feature_cache


def get_features() -> Dict:
    """Get current feature state (from memory cache)"""
    global _feature_cache
    if _feature_cache is None:
        return fetch_and_cache_features()
    return _feature_cache


class HealthHandler(tornado.web.RequestHandler):
    """Health check endpoint"""
    def get(self):
        self.write({
            'status': 'ok',
            'service': 'featureflags',
            'version': '1.0.0'
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
    """Force refresh from Firestore"""
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


def make_app():
    return tornado.web.Application([
        (r'/health', HealthHandler),
        (r'/features', FeaturesHandler),
        (r'/features/(.+)', FeatureCheckHandler),
        (r'/refresh', RefreshHandler),
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

    # Start server
    app = make_app()
    app.listen(SERVICE_PORT)
    pS(f'*** Feature Flags Service Started on port {SERVICE_PORT} ***')

    try:
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        pS("*** Service Stopped ***")

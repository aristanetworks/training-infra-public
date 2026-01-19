"""
Configuration constants for Feature Flags Service
"""

import os

# Service configuration
SERVICE_PORT = int(os.getenv('FEATUREFLAGS_PORT', 50011))
SERVICE_HOST = os.getenv('FEATUREFLAGS_HOST', '0.0.0.0')

# File paths
ACCESS_INFO_PATH = os.getenv('ACCESS_INFO_PATH', '/etc/atd/ACCESS_INFO.yaml')
FEATURE_CACHE_PATH = os.getenv('FEATURE_CACHE_PATH', '/etc/atd/feature_flags_cache.yaml')
SERVICE_ACCOUNT_PATH = os.getenv(
    'GOOGLE_APPLICATION_CREDENTIALS',
    '/etc/atd/credentials/featureflags-sa.json'
)

# Firestore configuration
FIRESTORE_COLLECTION = os.getenv('FIRESTORE_COLLECTION', 'feature-flags')
FIRESTORE_GLOBAL_DOC = 'global'
FIRESTORE_TOPOLOGIES_DOC = 'topologies'

# Retry configuration
FIRESTORE_MAX_RETRIES = int(os.getenv('FIRESTORE_MAX_RETRIES', 3))
FIRESTORE_RETRY_DELAY_SECONDS = int(os.getenv('FIRESTORE_RETRY_DELAY', 5))

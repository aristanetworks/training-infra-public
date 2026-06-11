# ConfigService Function Reference

Complete reference of all functions and methods in the ConfigService codebase.

---

## configservice.py

Main service module with HTTP handlers and business logic.

### Global State Functions

#### `get_topology() -> str`

Get the topology name from ACCESS_INFO.yaml file.

**Parameters:** None

**Returns:** `str` - Topology name (e.g., "training-level7-cl")

**Caches:** Result in global `_topology` variable

**Raises:**
- `FileNotFoundError`: If ACCESS_INFO.yaml doesn't exist
- `KeyError`: If 'topology' key missing in YAML

**Example:**
```python
topology = get_topology()  # "training-level7-cl"
```

---

### Feature Flag Functions

#### `load_feature_cache_from_file() -> Optional[Dict]`

Load cached feature flags from local YAML file.

**Parameters:** None

**Returns:**
- `Dict` with cached feature data if file exists and is valid
- `None` if file doesn't exist or parsing fails

**Side Effects:** Adds `'source': 'cache'` to returned data

**Example:**
```python
cached = load_feature_cache_from_file()
if cached:
    print(f"Loaded {len(cached['enabled_features'])} features from cache")
```

---

#### `save_feature_cache_to_file(data: Dict) -> bool`

Persist feature flags to local YAML cache file.

**Parameters:**
- `data` (Dict): Feature data to save

**Returns:**
- `True` if save successful
- `False` if save failed

**Side Effects:** Writes to FEATURE_CACHE_PATH

**Example:**
```python
features = {'enabled_features': ['feature1', 'feature2']}
success = save_feature_cache_to_file(features)
```

---

#### `fetch_and_cache_features() -> Dict`

Fetch features from Firestore and cache both in memory and on disk.

**Parameters:** None

**Returns:** `Dict` with feature data

**Fallback Chain:**
1. Try Firestore (with retries)
2. Fall back to YAML cache if Firestore fails
3. Return empty feature set if no cache available

**Side Effects:**
- Updates global `_feature_cache`
- Writes to FEATURE_CACHE_PATH
- Logs fetch status

**Example:**
```python
features = fetch_and_cache_features()
print(f"Fetched {len(features['enabled_features'])} features")
print(f"Source: {features['source']}")  # 'firestore', 'cache', or 'empty_fallback'
```

---

#### `get_features() -> Dict`

Get current feature state from memory cache.

**Parameters:** None

**Returns:** `Dict` with feature data

**Lazy Loading:** Calls `fetch_and_cache_features()` if memory cache is `None`

**Example:**
```python
features = get_features()
if 'countdown-timer' in features['enabled_features']:
    # Feature is enabled
```

---

### Announcement Functions

#### `load_announcement_cache_from_file() -> Optional[Dict]`

Load cached announcements from local YAML file.

**Parameters:** None

**Returns:**
- `Dict` with cached announcement data if file exists and is valid
- `None` if file doesn't exist or parsing fails

**Side Effects:**
- Adds `'source': 'cache'` to returned data
- Re-filters announcements for current active status

**Example:**
```python
cached = load_announcement_cache_from_file()
if cached:
    print(f"Loaded {len(cached['active_announcements'])} announcements from cache")
```

---

#### `save_announcement_cache_to_file(data: Dict) -> bool`

Persist announcements to local YAML cache file.

**Parameters:**
- `data` (Dict): Announcement data to save

**Returns:**
- `True` if save successful
- `False` if save failed

**Side Effects:** Writes to ANNOUNCEMENT_CACHE_PATH

**Example:**
```python
announcements = {'active_announcements': [...]}
success = save_announcement_cache_to_file(announcements)
```

---

#### `fetch_and_cache_announcements() -> Dict`

Fetch announcements from Firestore and cache both in memory and on disk.

**Parameters:** None

**Returns:** `Dict` with announcement data

**Fallback Chain:**
1. Try Firestore (with retries)
2. Fall back to YAML cache if Firestore fails
3. Return empty announcement set if no cache available

**Side Effects:**
- Updates global `_announcement_cache`
- Writes to ANNOUNCEMENT_CACHE_PATH
- Logs fetch status

**Example:**
```python
announcements = fetch_and_cache_announcements()
print(f"Fetched {len(announcements['active_announcements'])} active announcements")
```

---

#### `get_announcements() -> Dict`

Get current announcement state from memory cache.

**Parameters:** None

**Returns:** `Dict` with announcement data

**Lazy Loading:** Calls `fetch_and_cache_announcements()` if memory cache is `None`

**Example:**
```python
announcements = get_announcements()
for ann in announcements['active_announcements']:
    print(f"{ann['title']}: {ann['message']}")
```

---

### HTTP Handlers

All handlers are Tornado RequestHandler classes.

#### `HealthHandler`

**Route:** `GET /health`

**Methods:**
- `get(self)`: Returns service health status

**Response:**
```python
{
    'status': 'ok',
    'service': SERVICE_NAME,
    'version': SERVICE_VERSION
}
```

---

#### `FeaturesHandler`

**Route:** `GET /features`

**Methods:**
- `get(self)`: Returns all enabled features

**Calls:** `get_features()`

**Response:** Full feature data dict

---

#### `FeatureCheckHandler`

**Route:** `GET /features/{feature_id}`

**Methods:**
- `get(self, feature_id: str)`: Check if specific feature is enabled

**Parameters:**
- `feature_id` (str): Feature identifier from URL path

**Calls:** `get_features()`

**Response:**
```python
{
    'feature_id': feature_id,
    'enabled': bool,
    'topology': str
}
```

---

#### `RefreshHandler`

**Route:** `POST /refresh`

**Methods:**
- `post(self)`: Force refresh features from Firestore

**Calls:** `fetch_and_cache_features()`

**Response (success):**
```python
{
    'status': 'refreshed',
    'features_count': int,
    'source': str
}
```

**Response (error):** Status 500 with error message

---

#### `AnnouncementsHandler`

**Route:** `GET /announcements`

**Methods:**
- `get(self)`: Returns all active announcements

**Calls:** `get_announcements()`

**Response:** Full announcement data dict

---

#### `AnnouncementCheckHandler`

**Route:** `GET /announcements/{announcement_id}`

**Methods:**
- `get(self, announcement_id: str)`: Get specific announcement by ID

**Parameters:**
- `announcement_id` (str): Announcement identifier from URL path

**Calls:** `get_announcements()`

**Response:**
```python
{
    'announcement_id': str,
    'active': bool,
    'announcement': dict or None,
    'topology': str
}
```

---

#### `AnnouncementRefreshHandler`

**Route:** `POST /announcements/refresh`

**Methods:**
- `post(self)`: Force refresh announcements from Firestore

**Calls:** `fetch_and_cache_announcements()`

**Response (success):**
```python
{
    'status': 'refreshed',
    'announcements_count': int,
    'source': str
}
```

**Response (error):** Status 500 with error message

---

#### `ConfigHandler`

**Route:** `GET /config`

**Methods:**
- `get(self)`: Returns combined features and announcements

**Calls:**
- `get_features()`
- `get_announcements()`

**Response:**
```python
{
    'features': dict,
    'announcements': dict,
    'topology': str,
    'fetched_at': str (ISO 8601)
}
```

---

#### `RefreshAllHandler`

**Route:** `POST /refresh/all`

**Methods:**
- `post(self)`: Force refresh both features and announcements

**Calls:**
- `fetch_and_cache_features()`
- `fetch_and_cache_announcements()`

**Response (success):**
```python
{
    'status': 'refreshed',
    'features_count': int,
    'announcements_count': int,
    'features_source': str,
    'announcements_source': str
}
```

**Response (error):** Status 500 with error message

---

### Utility Functions

#### `make_app() -> tornado.web.Application`

Create and configure Tornado application with all routes.

**Parameters:** None

**Returns:** `tornado.web.Application` instance with registered handlers

**Routes Registered:**
- `/health` → HealthHandler
- `/features` → FeaturesHandler
- `/features/(.+)` → FeatureCheckHandler
- `/refresh` → RefreshHandler
- `/announcements` → AnnouncementsHandler
- `/announcements/refresh` → AnnouncementRefreshHandler
- `/announcements/(.+)` → AnnouncementCheckHandler
- `/config` → ConfigHandler
- `/refresh/all` → RefreshAllHandler

---

#### `pS(mtype: str) -> None`

Print formatted log message with timestamp.

**Parameters:**
- `mtype` (str): Message to log

**Side Effects:** Prints to stdout

**Format:** `[YYYY-MM-DD HH:MM:SS] message`

**Example:**
```python
pS("Service started successfully")
# Output: [2025-01-20 10:30:45] Service started successfully
```

---

## firestore_client.py

Firestore data access layer.

### BaseFirestoreClient

Base class for Firestore clients with shared connection management.

#### `_get_client() -> firestore.Client` (classmethod)

Get or create shared Firestore client instance.

**Parameters:** None

**Returns:** `firestore.Client` instance

**Singleton Pattern:** Creates client only once, reuses thereafter

**Environment:** Uses GOOGLE_APPLICATION_CREDENTIALS env var for auth

---

### FeatureFlagClient

Client for fetching feature flags from Firestore.

**Inherits:** BaseFirestoreClient

#### `fetch_all_features(self, topology: str) -> Dict`

Fetch all enabled features (global + topology-specific) from Firestore.

**Parameters:**
- `topology` (str): Topology name (e.g., "training-level7-cl")

**Returns:** `Dict` with structure:
```python
{
    'enabled_features': List[str],      # Combined, sorted, deduplicated
    'global_features': List[str],       # From global doc
    'topology_features': List[str],     # From topologies doc
    'topology': str,
    'fetched_at': str,                  # ISO 8601 UTC
    'source': 'firestore'
}
```

**Raises:**
- `RuntimeError`: If Firestore unreachable after max retries

**Retry Logic:**
- Max attempts: FIRESTORE_MAX_RETRIES (default 3)
- Delay between retries: FIRESTORE_RETRY_DELAY_SECONDS (default 5)

**Firestore Structure:**
- Collection: FIRESTORE_FEATURES_COLLECTION (default "feature-flags")
- Documents:
  - `global`: Contains `enabled_features` array
  - `topologies`: Contains topology-specific arrays

**Example:**
```python
client = FeatureFlagClient()
features = client.fetch_all_features("training-level7-cl")
print(features['enabled_features'])  # ['feature1', 'feature2', ...]
```

---

### AnnouncementClient

Client for fetching announcements from Firestore.

**Inherits:** BaseFirestoreClient

#### `fetch_all_announcements(self, topology: str) -> Dict`

Fetch all active announcements (global + topology-specific) from Firestore.

**Parameters:**
- `topology` (str): Topology name

**Returns:** `Dict` with structure:
```python
{
    'active_announcements': List[Dict],       # Combined, filtered, sorted by priority
    'global_announcements': List[Dict],       # Active global only
    'topology_announcements': List[Dict],     # Active topology only
    'topology': str,
    'fetched_at': str,                        # ISO 8601 UTC
    'source': 'firestore'
}
```

**Processing:**
1. Fetch global and topology documents
2. Filter each list for active announcements (current time within start/end dates)
3. Deduplicate by ID (topology-specific takes precedence)
4. Sort by priority (highest first)

**Raises:**
- `RuntimeError`: If Firestore unreachable after max retries

**Retry Logic:** Same as FeatureFlagClient

**Firestore Structure:**
- Collection: FIRESTORE_ANNOUNCEMENTS_COLLECTION (default "announcements")
- Documents:
  - `global`: Contains `announcements` array
  - `topologies`: Contains topology-specific announcement arrays

**Example:**
```python
client = AnnouncementClient()
announcements = client.fetch_all_announcements("training-level7-cl")
for ann in announcements['active_announcements']:
    print(f"{ann['title']} (priority: {ann['priority']})")
```

---

## models/announcement.py

Announcement data model and validation.

### AnnouncementType (Enum)

Valid announcement types.

**Values:**
- `INFO = "info"`
- `WARNING = "warning"`
- `ALERT = "alert"`
- `SUCCESS = "success"`

**Usage:**
```python
from models.announcement import AnnouncementType
ann_type = AnnouncementType.WARNING
print(ann_type.value)  # "warning"
```

---

### Announcement (dataclass)

Represents an announcement with time-based activation.

**Fields:**
- `id` (str): Unique identifier
- `title` (str): Announcement headline
- `message` (str): Full message text
- `type` (AnnouncementType): Visual type
- `priority` (int): Sort priority (0-100)
- `dismissible` (bool): Can user dismiss?
- `start_date` (datetime): Active start time (UTC)
- `end_date` (datetime): Active end time (UTC)

#### `from_dict(cls, data: Dict) -> Optional['Announcement']` (classmethod)

Create Announcement instance from Firestore document data.

**Parameters:**
- `data` (Dict): Raw announcement data from Firestore

**Returns:**
- `Announcement` instance if parsing succeeds
- `None` if parsing fails (logs warning)

**Validation:**
- Required fields: id, title, message, start_date, end_date
- Optional fields: type (default "info"), priority (default 50), dismissible (default True)
- Dates: Handles both "Z" suffix and "+00:00" timezone format

**Example:**
```python
data = {
    'id': 'ann-1',
    'title': 'Maintenance',
    'message': 'Scheduled maintenance tonight',
    'type': 'warning',
    'priority': 80,
    'dismissible': True,
    'start_date': '2025-01-20T00:00:00Z',
    'end_date': '2025-01-21T23:59:59Z'
}
ann = Announcement.from_dict(data)
```

---

#### `is_active(self, now: Optional[datetime] = None) -> bool`

Check if announcement is currently active based on dates.

**Parameters:**
- `now` (Optional[datetime]): Current time (defaults to UTC now)

**Returns:** `bool` - True if current time is within [start_date, end_date]

**Timezone Handling:** Converts naive datetime to UTC if needed

**Example:**
```python
from datetime import datetime, timezone

ann = Announcement.from_dict(data)
is_active = ann.is_active()  # Uses current time
is_active_at = ann.is_active(datetime(2025, 1, 20, 12, 0, tzinfo=timezone.utc))
```

---

#### `to_dict(self) -> Dict`

Convert announcement to dictionary for JSON serialization.

**Parameters:** None

**Returns:** `Dict` with all fields, dates in ISO 8601 format with "Z" suffix

**Example:**
```python
ann = Announcement.from_dict(data)
json_data = ann.to_dict()
# {
#     'id': 'ann-1',
#     'title': 'Maintenance',
#     'message': 'Scheduled maintenance tonight',
#     'type': 'warning',
#     'priority': 80,
#     'dismissible': True,
#     'start_date': '2025-01-20T00:00:00Z',
#     'end_date': '2025-01-21T23:59:59Z'
# }
```

---

### Module Functions

#### `filter_active_announcements(announcements: List[Dict]) -> List[Dict]`

Filter and sort announcements by active status and priority.

**Parameters:**
- `announcements` (List[Dict]): Raw announcement data from Firestore

**Returns:** `List[Dict]` - Active announcements sorted by priority (highest first)

**Processing:**
1. Parse each dict into Announcement object
2. Check if active using current UTC time
3. Convert active announcements back to dicts
4. Sort by priority descending

**Example:**
```python
from models.announcement import filter_active_announcements

raw_announcements = [
    {'id': '1', 'priority': 50, ...},
    {'id': '2', 'priority': 80, ...},
    {'id': '3', 'priority': 30, ...},  # Expired
]

active = filter_active_announcements(raw_announcements)
# Returns only active, sorted: [ann2 (80), ann1 (50)]
```

---

## config.py

Configuration constants and environment variables.

### Service Configuration

- `SERVICE_PORT: int` - HTTP server port (env: CONFIGSERVICE_PORT, default: 50011)
- `SERVICE_HOST: str` - HTTP bind address (env: CONFIGSERVICE_HOST, default: "0.0.0.0")
- `SERVICE_NAME: str` - Service identifier ("configservice")
- `SERVICE_VERSION: str` - Current version ("2.0.0")

### File Paths

- `ACCESS_INFO_PATH: str` - Topology info file (env: ACCESS_INFO_PATH, default: "/etc/atd/ACCESS_INFO.yaml")
- `FEATURE_CACHE_PATH: str` - Feature cache file (env: FEATURE_CACHE_PATH, default: "/etc/atd/feature_flags_cache.yaml")
- `ANNOUNCEMENT_CACHE_PATH: str` - Announcement cache file (env: ANNOUNCEMENT_CACHE_PATH, default: "/etc/atd/announcements_cache.yaml")
- `SERVICE_ACCOUNT_PATH: str` - GCP credentials (env: GOOGLE_APPLICATION_CREDENTIALS, default: "/etc/atd/credentials/configservice-sa.json")

### Firestore Configuration

**Feature Flags:**
- `FIRESTORE_FEATURES_COLLECTION: str` - Collection name (env: FIRESTORE_FEATURES_COLLECTION, default: "feature-flags")
- `FIRESTORE_GLOBAL_DOC: str` - Global document ID ("global")
- `FIRESTORE_TOPOLOGIES_DOC: str` - Topologies document ID ("topologies")

**Announcements:**
- `FIRESTORE_ANNOUNCEMENTS_COLLECTION: str` - Collection name (env: FIRESTORE_ANNOUNCEMENTS_COLLECTION, default: "announcements")

### Retry Configuration

- `FIRESTORE_MAX_RETRIES: int` - Max retry attempts (env: FIRESTORE_MAX_RETRIES, default: 3)
- `FIRESTORE_RETRY_DELAY_SECONDS: int` - Seconds between retries (env: FIRESTORE_RETRY_DELAY, default: 5)

---

## Startup Flow

When the service starts (`__main__` block):

1. **Set Credentials Path**
   - Check if SERVICE_ACCOUNT_PATH exists
   - Set GOOGLE_APPLICATION_CREDENTIALS environment variable
   - Log warning if not found

2. **Fetch Features**
   - Call `fetch_and_cache_features()`
   - Attempts Firestore fetch
   - Falls back to cache if Firestore fails
   - Updates memory and disk cache

3. **Fetch Announcements**
   - Call `fetch_and_cache_announcements()`
   - Same fallback logic as features
   - Filters for active announcements
   - Updates memory and disk cache

4. **Start HTTP Server**
   - Create Tornado app with `make_app()`
   - Bind to SERVICE_PORT
   - Start IOLoop
   - Handle Ctrl+C gracefully

**Example Startup Log:**
```
[2025-01-20 10:30:00] Using service account: /etc/atd/credentials/configservice-sa.json
[2025-01-20 10:30:00] Fetching feature flags from Firestore...
[2025-01-20 10:30:01] Fetching announcements from Firestore...
[2025-01-20 10:30:02] *** Config Service v2.0.0 Started on port 50011 ***
```

---

## Error Handling Patterns

### Graceful Degradation

All fetch functions follow this pattern:

```python
try:
    # Try Firestore (with retries)
    data = firestore_client.fetch(...)
    save_to_cache(data)
    return data
except Exception:
    # Fall back to cache
    cached = load_from_cache()
    if cached:
        return cached
    # Last resort: empty data
    return empty_default()
```

### Logging Levels

- `INFO`: Successful operations, cache loads, fetches
- `WARNING`: Firestore retry attempts, cache fallbacks, invalid announcement data
- `ERROR`: Firestore failures after all retries, cache save failures

### HTTP Error Responses

Refresh endpoints return 500 on failure:

```python
try:
    data = fetch_and_cache()
    self.write({'status': 'refreshed', ...})
except Exception as e:
    self.set_status(500)
    self.write({'status': 'error', 'message': str(e)})
```

Read endpoints always return 200 with cached/empty data.

---

## Thread Safety

**Not Thread-Safe:** Service uses global variables (`_feature_cache`, `_announcement_cache`, `_topology`) without locking.

**Safe Because:**
- Tornado is single-threaded by default
- All handlers run in the same event loop
- No concurrent modification of global state

**Warning:** If using Tornado with multiple processes or threads, add locking around global state.

---

## Performance Notes

### Memory Usage

Typical lab configuration:
- ~10 features: ~1 KB
- ~5 announcements: ~5 KB
- Total service memory: <50 MB

### Request Latency

- Health check: <1 ms
- Feature/announcement retrieval: <5 ms (memory lookup)
- Refresh operations: 100-500 ms (Firestore + disk I/O)

### Caching Strategy

**Boot-time Cache:**
- Features and announcements fetched once at startup
- Stored in memory for fast access
- Persisted to YAML for fallback

**No Automatic Refresh:**
- Cache is never automatically refreshed during runtime
- Refresh only happens via manual API calls
- Design assumes configuration is stable for lab lifetime

**Cache Invalidation:**
- Manual only via `/refresh`, `/announcements/refresh`, or `/refresh/all`
- Announcement time filtering re-applied on every read from cache

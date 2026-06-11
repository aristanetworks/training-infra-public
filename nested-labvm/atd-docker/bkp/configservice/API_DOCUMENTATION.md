# ConfigService API Documentation

**Version:** 2.0.0
**Service Name:** configservice
**Default Port:** 50011

## Overview

ConfigService is a centralized configuration management service for ATL (Arista Training Labs). It provides REST APIs for:

- **Feature Flags**: Topology-aware feature toggling with global and per-topology overrides
- **Announcements**: Time-based, prioritized announcements with filtering and topology targeting
- **Caching**: Local YAML cache with Firestore fallback for resilience

The service fetches configuration from Firestore at startup and caches it locally for the lab lifetime. This ensures labs remain functional even if Firestore becomes unavailable.

## Architecture

See `architecture.mmd` for a visual diagram of the service architecture.

### Key Components

1. **HTTP Layer** (Tornado): REST API handlers
2. **Business Logic**: Feature and announcement management with caching
3. **Data Access Layer**: Firestore clients with retry logic
4. **Data Models**: Announcement validation and filtering
5. **Local Cache**: YAML-based persistence layer

### Data Flow

```
Startup:
  Firestore → FeatureFlagClient/AnnouncementClient → Memory Cache → YAML Cache

Runtime (cache hit):
  HTTP Request → Handler → Memory Cache → Response

Runtime (refresh):
  HTTP Request → Handler → Firestore → Memory Cache → YAML Cache → Response

Fallback:
  Firestore Failure → YAML Cache → Memory Cache → Response
```

---

## API Endpoints

### Health Check

#### `GET /health`

Returns service health status.

**Response:**
```json
{
  "status": "ok",
  "service": "configservice",
  "version": "2.0.0"
}
```

**Status Codes:**
- `200 OK`: Service is healthy

---

### Feature Flags

#### `GET /features`

Returns all enabled features for the current lab topology.

**Response:**
```json
{
  "enabled_features": [
    "countdown-timer",
    "topology-diagram",
    "lab-feedback"
  ],
  "global_features": [
    "countdown-timer",
    "lab-feedback"
  ],
  "topology_features": [
    "topology-diagram"
  ],
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T10:30:00Z",
  "source": "firestore"
}
```

**Fields:**
- `enabled_features`: Combined list of all enabled features (global + topology-specific)
- `global_features`: Features enabled for all topologies
- `topology_features`: Features enabled only for this specific topology
- `topology`: Current lab topology name
- `fetched_at`: ISO 8601 timestamp of when data was fetched
- `source`: Data source (`firestore`, `cache`, or `empty_fallback`)

**Status Codes:**
- `200 OK`: Features retrieved successfully

**Notes:**
- Backward compatible with the old featureflags service
- Deduplicates features between global and topology lists
- Returns alphabetically sorted feature list

---

#### `GET /features/{feature_id}`

Check if a specific feature is enabled.

**Path Parameters:**
- `feature_id` (string): The feature identifier to check

**Response:**
```json
{
  "feature_id": "countdown-timer",
  "enabled": true,
  "topology": "training-level7-cl"
}
```

**Example (feature disabled):**
```json
{
  "feature_id": "experimental-feature",
  "enabled": false,
  "topology": "training-level7-cl"
}
```

**Status Codes:**
- `200 OK`: Check completed successfully

---

#### `POST /refresh`

Force refresh feature flags from Firestore.

**Response:**
```json
{
  "status": "refreshed",
  "features_count": 3,
  "source": "firestore"
}
```

**Error Response:**
```json
{
  "status": "error",
  "message": "Failed to connect to Firestore: connection timeout"
}
```

**Status Codes:**
- `200 OK`: Refresh successful
- `500 Internal Server Error`: Refresh failed

**Notes:**
- Updates both memory and YAML cache
- Use sparingly - data is cached for the lab lifetime by design

---

### Announcements

#### `GET /announcements`

Returns all currently active announcements for the lab topology.

**Response:**
```json
{
  "active_announcements": [
    {
      "id": "maintenance-2025-01-20",
      "title": "Scheduled Maintenance",
      "message": "Platform maintenance will occur on January 20, 2025 from 2-4 AM UTC.",
      "type": "warning",
      "priority": 80,
      "dismissible": true,
      "start_date": "2025-01-19T00:00:00Z",
      "end_date": "2025-01-21T23:59:59Z"
    },
    {
      "id": "new-feature-release",
      "title": "New Feature Available",
      "message": "Check out the new topology diagram viewer!",
      "type": "info",
      "priority": 50,
      "dismissible": true,
      "start_date": "2025-01-15T00:00:00Z",
      "end_date": "2025-02-01T23:59:59Z"
    }
  ],
  "global_announcements": [
    {
      "id": "new-feature-release",
      "title": "New Feature Available",
      "message": "Check out the new topology diagram viewer!",
      "type": "info",
      "priority": 50,
      "dismissible": true,
      "start_date": "2025-01-15T00:00:00Z",
      "end_date": "2025-02-01T23:59:59Z"
    }
  ],
  "topology_announcements": [
    {
      "id": "maintenance-2025-01-20",
      "title": "Scheduled Maintenance",
      "message": "Platform maintenance will occur on January 20, 2025 from 2-4 AM UTC.",
      "type": "warning",
      "priority": 80,
      "dismissible": true,
      "start_date": "2025-01-19T00:00:00Z",
      "end_date": "2025-01-21T23:59:59Z"
    }
  ],
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T10:30:00Z",
  "source": "firestore"
}
```

**Fields:**
- `active_announcements`: All active announcements (global + topology), sorted by priority
- `global_announcements`: Announcements shown to all topologies
- `topology_announcements`: Announcements specific to this topology
- `topology`: Current lab topology name
- `fetched_at`: ISO 8601 timestamp
- `source`: Data source

**Announcement Fields:**
- `id`: Unique identifier
- `title`: Short headline
- `message`: Full announcement text (supports markdown)
- `type`: Visual type (`info`, `warning`, `alert`, `success`)
- `priority`: Sort order (0-100, higher = shown first)
- `dismissible`: Whether users can close the announcement
- `start_date`: When announcement becomes active (ISO 8601)
- `end_date`: When announcement expires (ISO 8601)

**Status Codes:**
- `200 OK`: Announcements retrieved successfully

**Notes:**
- Only returns announcements within their active date range
- Sorted by priority (highest first)
- Deduplicates by ID (topology-specific takes precedence over global)
- Time filtering happens on every request to ensure accuracy

---

#### `GET /announcements/{announcement_id}`

Get a specific announcement by ID.

**Path Parameters:**
- `announcement_id` (string): The announcement identifier

**Response (active announcement):**
```json
{
  "announcement_id": "maintenance-2025-01-20",
  "active": true,
  "announcement": {
    "id": "maintenance-2025-01-20",
    "title": "Scheduled Maintenance",
    "message": "Platform maintenance will occur on January 20, 2025 from 2-4 AM UTC.",
    "type": "warning",
    "priority": 80,
    "dismissible": true,
    "start_date": "2025-01-19T00:00:00Z",
    "end_date": "2025-01-21T23:59:59Z"
  },
  "topology": "training-level7-cl"
}
```

**Response (inactive/not found):**
```json
{
  "announcement_id": "expired-announcement",
  "active": false,
  "announcement": null,
  "topology": "training-level7-cl"
}
```

**Status Codes:**
- `200 OK`: Query completed successfully

---

#### `POST /announcements/refresh`

Force refresh announcements from Firestore.

**Response:**
```json
{
  "status": "refreshed",
  "announcements_count": 2,
  "source": "firestore"
}
```

**Error Response:**
```json
{
  "status": "error",
  "message": "Failed to connect to Firestore: connection timeout"
}
```

**Status Codes:**
- `200 OK`: Refresh successful
- `500 Internal Server Error`: Refresh failed

---

### Combined Configuration

#### `GET /config`

Returns both features and announcements in a single request.

**Response:**
```json
{
  "features": {
    "enabled_features": ["countdown-timer", "topology-diagram"],
    "global_features": ["countdown-timer"],
    "topology_features": ["topology-diagram"],
    "topology": "training-level7-cl",
    "fetched_at": "2025-01-20T10:30:00Z",
    "source": "firestore"
  },
  "announcements": {
    "active_announcements": [
      {
        "id": "maintenance-2025-01-20",
        "title": "Scheduled Maintenance",
        "message": "Platform maintenance will occur on January 20, 2025 from 2-4 AM UTC.",
        "type": "warning",
        "priority": 80,
        "dismissible": true,
        "start_date": "2025-01-19T00:00:00Z",
        "end_date": "2025-01-21T23:59:59Z"
      }
    ],
    "global_announcements": [],
    "topology_announcements": [
      {
        "id": "maintenance-2025-01-20",
        "title": "Scheduled Maintenance",
        "message": "Platform maintenance will occur on January 20, 2025 from 2-4 AM UTC.",
        "type": "warning",
        "priority": 80,
        "dismissible": true,
        "start_date": "2025-01-19T00:00:00Z",
        "end_date": "2025-01-21T23:59:59Z"
      }
    ],
    "topology": "training-level7-cl",
    "fetched_at": "2025-01-20T10:30:00Z",
    "source": "firestore"
  },
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T10:30:00Z"
}
```

**Status Codes:**
- `200 OK`: Configuration retrieved successfully

**Notes:**
- Recommended endpoint for UI clients to reduce request count
- Both features and announcements share the same topology

---

#### `POST /refresh/all`

Force refresh both features and announcements from Firestore.

**Response:**
```json
{
  "status": "refreshed",
  "features_count": 3,
  "announcements_count": 2,
  "features_source": "firestore",
  "announcements_source": "firestore"
}
```

**Error Response:**
```json
{
  "status": "error",
  "message": "Failed to connect to Firestore: connection timeout"
}
```

**Status Codes:**
- `200 OK`: Refresh successful
- `500 Internal Server Error`: One or both refreshes failed

---

## Firestore Document Structure

### Feature Flags Collection

**Collection:** `feature-flags`

#### Global Document (`global`)

```json
{
  "enabled_features": [
    "countdown-timer",
    "lab-feedback",
    "resource-viewer"
  ]
}
```

#### Topologies Document (`topologies`)

```json
{
  "training-level7-cl": [
    "topology-diagram",
    "advanced-routing"
  ],
  "training-datacenter-evpn": [
    "evpn-simulator",
    "vxlan-debugger"
  ]
}
```

**Notes:**
- `enabled_features` in global applies to ALL topologies
- Topology-specific features are added to the global set
- Features are deduplicated automatically

---

### Announcements Collection

**Collection:** `announcements`

#### Global Document (`global`)

```json
{
  "announcements": [
    {
      "id": "new-feature-2025-01",
      "title": "New Feature Available",
      "message": "Check out the new topology diagram viewer in the lab interface!",
      "type": "info",
      "priority": 50,
      "dismissible": true,
      "start_date": "2025-01-15T00:00:00Z",
      "end_date": "2025-02-01T23:59:59Z"
    },
    {
      "id": "holiday-schedule",
      "title": "Holiday Support Schedule",
      "message": "Support hours will be reduced during the holiday period.",
      "type": "warning",
      "priority": 60,
      "dismissible": true,
      "start_date": "2025-12-20T00:00:00Z",
      "end_date": "2026-01-05T23:59:59Z"
    }
  ]
}
```

#### Topologies Document (`topologies`)

```json
{
  "training-level7-cl": [
    {
      "id": "level7-maintenance",
      "title": "Level 7 Lab Maintenance",
      "message": "This lab topology will undergo maintenance on January 25.",
      "type": "alert",
      "priority": 80,
      "dismissible": false,
      "start_date": "2025-01-20T00:00:00Z",
      "end_date": "2025-01-26T23:59:59Z"
    }
  ],
  "training-datacenter-evpn": [
    {
      "id": "evpn-beta-features",
      "title": "Beta Features Available",
      "message": "Try out our new EVPN troubleshooting tools!",
      "type": "success",
      "priority": 40,
      "dismissible": true,
      "start_date": "2025-01-10T00:00:00Z",
      "end_date": "2025-03-01T23:59:59Z"
    }
  ]
}
```

**Field Requirements:**
- `id` (string, required): Unique identifier for the announcement
- `title` (string, required): Short headline (recommended max 80 chars)
- `message` (string, required): Full message text (supports markdown)
- `type` (string, required): One of `info`, `warning`, `alert`, `success`
- `priority` (integer, optional): 0-100, default 50 (higher = shown first)
- `dismissible` (boolean, optional): default true
- `start_date` (ISO 8601 string, required): When announcement becomes active
- `end_date` (ISO 8601 string, required): When announcement expires

**Notes:**
- Dates must be in ISO 8601 format with timezone (Z or +00:00)
- Announcements outside their date range are automatically filtered
- Topology-specific announcements with same ID override global ones
- Higher priority announcements appear first in the list

---

## Client Integration Guide

### Frontend/UILanding Integration

#### 1. Basic Feature Flag Check

```javascript
async function checkFeature(featureId) {
  const response = await fetch(`http://configservice:50011/features/${featureId}`);
  const data = await response.json();
  return data.enabled;
}

// Usage
if (await checkFeature('countdown-timer')) {
  // Show countdown timer UI
}
```

#### 2. Load All Configuration at Startup

```javascript
async function loadLabConfig() {
  const response = await fetch('http://configservice:50011/config');
  const config = await response.json();

  return {
    features: config.features.enabled_features,
    announcements: config.announcements.active_announcements,
    topology: config.topology
  };
}

// Usage in app initialization
const labConfig = await loadLabConfig();
console.log('Lab topology:', labConfig.topology);
console.log('Enabled features:', labConfig.features);
console.log('Active announcements:', labConfig.announcements);
```

#### 3. Display Announcements

```javascript
function renderAnnouncements(announcements) {
  const container = document.getElementById('announcements-container');

  announcements.forEach(announcement => {
    const div = document.createElement('div');
    div.className = `announcement announcement-${announcement.type}`;

    div.innerHTML = `
      <div class="announcement-header">
        <h3>${announcement.title}</h3>
        ${announcement.dismissible ? '<button class="dismiss">×</button>' : ''}
      </div>
      <div class="announcement-body">
        ${renderMarkdown(announcement.message)}
      </div>
    `;

    if (announcement.dismissible) {
      div.querySelector('.dismiss').addEventListener('click', () => {
        dismissAnnouncement(announcement.id);
        div.remove();
      });
    }

    container.appendChild(div);
  });
}

function dismissAnnouncement(announcementId) {
  // Store in localStorage to remember dismissal
  const dismissed = JSON.parse(localStorage.getItem('dismissed-announcements') || '[]');
  dismissed.push(announcementId);
  localStorage.setItem('dismissed-announcements', JSON.stringify(dismissed));
}
```

#### 4. Announcement Styling (CSS)

```css
.announcement {
  border-left: 3px solid;
  padding: 15px;
  margin: 10px 0;
  border-radius: 4px;
}

.announcement-info {
  background-color: #dae0fe;
  border-color: #4c5cae;
}

.announcement-warning {
  background-color: #fff9e6;
  border-color: #fbb500;
}

.announcement-alert {
  background-color: #ffe6e6;
  border-color: #e30909;
}

.announcement-success {
  background-color: #e6ffe6;
  border-color: #78d82c;
}

.announcement-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.announcement-header h3 {
  margin: 0;
  color: #071c35;
}

.dismiss {
  background: none;
  border: none;
  font-size: 24px;
  cursor: pointer;
  color: #666;
}

.dismiss:hover {
  color: #e30909;
}
```

#### 5. Periodic Announcement Refresh (Optional)

```javascript
// Refresh announcements every 5 minutes to catch new/expired ones
setInterval(async () => {
  const response = await fetch('http://configservice:50011/announcements');
  const data = await response.json();

  // Filter out dismissed announcements
  const dismissed = JSON.parse(localStorage.getItem('dismissed-announcements') || '[]');
  const activeAnnouncements = data.active_announcements.filter(
    a => !dismissed.includes(a.id)
  );

  renderAnnouncements(activeAnnouncements);
}, 5 * 60 * 1000);
```

---

### Backend Service Integration

#### Python Example

```python
import requests
from typing import List, Dict

class ConfigServiceClient:
    def __init__(self, base_url: str = "http://configservice:50011"):
        self.base_url = base_url

    def is_feature_enabled(self, feature_id: str) -> bool:
        """Check if a specific feature is enabled"""
        response = requests.get(f"{self.base_url}/features/{feature_id}")
        response.raise_for_status()
        return response.json()['enabled']

    def get_all_features(self) -> List[str]:
        """Get list of all enabled features"""
        response = requests.get(f"{self.base_url}/features")
        response.raise_for_status()
        return response.json()['enabled_features']

    def get_announcements(self) -> List[Dict]:
        """Get all active announcements"""
        response = requests.get(f"{self.base_url}/announcements")
        response.raise_for_status()
        return response.json()['active_announcements']

# Usage
client = ConfigServiceClient()

if client.is_feature_enabled('lab-feedback'):
    # Enable feedback collection
    enable_feedback_module()

announcements = client.get_announcements()
for announcement in announcements:
    print(f"[{announcement['type'].upper()}] {announcement['title']}")
```

---

## Configuration Options

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIGSERVICE_PORT` | `50011` | HTTP server port |
| `CONFIGSERVICE_HOST` | `0.0.0.0` | HTTP server bind address |
| `ACCESS_INFO_PATH` | `/etc/atd/ACCESS_INFO.yaml` | Path to topology info file |
| `FEATURE_CACHE_PATH` | `/etc/atd/feature_flags_cache.yaml` | Local feature cache file |
| `ANNOUNCEMENT_CACHE_PATH` | `/etc/atd/announcements_cache.yaml` | Local announcement cache file |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/atd/credentials/configservice-sa.json` | GCP service account key path |
| `FIRESTORE_FEATURES_COLLECTION` | `feature-flags` | Firestore collection for features |
| `FIRESTORE_ANNOUNCEMENTS_COLLECTION` | `announcements` | Firestore collection for announcements |
| `FIRESTORE_MAX_RETRIES` | `3` | Max retry attempts for Firestore |
| `FIRESTORE_RETRY_DELAY` | `5` | Seconds between retry attempts |

### Docker Deployment

```yaml
services:
  configservice:
    image: configservice:2.0.0
    container_name: configservice
    ports:
      - "50011:50011"
    volumes:
      - /etc/atd:/etc/atd:ro
      - /etc/atd/credentials:/etc/atd/credentials:ro
    environment:
      - CONFIGSERVICE_PORT=50011
      - GOOGLE_APPLICATION_CREDENTIALS=/etc/atd/credentials/configservice-sa.json
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:50011/health"]
      interval: 30s
      timeout: 5s
      retries: 3
```

---

## Error Handling

### Firestore Connection Failures

When Firestore is unreachable:
1. Service retries 3 times with 5-second delays
2. Falls back to YAML cache if available
3. Returns empty configuration if no cache exists
4. Logs warnings but continues serving requests

**Example cached response:**
```json
{
  "enabled_features": ["countdown-timer"],
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T08:00:00Z",
  "source": "cache"
}
```

### Invalid Announcement Data

Announcements with missing required fields or invalid dates are:
- Logged as warnings
- Skipped in the response
- Not cached

### Cache Corruption

If cache files are corrupted:
- Service attempts to fetch from Firestore
- Falls back to empty configuration if Firestore fails
- Overwrites corrupted cache on next successful fetch

---

## Migration from Old FeatureFlags Service

The configservice is **backward compatible** with the old featureflags service.

### Endpoint Mapping

| Old Endpoint | New Endpoint | Notes |
|--------------|--------------|-------|
| `GET /features` | `GET /features` | Identical response structure |
| `GET /features/{id}` | `GET /features/{id}` | Identical response structure |
| `POST /refresh` | `POST /refresh` | Identical response structure |
| N/A | `GET /announcements` | New feature |
| N/A | `GET /config` | New feature |

### No Code Changes Required

Existing clients using the old featureflags API will work without modification:

```javascript
// This code works with both old and new service
const response = await fetch('http://configservice:50011/features');
const data = await response.json();
console.log('Enabled features:', data.enabled_features);
```

---

## Troubleshooting

### Features Not Updating

1. Check Firestore connectivity: `POST /refresh`
2. Verify service account credentials exist
3. Check logs for Firestore errors
4. Verify topology in ACCESS_INFO.yaml matches Firestore

### Announcements Not Appearing

1. Verify announcement dates are in ISO 8601 format with timezone
2. Check current time is within start_date and end_date
3. Verify topology matches or announcement is in global
4. Check announcement is not dismissed (clear localStorage)
5. Force refresh: `POST /announcements/refresh`

### Service Won't Start

1. Verify port 50011 is not in use
2. Check ACCESS_INFO.yaml exists and is valid YAML
3. Check service account JSON exists (optional but recommended)
4. Review startup logs for specific error messages

### Cache Issues

```bash
# Clear caches and force fresh fetch
rm /etc/atd/feature_flags_cache.yaml
rm /etc/atd/announcements_cache.yaml
curl -X POST http://configservice:50011/refresh/all
```

---

## Testing

### Health Check

```bash
curl http://configservice:50011/health
```

### Get All Features

```bash
curl http://configservice:50011/features | jq
```

### Check Specific Feature

```bash
curl http://configservice:50011/features/countdown-timer | jq
```

### Get Announcements

```bash
curl http://configservice:50011/announcements | jq
```

### Get Combined Config

```bash
curl http://configservice:50011/config | jq
```

### Force Refresh

```bash
curl -X POST http://configservice:50011/refresh/all | jq
```

---

## Performance Characteristics

- **Startup Time**: 1-3 seconds (includes Firestore fetch)
- **Request Latency**: <5ms (served from memory)
- **Memory Usage**: <50MB (typical lab configuration)
- **Cache Update**: Manual refresh only (no automatic polling)
- **Firestore Requests**: 2 at startup + on-demand refresh only

---

## Security Considerations

1. **Service Account**: Requires read-only Firestore access
2. **Network**: Runs on internal Docker network (not exposed externally)
3. **Authentication**: No authentication (internal service only)
4. **Data Validation**: Announcements validated before serving
5. **Cache Security**: Cache files readable only by service user

---

## Version History

### 2.0.0 (Current)
- Added announcements API
- Added combined `/config` endpoint
- Added `/refresh/all` endpoint
- Improved caching with separate feature/announcement caches
- Added time-based announcement filtering
- Added priority-based announcement sorting
- Backward compatible with 1.x feature flags API

### 1.0.0
- Initial release with feature flags only
- Firestore integration
- YAML caching
- Topology-aware feature toggling

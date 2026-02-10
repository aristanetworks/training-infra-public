# ConfigService

**Version:** 2.0.0
**Port:** 50011
**Framework:** Tornado (Python 3.11)

## Quick Start

```bash
# Build
docker build -t configservice:2.0.0 .

# Run
docker run -p 50011:50011 \
  -v /etc/atd:/etc/atd:ro \
  -v /etc/atd/credentials:/etc/atd/credentials:ro \
  configservice:2.0.0

# Health check
curl http://localhost:50011/health
```

## What is ConfigService?

ConfigService provides centralized configuration management for ATL (Arista Training Labs) with:

1. **Feature Flags** - Enable/disable features per topology
2. **Announcements** - Time-based, prioritized user notifications
3. **Caching** - Local YAML cache with Firestore fallback
4. **Backward Compatibility** - Drop-in replacement for old featureflags service

## Key Endpoints

```bash
# Get all features
curl http://localhost:50011/features

# Check specific feature
curl http://localhost:50011/features/countdown-timer

# Get announcements
curl http://localhost:50011/announcements

# Get everything
curl http://localhost:50011/config

# Force refresh
curl -X POST http://localhost:50011/refresh/all
```

## Architecture

- **HTTP Layer**: Tornado web framework
- **Data Source**: Google Cloud Firestore
- **Cache**: YAML files in `/etc/atd/`
- **Topology Detection**: Reads `ACCESS_INFO.yaml`

## Documentation

| Document | Description |
|----------|-------------|
| [API_DOCUMENTATION.md](./API_DOCUMENTATION.md) | Complete API reference with examples |
| [FUNCTION_REFERENCE.md](./FUNCTION_REFERENCE.md) | All functions and methods |
| [architecture.mmd](./architecture.mmd) | Mermaid architecture diagram |

## File Structure

```
configservice/
├── Dockerfile
├── requirements.txt
├── README.md                      # This file
├── API_DOCUMENTATION.md           # Full API docs
├── FUNCTION_REFERENCE.md          # Function reference
├── architecture.mmd               # Architecture diagram
└── src/
    ├── configservice.py           # Main service
    ├── config.py                  # Configuration
    ├── firestore_client.py        # Firestore clients
    └── models/
        ├── __init__.py
        └── announcement.py        # Announcement model
```

## Dependencies

- `tornado>=6.0` - Web framework
- `google-cloud-firestore>=2.0.0` - Firestore client
- `ruamel.yaml>=0.18.0` - YAML processing

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONFIGSERVICE_PORT` | `50011` | HTTP server port |
| `ACCESS_INFO_PATH` | `/etc/atd/ACCESS_INFO.yaml` | Topology file path |
| `FEATURE_CACHE_PATH` | `/etc/atd/feature_flags_cache.yaml` | Feature cache |
| `ANNOUNCEMENT_CACHE_PATH` | `/etc/atd/announcements_cache.yaml` | Announcement cache |
| `GOOGLE_APPLICATION_CREDENTIALS` | `/etc/atd/credentials/configservice-sa.json` | GCP credentials |

## Firestore Collections

### feature-flags
- `global` document: `enabled_features` array
- `topologies` document: Per-topology feature arrays

### announcements
- `global` document: `announcements` array
- `topologies` document: Per-topology announcement arrays

## Example Responses

### GET /features
```json
{
  "enabled_features": ["countdown-timer", "topology-diagram"],
  "global_features": ["countdown-timer"],
  "topology_features": ["topology-diagram"],
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T10:30:00Z",
  "source": "firestore"
}
```

### GET /announcements
```json
{
  "active_announcements": [
    {
      "id": "maintenance-2025",
      "title": "Scheduled Maintenance",
      "message": "Platform maintenance tonight from 2-4 AM UTC.",
      "type": "warning",
      "priority": 80,
      "dismissible": true,
      "start_date": "2025-01-20T00:00:00Z",
      "end_date": "2025-01-21T23:59:59Z"
    }
  ],
  "topology": "training-level7-cl",
  "fetched_at": "2025-01-20T10:30:00Z",
  "source": "firestore"
}
```

## Client Integration

### JavaScript
```javascript
const config = await fetch('http://configservice:50011/config').then(r => r.json());
console.log('Features:', config.features.enabled_features);
console.log('Announcements:', config.announcements.active_announcements);
```

### Python
```python
import requests

response = requests.get('http://configservice:50011/features')
features = response.json()['enabled_features']
```

## Migration from Old FeatureFlags Service

No changes needed! ConfigService is backward compatible:

- Same endpoints: `/features`, `/features/{id}`, `/refresh`
- Same response format
- Just update the service name in your URLs

## Troubleshooting

### Service won't start
- Check port 50011 is available
- Verify ACCESS_INFO.yaml exists
- Check service account JSON (optional but recommended)

### Features not updating
```bash
curl -X POST http://localhost:50011/refresh
```

### Clear cache
```bash
rm /etc/atd/feature_flags_cache.yaml
rm /etc/atd/announcements_cache.yaml
curl -X POST http://localhost:50011/refresh/all
```

## Performance

- **Startup**: 1-3 seconds (Firestore fetch)
- **Request Latency**: <5ms (memory cache)
- **Memory**: <50MB typical
- **Concurrency**: Single-threaded (Tornado)

## Security

- Internal service only (not externally exposed)
- No authentication (trusted Docker network)
- Read-only Firestore access required
- Service account JSON recommended but optional

## License

ATL Internal Service

## Support

ATL Team <training@arista.com>

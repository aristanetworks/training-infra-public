# Plan: Link Throughput Stats on Hover in Dynamic Topology Diagram

## Overview

Add the ability to display real-time interface statistics (throughput, errors, etc.) when hovering over a link in the Cytoscape.js-based topology diagram.

## Current Architecture

### Frontend Components
- `topology-manager.js` - Main orchestrator that initializes Cytoscape and manages components
- `event-handlers.js` - Handles mouse events including edge hover (lines 390-411)
- `status-updater.js` - WebSocket client for real-time updates (currently only handles node status)
- `cytoscape-styles.js` - Visual styling for nodes and edges

### Backend Components
- `uilanding.py` - Main Tornado web server
  - `/td-api/topology` - Returns topology data (nodes/edges)
  - `/td-ws` - WebSocket for status updates (CVP status, tasks, uptime)
- `confTopo.py` - CVP integration via `cvprac` library
- `cvpUpdater.py` - Uses `cvprac` and `rcvpapi` for CVP operations

### Data Sources for Interface Stats

**Important Finding:** `cvprac` does NOT support interface counters/telemetry. It only handles:
- Device inventory and provisioning
- Container management
- Configlet operations
- Task management

Available options for interface statistics:

1. **Direct EOS eAPI via pyeapi** (Recommended) - Query devices directly
2. **CVP Aeris REST API** (Legacy) - Internal Sysdb access via `/aeris/v1/rest/`
3. **cloudvision-python gRPC** - Resource APIs for telemetry
4. **gNMI Streaming Telemetry** - OpenConfig path subscriptions

## Implementation Plan

### Phase 1: Backend - Interface Stats API via pyeapi

**Primary Approach: Direct eAPI Query**

Use `pyeapi` to query EOS devices directly. This is the most reliable approach because:
- Works with any EOS device without CVP streaming telemetry configuration
- Returns structured JSON data immediately
- Well-documented with existing examples in Arista community

Create a new API endpoint:

```
GET /td-api/interface-stats?device=<device>&interface=<interface>
```

Response:
```json
{
  "device": "leaf1",
  "interface": "Ethernet1",
  "stats": {
    "in_octets": 123456789,
    "out_octets": 987654321,
    "in_rate_bps": 1000000,
    "out_rate_bps": 2000000,
    "in_errors": 0,
    "out_errors": 0,
    "speed": 10000000000,
    "utilization_in": 0.01,
    "utilization_out": 0.02,
    "operational_status": "up",
    "last_updated": "2025-12-05T10:30:00Z"
  }
}
```

**Implementation in `uilanding.py`:**

```python
import pyeapi

class InterfaceStatsAPIHandler(BaseHandler):
    """API endpoint for interface statistics via eAPI."""

    # Cache: {device_interface: (timestamp, data)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 10  # seconds

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write({'error': 'Authentication required'})
            return

        device = self.get_argument('device', None)
        interface = self.get_argument('interface', None)

        if not device or not interface:
            self.set_status(400)
            self.write({'error': 'device and interface parameters required'})
            return

        try:
            stats = self.get_interface_stats(device, interface)
            self.write(stats)
        except Exception as e:
            self.set_status(500)
            self.write({'error': str(e)})

    def get_interface_stats(self, device_name, interface_name):
        """Query EOS device for interface counters via eAPI."""
        cache_key = f"{device_name}:{interface_name}"

        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if time.time() - timestamp < self.CACHE_TTL:
                    return data

        # Get device IP from topology
        device_ip = self.get_device_ip(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found")

        # Connect via eAPI
        node = pyeapi.connect(
            host=device_ip,
            username=TOPO_USER,
            password=TOPO_PWD,
            transport='https',
            return_node=True
        )

        # Execute show commands
        commands = [
            f"show interfaces {interface_name}",
            f"show interfaces {interface_name} counters"
        ]
        result = node.enable(commands)

        # Parse interface data
        intf_data = result[0]['result']['interfaces'].get(interface_name, {})
        counters = intf_data.get('interfaceCounters', {})

        stats = {
            'device': device_name,
            'interface': interface_name,
            'stats': {
                'in_octets': counters.get('inOctets', 0),
                'out_octets': counters.get('outOctets', 0),
                'in_rate_bps': intf_data.get('bandwidth', 0),  # Would need rate calculation
                'out_rate_bps': intf_data.get('bandwidth', 0),
                'in_errors': counters.get('inErrors', 0),
                'out_errors': counters.get('outErrors', 0),
                'speed': intf_data.get('bandwidth', 0),
                'operational_status': intf_data.get('interfaceStatus', 'unknown'),
                'last_updated': datetime.now().isoformat()
            }
        }

        # Update cache
        with self._cache_lock:
            self._cache[cache_key] = (time.time(), stats)

        return stats

    def get_device_ip(self, device_name):
        """Look up device IP from topology data."""
        # Access MOD_YAML or topology data
        nodes = MOD_YAML.get('topology', {}).get('nodes', {})
        if device_name in nodes:
            return nodes[device_name].get('ip')
        return None
```

**EOS Commands Used:**
- `show interfaces <interface>` - Status, bandwidth, counters
- `show interfaces <interface> counters` - Detailed packet/byte counters
- `show interfaces <interface> counters rates` - Rate calculations (if available)

**Rate Calculation:**
EOS provides cumulative counters, not rates. To calculate rates:
1. Store previous counter values with timestamps
2. Calculate delta between readings
3. `rate_bps = (current_octets - prev_octets) * 8 / time_delta`

### Phase 2: Backend - Bulk Stats Endpoint

For better performance on topology load:

```
GET /td-api/interface-stats/bulk
```

Returns stats for all topology links in a single response, useful for:
- Initial page load
- Periodic background refresh (every 30 seconds)
- Reducing individual API calls

### Phase 3: Frontend - Edge Tooltip Enhancement

**Modify `event-handlers.js`:**

1. Update `showEdgeTooltip()` method (line 487) to:
   - Show loading state initially
   - Fetch interface stats from new API
   - Display throughput, utilization, errors

2. Add new method `fetchInterfaceStats(source, sourcePort, target, targetPort)`:
   - Make async fetch to `/td-api/interface-stats`
   - Handle loading/error states
   - Cache results briefly on frontend
   - Debounce requests (300ms delay before fetching)

**Enhanced Tooltip HTML:**
```html
<div class="topology-tooltip edge-tooltip">
  <div class="tooltip-header">
    <strong>Link Statistics</strong>
  </div>
  <div class="tooltip-body">
    <div class="tooltip-section">
      <span class="section-title">leaf1:Ethernet1</span>
      <div class="tooltip-row">
        <span class="tooltip-label">TX:</span>
        <span class="tooltip-value">1.2 Gbps (12%)</span>
      </div>
      <div class="tooltip-row">
        <span class="tooltip-label">RX:</span>
        <span class="tooltip-value">800 Mbps (8%)</span>
      </div>
    </div>
    <div class="tooltip-section">
      <span class="section-title">spine1:Ethernet3</span>
      <div class="tooltip-row">
        <span class="tooltip-label">TX:</span>
        <span class="tooltip-value">800 Mbps (8%)</span>
      </div>
      <div class="tooltip-row">
        <span class="tooltip-label">RX:</span>
        <span class="tooltip-value">1.2 Gbps (12%)</span>
      </div>
    </div>
    <div class="tooltip-row">
      <span class="tooltip-label">Errors:</span>
      <span class="tooltip-value status-up">None</span>
    </div>
    <div class="tooltip-footer">
      Updated: 5s ago
    </div>
  </div>
</div>
```

### Phase 4: Real-time Updates via WebSocket (Optional Enhancement)

**Extend `status-updater.js`:**

1. Add method to subscribe to interface stats for specific links
2. Server pushes stats updates when hovering (or for all visible links)
3. Update edge tooltip in real-time without re-fetching

**WebSocket Message Format:**
```json
{
  "type": "interface_stats",
  "data": {
    "leaf1-spine1-Ethernet1-Ethernet3": {
      "source_tx_bps": 1200000000,
      "source_rx_bps": 800000000,
      "target_tx_bps": 800000000,
      "target_rx_bps": 1200000000,
      "errors": 0
    }
  }
}
```

### Phase 5: Visual Link Styling Based on Utilization

**Enhance `cytoscape-styles.js`:**

Add dynamic edge styling based on utilization:
- Default (gray): No data or < 25% utilization
- Green: 25-50% utilization
- Yellow: 50-80% utilization
- Orange: 80-95% utilization
- Red: > 95% utilization or errors present

```javascript
{
  selector: 'edge.utilization-low',
  style: {
    'line-color': '#78d82c',
    'width': 2
  }
},
{
  selector: 'edge.utilization-medium',
  style: {
    'line-color': '#fbb500',
    'width': 3
  }
},
{
  selector: 'edge.utilization-high',
  style: {
    'line-color': '#ff8c00',
    'width': 3
  }
},
{
  selector: 'edge.utilization-critical',
  style: {
    'line-color': '#e30909',
    'width': 4
  }
},
{
  selector: 'edge.has-errors',
  style: {
    'line-color': '#e30909',
    'line-style': 'dashed'
  }
}
```

## Technical Considerations

### pyeapi Connection Management

```python
# Option 1: Direct connection per request
node = pyeapi.connect(host=ip, username=user, password=pwd, transport='https')

# Option 2: Connection pooling (better for performance)
# Maintain a dict of connections, reuse when possible
class EAPIConnectionPool:
    def __init__(self):
        self._connections = {}
        self._lock = threading.Lock()

    def get_connection(self, host, username, password):
        with self._lock:
            if host not in self._connections:
                self._connections[host] = pyeapi.connect(
                    host=host, username=username, password=password,
                    transport='https', return_node=True
                )
            return self._connections[host]
```

### Performance Optimization

1. **Debounce hover events** - Don't fetch on every mouseover, wait 300ms
2. **Cache aggressively** - Stats don't change rapidly, 10-30 second cache is fine
3. **Bulk fetch** - Get all link stats on page load, update periodically
4. **Progressive loading** - Show static info immediately, stats async
5. **Connection pooling** - Reuse eAPI connections to devices

### Rate Calculation Strategy

Since EOS provides cumulative counters, implement rate calculation:

```python
class RateCalculator:
    def __init__(self):
        self._previous = {}  # {key: (timestamp, counters)}

    def calculate_rate(self, key, current_counters):
        current_time = time.time()

        if key in self._previous:
            prev_time, prev_counters = self._previous[key]
            time_delta = current_time - prev_time

            if time_delta > 0:
                in_rate = (current_counters['inOctets'] - prev_counters['inOctets']) * 8 / time_delta
                out_rate = (current_counters['outOctets'] - prev_counters['outOctets']) * 8 / time_delta
            else:
                in_rate = out_rate = 0
        else:
            in_rate = out_rate = 0  # First reading, no rate available

        self._previous[key] = (current_time, current_counters)
        return {'in_rate_bps': in_rate, 'out_rate_bps': out_rate}
```

### Error Handling

- Device unreachable: Show "Device unreachable" in tooltip
- eAPI timeout: Show "Timeout - retrying..." with cached data
- Invalid interface: Show "Interface not found"
- Rate limiting: Queue requests, show cached data

## Files to Modify

1. `nested-labvm/atd-docker/uilanding/src/uilanding.py`
   - Add `InterfaceStatsAPIHandler` class
   - Add `/td-api/interface-stats` route
   - Add `pyeapi` import and connection handling
   - Optionally extend WebSocket to push interface stats

2. `nested-labvm/atd-docker/uilanding/src/html/js/topology/event-handlers.js`
   - Enhance `showEdgeTooltip()` to fetch and display stats
   - Add debouncing for hover events
   - Add `fetchInterfaceStats()` method

3. `nested-labvm/atd-docker/uilanding/src/html/js/topology/status-updater.js`
   - Add interface stats subscription capability (optional)
   - Handle `interface_stats` WebSocket messages

4. `nested-labvm/atd-docker/uilanding/src/html/js/topology/cytoscape-styles.js`
   - Add utilization-based edge styling classes

5. `nested-labvm/atd-docker/uilanding/src/html/css/topology.css`
   - Add tooltip styling for interface stats section

6. `nested-labvm/atd-docker/uilanding/Dockerfile`
   - Add `pyeapi` to requirements if not already present

## Dependencies

**Required:**
- `pyeapi` - For direct eAPI access to EOS devices

**Already Available:**
- `cvprac` - For CVP API access (not used for interface stats)
- Device credentials from `ACCESS_INFO.yaml`

**Installation:**
```dockerfile
RUN pip install pyeapi
```

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| eAPI connectivity to devices | Stats unavailable for some devices | Graceful error handling, show "unavailable" |
| High load from many simultaneous queries | Device CPU impact | Rate limiting, caching, bulk queries |
| Device credentials not available | Cannot authenticate | Use existing credentials from ACCESS_INFO.yaml |
| Large topologies (50+ links) | Slow initial load | Lazy load on hover, bulk prefetch in background |
| Network latency to devices | Slow tooltip response | Debounce, show loading state, cache aggressively |

## Testing Strategy

1. Unit tests for stats parsing and rate calculation
2. Integration tests with mock eAPI responses
3. Manual testing with real ATD topology
4. Performance testing with 20+ device topology
5. Error handling tests (device down, invalid interface, timeout)

## References

- [pyeapi Documentation](https://pyeapi.readthedocs.io/)
- [Arista Python Web2Py Examples](https://arista-python-web2py.readthedocs.io/en/latest/troubleshooting.html)
- [cvprac GitHub](https://github.com/aristanetworks/cvprac) - Confirmed no interface stats support
- [cloudvision-python](https://github.com/aristanetworks/cloudvision-python) - Alternative gRPC approach

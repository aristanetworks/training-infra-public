# Packet Capture Feature Plan

## Overview

Implement a browser-based, real-time packet capture and analysis feature for ATL topology diagrams. Users will be able to capture traffic on KVM/OVS bridges connecting network devices and view it in a Wireshark-like interface.

## Current Architecture Understanding

### KVM/OVS Bridge Infrastructure
- Bridges created via `kvm-topo-builder.py` using OpenVSwitch (OVS)
- Bridge naming convention: `{device1-short}{port1}-{device2-short}{port2}` (e.g., `sp1Et1-le1Et1`)
- Bridges created with `ovs-vsctl add-br` commands
- Bridge list stored in `OVS_BRIDGES` global array during topology build
- Scripts generated: `{topo}-ovs-create.sh` and `{topo}-ovs-delete.sh`

### Existing Web Architecture
- **uilanding.py**: Tornado web server with REST APIs and WebSocket handlers
- **WebSocket**: `topoDataHandler` at `/td-ws` for real-time status updates
- **Topology API**: `/td-api/topology` returns device/edge data with port information
- **Interface Stats API**: `/td-api/interface-stats` queries devices via eAPI

## Feature Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Browser (Frontend)                            │
├─────────────────────────────────────────────────────────────────────┤
│  Topology Diagram    │    Packet Capture Panel                      │
│  ┌─────────────────┐ │    ┌─────────────────────────────────────┐   │
│  │  Click edge to  │ │    │  Capture Controls                   │   │
│  │  start capture  │ │    │  [Start] [Stop] [Clear] [Download]  │   │
│  │       ↓         │ │    ├─────────────────────────────────────┤   │
│  │  Edge becomes   │ │    │  Packet List (virtual scroll)       │   │
│  │  highlighted    │ │    │  ┌─────┬─────┬────┬────┬─────────┐  │   │
│  │  when capturing │ │    │  │ No. │Time │Src │Dst │Protocol │  │   │
│  └─────────────────┘ │    │  ├─────┼─────┼────┼────┼─────────┤  │   │
│                      │    │  │  1  │0.00 │... │... │  TCP    │  │   │
│                      │    │  │  2  │0.01 │... │... │  UDP    │  │   │
│                      │    │  └─────┴─────┴────┴────┴─────────┘  │   │
│                      │    ├─────────────────────────────────────┤   │
│                      │    │  Packet Detail (collapsible)        │   │
│                      │    │  ├ Ethernet II                      │   │
│                      │    │  ├ Internet Protocol                │   │
│                      │    │  └ TCP/UDP                          │   │
│                      │    ├─────────────────────────────────────┤   │
│                      │    │  Hex Dump (optional)                │   │
│                      │    └─────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
           │                           │
           │  WebSocket (td-ws)        │  WebSocket (new: /capture-ws)
           ▼                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Backend (uilanding.py)                            │
├─────────────────────────────────────────────────────────────────────┤
│  CaptureWebSocketHandler                                             │
│  - Manages capture sessions per client                               │
│  - Spawns/kills tcpdump processes                                    │
│  - Parses tcpdump output to JSON                                     │
│  - Streams packets to connected clients                              │
├─────────────────────────────────────────────────────────────────────┤
│  CaptureAPIHandler                                                   │
│  - GET /td-api/capture/bridges - List available bridges              │
│  - GET /td-api/capture/status - Get active captures                  │
│  - POST /td-api/capture/start - Start capture (REST alternative)     │
│  - POST /td-api/capture/stop - Stop capture                          │
│  - GET /td-api/capture/download - Download pcap file                 │
└─────────────────────────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Host System                                       │
├─────────────────────────────────────────────────────────────────────┤
│  tcpdump / ovs-tcpdump                                               │
│  - Captures on OVS bridge interfaces                                 │
│  - Line-buffered output for real-time streaming                      │
│  - Writes pcap files for download                                    │
└─────────────────────────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Backend Capture Infrastructure

#### 1.1 Capture Manager Service
Create a new service to manage packet captures.

**File:** `nested-labvm/atd-docker/uilanding/src/capture_manager.py`

```python
# Key components:
class CaptureSession:
    """Represents a single capture session."""
    - bridge_name: str
    - process: subprocess.Popen
    - pcap_file: str
    - start_time: datetime
    - packet_count: int
    - client_id: str

class CaptureManager:
    """Manages all active capture sessions."""
    - active_sessions: Dict[str, CaptureSession]
    - max_sessions: int (limit concurrent captures, e.g., 5)
    - max_duration: int (auto-stop after N seconds, e.g., 300)
    - max_packets: int (auto-stop after N packets, e.g., 10000)

    Methods:
    - start_capture(bridge_name, client_id, filters={}) -> session_id
    - stop_capture(session_id)
    - get_bridge_list() -> List[Dict]  # bridge name + connected devices
    - cleanup_stale_sessions()
```

#### 1.2 Bridge Discovery
Add API to discover available OVS bridges and map them to topology edges.

**Approach:**
1. Run `ovs-vsctl list-br` to get all bridges
2. Parse bridge names to extract device/port info
3. Cross-reference with topology data to provide edge context

#### 1.3 tcpdump Integration

**Two capture modes:**

1. **Live streaming mode** (WebSocket):
   ```bash
   tcpdump -i {bridge} -l -nn -tttt -e -v 2>/dev/null
   ```
   - `-l`: Line-buffered for real-time
   - `-nn`: Don't resolve names
   - `-tttt`: Readable timestamps
   - `-e`: Show Ethernet header
   - `-v`: Verbose output

2. **PCAP file mode** (for download):
   ```bash
   tcpdump -i {bridge} -w /tmp/capture_{session_id}.pcap -c {max_packets}
   ```

**Using ovs-tcpdump (preferred):**
```bash
ovs-tcpdump -i {bridge} -l -nn -tttt -e -v
```
This automatically handles OVS port mirroring.

### Phase 2: WebSocket Capture Handler

#### 2.1 New WebSocket Endpoint
**File:** `nested-labvm/atd-docker/uilanding/src/uilanding.py`

Add new handler:
```python
class CaptureWebSocketHandler(tornado.websocket.WebSocketHandler):
    """WebSocket handler for real-time packet streaming."""

    # Message types:
    # Client -> Server:
    #   { "type": "start", "bridge": "sp1Et1-le1Et1", "filters": {...} }
    #   { "type": "stop" }
    #   { "type": "clear" }

    # Server -> Client:
    #   { "type": "packet", "data": { packet_fields } }
    #   { "type": "status", "capturing": true, "count": 100 }
    #   { "type": "error", "message": "..." }
```

#### 2.2 Packet Parser
Parse tcpdump output into structured JSON.

```python
class PacketParser:
    """Parse tcpdump output into structured packet data."""

    def parse_line(self, line: str) -> dict:
        """Parse a single tcpdump output line."""
        return {
            'number': int,
            'timestamp': str,
            'src_mac': str,
            'dst_mac': str,
            'ethertype': str,
            'src_ip': str,
            'dst_ip': str,
            'protocol': str,
            'src_port': int,
            'dst_port': int,
            'length': int,
            'info': str,  # Protocol-specific info
            'raw': str    # Original line for debugging
        }
```

### Phase 3: Frontend UI Components

#### 3.1 Capture Panel Component
**File:** `nested-labvm/atd-docker/uilanding/src/html/js/topology/capture-panel.js`

```javascript
export class CapturePanel {
    constructor(container, topologyManager) {...}

    // State
    - isCapturing: boolean
    - currentBridge: string
    - packets: VirtualScrollList  // For performance with large captures
    - selectedPacket: object

    // Methods
    - show(edgeData)  // Called when edge is clicked
    - hide()
    - startCapture()
    - stopCapture()
    - clearPackets()
    - downloadPcap()
    - applyFilter(filter)
}
```

#### 3.2 Virtual Scroll for Packet List
Essential for performance with thousands of packets.

Options:
- Custom implementation (lightweight)
- Use existing library like `virtual-scroller`

#### 3.3 Packet Detail View
Collapsible tree view showing protocol layers.

```
├─ Ethernet II
│  ├ Src: 00:1c:73:00:00:01
│  ├ Dst: 00:1c:73:00:00:02
│  └ Type: IPv4 (0x0800)
├─ Internet Protocol Version 4
│  ├ Src: 192.168.1.1
│  ├ Dst: 192.168.1.2
│  ├ TTL: 64
│  └ Protocol: TCP (6)
└─ Transmission Control Protocol
   ├ Src Port: 443
   ├ Dst Port: 52341
   ├ Flags: [ACK]
   └ Seq: 1234
```

#### 3.4 Topology Integration
- Add "Capture" option to edge context menu
- Highlight edge while capturing (animated dashed line)
- Show packet count badge on edge

### Phase 4: VXLAN/Tunnel Decoding

#### 4.1 VXLAN Header Parsing
For VXLAN-encapsulated traffic (UDP port 4789):

```
Packet Display:
├─ Ethernet II (Outer)
│  └ Type: IPv4
├─ IP (Outer)
│  ├ Src: 10.0.0.1 (VTEP)
│  └ Dst: 10.0.0.2 (VTEP)
├─ UDP
│  └ Dst Port: 4789 (VXLAN)
├─ VXLAN
│  └ VNI: 10010
└─ [Inner Frame]
   ├─ Ethernet II (Inner)
   │  └ Type: IPv4
   ├─ IP (Inner)
   │  ├ Src: 192.168.10.1
   │  └ Dst: 192.168.10.2
   └─ TCP/UDP (Inner)
```

#### 4.2 GRE Decoding
Similar approach for GRE-encapsulated traffic.

#### 4.3 Protocol Detection
```python
def detect_tunnel(packet):
    if packet.udp and packet.udp.dst_port == 4789:
        return 'vxlan'
    if packet.ip.protocol == 47:  # GRE
        return 'gre'
    return None
```

### Phase 5: Display Filters

#### 5.1 BPF Filter Support
Pass through to tcpdump for efficient kernel-level filtering.

```
Common filters:
- tcp port 80
- host 192.168.1.1
- arp
- icmp
- vlan
- tcp[tcpflags] & tcp-syn != 0
```

#### 5.2 Display Filters (Post-capture)
Filter packets already captured (client-side).

```javascript
// Filter syntax (simplified Wireshark-like)
"ip.src == 192.168.1.1"
"tcp.port == 80"
"eth.type == 0x0800"
```

### Phase 6: PCAP Export

#### 6.1 Download Captured Packets
- Button to download as `.pcap` file
- tcpdump writes to file in parallel with streaming
- Cleanup old pcap files after timeout

#### 6.2 Import to Wireshark
- Standard pcap format compatible with Wireshark
- Include link to open in desktop Wireshark

## Security Considerations

### 5.1 Authentication
- All capture endpoints require authenticated session
- Reuse existing login cookie authentication

### 5.2 Resource Limits
- Max concurrent captures per user: 2
- Max capture duration: 5 minutes (configurable)
- Max packets per session: 10,000
- Max stored pcap size: 50MB
- Auto-cleanup of old pcap files

### 5.3 Container Permissions
- Docker container needs `NET_ADMIN` capability
- May need `NET_RAW` for raw socket access
- Consider running capture daemon as separate privileged container

### 5.4 Data Sensitivity
- Captured packets may contain sensitive data
- Clear warning to users
- No persistent storage of captures (memory only + temp files)
- Pcap files deleted after download or timeout

## Docker Configuration Changes

### 5.1 Dockerfile Updates
```dockerfile
# Install tcpdump and ovs tools
RUN apt-get update && apt-get install -y \
    tcpdump \
    openvswitch-common

# Need libpcap for packet parsing
RUN pip install dpkt scapy
```

### 5.2 docker-compose.yml Updates
```yaml
uilanding:
  cap_add:
    - NET_ADMIN
    - NET_RAW
  volumes:
    - /var/run/openvswitch:/var/run/openvswitch:ro
  environment:
    - CAPTURE_ENABLED=true
    - CAPTURE_MAX_DURATION=300
    - CAPTURE_MAX_PACKETS=10000
```

## API Specification

### REST Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/td-api/capture/bridges` | List available bridges with edge mapping |
| GET | `/td-api/capture/status` | Get active capture sessions |
| POST | `/td-api/capture/start` | Start capture on a bridge |
| POST | `/td-api/capture/stop` | Stop active capture |
| GET | `/td-api/capture/download/{session_id}` | Download pcap file |

### WebSocket Messages

**Client to Server:**
```json
{ "type": "start", "bridge": "sp1Et1-le1Et1", "filter": "tcp port 80" }
{ "type": "stop" }
{ "type": "clear" }
{ "type": "ping" }
```

**Server to Client:**
```json
{ "type": "started", "session_id": "abc123", "bridge": "sp1Et1-le1Et1" }
{ "type": "packet", "number": 1, "timestamp": "2025-01-15 10:30:45.123456",
  "src_mac": "00:1c:73:00:00:01", "dst_mac": "00:1c:73:00:00:02",
  "src_ip": "192.168.1.1", "dst_ip": "192.168.1.2",
  "protocol": "TCP", "src_port": 443, "dst_port": 52341,
  "length": 64, "info": "[ACK] Seq=1234 Ack=5678 Win=65535" }
{ "type": "stopped", "reason": "user", "packet_count": 1234 }
{ "type": "error", "message": "Bridge not found" }
{ "type": "status", "capturing": true, "packet_count": 500, "duration": 30 }
```

## File Structure

```
nested-labvm/atd-docker/uilanding/src/
├── capture_manager.py      # NEW: Capture session management
├── packet_parser.py        # NEW: tcpdump output parser
├── uilanding.py            # MODIFY: Add capture endpoints
├── html/
│   ├── js/
│   │   └── topology/
│   │       ├── capture-panel.js      # NEW: Capture UI component
│   │       ├── packet-list.js        # NEW: Virtual scroll packet list
│   │       ├── packet-detail.js      # NEW: Packet detail tree view
│   │       ├── event-handlers.js     # MODIFY: Add edge capture action
│   │       └── topology-manager.js   # MODIFY: Integrate capture panel
│   └── css/
│       └── capture.css               # NEW: Capture panel styles
└── Dockerfile                         # MODIFY: Add tcpdump, capabilities
```

## Testing Plan

### Unit Tests
- Packet parser with sample tcpdump output
- Bridge name parsing
- Filter validation

### Integration Tests
- WebSocket capture start/stop
- Packet streaming
- Pcap download

### Manual Testing
- Capture on actual topology bridges
- Verify packet accuracy with Wireshark comparison
- Performance with high packet rates
- Browser memory usage with large captures

## Estimated Effort

| Phase | Component | Effort |
|-------|-----------|--------|
| 1 | Backend Capture Infrastructure | 3-4 days |
| 2 | WebSocket Handler | 2 days |
| 3 | Frontend UI (basic) | 3-4 days |
| 4 | VXLAN/Tunnel Decoding | 1-2 days |
| 5 | Display Filters | 2 days |
| 6 | PCAP Export | 1 day |
| - | Testing & Polish | 2-3 days |
| **Total** | | **14-18 days** |

## Alternative Approaches Considered

### 1. CloudShark Integration
- Pros: Full Wireshark-like analysis, existing solution
- Cons: External dependency, licensing, complexity

### 2. Wireshark's sharkd Backend
- Pros: Full protocol dissection, uses Wireshark's parsers
- Cons: Requires running sharkd daemon, heavier weight

### 3. Separate Capture Container
- Pros: Better security isolation, simpler permissions
- Cons: More complex architecture, inter-container communication

## Design Decisions

1. **Protocol Depth**: Basic dissection only (Ethernet, IP, TCP/UDP, ICMP, ARP)
   - Sufficient for lab troubleshooting
   - Keeps implementation lightweight
   - Users can download pcap for deep analysis in Wireshark

2. **Multi-bridge Capture**: Single bridge at a time, but bi-directional
   - Captures traffic in both directions on the selected link
   - Simplifies UI and resource management
   - User can switch between links as needed

3. **Capture Persistence**: Real-time only, no persistence
   - Captures exist only while running
   - Cleared when stopped or page navigated away
   - Download pcap if preservation needed

4. **Performance Target**: Low traffic expected
   - Lab environments have minimal background traffic
   - No need for aggressive optimization
   - Standard virtual scroll sufficient for packet list

5. **Tunnel Decoding**: Yes, decode VXLAN/GRE when possible
   - Important for EVPN/VXLAN labs
   - Show outer and inner headers
   - Parse VXLAN VNI and inner Ethernet frame

## References

- [ovs-tcpdump documentation](https://docs.openvswitch.org/en/latest/ref/ovs-tcpdump.8/)
- [Real-time Packet Capture with WebSocket](https://autonetmate.com/software/real-time-packet-capture-of-linux-bridge-ping-namespaces-and-display-with-websocket/)
- [Webshark - pcap visualization](https://github.com/FireyFly/webshark)
- [PacketPeek - HTML5 pcap viewer](https://github.com/tinpotnick/packetpeek)
- [Juniper Mist WebSocket Packet Streaming](https://www.juniper.net/documentation/us/en/software/mist/automation-integration/topics/example/stream-device-pcap-with-websocket.html)

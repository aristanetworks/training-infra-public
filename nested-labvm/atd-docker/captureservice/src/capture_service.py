#!/usr/bin/env python3
"""
Capture Service for ATL Platform

Dedicated service that runs with host network mode to access OVS bridges.
Provides a simple HTTP/WebSocket API for the uilanding container to connect.

Architecture:
- Runs with network_mode: host to access OVS bridges
- Exposes internal API on port 8089 (localhost only)
- uilanding proxies WebSocket connections to this service
- Includes bridge discovery and edge mapping logic
"""

import asyncio
import fcntl
import json
import os
import re
import subprocess
import threading
import time
import urllib.request
import urllib.error
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List

import tornado.web
import tornado.websocket
import tornado.ioloop
from tornado.options import define, options

# Configuration
define("port", default=8089, help="Port to listen on")
define("allowed_hosts", default="127.0.0.1,localhost", help="Allowed hosts for API access")

# Global state
TOPOLOGY_DATA_PATH = "/etc/atd/ACCESS_INFO.yaml"
TOPOLOGY_FILE_PATH = "/opt/atd/topologies/topo_build.yml"

# Nodebuilder API endpoint for bridge information
# Captureservice runs with host network, so nodebuilder is at localhost
NODEBUILDER_BRIDGES_URL = "http://localhost:8090/bridges"
NODEBUILDER_TIMEOUT = 5  # seconds


@dataclass
class CaptureSession:
    """Represents a single packet capture session."""
    session_id: str
    bridge_name: str
    client_id: str
    websocket: Optional[tornado.websocket.WebSocketHandler] = None
    process: Optional[subprocess.Popen] = None
    start_time: datetime = field(default_factory=datetime.now)
    packet_count: int = 0
    is_active: bool = True
    bpf_filter: str = ""
    reader_thread: Optional[threading.Thread] = None

    def elapsed_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()


class TsharkParser:
    """Parse tshark JSON (ek format) output into structured packet data."""

    # Protocol priority for display (highest priority protocol shown)
    PROTOCOL_PRIORITY = [
        'bgp', 'ospf', 'isis', 'eigrp', 'rip', 'bfd',  # Routing
        'evpn', 'vxlan', 'mpls', 'gre',                 # Overlay/MPLS
        'lacp', 'lldp', 'stp', 'rstp',                  # L2 protocols
        'dhcp', 'dhcpv6', 'dns', 'ntp',                 # Services
        'icmp', 'icmpv6', 'arp',                        # L3 utilities
        'tcp', 'udp',                                    # Transport
        'ip', 'ipv6',                                    # Network
        'eth'                                            # Data link
    ]

    # Human-readable protocol names
    PROTOCOL_NAMES = {
        'bgp': 'BGP',
        'ospf': 'OSPF',
        'isis': 'IS-IS',
        'eigrp': 'EIGRP',
        'rip': 'RIP',
        'bfd': 'BFD',
        'evpn': 'EVPN',
        'vxlan': 'VXLAN',
        'mpls': 'MPLS',
        'gre': 'GRE',
        'lacp': 'LACP',
        'lldp': 'LLDP',
        'stp': 'STP',
        'rstp': 'RSTP',
        'dhcp': 'DHCP',
        'dhcpv6': 'DHCPv6',
        'dns': 'DNS',
        'ntp': 'NTP',
        'icmp': 'ICMP',
        'icmpv6': 'ICMPv6',
        'arp': 'ARP',
        'tcp': 'TCP',
        'udp': 'UDP',
        'ip': 'IPv4',
        'ipv6': 'IPv6',
        'eth': 'Ethernet',
    }

    # BGP message type names
    BGP_MSG_TYPES = {
        '1': 'OPEN',
        '2': 'UPDATE',
        '3': 'NOTIFICATION',
        '4': 'KEEPALIVE',
        '5': 'ROUTE-REFRESH',
    }

    # OSPF message type names
    OSPF_MSG_TYPES = {
        '1': 'Hello',
        '2': 'DB Description',
        '3': 'LS Request',
        '4': 'LS Update',
        '5': 'LS Acknowledge',
    }

    # ICMP type names
    ICMP_TYPES = {
        '0': 'Echo Reply',
        '3': 'Destination Unreachable',
        '5': 'Redirect',
        '8': 'Echo Request',
        '11': 'Time Exceeded',
    }

    def parse_line(self, line: str, packet_number: int) -> Optional[Dict]:
        """Parse a single tshark JSON line (ek format)."""
        if not line or not line.strip():
            return None

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return None

        # tshark ek format has 'index' lines and 'layers' lines
        # We only want lines with 'layers'
        if 'layers' not in data:
            return None

        layers = data['layers']

        # Build packet structure
        packet = {
            'number': packet_number,
            'timestamp': '',
            'src_mac': '',
            'dst_mac': '',
            'src_ip': '',
            'dst_ip': '',
            'src_port': None,
            'dst_port': None,
            'protocol': 'Unknown',
            'length': 0,
            'info': '',
            'layers': {}  # Full protocol layer details
        }

        try:
            # Extract frame info
            if 'frame' in layers:
                frame = layers['frame']
                # Timestamp
                if 'frame_frame_time_epoch' in frame:
                    epoch = float(frame['frame_frame_time_epoch'][0])
                    from datetime import datetime
                    packet['timestamp'] = datetime.fromtimestamp(epoch).strftime('%Y-%m-%d %H:%M:%S.%f')
                # Length
                if 'frame_frame_len' in frame:
                    packet['length'] = int(frame['frame_frame_len'][0])

            # Extract Ethernet
            if 'eth' in layers:
                eth = layers['eth']
                packet['src_mac'] = eth.get('eth_eth_src', [''])[0]
                packet['dst_mac'] = eth.get('eth_eth_dst', [''])[0]
                packet['layers']['eth'] = self._clean_layer(eth, 'eth')

            # Extract IP
            if 'ip' in layers:
                ip = layers['ip']
                packet['src_ip'] = ip.get('ip_ip_src', [''])[0]
                packet['dst_ip'] = ip.get('ip_ip_dst', [''])[0]
                packet['layers']['ip'] = self._clean_layer(ip, 'ip')
            elif 'ipv6' in layers:
                ipv6 = layers['ipv6']
                packet['src_ip'] = ipv6.get('ipv6_ipv6_src', [''])[0]
                packet['dst_ip'] = ipv6.get('ipv6_ipv6_dst', [''])[0]
                packet['layers']['ipv6'] = self._clean_layer(ipv6, 'ipv6')

            # Extract TCP/UDP ports
            if 'tcp' in layers:
                tcp = layers['tcp']
                packet['src_port'] = int(tcp.get('tcp_tcp_srcport', ['0'])[0])
                packet['dst_port'] = int(tcp.get('tcp_tcp_dstport', ['0'])[0])
                packet['layers']['tcp'] = self._clean_layer(tcp, 'tcp')
            elif 'udp' in layers:
                udp = layers['udp']
                packet['src_port'] = int(udp.get('udp_udp_srcport', ['0'])[0])
                packet['dst_port'] = int(udp.get('udp_udp_dstport', ['0'])[0])
                packet['layers']['udp'] = self._clean_layer(udp, 'udp')

            # Determine highest-priority protocol and extract its layer
            detected_protocol = self._detect_protocol(layers)
            packet['protocol'] = self.PROTOCOL_NAMES.get(detected_protocol, detected_protocol.upper())

            # Extract protocol-specific layers
            for proto in self.PROTOCOL_PRIORITY:
                if proto in layers and proto not in packet['layers']:
                    packet['layers'][proto] = self._clean_layer(layers[proto], proto)

            # Build info string based on protocol
            packet['info'] = self._build_info(packet, layers, detected_protocol)

            return packet

        except Exception as e:
            print(f"[TsharkParser] Parse error: {e}")
            packet['info'] = f'Parse error: {str(e)[:50]}'
            return packet

    def _detect_protocol(self, layers: Dict) -> str:
        """Detect the highest-priority protocol present in the packet."""
        for proto in self.PROTOCOL_PRIORITY:
            if proto in layers:
                return proto
        return 'eth'

    def _clean_layer(self, layer_data: Dict, proto: str) -> Dict:
        """Clean layer data by removing prefix and simplifying structure."""
        cleaned = {}
        prefix = f'{proto}_{proto}_'

        for key, value in layer_data.items():
            # Remove the protocol prefix (e.g., 'bgp_bgp_type' -> 'type')
            clean_key = key
            if key.startswith(prefix):
                clean_key = key[len(prefix):]
            elif key.startswith(f'{proto}_'):
                clean_key = key[len(proto) + 1:]

            # Flatten single-item lists
            if isinstance(value, list) and len(value) == 1:
                cleaned[clean_key] = value[0]
            else:
                cleaned[clean_key] = value

        return cleaned

    def _build_info(self, packet: Dict, layers: Dict, protocol: str) -> str:
        """Build human-readable info string for the packet."""
        try:
            # BGP
            if protocol == 'bgp' and 'bgp' in layers:
                bgp = layers['bgp']
                msg_type = bgp.get('bgp_bgp_type', [''])[0]
                type_name = self.BGP_MSG_TYPES.get(msg_type, f'Type {msg_type}')
                info = f'BGP {type_name}'

                # Add details for UPDATE messages
                if msg_type == '2':
                    if 'bgp_bgp_update_path_attribute_origin' in bgp:
                        info += ' (with path attributes)'
                    withdrawn = bgp.get('bgp_bgp_update_withdrawn_routes_length', ['0'])[0]
                    if withdrawn != '0':
                        info += f' [withdrawn: {withdrawn}]'
                return info

            # OSPF
            if protocol == 'ospf' and 'ospf' in layers:
                ospf = layers['ospf']
                msg_type = ospf.get('ospf_ospf_msg', [''])[0]
                type_name = self.OSPF_MSG_TYPES.get(msg_type, f'Type {msg_type}')
                router_id = ospf.get('ospf_ospf_srcrouter', [''])[0]
                area = ospf.get('ospf_ospf_area_id', [''])[0]
                info = f'OSPF {type_name}'
                if router_id:
                    info += f' Router:{router_id}'
                if area:
                    info += f' Area:{area}'
                return info

            # IS-IS
            if protocol == 'isis' and 'isis' in layers:
                isis = layers['isis']
                pdu_type = isis.get('isis_isis_type', [''])[0]
                sys_id = isis.get('isis_isis_system_id', [''])[0]
                info = f'IS-IS PDU:{pdu_type}'
                if sys_id:
                    info += f' SysID:{sys_id}'
                return info

            # VXLAN
            if protocol == 'vxlan' and 'vxlan' in layers:
                vxlan = layers['vxlan']
                vni = vxlan.get('vxlan_vxlan_vni', [''])[0]
                info = f'VXLAN VNI:{vni}'
                # Check for inner protocols
                if 'eth' in layers:
                    # There might be inner frame info
                    pass
                return info

            # EVPN
            if protocol == 'evpn' and 'evpn' in layers:
                evpn = layers['evpn']
                route_type = evpn.get('evpn_evpn_route_type', [''])[0]
                info = f'EVPN Route-Type:{route_type}'
                return info

            # LLDP
            if protocol == 'lldp' and 'lldp' in layers:
                lldp = layers['lldp']
                chassis = lldp.get('lldp_lldp_chassis_id', [''])[0]
                port = lldp.get('lldp_lldp_port_id', [''])[0]
                info = 'LLDP'
                if chassis:
                    info += f' Chassis:{chassis[:20]}'
                if port:
                    info += f' Port:{port[:15]}'
                return info

            # LACP
            if protocol == 'lacp' and 'lacp' in layers:
                lacp = layers['lacp']
                info = 'LACP'
                actor_port = lacp.get('lacp_lacp_actor_port', [''])[0]
                if actor_port:
                    info += f' ActorPort:{actor_port}'
                return info

            # STP/RSTP
            if protocol in ('stp', 'rstp') and protocol in layers:
                stp = layers[protocol]
                root_id = stp.get(f'{protocol}_{protocol}_root_identifier', [''])[0]
                info = protocol.upper()
                if root_id:
                    info += f' Root:{root_id[:20]}'
                return info

            # ICMP
            if protocol == 'icmp' and 'icmp' in layers:
                icmp = layers['icmp']
                icmp_type = icmp.get('icmp_icmp_type', [''])[0]
                type_name = self.ICMP_TYPES.get(icmp_type, f'Type {icmp_type}')
                seq = icmp.get('icmp_icmp_seq', [''])[0]
                info = f'ICMP {type_name}'
                if seq:
                    info += f' seq={seq}'
                return info

            # ARP
            if protocol == 'arp' and 'arp' in layers:
                arp = layers['arp']
                opcode = arp.get('arp_arp_opcode', [''])[0]
                op_name = 'Request' if opcode == '1' else 'Reply' if opcode == '2' else f'Op:{opcode}'
                src_ip = arp.get('arp_arp_src_proto_ipv4', [''])[0]
                dst_ip = arp.get('arp_arp_dst_proto_ipv4', [''])[0]
                info = f'ARP {op_name}'
                if opcode == '1' and dst_ip:
                    info += f' Who has {dst_ip}?'
                elif opcode == '2' and src_ip:
                    info += f' {src_ip} is at ...'
                return info

            # TCP
            if protocol == 'tcp' and 'tcp' in layers:
                tcp = layers['tcp']
                flags = tcp.get('tcp_tcp_flags_str', [''])[0]
                if not flags:
                    # Build flags from individual flag fields
                    flag_parts = []
                    if tcp.get('tcp_tcp_flags_syn', ['0'])[0] == '1':
                        flag_parts.append('SYN')
                    if tcp.get('tcp_tcp_flags_ack', ['0'])[0] == '1':
                        flag_parts.append('ACK')
                    if tcp.get('tcp_tcp_flags_fin', ['0'])[0] == '1':
                        flag_parts.append('FIN')
                    if tcp.get('tcp_tcp_flags_rst', ['0'])[0] == '1':
                        flag_parts.append('RST')
                    if tcp.get('tcp_tcp_flags_push', ['0'])[0] == '1':
                        flag_parts.append('PSH')
                    flags = ','.join(flag_parts) if flag_parts else ''
                src_port = packet.get('src_port', '')
                dst_port = packet.get('dst_port', '')
                info = f'{src_port} → {dst_port}'
                if flags:
                    info += f' [{flags}]'
                seq = tcp.get('tcp_tcp_seq', [''])[0]
                if seq:
                    info += f' Seq={seq}'
                return info

            # UDP
            if protocol == 'udp':
                src_port = packet.get('src_port', '')
                dst_port = packet.get('dst_port', '')
                return f'UDP {src_port} → {dst_port}'

            # Default: show addresses
            if packet['src_ip'] and packet['dst_ip']:
                return f"{packet['src_ip']} → {packet['dst_ip']}"
            elif packet['src_mac'] and packet['dst_mac']:
                return f"{packet['src_mac']} → {packet['dst_mac']}"

            return ''

        except Exception as e:
            return f'{protocol.upper()}'


# Keep old parser as fallback
class PacketParser:
    """Legacy tcpdump parser - kept as fallback."""

    def parse_line(self, line: str, packet_number: int) -> Optional[Dict]:
        """Parse a single tcpdump output line."""
        if not line or line.startswith('tcpdump:') or 'listening on' in line:
            return None
        if not re.match(r'^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+', line):
            return None

        packet = {
            'number': packet_number,
            'timestamp': '',
            'src_mac': '',
            'dst_mac': '',
            'src_ip': '',
            'dst_ip': '',
            'src_port': None,
            'dst_port': None,
            'protocol': 'Unknown',
            'length': 0,
            'info': line[:100] if len(line) > 100 else line,
            'layers': {}
        }

        ts_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)', line)
        if ts_match:
            packet['timestamp'] = ts_match.group(1)

        return packet


class CaptureManager:
    """Manages packet capture sessions."""

    MAX_SESSIONS_PER_USER = 1
    MAX_TOTAL_SESSIONS = 5
    MAX_DURATION_SECONDS = 300
    MAX_PACKETS = 10000
    SESSION_CLEANUP_DELAY = 60  # Seconds after stop before removing from dict
    MAX_BPF_NESTING = 10  # Maximum parenthesis nesting depth
    CONNECTION_RATE_LIMIT = 5  # Max connections per client per minute

    def __init__(self):
        self.sessions: Dict[str, CaptureSession] = {}
        self._lock = threading.Lock()
        self._running = True
        self._cleanup_task = None
        self._bridge_cache: Optional[List[Dict]] = None
        self._bridge_cache_time: float = 0
        self._bridge_cache_ttl = 30  # seconds
        # Rate limiting: {client_id: [timestamps]}
        self._connection_attempts: Dict[str, List[float]] = {}

    def start_cleanup_task(self, ioloop):
        """Start periodic cleanup task."""
        async def cleanup():
            while self._running:
                try:
                    self._cleanup_stale_sessions()
                    self._cleanup_old_sessions()
                    self._cleanup_rate_limit_entries()
                except Exception as e:
                    print(f"[CaptureManager] Cleanup error: {e}")
                await asyncio.sleep(10)

        self._cleanup_task = ioloop.asyncio_loop.create_task(cleanup())

    def _cleanup_stale_sessions(self):
        """Stop sessions exceeding limits."""
        with self._lock:
            stale = []
            for sid, session in list(self.sessions.items()):
                if session.is_active:
                    if session.elapsed_seconds() > self.MAX_DURATION_SECONDS:
                        stale.append(sid)
                    elif session.packet_count >= self.MAX_PACKETS:
                        stale.append(sid)

            for sid in stale:
                self._stop_session_unsafe(sid, "limit")

    def _cleanup_old_sessions(self):
        """Remove stopped sessions from dictionary after delay."""
        with self._lock:
            to_remove = []
            now = datetime.now()
            for sid, session in list(self.sessions.items()):
                if not session.is_active:
                    # Calculate time since session stopped (approximate)
                    stopped_duration = session.elapsed_seconds() - self.MAX_DURATION_SECONDS
                    if stopped_duration > self.SESSION_CLEANUP_DELAY or session.elapsed_seconds() > self.MAX_DURATION_SECONDS + self.SESSION_CLEANUP_DELAY:
                        to_remove.append(sid)

            for sid in to_remove:
                session = self.sessions.pop(sid, None)
                if session:
                    # Join reader thread with timeout
                    if session.reader_thread and session.reader_thread.is_alive():
                        session.reader_thread.join(timeout=1.0)
                    print(f"[CaptureManager] Removed session {sid} from memory")

    def _cleanup_rate_limit_entries(self):
        """Clean up old rate limit entries."""
        now = time.time()
        cutoff = now - 60  # Keep entries from last minute
        with self._lock:
            for client_id in list(self._connection_attempts.keys()):
                self._connection_attempts[client_id] = [
                    ts for ts in self._connection_attempts[client_id]
                    if ts > cutoff
                ]
                if not self._connection_attempts[client_id]:
                    del self._connection_attempts[client_id]

    def _check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limit. Returns True if allowed."""
        now = time.time()
        cutoff = now - 60

        with self._lock:
            if client_id not in self._connection_attempts:
                self._connection_attempts[client_id] = []

            # Clean old entries
            self._connection_attempts[client_id] = [
                ts for ts in self._connection_attempts[client_id]
                if ts > cutoff
            ]

            # Check limit
            if len(self._connection_attempts[client_id]) >= self.CONNECTION_RATE_LIMIT:
                return False

            # Record this attempt
            self._connection_attempts[client_id].append(now)
            return True

    def start_capture(
        self,
        bridge_name: str,
        client_id: str,
        websocket: tornado.websocket.WebSocketHandler,
        bpf_filter: str = ""
    ) -> Dict:
        """Start a capture session."""
        # Check rate limit BEFORE acquiring lock
        if not self._check_rate_limit(client_id):
            return {"error": "Rate limit exceeded. Please wait before starting new captures."}

        with self._lock:
            # Check limits
            user_count = sum(1 for s in self.sessions.values()
                           if s.client_id == client_id and s.is_active)
            if user_count >= self.MAX_SESSIONS_PER_USER:
                return {"error": "Maximum concurrent captures reached"}

            active_count = sum(1 for s in self.sessions.values() if s.is_active)
            if active_count >= self.MAX_TOTAL_SESSIONS:
                return {"error": "System capture limit reached"}

            # Validate bridge name (basic format check)
            if not self._validate_bridge_name(bridge_name):
                return {"error": "Invalid bridge name format"}

            # Verify bridge exists on the system
            if not self._bridge_exists(bridge_name):
                return {"error": f"Bridge '{bridge_name}' not found"}

            # Validate display filter syntax (Wireshark syntax like "lldp", "bgp", etc.)
            filter_error = self._validate_display_filter(bpf_filter)
            if filter_error:
                return {"error": filter_error}

            # Dry-run tshark to validate display filter is recognized
            if bpf_filter:
                filter_test_error = self._test_display_filter(bridge_name, bpf_filter)
                if filter_test_error:
                    return {"error": f"Invalid filter: {filter_test_error}"}

            session_id = str(uuid.uuid4())[:8]

            # Get the ports attached to this bridge - we need to capture on a port
            # because OVS bridges don't see data plane traffic on the bridge interface
            capture_interface = None
            try:
                result = subprocess.run(
                    ["ovs-vsctl", "list-ports", bridge_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0 and result.stdout.strip():
                    ports = result.stdout.strip().split('\n')
                    ports = [p.strip() for p in ports if p.strip()]
                    if ports:
                        # Use the first port for capture
                        # Note: This captures traffic in one direction only on point-to-point links
                        capture_interface = ports[0]
                        print(f"[CaptureManager] Capturing on port {capture_interface} (bridge: {bridge_name})")
            except Exception as e:
                print(f"[CaptureManager] Error getting bridge ports: {e}")

            # Fail explicitly if we couldn't find a port to capture on
            if not capture_interface:
                return {"error": f"No ports found on bridge '{bridge_name}'. Cannot capture data plane traffic."}

            # Ensure the capture interface is up
            try:
                subprocess.run(
                    ["ip", "link", "set", "dev", capture_interface, "up"],
                    capture_output=True,
                    timeout=5
                )
                print(f"[CaptureManager] Brought interface {capture_interface} up")
            except Exception as e:
                print(f"[CaptureManager] Warning: Could not bring interface up: {e}")

            # Build tshark command with JSON output for rich protocol decoding
            # Using 'ek' format (Elasticsearch/newline-delimited JSON) - one JSON object per line
            cmd = [
                "tshark",
                "-i", capture_interface,
                "-T", "ek",          # Newline-delimited JSON output
                "-l",                # Line-buffered output
                "-n",                # No name resolution
                "-Q",                # Quiet (no packet count summary)
            ]
            if bpf_filter:
                # Display filter uses -Y flag (Wireshark syntax like "lldp", "bgp", "ospf")
                cmd.extend(["-Y", bpf_filter])

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=0  # Unbuffered
                )

                session = CaptureSession(
                    session_id=session_id,
                    bridge_name=bridge_name,
                    client_id=client_id,
                    websocket=websocket,
                    process=process,
                    bpf_filter=bpf_filter
                )

                self.sessions[session_id] = session

                # Capture the main IOLoop before starting thread
                # (IOLoop.current() returns different IOLoop in new thread)
                main_ioloop = tornado.ioloop.IOLoop.current()

                # Start reader thread
                reader = threading.Thread(
                    target=self._read_output,
                    args=(session, main_ioloop),
                    daemon=True
                )
                session.reader_thread = reader
                reader.start()

                print(f"[CaptureManager] Started {session_id} on {bridge_name}")

                return {
                    "session_id": session_id,
                    "bridge": bridge_name,
                    "started_at": session.start_time.isoformat()
                }

            except FileNotFoundError:
                return {"error": "tshark not installed"}
            except PermissionError:
                return {"error": "Permission denied - need NET_ADMIN"}
            except Exception as e:
                return {"error": str(e)}

    def _read_output(self, session: CaptureSession, ioloop):
        """Read tshark JSON output and send to WebSocket."""
        parser = TsharkParser()
        local_packet_count = 0  # Local counter to avoid race conditions

        print(f"[CaptureManager] Reader thread started for {session.session_id} on {session.bridge_name}")

        # Check for any stderr output (tshark errors)
        if session.process and session.process.stderr:
            # Non-blocking check for stderr
            try:
                fd = session.process.stderr.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                stderr_data = session.process.stderr.read()
                if stderr_data:
                    print(f"[CaptureManager] tshark stderr: {stderr_data}")
            except Exception:
                pass  # Ignore non-blocking read errors

        try:
            while session.is_active and session.process:
                # Check if stdout is still available
                if not session.process.stdout:
                    print(f"[CaptureManager] stdout not available for {session.session_id}")
                    break

                line = session.process.stdout.readline()
                if not line:
                    # Check if process exited
                    if session.process.poll() is not None:
                        print(f"[CaptureManager] tshark exited with code {session.process.returncode}")
                        # Try to get stderr
                        try:
                            stderr = session.process.stderr.read()
                            if stderr:
                                print(f"[CaptureManager] tshark stderr: {stderr}")
                        except Exception:
                            pass
                    break

                line = line.strip()
                if not line:
                    continue

                # Parse JSON line - returns None for non-packet lines (index lines, etc.)
                packet = parser.parse_line(line, local_packet_count + 1)

                if packet:
                    local_packet_count += 1
                    packet['number'] = local_packet_count

                    # Debug: log first few packets
                    if local_packet_count <= 3:
                        print(f"[CaptureManager] Packet {local_packet_count}: {packet.get('protocol', '?')} - {packet.get('info', '')[:60]}")

                    # Update session packet count atomically - only every 10 packets to reduce lock contention
                    if local_packet_count % 10 == 0:
                        with self._lock:
                            session.packet_count = local_packet_count

                    # Send via WebSocket (thread-safe via IOLoop callback)
                    # Capture websocket reference to avoid race with None assignment
                    ws = session.websocket
                    if ws:
                        def send(websocket, pkt, count):
                            try:
                                if websocket and websocket.ws_connection:
                                    msg = json.dumps({
                                        'type': 'packet',
                                        'data': pkt
                                    })
                                    websocket.write_message(msg)
                                    if count <= 3:
                                        print(f"[CaptureManager] Sent packet {count} to WebSocket")
                                else:
                                    print(f"[CaptureManager] WebSocket not connected, can't send packet {count}")
                            except Exception as e:
                                print(f"[CaptureManager] WebSocket send error: {e}")

                        ioloop.add_callback(send, ws, packet, local_packet_count)
                    else:
                        if local_packet_count <= 3:
                            print(f"[CaptureManager] No WebSocket reference for packet {local_packet_count}")

                    if local_packet_count >= self.MAX_PACKETS:
                        break

        except Exception as e:
            print(f"[CaptureManager] Reader error: {e}")
        finally:
            # Update final packet count
            with self._lock:
                session.packet_count = local_packet_count
                session.is_active = False

            # Notify client capture stopped
            ws = session.websocket
            if ws:
                def notify(websocket, sid, count):
                    try:
                        if websocket and websocket.ws_connection:
                            websocket.write_message(json.dumps({
                                'type': 'stopped',
                                'session_id': sid,
                                'packet_count': count,
                                'reason': 'completed'
                            }))
                    except Exception:
                        pass
                ioloop.add_callback(notify, ws, session.session_id, local_packet_count)

    def stop_capture(self, session_id: str, client_id: Optional[str] = None) -> Dict:
        """Stop a capture session."""
        with self._lock:
            if session_id not in self.sessions:
                return {"error": "Session not found"}

            session = self.sessions[session_id]
            if client_id and session.client_id != client_id:
                return {"error": "Not authorized"}

            return self._stop_session_unsafe(session_id, "user")

    def _stop_session_unsafe(self, session_id: str, reason: str = "unknown") -> Dict:
        """Stop session without lock. Caller must hold the lock."""
        if session_id not in self.sessions:
            return {"error": "Session not found"}

        session = self.sessions[session_id]
        if not session.is_active:
            return {"status": "already_stopped", "packet_count": session.packet_count}

        # Mark as inactive first to signal reader thread to stop
        session.is_active = False

        # Terminate the tcpdump process
        if session.process:
            try:
                session.process.terminate()
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
                # Must wait after kill to prevent zombie processes
                try:
                    session.process.wait(timeout=2)
                except Exception:
                    pass
            except Exception as e:
                print(f"[CaptureManager] Stop error: {e}")

            # Close subprocess pipes to prevent file descriptor leaks
            try:
                if session.process.stdout:
                    session.process.stdout.close()
                if session.process.stderr:
                    session.process.stderr.close()
            except Exception as e:
                print(f"[CaptureManager] Pipe close error: {e}")

        # Store final packet count before potential thread access issues
        final_packet_count = session.packet_count
        final_duration = session.elapsed_seconds()

        print(f"[CaptureManager] Stopped {session_id}, reason: {reason}, packets: {final_packet_count}")

        return {
            "status": "stopped",
            "session_id": session_id,
            "reason": reason,
            "packet_count": final_packet_count,
            "duration": final_duration
        }

    def _validate_bridge_name(self, name: str) -> bool:
        """Validate bridge name format."""
        if not name or len(name) > 255:
            return False
        return bool(re.match(r'^[a-zA-Z0-9\-_]+$', name))

    def _validate_display_filter(self, filter_expr: str) -> Optional[str]:
        """
        Validate Wireshark display filter syntax to prevent injection.
        Returns None if valid, or error message if invalid.

        Display filters use Wireshark syntax like:
        - lldp, bgp, ospf, arp, icmp (protocol names)
        - tcp.port == 179 (field comparisons)
        - ip.addr == 192.168.1.1 (IP filtering)
        - bgp.type == 2 (protocol field values)
        """
        if not filter_expr:
            return None  # Empty filter is valid

        # Length limit
        if len(filter_expr) > 500:
            return "Filter too long (max 500 characters)"

        # Character whitelist for display filter syntax
        # Allows: alphanumeric, spaces, parentheses, brackets, dots, colons,
        # comparison operators, underscores, hyphens, forward slash (for CIDR)
        if not re.match(r'^[a-zA-Z0-9\s\(\)\[\]\.\:\-\_\|&!<>=,/]+$', filter_expr):
            return "Filter contains invalid characters"

        # Check for dangerous patterns (shell injection prevention)
        dangerous = ['--', ';', '$', '`', '\n', '\r', '\\', "'", '"', '#']
        for d in dangerous:
            if d in filter_expr:
                return f"Filter contains invalid pattern: {d}"

        # Check parenthesis nesting depth to prevent DoS
        depth = 0
        max_depth = 0
        for char in filter_expr:
            if char == '(':
                depth += 1
                max_depth = max(max_depth, depth)
            elif char == ')':
                depth -= 1
            if depth < 0:
                return "Unbalanced parentheses in filter"

        if depth != 0:
            return "Unbalanced parentheses in filter"

        if max_depth > self.MAX_BPF_NESTING:
            return f"Filter too complex (max nesting depth: {self.MAX_BPF_NESTING})"

        # Check for excessively long filters (DoS prevention)
        words = filter_expr.split()
        if len(words) > 50:
            return "Filter has too many terms (max 50)"

        return None  # Valid

    def _test_display_filter(self, bridge_name: str, display_filter: str) -> Optional[str]:
        """
        Test display filter by running tshark to validate the filter syntax.
        Returns None if valid, or error message if invalid.
        Uses -Y flag for Wireshark display filter syntax.

        We validate by reading from /dev/null which checks filter syntax
        without requiring a live interface.
        """
        try:
            # Validate filter syntax by reading from /dev/null
            # This checks that the filter expression is valid without
            # needing a live capture interface
            result = subprocess.run(
                ["tshark", "-r", "/dev/null", "-Y", display_filter],
                capture_output=True,
                text=True,
                timeout=3
            )
            # tshark exits with 0 if filter syntax is valid (even with no input)
            # Non-zero exit with filter error in stderr means invalid filter
            if result.returncode != 0:
                # Extract useful error from stderr, filtering out noise
                stderr_lines = result.stderr.strip().split('\n')
                # Filter out tshark warnings and informational messages
                error_lines = [
                    line for line in stderr_lines
                    if line.strip()
                    and not line.startswith('Running as user')
                    and 'Capturing on' not in line
                    and 'packet count' not in line.lower()
                    and '/dev/null' not in line.lower()
                ]
                error = '\n'.join(error_lines).strip()

                if 'syntax error' in error.lower():
                    return "Syntax error in filter expression"
                if 'neither a field nor a protocol name' in error.lower():
                    return "Unknown protocol or field name"
                if 'invalid' in error.lower():
                    return "Invalid filter syntax"
                # Return a clean error message
                if error:
                    # Take first meaningful line, truncated
                    first_line = error_lines[0] if error_lines else error
                    return first_line[:100] if len(first_line) > 100 else first_line
                # If no meaningful error lines, the filter is likely valid
                # (error might be about /dev/null not being a pcap file)
                return None
            return None  # Valid
        except subprocess.TimeoutExpired:
            # Timeout is unexpected for /dev/null read, but treat as valid
            return None
        except Exception as e:
            return f"Filter validation failed: {str(e)}"

    def _bridge_exists(self, name: str) -> bool:
        """Check if bridge exists."""
        try:
            result = subprocess.run(
                ["ovs-vsctl", "br-exists", name],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except:
            try:
                result = subprocess.run(
                    ["ip", "link", "show", name],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0
            except:
                return False

    def invalidate_bridge_cache(self):
        """Invalidate the bridge cache to force a refresh."""
        with self._lock:
            self._bridge_cache = None
            self._bridge_cache_time = 0

    def get_bridges(self, refresh: bool = False) -> List[Dict]:
        """
        Get list of OVS bridges with edge mapping.

        Fetches bridge information from nodebuilder API which is the single
        source of truth for bridge name parsing. Falls back to local OVS
        command with local parsing if nodebuilder is unreachable.

        Thread-safe: Uses lock for cache and session access.
        """
        now = time.time()

        # Thread-safe cache check
        with self._lock:
            if not refresh and self._bridge_cache and (now - self._bridge_cache_time) < self._bridge_cache_ttl:
                return list(self._bridge_cache)  # Return copy to avoid mutations

        bridges = []

        # Try nodebuilder API first with retry logic
        max_retries = 2
        last_error = None
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    NODEBUILDER_BRIDGES_URL,
                    headers={'Accept': 'application/json'}
                )
                with urllib.request.urlopen(req, timeout=NODEBUILDER_TIMEOUT) as response:
                    data = json.loads(response.read().decode('utf-8'))

                    # Get set of capturing bridges with single lock acquisition
                    with self._lock:
                        capturing_bridges = {
                            s.bridge_name for s in self.sessions.values()
                            if s.is_active
                        }

                    for bridge_info in data.get('bridges', []):
                        bridge_info['is_capturing'] = bridge_info.get('name', '') in capturing_bridges
                        bridges.append(bridge_info)

                    print(f"[CaptureManager] Got {len(bridges)} bridges from nodebuilder API")

                    # Thread-safe cache update
                    with self._lock:
                        self._bridge_cache = bridges
                        self._bridge_cache_time = time.time()
                    return bridges

            except urllib.error.URLError as e:
                last_error = f"Nodebuilder API unavailable: {e}"
            except json.JSONDecodeError as e:
                last_error = f"Invalid JSON from nodebuilder: {e}"
            except Exception as e:
                last_error = f"Error calling nodebuilder API: {e}"

            if attempt < max_retries - 1:
                print(f"[CaptureManager] Retry {attempt + 1}/{max_retries}: {last_error}")
                time.sleep(0.5)  # Brief delay before retry

        # All retries failed
        print(f"[CaptureManager] {last_error}")

        # Fallback: Get bridges locally with local parsing
        print("[CaptureManager] Falling back to local OVS bridge list with local parsing")
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-br"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # Get set of capturing bridges with single lock acquisition
                with self._lock:
                    capturing_bridges = {
                        s.bridge_name for s in self.sessions.values()
                        if s.is_active
                    }

                for name in result.stdout.strip().split('\n'):
                    name = name.strip()
                    if not name:
                        continue

                    info = self._parse_bridge_name(name)
                    info['name'] = name
                    info['is_capturing'] = name in capturing_bridges
                    bridges.append(info)
                print(f"[CaptureManager] Found {len(bridges)} OVS bridges (local fallback)")
            else:
                print(f"[CaptureManager] ovs-vsctl failed: {result.stderr.strip()}")
        except FileNotFoundError:
            print("[CaptureManager] ovs-vsctl not found - OVS not installed?")
        except Exception as e:
            print(f"[CaptureManager] Error listing bridges: {e}")

        # Thread-safe cache update
        with self._lock:
            self._bridge_cache = bridges
            self._bridge_cache_time = time.time()
        return bridges

    # Mapping of 2-letter abbreviations to full device type names
    # IMPORTANT: The canonical source of truth is nodebuilder/src/bridge_utils.py
    # This copy exists for performance (avoids API calls) but MUST stay in sync.
    # Run tests/test_bridge_consistency.py to verify consistency.
    DEVICE_ABBREVIATIONS = {
        'sp': 'spine',
        'le': 'leaf',
        'bo': 'borderleaf',  # 'bo' = first 2 letters of 'borderleaf'
        'ho': 'host',
        'fi': 'firewall',    # 'fi' = first 2 letters of 'firewall'
        've': 'vce',         # VeloCloud Edge
        'vc': 'vcg',         # VeloCloud Gateway
        'vo': 'vco',         # VeloCloud Orchestrator
        'cl': 'client',
        'co': 'core',
        'pe': 'pe',
        'ce': 'ce',
        'dc': 'dci',
        'rr': 'rr',
        'ga': 'gateway',     # 'ga' = first 2 letters of 'gateway'
        'ro': 'router',
        'is': 'isp',
        'in': 'internet',
        'me': 'memleaf',
        'cu': 'customer',
        'oo': 'oob',
        # Legacy kvmbuilder mappings (may use different abbreviations)
        'bl': 'borderleaf',  # Legacy: some old bridges use 'bl'
        'gw': 'gateway',     # Legacy: some old bridges use 'gw'
        'fw': 'firewall',    # Legacy: some old bridges use 'fw'
    }

    def _parse_bridge_name(self, bridge_name: str) -> Dict:
        """
        Parse bridge name to extract device/port info.

        Supports multiple bridge naming conventions:

        1. Nodebuilder format (with 'x' separator):
           le5x1-sp4x9 -> leaf5:Ethernet1 <-> spine4:Ethernet9
           fi1xet1-bo1x7 -> firewall1:eth1 <-> borderleaf1:Ethernet7

        2. Legacy kvmbuilder format (no separator):
           le11-le21 -> leaf1:Ethernet1 <-> leaf2:Ethernet1
           sp13-le14 -> spine1:Ethernet3 <-> leaf1:Ethernet4

        3. Full name format:
           leaf3Et3-leaf1Et3 -> leaf3:Ethernet3 <-> leaf1:Ethernet3
        """
        result = {
            "source_device": "",
            "source_port": "",
            "source_device_name": "",
            "source_port_name": "",
            "target_device": "",
            "target_port": "",
            "target_device_name": "",
            "target_port_name": ""
        }

        if '-' not in bridge_name:
            return result

        try:
            parts = bridge_name.split('-')
            if len(parts) >= 2:
                src = parts[0]
                tgt = parts[1]

                # Parse each part
                src_device, src_port = self._split_device_port(src)
                tgt_device, tgt_port = self._split_device_port(tgt)

                # Store abbreviated codes
                result["source_device"] = src_device
                result["source_port"] = src_port
                result["target_device"] = tgt_device
                result["target_port"] = tgt_port

                # Expand to full names for display
                result["source_device_name"] = self._expand_device_code(src_device)
                result["source_port_name"] = self._expand_port_code(src_port)
                result["target_device_name"] = self._expand_device_code(tgt_device)
                result["target_port_name"] = self._expand_port_code(tgt_port)

        except Exception as e:
            print(f"[CaptureManager] Parse error for {bridge_name}: {e}")

        return result

    def _split_device_port(self, part: str) -> tuple:
        """
        Split a device+port string into (device, port).

        Handles multiple formats:
        1. 'x' separator: 'le5x1' -> ('le5', '1'), 'fw1xet1' -> ('fw1', 'et1')
        2. 'eth' prefix: 'client1eth1' -> ('client1', 'eth1')
        3. 'et' prefix: 'sp1et1' -> ('sp1', 'et1')
        4. Full names: 'leaf3Et3' -> ('leaf3', 'Et3')
        5. Legacy kvmbuilder: 'le11' -> ('le1', '1')
        """
        lower = part.lower()

        # Check for 'x' separator (nodebuilder format)
        # The separator 'x' should have a digit before it and:
        # - digit after (e.g., le5x1)
        # - 'e' after (e.g., fi1xet1 for eth port)
        # - 'w' after (e.g., ve1xwa1 for VeloCloud WAN)
        # - 'l' after (e.g., ve1xla1 for VeloCloud LAN)
        for i, c in enumerate(lower):
            if c == 'x' and i > 0 and i < len(part) - 1:
                prev_char = lower[i - 1]
                next_char = lower[i + 1]
                if prev_char.isdigit() and (next_char.isdigit() or next_char in 'ewl'):
                    return part[:i], part[i + 1:]

        # Look for 'eth' (longer prefix, for Linux hosts)
        eth_idx = lower.find('eth')
        if eth_idx > 0:
            return part[:eth_idx], part[eth_idx:]

        # Look for 'et' (for Ethernet)
        et_idx = lower.find('et')
        if et_idx > 0:
            return part[:et_idx], part[et_idx:]

        # Legacy kvmbuilder format: {2-letter-prefix}{device-num}{port-num}
        # e.g., 'le11' = prefix 'le', device '1', port '1'
        match = re.match(r'^([a-zA-Z]{2})(\d)(\d+)$', part)
        if match:
            prefix = match.group(1)
            device_num = match.group(2)
            port_num = match.group(3).lstrip('0') or '0'
            return f"{prefix}{device_num}", port_num

        # Fallback: return whole part as device with empty port
        return part, ""

    def _expand_device_code(self, code: str) -> str:
        """
        Expand abbreviated device code to full device name.

        Examples:
            le5 -> leaf5
            sp4 -> spine4
            fi1 -> firewall1
        """
        if not code:
            return code

        # Extract prefix and number
        prefix = ''
        number = ''
        for char in code:
            if char.isalpha():
                prefix += char
            elif char.isdigit():
                number += char

        # Look up the full name
        prefix_lower = prefix.lower()
        if prefix_lower in self.DEVICE_ABBREVIATIONS:
            full_prefix = self.DEVICE_ABBREVIATIONS[prefix_lower]
            return f"{full_prefix}{number}"

        # If not found in mapping, return as-is (might already be full name)
        return code

    def _expand_port_code(self, code: str) -> str:
        """
        Expand abbreviated port code to full port name.

        Examples:
            1 -> Ethernet1
            et5 -> Ethernet5
            eth1 -> eth1 (Linux host interface, keep as-is)
            wa1 -> wan1 (VeloCloud WAN port)
            la1 -> lan1 (VeloCloud LAN port)
        """
        if not code:
            return code

        code_lower = code.lower()

        # Linux host interface - keep as-is
        if code_lower.startswith('eth'):
            return code

        # Full VeloCloud port names - keep as-is
        if code_lower.startswith('wan') or code_lower.startswith('lan'):
            return code

        # Abbreviated VeloCloud WAN port: wa1 -> wan1
        if code_lower.startswith('wa') and len(code) >= 3:
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"wan{number}"
            return code

        # Abbreviated VeloCloud LAN port: la1 -> lan1
        if code_lower.startswith('la') and len(code) >= 3:
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"lan{number}"
            return code

        # Ethernet abbreviation: et5 -> Ethernet5
        if code_lower.startswith('et'):
            number = ''.join(c for c in code if c.isdigit())
            if number:
                return f"Ethernet{number}"
            return code

        # Just a number: 1 -> Ethernet1
        if code.isdigit():
            return f"Ethernet{code}"

        # Default: return as-is
        return code

    def _parse_short_code(self, code: str) -> Optional[Dict]:
        """
        Parse a short device code like 'le11' or 'sp23'.
        DEPRECATED: Use _split_device_port + _expand_device_code instead.

        Format: {2-letter-prefix}{device-num}{port-num}
        Returns dict with device_code, device_name, device_num, port_num
        """
        # Match: 2 letters, then digits
        match = re.match(r'^([a-zA-Z]{2})(\d+)$', code)
        if not match:
            return None

        prefix = match.group(1).lower()
        digits = match.group(2)

        # First digit is device number, rest is port number
        if len(digits) < 2:
            return None

        device_num = digits[0]
        port_num = digits[1:].lstrip('0') or '0'  # Remove leading zeros from port

        device_type = self.DEVICE_ABBREVIATIONS.get(prefix, prefix.upper())
        device_name = f"{device_type}{device_num}"

        return {
            'device_code': f"{prefix}{device_num}",
            'device_name': device_name,
            'device_num': device_num,
            'port_num': port_num
        }

    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session status."""
        with self._lock:
            if session_id not in self.sessions:
                return None
            s = self.sessions[session_id]
            return {
                "session_id": session_id,
                "bridge": s.bridge_name,
                "is_active": s.is_active,
                "packet_count": s.packet_count,
                "duration": s.elapsed_seconds(),
                "started_at": s.start_time.isoformat()
            }

    def shutdown(self):
        """Shutdown manager."""
        self._running = False
        with self._lock:
            for sid in list(self.sessions.keys()):
                self._stop_session_unsafe(sid, "shutdown")


class LatencyManager:
    """Manages link latency injection using Linux tc (traffic control)."""

    # Latency constraints
    MIN_DELAY_MS = 1
    MAX_DELAY_MS = 10000
    DEFAULT_RATE = "1000mbit"  # Rate limit for htb class

    def __init__(self):
        self._lock = threading.Lock()
        # Cache: {interface: delay_ms}
        self._latency_state: Dict[str, int] = {}

    def _validate_delay(self, delay_ms: int) -> Optional[str]:
        """Validate delay value. Returns error message or None if valid."""
        if not isinstance(delay_ms, int):
            return "Delay must be an integer"
        if delay_ms < self.MIN_DELAY_MS:
            return f"Delay must be at least {self.MIN_DELAY_MS}ms"
        if delay_ms > self.MAX_DELAY_MS:
            return f"Delay must not exceed {self.MAX_DELAY_MS}ms"
        return None

    def _get_interface_for_bridge(self, bridge_name: str) -> Optional[str]:
        """Get the first port/interface attached to an OVS bridge."""
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-ports", bridge_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                ports = result.stdout.strip().split('\n')
                ports = [p.strip() for p in ports if p.strip()]
                if ports:
                    return ports[0]
        except Exception as e:
            print(f"[LatencyManager] Error getting bridge ports: {e}")
        return None

    def check_tc_exists(self, interface: str) -> bool:
        """Check if tc qdisc (htb) is configured on interface."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            return "htb" in result.stdout
        except Exception as e:
            print(f"[LatencyManager] Error checking tc on {interface}: {e}")
            return False

    def get_tc_delay(self, interface: str) -> Optional[int]:
        """Get current netem delay in ms from interface. Returns None if not configured."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse "delay Xms" or "delay X.Xms" from netem output
                match = re.search(r'delay\s+(\d+(?:\.\d+)?)(ms|s|us)', result.stdout)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2)
                    if unit == 's':
                        value *= 1000
                    elif unit == 'us':
                        value /= 1000
                    return int(value)
        except Exception as e:
            print(f"[LatencyManager] Error getting tc delay on {interface}: {e}")
        return None

    def enable_latency(self, bridge_name: str, delay_ms: int) -> Dict:
        """
        Enable latency on a bridge's interface using tc netem.

        Uses htb (Hierarchical Token Bucket) with netem for delay injection:
        - Root qdisc: htb with default class
        - Class: htb rate limit
        - Leaf qdisc: netem delay
        """
        # Validate delay
        error = self._validate_delay(delay_ms)
        if error:
            return {"error": error}

        # Validate bridge name
        if not bridge_name or not re.match(r'^[a-zA-Z0-9\-_]+$', bridge_name):
            return {"error": "Invalid bridge name format"}

        # Get interface for this bridge
        interface = self._get_interface_for_bridge(bridge_name)
        if not interface:
            return {"error": f"No interface found for bridge '{bridge_name}'"}

        with self._lock:
            # Check if already configured
            if self.check_tc_exists(interface):
                # Remove existing config first
                self._disable_tc_unsafe(interface)

            try:
                # Ensure interface is up
                subprocess.run(
                    ["ip", "link", "set", "dev", interface, "up"],
                    capture_output=True,
                    timeout=5
                )

                # Add htb root qdisc
                result = subprocess.run(
                    ["tc", "qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", "12"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    return {"error": f"Failed to add root qdisc: {result.stderr.strip()}"}

                # Add htb class
                result = subprocess.run(
                    ["tc", "class", "add", "dev", interface, "parent", "1:1", "classid", "1:12",
                     "htb", "rate", self.DEFAULT_RATE],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    # Cleanup on failure
                    self._disable_tc_unsafe(interface)
                    return {"error": f"Failed to add htb class: {result.stderr.strip()}"}

                # Add netem qdisc with delay
                result = subprocess.run(
                    ["tc", "qdisc", "add", "dev", interface, "parent", "1:12", "handle", "10:",
                     "netem", "delay", f"{delay_ms}ms"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    # Cleanup on failure
                    self._disable_tc_unsafe(interface)
                    return {"error": f"Failed to add netem delay: {result.stderr.strip()}"}

                # Update state cache
                self._latency_state[interface] = delay_ms

                print(f"[LatencyManager] Enabled {delay_ms}ms latency on {interface} (bridge: {bridge_name})")

                return {
                    "status": "enabled",
                    "bridge": bridge_name,
                    "interface": interface,
                    "delay_ms": delay_ms
                }

            except subprocess.TimeoutExpired:
                return {"error": "tc command timed out"}
            except FileNotFoundError:
                return {"error": "tc command not found"}
            except Exception as e:
                return {"error": str(e)}

    def _disable_tc_unsafe(self, interface: str) -> bool:
        """Remove tc qdisc from interface. Caller must hold lock."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "del", "dev", interface, "root"],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Remove from state cache
            self._latency_state.pop(interface, None)
            return result.returncode == 0
        except Exception as e:
            print(f"[LatencyManager] Error disabling tc on {interface}: {e}")
            return False

    def disable_latency(self, bridge_name: str) -> Dict:
        """Disable latency on a bridge's interface."""
        # Validate bridge name
        if not bridge_name or not re.match(r'^[a-zA-Z0-9\-_]+$', bridge_name):
            return {"error": "Invalid bridge name format"}

        # Get interface for this bridge
        interface = self._get_interface_for_bridge(bridge_name)
        if not interface:
            return {"error": f"No interface found for bridge '{bridge_name}'"}

        with self._lock:
            if not self.check_tc_exists(interface):
                return {"status": "already_disabled", "bridge": bridge_name}

            if self._disable_tc_unsafe(interface):
                print(f"[LatencyManager] Disabled latency on {interface} (bridge: {bridge_name})")
                return {"status": "disabled", "bridge": bridge_name}
            else:
                return {"error": f"Failed to disable latency on {interface}"}

    def disable_all_latency(self) -> Dict:
        """Remove latency from all interfaces with tc configured."""
        disabled = []
        errors = []

        # Get all OVS bridges
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-br"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return {"error": "Failed to list bridges"}

            bridges = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]

            with self._lock:
                for bridge in bridges:
                    interface = self._get_interface_for_bridge(bridge)
                    if interface and self.check_tc_exists(interface):
                        if self._disable_tc_unsafe(interface):
                            disabled.append(interface)
                            print(f"[LatencyManager] Disabled latency on {interface}")
                        else:
                            errors.append(interface)

            return {
                "status": "success",
                "disabled_count": len(disabled),
                "interfaces": disabled,
                "errors": errors if errors else None
            }

        except Exception as e:
            return {"error": str(e)}

    def get_bridges_with_status(self) -> List[Dict]:
        """Get list of bridges with their latency status."""
        result_bridges = []

        try:
            # Use CaptureManager's get_bridges() which calls nodebuilder API
            capture_manager = get_manager()
            bridges = capture_manager.get_bridges()

            for info in bridges:
                # Make a copy to avoid modifying the cached data
                bridge_info = dict(info)
                name = bridge_info.get('name', '')

                # Get interface for this bridge
                interface = self._get_interface_for_bridge(name)
                if interface:
                    delay = self.get_tc_delay(interface)
                    bridge_info['latency_enabled'] = delay is not None
                    bridge_info['latency_delay_ms'] = delay
                    bridge_info['interface'] = interface
                else:
                    bridge_info['latency_enabled'] = False
                    bridge_info['latency_delay_ms'] = None
                    bridge_info['interface'] = None

                result_bridges.append(bridge_info)

        except Exception as e:
            print(f"[LatencyManager] Error getting bridges with status: {e}")

        return result_bridges


class PacketLossManager:
    """Manages packet loss injection using Linux tc netem."""

    VALID_PERCENTAGES = [10, 20, 30, 40, 50]

    def __init__(self):
        self._lock = threading.Lock()
        # Cache: {interface: loss_percent}
        self._loss_state: Dict[str, int] = {}

    def validate_percent(self, percent: int) -> Optional[str]:
        """Validate loss percentage. Returns error message or None if valid."""
        if percent not in self.VALID_PERCENTAGES and percent != 0:
            return f"Loss percent must be one of {self.VALID_PERCENTAGES} or 0"
        return None

    def get_loss_percent(self, interface: str) -> Optional[int]:
        """Get current loss percentage from interface tc config."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse "loss X%" from netem output
                match = re.search(r'loss\s+(\d+(?:\.\d+)?)%', result.stdout)
                if match:
                    return int(float(match.group(1)))
        except Exception as e:
            print(f"[PacketLossManager] Error getting loss on {interface}: {e}")
        return None

    def get_state(self, interface: str) -> int:
        """Get cached loss state for interface."""
        return self._loss_state.get(interface, 0)

    def set_state(self, interface: str, percent: int):
        """Update cached loss state."""
        with self._lock:
            if percent > 0:
                self._loss_state[interface] = percent
            else:
                self._loss_state.pop(interface, None)

    def clear_state(self, interface: str):
        """Clear cached loss state."""
        with self._lock:
            self._loss_state.pop(interface, None)


class DuplicationManager:
    """Manages packet duplication injection using Linux tc netem."""

    VALID_PERCENTAGES = [10, 20, 30, 40, 50]

    def __init__(self):
        self._lock = threading.Lock()
        # Cache: {interface: dup_percent}
        self._dup_state: Dict[str, int] = {}

    def validate_percent(self, percent: int) -> Optional[str]:
        """Validate duplication percentage. Returns error message or None if valid."""
        if percent not in self.VALID_PERCENTAGES and percent != 0:
            return f"Duplication percent must be one of {self.VALID_PERCENTAGES} or 0"
        return None

    def get_dup_percent(self, interface: str) -> Optional[int]:
        """Get current duplication percentage from interface tc config."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse "duplicate X%" from netem output
                match = re.search(r'duplicate\s+(\d+(?:\.\d+)?)%', result.stdout)
                if match:
                    return int(float(match.group(1)))
        except Exception as e:
            print(f"[DuplicationManager] Error getting duplication on {interface}: {e}")
        return None

    def get_state(self, interface: str) -> int:
        """Get cached duplication state for interface."""
        return self._dup_state.get(interface, 0)

    def set_state(self, interface: str, percent: int):
        """Update cached duplication state."""
        with self._lock:
            if percent > 0:
                self._dup_state[interface] = percent
            else:
                self._dup_state.pop(interface, None)

    def clear_state(self, interface: str):
        """Clear cached duplication state."""
        with self._lock:
            self._dup_state.pop(interface, None)


class CorruptionManager:
    """Manages packet corruption injection using Linux tc netem."""

    VALID_PERCENTAGES = [10, 20, 30, 40, 50]

    def __init__(self):
        self._lock = threading.Lock()
        # Cache: {interface: corrupt_percent}
        self._corrupt_state: Dict[str, int] = {}

    def validate_percent(self, percent: int) -> Optional[str]:
        """Validate corruption percentage. Returns error message or None if valid."""
        if percent not in self.VALID_PERCENTAGES and percent != 0:
            return f"Corruption percent must be one of {self.VALID_PERCENTAGES} or 0"
        return None

    def get_corrupt_percent(self, interface: str) -> Optional[int]:
        """Get current corruption percentage from interface tc config."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse "corrupt X%" from netem output
                match = re.search(r'corrupt\s+(\d+(?:\.\d+)?)%', result.stdout)
                if match:
                    return int(float(match.group(1)))
        except Exception as e:
            print(f"[CorruptionManager] Error getting corruption on {interface}: {e}")
        return None

    def get_state(self, interface: str) -> int:
        """Get cached corruption state for interface."""
        return self._corrupt_state.get(interface, 0)

    def set_state(self, interface: str, percent: int):
        """Update cached corruption state."""
        with self._lock:
            if percent > 0:
                self._corrupt_state[interface] = percent
            else:
                self._corrupt_state.pop(interface, None)

    def clear_state(self, interface: str):
        """Clear cached corruption state."""
        with self._lock:
            self._corrupt_state.pop(interface, None)


class ReorderManager:
    """
    Manages packet reordering (jitter) injection using Linux tc netem.

    Uses: tc qdisc ... netem delay Xms reorder Y%
    The reorder option causes Y% of packets to be sent immediately,
    while the remaining (100-Y)% are delayed by X ms, creating out-of-order delivery.
    """

    VALID_PERCENTAGES = [10, 20, 30, 40, 50]
    MIN_DELAY_MS = 100
    MAX_DELAY_MS = 10000

    def __init__(self):
        self._lock = threading.Lock()
        # Cache: {interface: {delay_ms, reorder_percent}}
        self._reorder_state: Dict[str, Dict[str, int]] = {}

    def validate_params(self, delay_ms: int, reorder_percent: int) -> Optional[str]:
        """Validate reorder parameters. Returns error message or None if valid."""
        if reorder_percent not in self.VALID_PERCENTAGES and reorder_percent != 0:
            return f"Reorder percent must be one of {self.VALID_PERCENTAGES} or 0"
        if reorder_percent > 0:
            if delay_ms < self.MIN_DELAY_MS:
                return f"Reorder delay must be at least {self.MIN_DELAY_MS}ms"
            if delay_ms > self.MAX_DELAY_MS:
                return f"Reorder delay must not exceed {self.MAX_DELAY_MS}ms"
        return None

    def get_reorder_params(self, interface: str) -> Optional[Dict[str, int]]:
        """Get current reorder parameters from interface tc config."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                # Parse "reorder X%" from netem output
                reorder_match = re.search(r'reorder\s+(\d+(?:\.\d+)?)%', result.stdout)
                if reorder_match:
                    reorder_pct = int(float(reorder_match.group(1)))
                    # Also get the delay value (required for reorder to work)
                    delay_match = re.search(r'delay\s+(\d+(?:\.\d+)?)(ms|s|us)', result.stdout)
                    delay_ms = 0
                    if delay_match:
                        value = float(delay_match.group(1))
                        unit = delay_match.group(2)
                        if unit == 's':
                            value *= 1000
                        elif unit == 'us':
                            value /= 1000
                        delay_ms = int(value)
                    return {"delay_ms": delay_ms, "reorder_percent": reorder_pct}
        except Exception as e:
            print(f"[ReorderManager] Error getting reorder on {interface}: {e}")
        return None

    def get_state(self, interface: str) -> Dict[str, int]:
        """Get cached reorder state for interface."""
        return self._reorder_state.get(interface, {"delay_ms": 0, "reorder_percent": 0})

    def set_state(self, interface: str, delay_ms: int, reorder_percent: int):
        """Update cached reorder state."""
        with self._lock:
            if reorder_percent > 0:
                self._reorder_state[interface] = {
                    "delay_ms": delay_ms,
                    "reorder_percent": reorder_percent
                }
            else:
                self._reorder_state.pop(interface, None)

    def clear_state(self, interface: str):
        """Clear cached reorder state."""
        with self._lock:
            self._reorder_state.pop(interface, None)


class ImpairmentCoordinator:
    """
    Orchestrates all network impairment managers and executes combined tc netem commands.

    This coordinator ensures that all impairments (latency, loss, duplication, corruption, reorder)
    are applied together in a single tc netem command, as netem supports combining them.
    """

    DEFAULT_RATE = "1000mbit"

    def __init__(self, latency_mgr: LatencyManager, loss_mgr: PacketLossManager,
                 dup_mgr: DuplicationManager, corrupt_mgr: CorruptionManager,
                 reorder_mgr: ReorderManager):
        self.latency_mgr = latency_mgr
        self.loss_mgr = loss_mgr
        self.dup_mgr = dup_mgr
        self.corrupt_mgr = corrupt_mgr
        self.reorder_mgr = reorder_mgr
        self._lock = threading.Lock()

    def _get_interface_for_bridge(self, bridge_name: str) -> Optional[str]:
        """Get the first port/interface attached to an OVS bridge."""
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-ports", bridge_name],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0 and result.stdout.strip():
                ports = result.stdout.strip().split('\n')
                ports = [p.strip() for p in ports if p.strip()]
                if ports:
                    return ports[0]
        except Exception as e:
            print(f"[ImpairmentCoordinator] Error getting bridge ports: {e}")
        return None

    def _check_tc_exists(self, interface: str) -> bool:
        """Check if tc qdisc (htb) is configured on interface."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=5
            )
            return "htb" in result.stdout
        except Exception:
            return False

    def _disable_tc(self, interface: str) -> bool:
        """Remove tc qdisc from interface."""
        try:
            result = subprocess.run(
                ["tc", "qdisc", "del", "dev", interface, "root"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            print(f"[ImpairmentCoordinator] Error disabling tc on {interface}: {e}")
            return False

    def configure_impairments(self, bridge_name: str, latency_ms: int = 0,
                               loss_percent: int = 0, dup_percent: int = 0,
                               corrupt_percent: int = 0, reorder_delay_ms: int = 0,
                               reorder_percent: int = 0) -> Dict:
        """
        Configure all impairments on a bridge's interface using a single tc netem command.

        Args:
            bridge_name: OVS bridge name
            latency_ms: Delay in milliseconds (0 = no delay)
            loss_percent: Packet loss percentage (0, 10, 20, 30, 40, 50)
            dup_percent: Packet duplication percentage (0, 10, 20, 30, 40, 50)
            corrupt_percent: Packet corruption percentage (0, 10, 20, 30, 40, 50)
            reorder_delay_ms: Delay for reordering in milliseconds (100-10000)
            reorder_percent: Packet reorder percentage (0, 10, 20, 30, 40, 50)
        """
        # Validate bridge name
        if not bridge_name or not re.match(r'^[a-zA-Z0-9\-_]+$', bridge_name):
            return {"error": "Invalid bridge name format"}

        # Validate all parameters
        if latency_ms > 0:
            error = self.latency_mgr._validate_delay(latency_ms)
            if error:
                return {"error": error}

        if loss_percent > 0:
            error = self.loss_mgr.validate_percent(loss_percent)
            if error:
                return {"error": error}

        if dup_percent > 0:
            error = self.dup_mgr.validate_percent(dup_percent)
            if error:
                return {"error": error}

        if corrupt_percent > 0:
            error = self.corrupt_mgr.validate_percent(corrupt_percent)
            if error:
                return {"error": error}

        if reorder_percent > 0:
            error = self.reorder_mgr.validate_params(reorder_delay_ms, reorder_percent)
            if error:
                return {"error": error}

        # Check if all are zero (clear operation)
        all_zero = (latency_ms == 0 and loss_percent == 0 and dup_percent == 0 and
                    corrupt_percent == 0 and reorder_percent == 0)
        if all_zero:
            return self.clear_impairments(bridge_name)

        # Get interface for this bridge
        interface = self._get_interface_for_bridge(bridge_name)
        if not interface:
            return {"error": f"No interface found for bridge '{bridge_name}'"}

        with self._lock:
            # Remove existing tc config if present
            if self._check_tc_exists(interface):
                self._disable_tc(interface)

            try:
                # Ensure interface is up
                subprocess.run(
                    ["ip", "link", "set", "dev", interface, "up"],
                    capture_output=True,
                    timeout=5
                )

                # Add htb root qdisc
                result = subprocess.run(
                    ["tc", "qdisc", "add", "dev", interface, "root", "handle", "1:", "htb", "default", "12"],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    return {"error": f"Failed to add root qdisc: {result.stderr.strip()}"}

                # Add htb class
                result = subprocess.run(
                    ["tc", "class", "add", "dev", interface, "parent", "1:1", "classid", "1:12",
                     "htb", "rate", self.DEFAULT_RATE],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode != 0:
                    self._disable_tc(interface)
                    return {"error": f"Failed to add htb class: {result.stderr.strip()}"}

                # Build netem command with all active impairments
                netem_cmd = ["tc", "qdisc", "add", "dev", interface, "parent", "1:12",
                             "handle", "10:", "netem"]

                # For reorder to work, we need delay. If reorder is set but no latency,
                # use the reorder_delay_ms as the delay value
                effective_delay = latency_ms
                if reorder_percent > 0 and latency_ms == 0:
                    effective_delay = reorder_delay_ms

                if effective_delay > 0:
                    netem_cmd.extend(["delay", f"{effective_delay}ms"])
                if loss_percent > 0:
                    netem_cmd.extend(["loss", f"{loss_percent}%"])
                if dup_percent > 0:
                    netem_cmd.extend(["duplicate", f"{dup_percent}%"])
                if corrupt_percent > 0:
                    netem_cmd.extend(["corrupt", f"{corrupt_percent}%"])
                if reorder_percent > 0:
                    netem_cmd.extend(["reorder", f"{reorder_percent}%"])

                result = subprocess.run(netem_cmd, capture_output=True, text=True, timeout=5)
                if result.returncode != 0:
                    self._disable_tc(interface)
                    return {"error": f"Failed to configure netem: {result.stderr.strip()}"}

                # Update all manager states
                self.latency_mgr._latency_state[interface] = latency_ms if latency_ms > 0 else 0
                self.loss_mgr.set_state(interface, loss_percent)
                self.dup_mgr.set_state(interface, dup_percent)
                self.corrupt_mgr.set_state(interface, corrupt_percent)
                self.reorder_mgr.set_state(interface, reorder_delay_ms, reorder_percent)

                impairments = {
                    "latency_ms": latency_ms,
                    "loss_percent": loss_percent,
                    "duplication_percent": dup_percent,
                    "corruption_percent": corrupt_percent,
                    "reorder_delay_ms": reorder_delay_ms,
                    "reorder_percent": reorder_percent
                }

                print(f"[ImpairmentCoordinator] Configured impairments on {interface} (bridge: {bridge_name}): {impairments}")

                return {
                    "status": "configured",
                    "bridge": bridge_name,
                    "interface": interface,
                    "impairments": impairments
                }

            except subprocess.TimeoutExpired:
                return {"error": "tc command timed out"}
            except FileNotFoundError:
                return {"error": "tc command not found"}
            except Exception as e:
                return {"error": str(e)}

    def clear_impairments(self, bridge_name: str) -> Dict:
        """Clear all impairments on a bridge's interface."""
        # Validate bridge name
        if not bridge_name or not re.match(r'^[a-zA-Z0-9\-_]+$', bridge_name):
            return {"error": "Invalid bridge name format"}

        # Get interface for this bridge
        interface = self._get_interface_for_bridge(bridge_name)
        if not interface:
            return {"error": f"No interface found for bridge '{bridge_name}'"}

        with self._lock:
            if not self._check_tc_exists(interface):
                return {"status": "already_cleared", "bridge": bridge_name}

            if self._disable_tc(interface):
                # Clear all manager states
                self.latency_mgr._latency_state.pop(interface, None)
                self.loss_mgr.clear_state(interface)
                self.dup_mgr.clear_state(interface)
                self.corrupt_mgr.clear_state(interface)
                self.reorder_mgr.clear_state(interface)

                print(f"[ImpairmentCoordinator] Cleared impairments on {interface} (bridge: {bridge_name})")
                return {"status": "cleared", "bridge": bridge_name}
            else:
                return {"error": f"Failed to clear impairments on {interface}"}

    def clear_all_impairments(self) -> Dict:
        """Clear impairments from all interfaces."""
        cleared = []
        errors = []

        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-br"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                return {"error": "Failed to list bridges"}

            bridges = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]

            with self._lock:
                for bridge in bridges:
                    interface = self._get_interface_for_bridge(bridge)
                    if interface and self._check_tc_exists(interface):
                        if self._disable_tc(interface):
                            # Clear all manager states
                            self.latency_mgr._latency_state.pop(interface, None)
                            self.loss_mgr.clear_state(interface)
                            self.dup_mgr.clear_state(interface)
                            self.corrupt_mgr.clear_state(interface)
                            self.reorder_mgr.clear_state(interface)
                            cleared.append(interface)
                            print(f"[ImpairmentCoordinator] Cleared impairments on {interface}")
                        else:
                            errors.append(interface)

            return {
                "status": "success",
                "cleared_count": len(cleared),
                "interfaces": cleared,
                "errors": errors if errors else None
            }

        except Exception as e:
            return {"error": str(e)}

    def get_bridge_impairments(self, bridge_name: str) -> Dict:
        """Get all impairment status for a bridge."""
        interface = self._get_interface_for_bridge(bridge_name)
        if not interface:
            return {
                "latency_ms": 0,
                "loss_percent": 0,
                "duplication_percent": 0,
                "corruption_percent": 0,
                "reorder_delay_ms": 0,
                "reorder_percent": 0,
                "has_impairments": False
            }

        # Try to get from tc output first
        latency = self.latency_mgr.get_tc_delay(interface) or 0
        loss = self.loss_mgr.get_loss_percent(interface) or 0
        dup = self.dup_mgr.get_dup_percent(interface) or 0
        corrupt = self.corrupt_mgr.get_corrupt_percent(interface) or 0
        reorder_params = self.reorder_mgr.get_reorder_params(interface)
        reorder_delay = reorder_params["delay_ms"] if reorder_params else 0
        reorder_pct = reorder_params["reorder_percent"] if reorder_params else 0

        has_impairments = latency > 0 or loss > 0 or dup > 0 or corrupt > 0 or reorder_pct > 0

        return {
            "latency_ms": latency,
            "loss_percent": loss,
            "duplication_percent": dup,
            "corruption_percent": corrupt,
            "reorder_delay_ms": reorder_delay,
            "reorder_percent": reorder_pct,
            "has_impairments": has_impairments
        }

    def get_all_bridges_with_status(self) -> List[Dict]:
        """Get list of all bridges with their impairment status."""
        result_bridges = []

        try:
            # Use CaptureManager's get_bridges() which calls nodebuilder API
            capture_manager = get_manager()
            bridges = capture_manager.get_bridges()

            for info in bridges:
                # Make a copy to avoid modifying the cached data
                bridge_info = dict(info)
                name = bridge_info.get('name', '')

                # Get impairment status
                impairments = self.get_bridge_impairments(name)
                bridge_info['impairments'] = impairments
                bridge_info['has_impairments'] = impairments['has_impairments']

                result_bridges.append(bridge_info)

        except Exception as e:
            print(f"[ImpairmentCoordinator] Error getting bridges with status: {e}")

        return result_bridges


# Global managers
_manager: Optional[CaptureManager] = None
_latency_manager: Optional[LatencyManager] = None
_loss_manager: Optional[PacketLossManager] = None
_dup_manager: Optional[DuplicationManager] = None
_corrupt_manager: Optional[CorruptionManager] = None
_reorder_manager: Optional[ReorderManager] = None
_impairment_coordinator: Optional[ImpairmentCoordinator] = None


def get_manager() -> CaptureManager:
    global _manager
    if _manager is None:
        _manager = CaptureManager()
    return _manager


def get_latency_manager() -> LatencyManager:
    global _latency_manager
    if _latency_manager is None:
        _latency_manager = LatencyManager()
    return _latency_manager


def get_loss_manager() -> PacketLossManager:
    global _loss_manager
    if _loss_manager is None:
        _loss_manager = PacketLossManager()
    return _loss_manager


def get_dup_manager() -> DuplicationManager:
    global _dup_manager
    if _dup_manager is None:
        _dup_manager = DuplicationManager()
    return _dup_manager


def get_corrupt_manager() -> CorruptionManager:
    global _corrupt_manager
    if _corrupt_manager is None:
        _corrupt_manager = CorruptionManager()
    return _corrupt_manager


def get_reorder_manager() -> ReorderManager:
    global _reorder_manager
    if _reorder_manager is None:
        _reorder_manager = ReorderManager()
    return _reorder_manager


def get_impairment_coordinator() -> ImpairmentCoordinator:
    global _impairment_coordinator
    if _impairment_coordinator is None:
        _impairment_coordinator = ImpairmentCoordinator(
            get_latency_manager(),
            get_loss_manager(),
            get_dup_manager(),
            get_corrupt_manager(),
            get_reorder_manager()
        )
    return _impairment_coordinator


# HTTP/WebSocket Handlers

# Allowed IP ranges for access control
# Includes localhost, Docker bridge networks, and private networks
ALLOWED_IP_PREFIXES = (
    '127.',           # Localhost
    '172.17.',        # Default Docker bridge
    '172.18.',        # Docker networks
    '172.19.',        # Docker networks
    '172.20.',        # Docker networks
    '172.21.',        # Docker networks
    '192.168.',       # Private network (lab VMs)
    '10.',            # Private network
)


class SecureHandler(tornado.web.RequestHandler):
    """Base handler with IP-based access control."""

    def prepare(self):
        """Check if request is from allowed IP."""
        remote_ip = self.request.remote_ip
        if not self._is_allowed_ip(remote_ip):
            print(f"[CaptureService] Blocked request from {remote_ip}")
            self.set_status(403)
            self.finish({"error": "Access denied"})
            return

    def _is_allowed_ip(self, ip: str) -> bool:
        """Check if IP is in allowed ranges."""
        if not ip:
            return False
        return ip.startswith(ALLOWED_IP_PREFIXES)


class HealthHandler(SecureHandler):
    """Health check endpoint."""
    def get(self):
        self.write({"status": "ok", "service": "capture"})


class BridgesHandler(SecureHandler):
    """List available bridges."""
    def get(self):
        # Support refresh=1 or refresh=true query param to bypass cache
        refresh_param = self.get_argument('refresh', '').lower()
        refresh = refresh_param in ('1', 'true')
        bridges = get_manager().get_bridges(refresh=refresh)
        self.write({"bridges": bridges})


class CaptureWebSocketHandler(tornado.websocket.WebSocketHandler):
    """WebSocket handler for capture streaming."""

    def check_origin(self, origin):
        # Allow connections from same host
        return True

    def _is_allowed_ip(self, ip: str) -> bool:
        """Check if IP is in allowed ranges."""
        if not ip:
            return False
        return ip.startswith(ALLOWED_IP_PREFIXES)

    def open(self):
        # Check IP-based access control
        remote_ip = self.request.remote_ip
        if not self._is_allowed_ip(remote_ip):
            print(f"[CaptureWS] Blocked connection from {remote_ip}")
            self.close(code=1008, reason="Access denied")
            return

        self.client_id = str(uuid.uuid4())[:8]
        self.session_id = None
        print(f"[CaptureWS] Client {self.client_id} connected from {remote_ip}")

    def on_message(self, message):
        try:
            msg = json.loads(message)
            msg_type = msg.get('type')

            if msg_type == 'start':
                result = get_manager().start_capture(
                    bridge_name=msg.get('bridge', ''),
                    client_id=self.client_id,
                    websocket=self,
                    bpf_filter=msg.get('filter', '')
                )

                if 'error' in result:
                    self.write_message(json.dumps({
                        'type': 'error',
                        'message': result['error']
                    }))
                else:
                    self.session_id = result['session_id']
                    self.write_message(json.dumps({
                        'type': 'started',
                        'session_id': result['session_id'],
                        'bridge': result['bridge']
                    }))

            elif msg_type == 'stop':
                if self.session_id:
                    result = get_manager().stop_capture(self.session_id, self.client_id)
                    self.write_message(json.dumps({
                        'type': 'stopped',
                        **result
                    }))

            elif msg_type == 'ping':
                self.write_message(json.dumps({'type': 'pong'}))

        except json.JSONDecodeError:
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Invalid JSON'
            }))
        except Exception as e:
            self.write_message(json.dumps({
                'type': 'error',
                'message': str(e)
            }))

    def on_close(self):
        print(f"[CaptureWS] Client {self.client_id} disconnected")
        if self.session_id:
            get_manager().stop_capture(self.session_id, self.client_id)


# Latency API Handlers

class LatencyBridgesHandler(SecureHandler):
    """List bridges with latency status."""
    def get(self):
        bridges = get_latency_manager().get_bridges_with_status()
        self.write({"bridges": bridges})


class LatencyEnableHandler(SecureHandler):
    """Enable latency on a bridge."""
    def post(self):
        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        bridge = body.get('bridge', '')
        delay_ms = body.get('delay_ms')

        if not bridge:
            self.set_status(400)
            self.write({"error": "Missing 'bridge' parameter"})
            return

        if delay_ms is None:
            self.set_status(400)
            self.write({"error": "Missing 'delay_ms' parameter"})
            return

        try:
            delay_ms = int(delay_ms)
        except (TypeError, ValueError):
            self.set_status(400)
            self.write({"error": "delay_ms must be an integer"})
            return

        result = get_latency_manager().enable_latency(bridge, delay_ms)

        if 'error' in result:
            self.set_status(400)
            self.write(result)
        else:
            self.write(result)


class LatencyDisableHandler(SecureHandler):
    """Disable latency on a bridge."""
    def post(self):
        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        bridge = body.get('bridge', '')

        if not bridge:
            self.set_status(400)
            self.write({"error": "Missing 'bridge' parameter"})
            return

        result = get_latency_manager().disable_latency(bridge)

        if 'error' in result:
            self.set_status(400)
            self.write(result)
        else:
            self.write(result)


class LatencyDisableAllHandler(SecureHandler):
    """Disable latency on all bridges."""
    def post(self):
        result = get_latency_manager().disable_all_latency()

        if 'error' in result:
            self.set_status(500)
            self.write(result)
        else:
            self.write(result)


# Impairment API Handlers (unified control for latency, loss, duplication, corruption)

class ImpairmentsBridgesHandler(SecureHandler):
    """List bridges with all impairment status."""
    def get(self):
        bridges = get_impairment_coordinator().get_all_bridges_with_status()
        self.write({"bridges": bridges})


class ImpairmentsConfigureHandler(SecureHandler):
    """Configure impairments on a bridge."""
    def post(self):
        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        bridge = body.get('bridge', '')
        if not bridge:
            self.set_status(400)
            self.write({"error": "Missing 'bridge' parameter"})
            return

        # Get impairment values (default to 0 if not provided)
        try:
            latency_ms = int(body.get('latency_ms', 0))
            loss_percent = int(body.get('loss_percent', 0))
            dup_percent = int(body.get('duplication_percent', 0))
            corrupt_percent = int(body.get('corruption_percent', 0))
            reorder_delay_ms = int(body.get('reorder_delay_ms', 0))
            reorder_percent = int(body.get('reorder_percent', 0))
        except (TypeError, ValueError) as e:
            self.set_status(400)
            self.write({"error": f"Invalid parameter value: {str(e)}"})
            return

        result = get_impairment_coordinator().configure_impairments(
            bridge_name=bridge,
            latency_ms=latency_ms,
            loss_percent=loss_percent,
            dup_percent=dup_percent,
            corrupt_percent=corrupt_percent,
            reorder_delay_ms=reorder_delay_ms,
            reorder_percent=reorder_percent
        )

        if 'error' in result:
            self.set_status(400)
            self.write(result)
        else:
            self.write(result)


class ImpairmentsClearHandler(SecureHandler):
    """Clear all impairments on a bridge."""
    def post(self):
        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON"})
            return

        bridge = body.get('bridge', '')
        if not bridge:
            self.set_status(400)
            self.write({"error": "Missing 'bridge' parameter"})
            return

        result = get_impairment_coordinator().clear_impairments(bridge)

        if 'error' in result:
            self.set_status(400)
            self.write(result)
        else:
            self.write(result)


class ImpairmentsClearAllHandler(SecureHandler):
    """Clear all impairments on all bridges."""
    def post(self):
        result = get_impairment_coordinator().clear_all_impairments()

        if 'error' in result:
            self.set_status(500)
            self.write(result)
        else:
            self.write(result)


def make_app():
    return tornado.web.Application([
        (r"/health", HealthHandler),
        (r"/bridges", BridgesHandler),
        (r"/ws", CaptureWebSocketHandler),
        # Latency API endpoints (legacy, kept for backwards compatibility)
        (r"/latency/bridges", LatencyBridgesHandler),
        (r"/latency/enable", LatencyEnableHandler),
        (r"/latency/disable", LatencyDisableHandler),
        (r"/latency/disable-all", LatencyDisableAllHandler),
        # Impairment API endpoints (unified control)
        (r"/impairments/bridges", ImpairmentsBridgesHandler),
        (r"/impairments/configure", ImpairmentsConfigureHandler),
        (r"/impairments/clear", ImpairmentsClearHandler),
        (r"/impairments/clear-all", ImpairmentsClearAllHandler),
    ])


def main():
    tornado.options.parse_command_line()

    app = make_app()

    # Listen on all interfaces (IP-based access control is in handlers)
    # Required for Docker containers to reach this service
    app.listen(options.port, address="0.0.0.0")
    print(f"[CaptureService] Listening on 0.0.0.0:{options.port}")
    print(f"[CaptureService] Access restricted to: {', '.join(ALLOWED_IP_PREFIXES)}")

    ioloop = tornado.ioloop.IOLoop.current()
    get_manager().start_cleanup_task(ioloop)

    try:
        ioloop.start()
    except KeyboardInterrupt:
        print("[CaptureService] Shutting down...")
        get_manager().shutdown()


if __name__ == "__main__":
    main()

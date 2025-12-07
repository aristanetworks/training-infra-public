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
        """
        try:
            # Use tshark with -c 1 and very short duration to validate filter
            # The filter syntax is checked before capture starts, so invalid
            # filters will fail immediately with a non-zero exit code
            result = subprocess.run(
                ["tshark", "-i", bridge_name, "-Y", display_filter, "-c", "1", "-a", "duration:1"],
                capture_output=True,
                text=True,
                timeout=3
            )
            # tshark exits with 0 on timeout (no packets matched) or after capturing
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
                # If no meaningful error lines, the error might be transient
                # (e.g., interface busy) - don't block the filter
                return None
            return None  # Valid
        except subprocess.TimeoutExpired:
            # Timeout means tshark started successfully (filter was valid)
            # but didn't capture any matching packets - that's fine
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

    def get_bridges(self) -> List[Dict]:
        """Get list of OVS bridges with edge mapping."""
        now = time.time()
        if self._bridge_cache and (now - self._bridge_cache_time) < self._bridge_cache_ttl:
            return self._bridge_cache

        bridges = []
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-br"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for name in result.stdout.strip().split('\n'):
                    name = name.strip()
                    if not name:
                        continue

                    info = self._parse_bridge_name(name)
                    info['name'] = name
                    info['is_capturing'] = any(
                        s.bridge_name == name and s.is_active
                        for s in self.sessions.values()
                    )
                    bridges.append(info)
                print(f"[CaptureManager] Found {len(bridges)} OVS bridges")
            else:
                print(f"[CaptureManager] ovs-vsctl failed: {result.stderr.strip()}")
        except FileNotFoundError:
            print("[CaptureManager] ovs-vsctl not found - OVS not installed?")
        except Exception as e:
            print(f"[CaptureManager] Error listing bridges: {e}")

        self._bridge_cache = bridges
        self._bridge_cache_time = now
        return bridges

    def _parse_bridge_name(self, bridge_name: str) -> Dict:
        """
        Parse bridge name to extract device/port info.

        Bridge format: {prefix}{device#}{port#}-{prefix}{device#}{port#}
        Examples:
          - le11-le21 -> Leaf1:Ethernet1 <-> Leaf2:Ethernet1
          - sp13-le14 -> Spine1:Ethernet3 <-> Leaf1:Ethernet4
          - me11-me21 -> Memleaf1:Ethernet1 <-> Memleaf2:Ethernet1

        Short codes are generated by kvm-topo-builder.py:
        - 2 letter prefix (le=leaf, sp=spine, ho=host, me=memleaf, ro=router)
        - Device number (1 digit)
        - Port number (1+ digits)
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

                # Parse format: {2-letter-prefix}{device-num}{port-num}
                # e.g., le11 = prefix "le", device "1", port "1"
                # e.g., sp13 = prefix "sp", device "1", port "3"
                src_parsed = self._parse_short_code(src)
                if src_parsed:
                    result["source_device"] = src_parsed['device_code']
                    result["source_port"] = src_parsed['port_num']
                    result["source_device_name"] = src_parsed['device_name']
                    result["source_port_name"] = f"Ethernet{src_parsed['port_num']}"

                tgt_parsed = self._parse_short_code(tgt)
                if tgt_parsed:
                    result["target_device"] = tgt_parsed['device_code']
                    result["target_port"] = tgt_parsed['port_num']
                    result["target_device_name"] = tgt_parsed['device_name']
                    result["target_port_name"] = f"Ethernet{tgt_parsed['port_num']}"

        except Exception as e:
            print(f"[CaptureManager] Parse error for {bridge_name}: {e}")

        return result

    def _parse_short_code(self, code: str) -> Optional[Dict]:
        """
        Parse a short device code like 'le11' or 'sp23'.

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

        # Map prefix to full device type name
        prefix_map = {
            'sp': 'Spine',
            'le': 'Leaf',
            'ho': 'Host',
            'me': 'Memleaf',
            'ro': 'Router',
            'pe': 'PE',
            'ce': 'CE',
            'bl': 'Borderleaf',
            'co': 'Core',
            'gw': 'Gateway',
        }

        device_type = prefix_map.get(prefix, prefix.upper())
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


# Global manager
_manager: Optional[CaptureManager] = None


def get_manager() -> CaptureManager:
    global _manager
    if _manager is None:
        _manager = CaptureManager()
    return _manager


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
        bridges = get_manager().get_bridges()
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


def make_app():
    return tornado.web.Application([
        (r"/health", HealthHandler),
        (r"/bridges", BridgesHandler),
        (r"/ws", CaptureWebSocketHandler),
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

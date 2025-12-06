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
import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, List, Callable, Set

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


class PacketParser:
    """Parse tcpdump output lines into structured packet data."""

    VXLAN_PORT = 4789

    # Protocol display mapping
    PROTOCOL_NAMES = {
        '0800': 'IPv4',
        '0806': 'ARP',
        '86dd': 'IPv6',
        '8100': '802.1Q',
    }

    def parse_line(self, line: str, packet_number: int) -> Optional[Dict]:
        """Parse a single tcpdump output line."""
        if not line or line.startswith('tcpdump:') or 'listening on' in line:
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
            'info': '',
            'ethertype': '',
            'ethertype_name': '',
            'is_vxlan': False,
            'vxlan_vni': None,
            'inner_src_mac': '',
            'inner_dst_mac': '',
            'inner_src_ip': '',
            'inner_dst_ip': '',
            'inner_protocol': '',
        }

        try:
            # Parse timestamp (format: 2024-01-15 10:30:45.123456)
            ts_match = re.match(r'^(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\.\d+)', line)
            if ts_match:
                packet['timestamp'] = ts_match.group(1)
                line = line[len(ts_match.group(0)):].strip()

            # Parse ethernet header (format: aa:bb:cc:dd:ee:ff > 11:22:33:44:55:66)
            eth_match = re.match(r'^([0-9a-f:]{17})\s*>\s*([0-9a-f:]{17})', line, re.I)
            if eth_match:
                packet['src_mac'] = eth_match.group(1)
                packet['dst_mac'] = eth_match.group(2)
                line = line[len(eth_match.group(0)):].strip()

            # Parse ethertype
            etype_match = re.search(r'ethertype\s+(\S+)\s+\(0x([0-9a-f]+)\)', line, re.I)
            if etype_match:
                packet['ethertype_name'] = etype_match.group(1)
                packet['ethertype'] = etype_match.group(2)
                packet['protocol'] = self.PROTOCOL_NAMES.get(
                    packet['ethertype'].lower(),
                    packet['ethertype_name']
                )

            # Parse length
            len_match = re.search(r'length\s+(\d+)', line, re.I)
            if len_match:
                packet['length'] = int(len_match.group(1))

            # Parse IP addresses
            ip_match = re.search(
                r'(\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?\s*>\s*(\d+\.\d+\.\d+\.\d+)(?:\.(\d+))?',
                line
            )
            if ip_match:
                packet['src_ip'] = ip_match.group(1)
                packet['src_port'] = int(ip_match.group(2)) if ip_match.group(2) else None
                packet['dst_ip'] = ip_match.group(3)
                packet['dst_port'] = int(ip_match.group(4)) if ip_match.group(4) else None

            # Detect protocol from content
            if 'ICMP' in line or 'icmp' in line:
                packet['protocol'] = 'ICMP'
                icmp_match = re.search(r'ICMP\s+(.+?)(?:,|$)', line)
                if icmp_match:
                    packet['info'] = 'ICMP ' + icmp_match.group(1).strip()
            elif 'ARP' in line or 'arp' in line:
                packet['protocol'] = 'ARP'
                if 'Request' in line:
                    packet['info'] = 'ARP Request'
                elif 'Reply' in line:
                    packet['info'] = 'ARP Reply'
                arp_detail = re.search(r'(who-has|tell|is-at)\s+(\S+)', line)
                if arp_detail:
                    packet['info'] += f' {arp_detail.group(1)} {arp_detail.group(2)}'
            elif packet['dst_port'] == self.VXLAN_PORT or packet['src_port'] == self.VXLAN_PORT:
                packet['protocol'] = 'VXLAN'
                packet['is_vxlan'] = True
                # Try to parse VNI
                vni_match = re.search(r'vni\s+(\d+)', line, re.I)
                if vni_match:
                    packet['vxlan_vni'] = int(vni_match.group(1))
                # Parse inner frame
                inner_match = re.search(
                    r'([0-9a-f:]{17})\s*>\s*([0-9a-f:]{17}).*?(\d+\.\d+\.\d+\.\d+)\s*>\s*(\d+\.\d+\.\d+\.\d+)',
                    line[line.find('VXLAN'):] if 'VXLAN' in line else '',
                    re.I
                )
                if inner_match:
                    packet['inner_src_mac'] = inner_match.group(1)
                    packet['inner_dst_mac'] = inner_match.group(2)
                    packet['inner_src_ip'] = inner_match.group(3)
                    packet['inner_dst_ip'] = inner_match.group(4)
            elif packet['dst_port'] == 80 or packet['src_port'] == 80:
                packet['protocol'] = 'HTTP'
            elif packet['dst_port'] == 443 or packet['src_port'] == 443:
                packet['protocol'] = 'HTTPS'
            elif packet['dst_port'] == 22 or packet['src_port'] == 22:
                packet['protocol'] = 'SSH'
            elif packet['dst_port'] == 179 or packet['src_port'] == 179:
                packet['protocol'] = 'BGP'
            elif 'UDP' in line or 'udp' in line:
                packet['protocol'] = 'UDP'
            elif 'TCP' in line or 'tcp' in line or 'Flags' in line:
                packet['protocol'] = 'TCP'
                # Parse TCP flags
                flags_match = re.search(r'Flags\s+\[([^\]]+)\]', line)
                if flags_match:
                    packet['info'] = f'TCP [{flags_match.group(1)}]'

            # Build info string if not set
            if not packet['info']:
                parts = []
                if packet['src_ip'] and packet['dst_ip']:
                    src = packet['src_ip']
                    dst = packet['dst_ip']
                    if packet['src_port']:
                        src += f':{packet["src_port"]}'
                    if packet['dst_port']:
                        dst += f':{packet["dst_port"]}'
                    parts.append(f'{src} > {dst}')
                if packet['length']:
                    parts.append(f'Len={packet["length"]}')
                packet['info'] = ' '.join(parts)

            return packet

        except Exception as e:
            # Return basic packet on parse error
            packet['info'] = line[:100] if len(line) > 100 else line
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

            # Validate BPF filter syntax
            bpf_error = self._validate_bpf_filter(bpf_filter)
            if bpf_error:
                return {"error": bpf_error}

            # Dry-run tcpdump to validate BPF filter compiles correctly
            if bpf_filter:
                bpf_test_error = self._test_bpf_filter(bridge_name, bpf_filter)
                if bpf_test_error:
                    return {"error": f"Invalid BPF filter: {bpf_test_error}"}

            session_id = str(uuid.uuid4())[:8]

            # Ensure the bridge interface is up (OVS bridges are often down by default)
            try:
                subprocess.run(
                    ["ip", "link", "set", "dev", bridge_name, "up"],
                    capture_output=True,
                    timeout=5
                )
                print(f"[CaptureManager] Brought interface {bridge_name} up")
            except Exception as e:
                print(f"[CaptureManager] Warning: Could not bring interface up: {e}")

            # Build tcpdump command
            cmd = [
                "tcpdump",
                "-i", bridge_name,
                "-l",       # Line-buffered
                "-nn",      # No name resolution
                "-tttt",    # Human-readable timestamps
                "-e",       # Show ethernet header
                "-s", "0",  # Full packets
                "-v",       # Verbose
            ]
            if bpf_filter:
                # BPF filter is already validated, safe to append
                cmd.append(bpf_filter)

            try:
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1
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

                # Start reader thread
                reader = threading.Thread(
                    target=self._read_output,
                    args=(session,),
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
                return {"error": "tcpdump not installed"}
            except PermissionError:
                return {"error": "Permission denied - need NET_ADMIN"}
            except Exception as e:
                return {"error": str(e)}

    def _read_output(self, session: CaptureSession):
        """Read tcpdump output and send to WebSocket."""
        parser = PacketParser()
        ioloop = tornado.ioloop.IOLoop.current()
        local_packet_count = 0  # Local counter to avoid race conditions

        print(f"[CaptureManager] Reader thread started for {session.session_id} on {session.bridge_name}")

        # Check for any stderr output (tcpdump errors)
        if session.process and session.process.stderr:
            import select
            # Non-blocking check for stderr
            try:
                import os
                import fcntl
                fd = session.process.stderr.fileno()
                fl = fcntl.fcntl(fd, fcntl.F_GETFL)
                fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
                stderr_data = session.process.stderr.read()
                if stderr_data:
                    print(f"[CaptureManager] tcpdump stderr: {stderr_data}")
            except Exception as e:
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
                        print(f"[CaptureManager] tcpdump exited with code {session.process.returncode}")
                        # Try to get stderr
                        try:
                            stderr = session.process.stderr.read()
                            if stderr:
                                print(f"[CaptureManager] tcpdump stderr: {stderr}")
                        except:
                            pass
                    break

                line = line.strip()
                if not line:
                    continue

                # Debug: log first few packets
                if local_packet_count < 3:
                    print(f"[CaptureManager] Raw line {local_packet_count + 1}: {line[:100]}...")

                local_packet_count += 1
                packet = parser.parse_line(line, local_packet_count)

                if packet:
                    # Update session packet count atomically
                    with self._lock:
                        session.packet_count = local_packet_count

                    packet['number'] = local_packet_count

                    # Send via WebSocket (thread-safe via IOLoop callback)
                    # Capture websocket reference to avoid race with None assignment
                    ws = session.websocket
                    if ws:
                        def send(websocket, pkt):
                            try:
                                if websocket and websocket.ws_connection:
                                    websocket.write_message(json.dumps({
                                        'type': 'packet',
                                        'data': pkt
                                    }))
                            except Exception as e:
                                print(f"[CaptureManager] WebSocket send error: {e}")

                        ioloop.add_callback(send, ws, packet)

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

    def _validate_bpf_filter(self, bpf: str) -> Optional[str]:
        """
        Validate BPF filter syntax to prevent injection.
        Returns None if valid, or error message if invalid.
        """
        if not bpf:
            return None  # Empty filter is valid

        # Length limit
        if len(bpf) > 500:
            return "BPF filter too long (max 500 characters)"

        # Character whitelist - only allow safe BPF syntax characters
        if not re.match(r'^[a-zA-Z0-9\s\(\)\[\]\.\:\-\|&!<>=]+$', bpf):
            return "BPF filter contains invalid characters"

        # Check for dangerous patterns
        dangerous = ['--', ';', '$', '`', '\n', '\r', '\\', "'", '"', '/', '#']
        for d in dangerous:
            if d in bpf:
                return f"BPF filter contains invalid pattern: {d}"

        # Check parenthesis nesting depth to prevent DoS
        depth = 0
        max_depth = 0
        for char in bpf:
            if char == '(':
                depth += 1
                max_depth = max(max_depth, depth)
            elif char == ')':
                depth -= 1
            if depth < 0:
                return "Unbalanced parentheses in BPF filter"

        if depth != 0:
            return "Unbalanced parentheses in BPF filter"

        if max_depth > self.MAX_BPF_NESTING:
            return f"BPF filter too complex (max nesting depth: {self.MAX_BPF_NESTING})"

        # Check for excessively repetitive patterns (DoS prevention)
        words = bpf.split()
        if len(words) > 50:
            return "BPF filter has too many terms (max 50)"

        return None  # Valid

    def _test_bpf_filter(self, bridge_name: str, bpf_filter: str) -> Optional[str]:
        """
        Test BPF filter by running tcpdump in dump mode.
        Returns None if valid, or error message if invalid.
        """
        try:
            # Use -d to dump compiled filter (doesn't capture, just validates)
            result = subprocess.run(
                ["tcpdump", "-i", bridge_name, "-d", bpf_filter],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode != 0:
                # Extract useful error from stderr
                error = result.stderr.strip()
                if 'syntax error' in error.lower():
                    return "Syntax error in filter expression"
                return error[:100] if len(error) > 100 else error or "Unknown filter error"
            return None  # Valid
        except subprocess.TimeoutExpired:
            return "Filter validation timed out"
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

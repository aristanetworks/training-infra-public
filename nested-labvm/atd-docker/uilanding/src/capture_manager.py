"""
Packet Capture Manager for ATL Platform

Manages packet capture sessions on OVS bridges connecting topology devices.
Provides real-time packet streaming via WebSocket and pcap file downloads.
"""

import subprocess
import threading
import os
import time
import uuid
from datetime import datetime
from typing import Dict, Optional, List, Callable
from dataclasses import dataclass, field


@dataclass
class CaptureSession:
    """Represents a single packet capture session."""
    session_id: str
    bridge_name: str
    client_id: str
    process: Optional[subprocess.Popen] = None
    pcap_file: Optional[str] = None
    start_time: datetime = field(default_factory=datetime.now)
    packet_count: int = 0
    is_active: bool = True
    bpf_filter: str = ""
    # Callback for sending packets to WebSocket
    packet_callback: Optional[Callable] = None
    # Thread for reading process output
    reader_thread: Optional[threading.Thread] = None

    def elapsed_seconds(self) -> float:
        """Get elapsed time since capture started."""
        return (datetime.now() - self.start_time).total_seconds()


class CaptureManager:
    """
    Manages all active packet capture sessions.

    Responsible for:
    - Starting/stopping tcpdump processes on OVS bridges
    - Enforcing resource limits (max sessions, duration, packets)
    - Discovering available bridges from topology
    - Cleanup of stale sessions and temp files
    """

    # Configuration
    MAX_SESSIONS_PER_USER = 1  # Single capture at a time
    MAX_TOTAL_SESSIONS = 5     # Total across all users
    MAX_DURATION_SECONDS = 300  # 5 minutes auto-stop
    MAX_PACKETS = 10000        # Auto-stop after this many packets
    PCAP_DIR = "/tmp/atl_captures"

    def __init__(self):
        self.sessions: Dict[str, CaptureSession] = {}
        self._lock = threading.Lock()
        self._cleanup_thread: Optional[threading.Thread] = None
        self._running = True

        # Ensure pcap directory exists
        os.makedirs(self.PCAP_DIR, exist_ok=True)

        # Start cleanup thread
        self._start_cleanup_thread()

    def _start_cleanup_thread(self):
        """Start background thread to cleanup stale sessions."""
        def cleanup_loop():
            while self._running:
                try:
                    self._cleanup_stale_sessions()
                except Exception as e:
                    print(f"[CaptureManager] Cleanup error: {e}")
                time.sleep(10)  # Check every 10 seconds

        self._cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _cleanup_stale_sessions(self):
        """Stop sessions that have exceeded time or packet limits."""
        with self._lock:
            stale_sessions = []
            # Use list() to create a copy, allowing safe modification during iteration
            for session_id, session in list(self.sessions.items()):
                if not session.is_active:
                    stale_sessions.append(session_id)
                    continue

                # Check duration limit
                if session.elapsed_seconds() > self.MAX_DURATION_SECONDS:
                    print(f"[CaptureManager] Session {session_id} exceeded duration limit")
                    stale_sessions.append(session_id)
                    continue

                # Check packet limit
                if session.packet_count >= self.MAX_PACKETS:
                    print(f"[CaptureManager] Session {session_id} exceeded packet limit")
                    stale_sessions.append(session_id)

            # Stop stale sessions while still holding the lock
            # This prevents race conditions with other operations
            for session_id in stale_sessions:
                self._stop_session_unsafe(session_id, reason="limit_exceeded")

    def get_user_session_count(self, client_id: str) -> int:
        """Get number of active sessions for a user."""
        with self._lock:
            return sum(1 for s in self.sessions.values()
                      if s.client_id == client_id and s.is_active)

    def get_user_active_session(self, client_id: str) -> Optional[CaptureSession]:
        """Get active session for a user (if any)."""
        with self._lock:
            for session in self.sessions.values():
                if session.client_id == client_id and session.is_active:
                    return session
            return None

    def start_capture(
        self,
        bridge_name: str,
        client_id: str,
        packet_callback: Optional[Callable] = None,
        bpf_filter: str = ""
    ) -> Dict:
        """
        Start a packet capture session on an OVS bridge.

        Args:
            bridge_name: Name of the OVS bridge to capture on
            client_id: Unique identifier for the client/user
            packet_callback: Function to call with each parsed packet
            bpf_filter: Optional BPF filter expression

        Returns:
            Dict with session_id on success, or error message on failure
        """
        with self._lock:
            # Check user session limit
            user_sessions = sum(1 for s in self.sessions.values()
                               if s.client_id == client_id and s.is_active)
            if user_sessions >= self.MAX_SESSIONS_PER_USER:
                return {"error": "Maximum concurrent captures reached. Stop existing capture first."}

            # Check total session limit
            active_sessions = sum(1 for s in self.sessions.values() if s.is_active)
            if active_sessions >= self.MAX_TOTAL_SESSIONS:
                return {"error": "System capture limit reached. Try again later."}

            # Validate bridge name format (prevent injection)
            if not self._validate_bridge_name(bridge_name):
                return {"error": "Invalid bridge name format"}

            # Validate bridge exists
            if not self._bridge_exists(bridge_name):
                return {"error": f"Bridge '{bridge_name}' not found"}

            # Validate BPF filter (prevent command injection)
            if bpf_filter and not self._validate_bpf_filter(bpf_filter):
                return {"error": "Invalid BPF filter syntax"}

            # Generate session ID
            session_id = str(uuid.uuid4())[:8]

            # Create pcap file path
            pcap_file = os.path.join(
                self.PCAP_DIR,
                f"capture_{session_id}_{int(time.time())}.pcap"
            )

            # Build tcpdump command
            # -i: interface, -l: line-buffered, -nn: no name resolution
            # -tttt: readable timestamps, -e: show ethernet header
            # -s0: capture full packets, -U: packet-buffered output
            cmd = [
                "tcpdump",
                "-i", bridge_name,
                "-l",           # Line-buffered for real-time
                "-nn",          # Don't resolve names
                "-tttt",        # Human-readable timestamps
                "-e",           # Show ethernet header
                "-s", "0",      # Full packet capture
                "-v",           # Verbose
            ]

            # Add BPF filter if provided
            if bpf_filter:
                cmd.append(bpf_filter)

            try:
                # Start tcpdump process
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1  # Line-buffered
                )

                # Create session
                session = CaptureSession(
                    session_id=session_id,
                    bridge_name=bridge_name,
                    client_id=client_id,
                    process=process,
                    pcap_file=pcap_file,
                    bpf_filter=bpf_filter,
                    packet_callback=packet_callback
                )

                self.sessions[session_id] = session

                # Start reader thread
                reader_thread = threading.Thread(
                    target=self._read_process_output,
                    args=(session,),
                    daemon=True
                )
                session.reader_thread = reader_thread
                reader_thread.start()

                print(f"[CaptureManager] Started capture {session_id} on {bridge_name}")

                return {
                    "session_id": session_id,
                    "bridge": bridge_name,
                    "started_at": session.start_time.isoformat()
                }

            except FileNotFoundError:
                return {"error": "tcpdump not installed or not in PATH"}
            except PermissionError:
                return {"error": "Permission denied. Capture requires NET_ADMIN capability."}
            except Exception as e:
                return {"error": f"Failed to start capture: {str(e)}"}

    def _read_process_output(self, session: CaptureSession):
        """Read tcpdump output and send to callback."""
        # Import here to avoid circular dependency
        from packet_parser import PacketParser
        parser = PacketParser()

        try:
            while session.is_active and session.process:
                line = session.process.stdout.readline()
                if not line:
                    # Process ended
                    break

                line = line.strip()
                if not line:
                    continue

                # Parse the line
                packet = parser.parse_line(line, session.packet_count + 1)
                if packet:
                    session.packet_count += 1
                    packet['number'] = session.packet_count

                    # Call the callback if provided
                    if session.packet_callback:
                        try:
                            session.packet_callback(packet)
                        except Exception as e:
                            print(f"[CaptureManager] Callback error: {e}")

                    # Check packet limit
                    if session.packet_count >= self.MAX_PACKETS:
                        print(f"[CaptureManager] Session {session.session_id} hit packet limit")
                        break

        except Exception as e:
            print(f"[CaptureManager] Reader error for {session.session_id}: {e}")
        finally:
            session.is_active = False

    def stop_capture(self, session_id: str, client_id: Optional[str] = None) -> Dict:
        """
        Stop an active capture session.

        Args:
            session_id: ID of the session to stop
            client_id: Optional client ID for authorization check

        Returns:
            Dict with status or error message
        """
        with self._lock:
            if session_id not in self.sessions:
                return {"error": "Session not found"}

            session = self.sessions[session_id]

            # Check authorization if client_id provided
            if client_id and session.client_id != client_id:
                return {"error": "Not authorized to stop this capture"}

            return self._stop_session_unsafe(session_id, reason="user")

    def _stop_session_unsafe(self, session_id: str, reason: str = "unknown") -> Dict:
        """Stop a session without lock (caller must hold lock)."""
        if session_id not in self.sessions:
            return {"error": "Session not found"}

        session = self.sessions[session_id]

        if not session.is_active:
            return {"status": "already_stopped", "packet_count": session.packet_count}

        session.is_active = False

        # Terminate the tcpdump process
        if session.process:
            try:
                session.process.terminate()
                session.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                session.process.kill()
            except Exception as e:
                print(f"[CaptureManager] Error stopping process: {e}")

        print(f"[CaptureManager] Stopped session {session_id}, reason: {reason}, packets: {session.packet_count}")

        return {
            "status": "stopped",
            "session_id": session_id,
            "reason": reason,
            "packet_count": session.packet_count,
            "duration": session.elapsed_seconds()
        }

    def get_session_status(self, session_id: str) -> Optional[Dict]:
        """Get status of a capture session."""
        with self._lock:
            if session_id not in self.sessions:
                return None

            session = self.sessions[session_id]
            return {
                "session_id": session_id,
                "bridge": session.bridge_name,
                "is_active": session.is_active,
                "packet_count": session.packet_count,
                "duration": session.elapsed_seconds(),
                "started_at": session.start_time.isoformat(),
                "bpf_filter": session.bpf_filter
            }

    def get_all_sessions(self, client_id: Optional[str] = None) -> List[Dict]:
        """Get status of all sessions, optionally filtered by client."""
        with self._lock:
            sessions = []
            for session_id, session in self.sessions.items():
                if client_id and session.client_id != client_id:
                    continue
                sessions.append({
                    "session_id": session_id,
                    "bridge": session.bridge_name,
                    "is_active": session.is_active,
                    "packet_count": session.packet_count,
                    "duration": session.elapsed_seconds(),
                    "started_at": session.start_time.isoformat()
                })
            return sessions

    def _validate_bridge_name(self, bridge_name: str) -> bool:
        """Validate bridge name format to prevent command injection."""
        import re
        if not bridge_name:
            return False

        # Length limit
        if len(bridge_name) > 255:
            return False

        # Whitelist: alphanumeric, hyphens, underscores only
        if not re.match(r'^[a-zA-Z0-9\-_]+$', bridge_name):
            return False

        return True

    def _validate_bpf_filter(self, bpf_filter: str) -> bool:
        """Validate BPF filter syntax to prevent command injection."""
        import re
        if not bpf_filter:
            return True

        # Length limit
        if len(bpf_filter) > 1000:
            return False

        # Whitelist allowed characters for BPF syntax
        # Allows: alphanumeric, spaces, operators, brackets, dots, colons, comparisons
        if not re.match(r'^[a-zA-Z0-9\s\(\)\[\]\.\:\-\|&!<>=\/]+$', bpf_filter):
            return False

        # Blacklist dangerous patterns that could be shell injection
        dangerous = ['--', ';', '$', '`', '\n', '\r', '\\', "'", '"']
        if any(d in bpf_filter for d in dangerous):
            return False

        return True

    def _bridge_exists(self, bridge_name: str) -> bool:
        """Check if an OVS bridge exists."""
        try:
            result = subprocess.run(
                ["ovs-vsctl", "br-exists", bridge_name],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception:
            # If ovs-vsctl fails, try checking if interface exists
            try:
                result = subprocess.run(
                    ["ip", "link", "show", bridge_name],
                    capture_output=True,
                    timeout=5
                )
                return result.returncode == 0
            except Exception:
                return False

    def get_bridge_list(self) -> List[Dict]:
        """
        Get list of available OVS bridges with topology edge mapping.

        Returns:
            List of dicts with bridge name and connected devices/ports
        """
        bridges = []
        try:
            result = subprocess.run(
                ["ovs-vsctl", "list-br"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                for bridge_name in result.stdout.strip().split('\n'):
                    bridge_name = bridge_name.strip()
                    if not bridge_name:
                        continue

                    # Parse bridge name to extract device info
                    # Format: {dev1}{port1}-{dev2}{port2}
                    # Example: sp1Et1-le1Et1
                    bridge_info = self._parse_bridge_name(bridge_name)
                    bridge_info['name'] = bridge_name
                    bridge_info['is_capturing'] = self._is_bridge_capturing(bridge_name)
                    bridges.append(bridge_info)

        except FileNotFoundError:
            print("[CaptureManager] ovs-vsctl not found")
        except subprocess.TimeoutExpired:
            print("[CaptureManager] ovs-vsctl timed out")
        except Exception as e:
            print(f"[CaptureManager] Error listing bridges: {e}")

        return bridges

    def _parse_bridge_name(self, bridge_name: str) -> Dict:
        """
        Parse OVS bridge name to extract device and port info.

        Supports multiple bridge naming conventions:

        1. Nodebuilder format (with 'x' separator):
           fw1xet1-bo1x7 -> fw1:eth1 <-> borderleaf1:Ethernet7

        2. Legacy nodebuilder format (no separator):
           fw1et1-bo17 -> fw1:eth1 <-> borderleaf1:Ethernet7

        3. kvmbuilder format (full names):
           leaf3Et3-leaf1Et3 -> leaf3:Ethernet3 <-> leaf1:Ethernet3

        4. Mixed port prefixes:
           sp1et1-le1et1, client1eth1-sp4et9
        """
        result = {
            "source_device": "",
            "source_port": "",
            "target_device": "",
            "target_port": ""
        }

        if '-' not in bridge_name:
            return result

        try:
            parts = bridge_name.split('-')
            if len(parts) == 2:
                src = parts[0]
                tgt = parts[1]

                # Parse each part to find device/port boundary
                src_device, src_port = self._split_device_port(src)
                tgt_device, tgt_port = self._split_device_port(tgt)

                result["source_device"] = src_device
                result["source_port"] = src_port
                result["target_device"] = tgt_device
                result["target_port"] = tgt_port

        except Exception:
            pass

        return result

    def _split_device_port(self, part: str) -> tuple:
        """
        Split a device+port string into (device, port).

        Handles multiple formats:
        1. 'x' separator: 'fw1xet1' -> ('fw1', 'et1'), 'bo1x7' -> ('bo1', '7')
           Note: Only matches 'x' with digit before and digit/'e' after
           to avoid matching 'x' in device names like 'mux1'
        2. 'eth' prefix: 'client1eth1' -> ('client1', 'eth1')
        3. 'et' prefix: 'sp1et1' -> ('sp1', 'et1'), 'le1Et5' -> ('le1', 'Et5')
        4. Full names: 'leaf3Et3' -> ('leaf3', 'Et3')
        5. No separator (legacy): 'bo17' -> ('bo1', '7') via heuristic
        """
        lower = part.lower()

        # Check for 'x' separator (nodebuilder format)
        # The separator 'x' should have a digit before it (device code ends with number)
        # and either a digit or 'e' after it (port code starts with number or 'et/eth')
        # This avoids matching 'x' that's part of device name (e.g., 'mux1' -> 'mu1')
        for i, c in enumerate(lower):
            if c == 'x' and i > 0 and i < len(part) - 1:
                prev_char = lower[i - 1]
                next_char = lower[i + 1]
                # Valid separator: digit before, digit or 'e' (for et/eth) after
                if prev_char.isdigit() and (next_char.isdigit() or next_char == 'e'):
                    return part[:i], part[i + 1:]

        # Look for 'eth' (longer prefix, for Linux hosts)
        eth_idx = lower.find('eth')
        if eth_idx > 0:
            return part[:eth_idx], part[eth_idx:]

        # Look for 'et' (for Ethernet)
        et_idx = lower.find('et')
        if et_idx > 0:
            return part[:et_idx], part[et_idx:]

        # No separator or prefix found - try heuristic for legacy nodebuilder format
        # Format is {2-letter-prefix}{device-number}{port-number}
        # Example: 'bo17' -> 'bo' (prefix) + '1' (device) + '7' (port)
        # Heuristic: assume last 1-2 digits are port number for single-digit ports
        if len(part) >= 3:
            # Find where letters end
            letter_end = 0
            for i, c in enumerate(part):
                if c.isdigit():
                    letter_end = i
                    break

            if letter_end > 0 and letter_end < len(part):
                prefix = part[:letter_end]  # e.g., 'bo'
                numbers = part[letter_end:]  # e.g., '17'

                # For numbers like '17', assume last digit is port
                # For numbers like '112', assume last 2 digits are port
                if len(numbers) >= 2:
                    # Check if this could be device1 + port12 or device11 + port2
                    # Prefer single-digit ports as they're more common
                    device_num = numbers[:-1]  # All but last digit
                    port_num = numbers[-1]     # Last digit
                    return prefix + device_num, port_num

        # Fallback: return whole part as device with empty port
        return part, ""

    def _is_bridge_capturing(self, bridge_name: str) -> bool:
        """Check if a bridge currently has an active capture."""
        with self._lock:
            for session in self.sessions.values():
                if session.bridge_name == bridge_name and session.is_active:
                    return True
            return False

    def cleanup_session(self, session_id: str):
        """Remove a stopped session and its pcap file."""
        with self._lock:
            if session_id in self.sessions:
                session = self.sessions[session_id]

                # Don't cleanup active sessions
                if session.is_active:
                    return

                # Delete pcap file if exists
                if session.pcap_file and os.path.exists(session.pcap_file):
                    try:
                        os.remove(session.pcap_file)
                    except Exception as e:
                        print(f"[CaptureManager] Error removing pcap: {e}")

                del self.sessions[session_id]

    def shutdown(self):
        """Shutdown the capture manager and stop all sessions."""
        self._running = False

        with self._lock:
            for session_id in list(self.sessions.keys()):
                self._stop_session_unsafe(session_id, reason="shutdown")

        # Wait for cleanup thread
        if self._cleanup_thread:
            self._cleanup_thread.join(timeout=5)


# Global singleton instance
_capture_manager: Optional[CaptureManager] = None


def get_capture_manager() -> CaptureManager:
    """Get or create the global CaptureManager instance."""
    global _capture_manager
    if _capture_manager is None:
        _capture_manager = CaptureManager()
    return _capture_manager

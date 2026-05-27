"""Packet capture proxy handlers for UILanding.

Proxies WebSocket and HTTP requests to the captureservice container,
which runs with host network mode and has direct OVS bridge access.
"""

import json
import os
import traceback
import uuid

import tornado.web
import tornado.websocket
from ruamel.yaml import YAML
from tornado.httpclient import AsyncHTTPClient

from handlers.auth import BaseHandler
from utils import (safe_log, CAPTURE_SERVICE_URL, CAPTURE_SERVICE_URL_FALLBACK,
                   CAPTURE_WS_URL, CAPTURE_WS_URL_FALLBACK)


_TOPO_BUILD_CACHE = None


def _get_topo_build_data():
    """Load and cache topo_build.yml data. Returns cached data on subsequent calls."""
    global _TOPO_BUILD_CACHE

    if _TOPO_BUILD_CACHE is not None:
        return _TOPO_BUILD_CACHE

    # Determine topology path from ACCESS_INFO
    topo = None
    access_path = '/etc/atd/ACCESS_INFO.yaml'
    try:
        with open(access_path, 'r') as f:
            access_info = YAML().load(f)
        topo = access_info.get('topology', None)
    except Exception:
        pass

    if not topo:
        safe_log('warning', 'Could not determine topology for capture bridge enrichment',
                 event='config', handler='capture')
        _TOPO_BUILD_CACHE = {}
        return _TOPO_BUILD_CACHE

    topo_path = f"/opt/atd/topologies/{topo}/topo_build.yml"
    try:
        with open(topo_path, 'r') as f:
            _TOPO_BUILD_CACHE = YAML().load(f)
    except Exception as e:
        safe_log('error', f'Error loading topo_build.yml for capture enrichment: {e}',
                 event='error', handler='_get_topo_build_data')
        _TOPO_BUILD_CACHE = {}

    return _TOPO_BUILD_CACHE


# ===============================
# Packet Capture Handlers
# ===============================

class CaptureWebSocketHandler(tornado.websocket.WebSocketHandler):
    """
    WebSocket handler that proxies to the capture service.

    The uilanding container cannot access host OVS bridges directly.
    This handler connects to the captureservice container (running with
    host network mode) and relays packets to the browser client.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client_id = None
        self.current_user = None
        self.upstream_ws = None  # WebSocket connection to capture service
        self.is_connected = False

    def check_origin(self, origin):
        """Validate origin to prevent CSRF attacks."""
        host = self.request.headers.get('Host', '')
        if not host:
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            return parsed.netloc == host or parsed.netloc.split(':')[0] == host.split(':')[0]
        except Exception:
            return False

    async def open(self):
        """Handle new WebSocket connection from browser."""
        user = self.get_secure_cookie("user")
        if not user:
            safe_log('warning', 'Unauthenticated capture WS connection', event='capture', action='ws_reject')
            self.close(code=1008, reason="Authentication required")
            return

        self.current_user = user.decode() if isinstance(user, bytes) else str(user)
        self.client_id = str(uuid.uuid4())[:8]
        safe_log('info', 'Capture WebSocket opened', event='capture', action='ws_connect',
                 client_id=self.client_id, user=str(self.current_user))

        # Connect to upstream capture service
        await self.connect_upstream()

    async def connect_upstream(self):
        """Connect to the capture service WebSocket."""
        from tornado.websocket import websocket_connect
        import asyncio

        safe_log('info', 'Capture WS upstream connecting', event='capture', action='upstream_connect')

        try:
            # Try primary URL first (works on Docker Desktop)
            safe_log('info', f'Capture WS trying primary: {CAPTURE_WS_URL}', event='capture', action='upstream_primary')
            self.upstream_ws = await asyncio.wait_for(
                websocket_connect(
                    CAPTURE_WS_URL,
                    on_message_callback=self.on_upstream_message
                ),
                timeout=5.0
            )
            self.is_connected = True
            safe_log('info', f'Capture WS connected to {CAPTURE_WS_URL}', event='capture', action='upstream_connected')
        except Exception as e:
            safe_log('warning', f'Capture WS primary failed: {e}, trying fallback', event='capture', action='upstream_primary_failed')
            try:
                # Try fallback URL (works on Linux Docker)
                safe_log('info', f'Capture WS trying fallback: {CAPTURE_WS_URL_FALLBACK}', event='capture', action='upstream_fallback')
                self.upstream_ws = await asyncio.wait_for(
                    websocket_connect(
                        CAPTURE_WS_URL_FALLBACK,
                        on_message_callback=self.on_upstream_message
                    ),
                    timeout=5.0
                )
                self.is_connected = True
                safe_log('info', f'Capture WS connected to {CAPTURE_WS_URL_FALLBACK}', event='capture', action='upstream_connected')
            except Exception as e2:
                safe_log('error', f'Capture WS fallback also failed: {e2}', event='capture', action='upstream_all_failed')
                try:
                    self.write_message(json.dumps({
                        'type': 'error',
                        'message': 'Capture service unavailable. Is the captureservice container running?'
                    }))
                except Exception:
                    pass
                self.is_connected = False

    def on_upstream_message(self, message):
        """Handle message from capture service, relay to browser."""
        if message is None:
            # Upstream connection closed
            safe_log('info', 'Capture WS upstream connection closed', event='capture', action='upstream_closed')
            self.is_connected = False
            if self.ws_connection:
                self.write_message(json.dumps({
                    'type': 'error',
                    'message': 'Capture service connection lost'
                }))
            return

        # Relay message to browser client
        try:
            if self.ws_connection:
                self.write_message(message)
        except Exception as e:
            safe_log('error', f'Capture WS relay to browser failed: {e}', event='capture', action='relay_browser_error')

    def on_message(self, message):
        """Handle message from browser, relay to capture service."""
        if not self.is_connected or not self.upstream_ws:
            self.write_message(json.dumps({
                'type': 'error',
                'message': 'Not connected to capture service'
            }))
            return

        try:
            # Relay message to capture service
            self.upstream_ws.write_message(message)
        except Exception as e:
            safe_log('error', f'Capture WS relay to upstream failed: {e}', event='capture', action='relay_upstream_error')
            self.write_message(json.dumps({
                'type': 'error',
                'message': f'Failed to send to capture service: {e}'
            }))

    def on_close(self):
        """Handle WebSocket close from browser."""
        safe_log('info', 'Capture WebSocket closed', event='capture', action='ws_disconnect',
                 client_id=self.client_id)

        # Close upstream connection
        if self.upstream_ws:
            self.upstream_ws.close()
            self.upstream_ws = None
            self.is_connected = False


class CaptureBridgesAPIHandler(BaseHandler):
    """API endpoint to list available OVS bridges for capture."""

    async def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            http_client = AsyncHTTPClient()

            # Check for refresh parameter
            refresh = self.get_argument('refresh', '0')
            refresh_param = f"?refresh={refresh}" if refresh == '1' else ""

            # Try to fetch bridges from capture service
            bridges = []
            try:
                response = await http_client.fetch(
                    f"{CAPTURE_SERVICE_URL}/bridges{refresh_param}",
                    request_timeout=5
                )
                data = json.loads(response.body.decode('utf-8'))
                bridges = data.get('bridges', [])
            except Exception as e:
                safe_log('warning', f'CaptureBridges primary failed: {e}', event='proxy', handler='capture_bridges', action='primary_failed')
                try:
                    response = await http_client.fetch(
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/bridges{refresh_param}",
                        request_timeout=5
                    )
                    data = json.loads(response.body.decode('utf-8'))
                    bridges = data.get('bridges', [])
                except Exception as e2:
                    safe_log('error', f'CaptureBridges fallback also failed: {e2}', event='proxy', handler='capture_bridges', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({
                        'error': 'Capture service unavailable',
                        'bridges': []
                    }))
                    return

            # Enrich with topology edge info (map short codes to full device names)
            enriched_bridges = self.enrich_with_topology(bridges)

            self.write(json.dumps({
                'bridges': enriched_bridges,
                'count': len(enriched_bridges)
            }))

        except Exception as e:
            safe_log('error', f'Error in CaptureBridgesAPIHandler: {e}', event='error', handler='CaptureBridgesAPIHandler')
            safe_log('error', f'CaptureBridges error: {e}', event='proxy', handler='capture_bridges')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def enrich_with_topology(self, bridges):
        """Add topology edge information to bridges."""
        # Load topology data to map bridge names to device names
        topo_data = _get_topo_build_data()

        # Build device name lookup from short codes
        # This maps sp1 -> spine1, le1 -> leaf1, etc.
        device_lookup = {}
        if topo_data and 'nodes' in topo_data:
            for node_entry in topo_data['nodes']:
                if isinstance(node_entry, dict):
                    for device_name in node_entry.keys():
                        # Generate short code (same logic as kvm-topo-builder)
                        # Lowercase for consistent matching with bridge codes
                        short_code = self.get_short_code(device_name).lower()
                        device_lookup[short_code] = device_name

        # Also include user-added devices from persistence files
        user_files = [
            ('/etc/atd/user_nodes.yaml', 'nodes'),
            ('/etc/atd/user_hosts.yaml', 'hosts'),
            ('/etc/atd/user_firewalls.yaml', 'firewalls'),
        ]
        for user_file_path, key in user_files:
            try:
                if os.path.exists(user_file_path):
                    with open(user_file_path, 'r') as f:
                        user_data = YAML().load(f)
                    if user_data and key in user_data and user_data[key]:
                        for entry in user_data[key]:
                            if isinstance(entry, dict):
                                for device_name in entry.keys():
                                    short_code = self.get_short_code(device_name).lower()
                                    device_lookup[short_code] = device_name
            except Exception as e:
                safe_log('warning', f'Error loading {user_file_path} for bridge enrichment: {e}', event='config', handler='LatencyBridgesAPIHandler')

        # Enrich each bridge
        for bridge in bridges:
            src_code = bridge.get('source_device', '').lower()
            tgt_code = bridge.get('target_device', '').lower()

            if src_code in device_lookup:
                bridge['source_device_name'] = device_lookup[src_code]
            if tgt_code in device_lookup:
                bridge['target_device_name'] = device_lookup[tgt_code]

            # Port names are already parsed by nodebuilder API (source_port_name, target_port_name)
            # Only set defaults if not provided (e.g., fallback path didn't parse them)
            if not bridge.get('source_port_name') and bridge.get('source_port'):
                bridge['source_port_name'] = bridge['source_port']
            if not bridge.get('target_port_name') and bridge.get('target_port'):
                bridge['target_port_name'] = bridge['target_port']

        return bridges

    def get_short_code(self, device_name):
        """Generate short code for device name (matches kvm-topo-builder logic)."""
        alpha = ''
        numer = ''
        split_len = 2

        # Handle -DC suffix
        if '-dc' in device_name.lower() and 'dci' not in device_name.lower():
            parts = device_name.split('-')
            tmp_name = parts[0]
            dc_suffix = parts[1].lower().replace('c', '') if len(parts) > 1 else ''
            for char in tmp_name:
                if char.isalpha():
                    alpha += char
                elif char.isdigit():
                    numer += char
            return alpha[:split_len] + numer + dc_suffix
        else:
            for char in device_name:
                if char.isalpha():
                    alpha += char
                elif char.isdigit():
                    numer += char
            return alpha[:split_len] + numer


class CaptureStatusAPIHandler(BaseHandler):
    """API endpoint to get capture session status (placeholder - use WebSocket instead)."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        # Status is managed via WebSocket connection to capture service
        self.write(json.dumps({
            'message': 'Session status is managed via WebSocket connection',
            'sessions': []
        }))


class CaptureStartAPIHandler(BaseHandler):
    """API endpoint to start a capture (placeholder - use WebSocket instead)."""

    def post(self):
        safe_log('info', 'Packet capture started', event='capture', action='start')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        # Capture is started via WebSocket connection to capture service
        self.set_status(400)
        self.write(json.dumps({
            'error': 'Please use WebSocket connection at /capture-ws to start captures'
        }))


class CaptureStopAPIHandler(BaseHandler):
    """API endpoint to stop a capture (placeholder - use WebSocket instead)."""

    def post(self):
        safe_log('info', 'Packet capture stopped', event='capture', action='stop')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        # Capture is stopped via WebSocket connection to capture service
        self.set_status(400)
        self.write(json.dumps({
            'error': 'Please use WebSocket connection at /capture-ws to stop captures'
        }))

#!/usr/bin/env python3

from datetime import datetime, timedelta
from ruamel.yaml import YAML
from time import sleep
from base64 import b64decode, b64encode
import tornado.ioloop
import tornado.web
import tornado.websocket
import requests
import secrets
import hashlib, uuid
import json
import docker
import urllib3
import traceback
import os
import socket
import subprocess
import time
import threading
import queue
import pyeapi
from device_types import DeviceTypeConfig
from topology_converter import (
    TopologyConverterCurrentHandler,
    TopologyConverterAvailableHandler,
    TopologyConverterInfoHandler,
    TopologyConverterConvertHandler,
    TopologyConverterStatusHandler,
    TopologyConverterPageHandler,
)
# Note: capture_manager is no longer imported here.
# Packet capture runs in the dedicated captureservice container with host network mode.
# uilanding proxies WebSocket connections to the capture service.

from utils import (
    safe_log, pS, encodeID, decodeID, normalize_device_name,
    getAPI, getUptime, getEventStatus, genCookieSecret, update_hubspot_handler,
    CONNECTIVITY_LOG_PATH, CONNECTIVITY_LOG_MAX_BYTES
)
from handlers.auth import BaseHandler, LoginHandler
from handlers.lab import LabHandler, LabStausHandler, ResetLabHandler
from handlers.nodebuilder_proxy import NodeBuilderProxyHandler
from handlers.exam import (
    ExamSubmittedRedirectHandler,
    ExamAlreadyRunningHandler,
    ExamAuthenticationHandler,
    GetClientIdHandler,
    GetExamInstructionsHandler,
    GetUserSessionIdHandler,
    ExamStatusHandler,
    ExamSubmitHandler,
    ExamRedoRedirectHandler,
    BeginExamHandler,
    GetAccessInfoHandler,
    EndExamHandler,
)
from handlers.capture import (
    CaptureWebSocketHandler,
    CaptureBridgesAPIHandler,
    CaptureStatusAPIHandler,
    CaptureStartAPIHandler,
    CaptureStopAPIHandler,
)
from handlers.impairments import (
    ImpairmentsBridgesAPIHandler,
    ImpairmentsConfigureAPIHandler,
    ImpairmentsClearAPIHandler,
    ImpairmentsClearAllAPIHandler,
    LatencyBridgesAPIHandler,
    LatencyEnableAPIHandler,
    LatencyDisableAPIHandler,
    LatencyDisableAllAPIHandler,
)
from handlers.pages import (
    topoRequestHandler,
    ToolsHandler,
    ViewConfigHandler,
    BaseUrlHandler,
    UptimeWithRuntimeHandler,
    TerminalPageHandler,
    ConsolePageHandler,
    ClientLogHandler,
    ConnectivityStatusHandler,
)
from handlers.topology_api import (
    TopologyAPIHandler,
    DevicesAPIHandler,
    DeviceTypesAPIHandler,
    InterfaceStatsAPIHandler,
    DeviceStatusAPIHandler,
    RunningConfigAPIHandler,
    get_all_devices,
    get_device_ip_from_sources,
    invalidate_devices_cache,
    initialize as initialize_topology_api,
)

# Disable any TLS Warnings when getting instance Uptime
urllib3.disable_warnings()


PORT = 80
TOPO_API = 'atd-conftopo'

# Module-level Docker client (reuse connection, avoid per-request overhead)
try:
    DOCKER_CLIENT = docker.from_env(timeout=10)
except Exception:
    DOCKER_CLIENT = None
BASE_PATH = '/opt/topo/html/'
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'

ArBASE_PATH = '/opt/modules/'
MODULE_FILE = ArBASE_PATH + 'modules.yaml'
MENU_BASE_PATH = '/opt/menus/'
EXAM_END_TIME = 0
EXAM_START_TIME = 0
# Open yaml for the default yaml and read what file to lookup for default menu
MAX_STARTUP_WAIT = 300  # 5 minutes
default_menu_file_generated_flag = (os.path.join(MENU_BASE_PATH, 'labguides-done.txt'))
print ("Waiting for labguides-done.txt file existence to start the server")
_startup_wait_start = time.time()
while True:
    if os.path.exists(default_menu_file_generated_flag):
        print("Deleting labguides-done.txt file to start the server")
        os.remove(default_menu_file_generated_flag)
        break
    elif time.time() - _startup_wait_start > MAX_STARTUP_WAIT:
        print("WARNING: Timed out waiting for labguides-done.txt after 5 minutes, proceeding anyway")
        break
    else:
        print("labguides-done.txt file does not exist yet, waiting for 1 sec")
        sleep(1)
with open(MENU_BASE_PATH+'default.yaml', 'r') as default_menu_file:
    default_menu_info = YAML().load(default_menu_file)
if str(default_menu_info['default_menu']).lower() == 'ssh':
    NOMENUOPTIONFILE =True
else:
    # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
    NOMENUOPTIONFILE = False
    with open('/opt/menus/{0}'.format(default_menu_info['default_menu']), 'r') as menu_file:
        MENU_ITEMS = YAML().load(menu_file)
    DEFAULT_MENU_FILE_VALUE = default_menu_info['default_menu'].replace('.yaml', '')
    

with open(MODULE_FILE, 'r') as mf:
    MOD_YAML = YAML().load(mf)

# Add in check to make sure arista password has been updated
_startup_wait_start = time.time()
while True:
    with open(ATD_ACCESS_PATH, 'r') as f:
        host_yaml = YAML().load(f)
    if host_yaml['login_info']['jump_host']['pw'] == 'REPLACE_PWD':
        if time.time() - _startup_wait_start > MAX_STARTUP_WAIT:
            print("WARNING: Timed out waiting for password update after 5 minutes, proceeding anyway")
            break
        sleep(2)
    else:
        break

salt = uuid.uuid4().hex

accounts = {
    hashlib.sha512((host_yaml['login_info']['jump_host']['user'] + salt).encode('utf-8')).hexdigest(): hashlib.sha512((host_yaml['login_info']['jump_host']['pw'] + salt).encode('utf-8')).hexdigest()
}

# Get the topo project and update function
PROJECT = host_yaml['project']
FUNC_STATE = 'https://us-central1-{0}.cloudfunctions.net/atd-state'.format(PROJECT)
NAME = host_yaml['name']
ZONE = host_yaml['zone']
TOPO = host_yaml['topology']
EOS_TYPE = host_yaml.get('eos_type', 'veos')  # 'veos' or 'container-labs'
if 'schema' in host_yaml:
    SCHEMA = host_yaml['schema']
else:
    SCHEMA = 1
# Set title: prefer course_name from customer_details, fall back to title, then default
_course_name = host_yaml.get('customer_details', {}).get('course_name', '')
if _course_name:
    TITLE = _course_name
elif 'title' in host_yaml:
    TITLE = host_yaml['title']
else:
    TITLE = 'Arista Training Lab'

# Propagate config to topology_api module (called after all globals are resolved)
initialize_topology_api(TOPO, EOS_TYPE, TITLE, ATD_ACCESS_PATH)


def get_metadata_extract(attribute):
    try:
        metadata_url = "http://169.254.169.254/computeMetadata/v1/project/attributes/{}".format(attribute)
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers, timeout=5)
        if response.status_code == 200:
            return response.text
        else:
            return None
    except requests.exceptions.RequestException as e:
        safe_log('error', f'Error in get_metadata_extract: {e}', event='error', handler='get_metadata_extract')
        return None

HonorLockClientID = get_metadata_extract('honorlockClientID')
HonorLockSecret = get_metadata_extract('honorlockClientSecret')


# topoRequestHandler — moved to handlers/pages.py

# ===============================
# Internal CVP gRPC Health Check
# ===============================

# CVP internal address and gRPC check state
CVP_INTERNAL_IP = '192.168.0.5'
CVP_GRPC_ENDPOINT = '/arista.studio.v1.StudioService/GetAll'
_cvp_grpc_token = None
_cvp_grpc_token_expires = 0

def _get_cvp_token():
    """Fetch a CVP session token for internal gRPC checks"""
    global _cvp_grpc_token, _cvp_grpc_token_expires
    now = time.time()
    # Reuse token if less than 20 minutes old
    if _cvp_grpc_token and now < _cvp_grpc_token_expires:
        return _cvp_grpc_token
    try:
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']
        response = requests.post(
            'https://{0}/cvpservice/login/authenticate.do'.format(CVP_INTERNAL_IP),
            auth=(username, password),
            verify=False,
            timeout=5
        )
        token = response.json().get('sessionId')
        if token:
            _cvp_grpc_token = token
            _cvp_grpc_token_expires = now + 1200  # 20 minutes
            return token
    except Exception as e:
        safe_log('error', 'Failed to get CVP token for internal gRPC check',
            event='connectivity', action='internal_grpc_token_error',
            error=str(e))
    return None

def check_cvp_grpc_internal():
    """
    Internal gRPC-Web health check to CVP at 192.168.0.5
    Uses same framing and auth as the frontend check.
    Results logged to Cloud Logging for comparison with frontend reports.
    """
    token = _get_cvp_token()
    if not token:
        _grpc_state['status'] = 'skipped'
        _grpc_state['last_check'] = time.time()
        safe_log('warning', 'Internal gRPC check skipped - no token',
            event='connectivity', action='grpc_check', source='internal',
            status='skipped', reason='no_token')
        return

    # Same 5-byte gRPC-Web frame as frontend: flag(0x00) + length(0x00000000)
    grpc_frame = b'\x00\x00\x00\x00\x00'

    headers = {
        'Content-Type': 'application/grpc-web+proto',
        'Accept': 'application/grpc-web+proto',
        'x-grpc-web': '1',
        'Authorization': 'Bearer ' + token
    }

    try:
        response = requests.post(
            'https://{0}{1}'.format(CVP_INTERNAL_IP, CVP_GRPC_ENDPOINT),
            headers=headers,
            data=grpc_frame,
            verify=False,
            timeout=5
        )

        # Check for valid gRPC response
        grpc_status = response.headers.get('grpc-status')
        status_code = response.status_code

        if status_code == 200 or grpc_status is not None:
            # Check grpc-status if present
            if grpc_status is not None and int(grpc_status) == 14:
                _grpc_state['status'] = 'unavailable'
                safe_log('warning', 'Internal gRPC check: CVP unavailable',
                    event='connectivity', action='grpc_check', source='internal',
                    status='unavailable', http_status=str(status_code),
                    grpc_status=str(grpc_status))
            else:
                _grpc_state['status'] = 'ok'
                safe_log('info', 'Internal gRPC check passed',
                    event='connectivity', action='grpc_check', source='internal',
                    status='ok', http_status=str(status_code),
                    grpc_status=str(grpc_status) if grpc_status else '')
        elif status_code in (401, 403, 405):
            # Token may be stale, clear it
            global _cvp_grpc_token
            _cvp_grpc_token = None
            _grpc_state['status'] = 'auth_rejected'
            safe_log('warning', 'Internal gRPC check: auth rejected',
                event='connectivity', action='grpc_check', source='internal',
                status='auth_rejected', http_status=str(status_code))
        elif status_code in (502, 503, 504):
            _grpc_state['status'] = 'unreachable'
            safe_log('warning', 'Internal gRPC check: CVP unreachable',
                event='connectivity', action='grpc_check', source='internal',
                status='unreachable', http_status=str(status_code))
        else:
            _grpc_state['status'] = 'unexpected'
            safe_log('warning', 'Internal gRPC check: unexpected response',
                event='connectivity', action='grpc_check', source='internal',
                status='unexpected', http_status=str(status_code))
        _grpc_state['last_check'] = time.time()
    except requests.exceptions.Timeout:
        _grpc_state['status'] = 'timeout'
        _grpc_state['last_check'] = time.time()
        safe_log('warning', 'Internal gRPC check: timeout',
            event='connectivity', action='grpc_check', source='internal',
            status='timeout')
    except Exception as e:
        _grpc_state['status'] = 'error'
        _grpc_state['last_check'] = time.time()
        safe_log('error', 'Internal gRPC check failed',
            event='connectivity', action='grpc_check', source='internal',
            status='error', error=str(e))

# ===============================
# Connectivity Session Tracking
# ===============================

# Track recently closed sessions for reconnect detection
# Key: client_ip, Value: {'session_id': str, 'closed_at': datetime, 'reconnect_count': int}
recent_sessions = {}
active_sessions = set()  # Set of session IDs currently connected
active_session_data = {}  # Snapshot of session info keyed by session_id
# Mutable dict so ConnectivityStatusHandler can hold a reference and see live updates
_grpc_state = {'status': None, 'last_check': None}
RECONNECT_WINDOW_SECONDS = 300  # 5 minutes

def prune_recent_sessions():
    """Remove entries older than the reconnect window"""
    now = datetime.utcnow()
    expired = [ip for ip, data in recent_sessions.items()
               if (now - data['closed_at']).total_seconds() > RECONNECT_WINDOW_SECONDS]
    for ip in expired:
        del recent_sessions[ip]

class topoDataHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        # Prefer X-Real-IP from nginx, fall back to remote_ip
        client_ip = self.request.headers.get('X-Real-IP',
                    self.request.headers.get('X-Forwarded-For', self.request.remote_ip))
        # X-Forwarded-For can be comma-separated — take the first (original client)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()
        session_id = str(uuid.uuid4())
        user_agent = self.request.headers.get('User-Agent', '')[:200]

        # Check for reconnect
        prune_recent_sessions()
        reconnect_count = 0
        reconnect_gap = None
        if client_ip in recent_sessions:
            prev = recent_sessions[client_ip]
            reconnect_count = prev['reconnect_count'] + 1
            reconnect_gap = (datetime.utcnow() - prev['closed_at']).total_seconds()

        self._closed = False
        self.session = {
            'id': session_id,
            'connected_at': datetime.utcnow(),
            'reconnect_count': reconnect_count,
            'missed_pongs': 0,
            'last_pong': None,
            'last_rtt': None,
            'client_ip': client_ip,
            'debug_mode': False,
            'user_agent': user_agent,
            'last_token_send': 0,
            'client_id': ''
        }
        active_sessions.add(session_id)

        safe_log('info', 'WebSocket session started',
            event='connectivity', action='session_start',
            session_id=session_id,
            client_ip=client_ip,
            reconnect_count=str(reconnect_count),
            user_agent=user_agent)

        if reconnect_gap is not None:
            safe_log('info', 'Client reconnected',
                event='connectivity', action='reconnect',
                session_id=session_id,
                client_ip=client_ip,
                reconnect_gap_seconds=str(round(reconnect_gap, 1)),
                reconnect_count=str(reconnect_count))

        self.cvp_status = ''
        self.cvp_tasks = ''
        self.uptime = {}
        self.schedule_summary()
        pS("New backend websocket connection")
    
    async def on_message(self, message):
        try:
            recv = json.loads(message)
            cdata = recv['data']
            msg_type = recv['type']
            session_id = self.session['id'][:8] if hasattr(self, 'session') else '?'

            if msg_type == 'hello':
                # Store persistent client_id from frontend (survives page refreshes)
                client_id = cdata.get('client_id', '')
                if client_id and hasattr(self, 'session'):
                    self.session['client_id'] = client_id
                pS("[{}] WS hello - client_id={} sending status + session info".format(session_id, client_id[:12] if client_id else '?'))
                # Grab current uptime of topology (run in executor to avoid blocking)
                loop = tornado.ioloop.IOLoop.current()
                self.uptime = await loop.run_in_executor(None, getUptime, '192.168.0.1')
                # Get initial topology status
                self.cvp_status = await loop.run_in_executor(None, getAPI, "cvp_status")
                self.endexamtime = EXAM_END_TIME
                self.startExamTime = EXAM_START_TIME
                if self.cvp_status['status'] == 'UP':
                    self.cvp_tasks = await loop.run_in_executor(None, getAPI, "cvp_tasks")
                else:
                    self.cvp_tasks = ''
                self.sendData('status')
                self.send_session_info()
                self.schedule_update()

            elif msg_type == 'pong':
                if hasattr(self, 'session'):
                    server_ts = cdata.get('server_ts', 0)
                    now_ms = int(time.time() * 1000)
                    rtt = now_ms - server_ts if server_ts else None
                    self.session['last_pong'] = datetime.utcnow()
                    self.session['last_rtt'] = rtt
                    self.session['missed_pongs'] = 0
                    pS("[{}] WS pong rtt={}ms".format(session_id, rtt))

                    if self.session.get('debug_mode'):
                        safe_log('debug', 'Pong received',
                            event='connectivity', action='pong',
                            session_id=self.session['id'],
                            rtt_ms=str(rtt) if rtt else 'unknown')

            elif msg_type == 'connectivity':
                event_name = cdata.get('event', '?')
                pS("[{}] WS connectivity: {}".format(session_id, event_name))
                self.handle_connectivity_event(cdata)

            elif msg_type == 'debug_toggle':
                if hasattr(self, 'session'):
                    self.session['debug_mode'] = not self.session['debug_mode']
                    pS("[{}] WS debug_toggle -> {}".format(session_id, self.session['debug_mode']))
                    safe_log('info', 'Debug mode toggled',
                        event='connectivity', action='debug_toggle',
                        session_id=self.session['id'],
                        debug_mode=str(self.session['debug_mode']))
                    try:
                        self.write_message(json.dumps({
                            'type': 'debug_ack',
                            'data': {'debug_mode': self.session['debug_mode']}
                        }))
                    except Exception:
                        pass

            elif msg_type == 'update':
                pass  # ACK from frontend status receipt — no action needed

            else:
                pS("[{}] WS unknown type: {}".format(session_id, msg_type))

        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.on_message: {e}', event='error', handler='topoDataHandler')
            pS("WS ERROR")

    def schedule_update(self):
        try:
            self.timeout = tornado.ioloop.IOLoop.current().call_later(30, self._run_keepalive)
        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.schedule_update: {e}', event='error', handler='topoDataHandler')

    def _run_keepalive(self):
        """Bridge between call_later (sync callback) and async keepalive."""
        tornado.ioloop.IOLoop.current().spawn_callback(self.keepalive)

    async def keepalive(self):
        if getattr(self, '_closed', True):
            return
        try:
            loop = tornado.ioloop.IOLoop.current()
            # Run blocking HTTP calls in executor to avoid freezing the event loop
            self.uptime = await loop.run_in_executor(None, getUptime, '192.168.0.1')
            self.endexamtime = EXAM_END_TIME
            self.startExamTime = EXAM_START_TIME
            self.cvp_status = await loop.run_in_executor(None, getAPI, "cvp_status")
            if self.cvp_status['status'] == 'UP':
                self.cvp_tasks = await loop.run_in_executor(None, getAPI, "cvp_tasks")
            else:
                self.cvp_tasks = ''
            self.sendData('status')

            # Send timestamped ping for latency measurement
            # Include internal gRPC status when available for synchronized checks
            ping_data = {'ts': int(time.time() * 1000)}
            if _grpc_state['status'] is not None:
                ping_data['internal_grpc'] = _grpc_state['status']
            if hasattr(self, 'session') and self.session['last_rtt'] is not None:
                ping_data['server_rtt'] = self.session['last_rtt']
            self.write_message(json.dumps({
                'type': 'ping',
                'data': ping_data
            }))

            # Check for missed pongs
            if hasattr(self, 'session'):
                if self.session['last_pong'] is not None:
                    pong_age = (datetime.utcnow() - self.session['last_pong']).total_seconds()
                    if pong_age > 60:
                        self.session['missed_pongs'] += 1
                        if self.session['missed_pongs'] in (3, 10, 30, 100) or self.session['missed_pongs'] % 100 == 0:
                            safe_log('warning', 'Client missing pong responses',
                                event='connectivity', action='missed_pongs',
                                session_id=self.session['id'],
                                missed_pongs=str(self.session['missed_pongs']),
                                last_pong_age_seconds=str(round(pong_age, 1)))
                elif self.session['connected_at']:
                    conn_age = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                    if conn_age > 90:
                        self.session['missed_pongs'] += 1

            # Refresh CVP token to frontend every 20 minutes
            if hasattr(self, 'session') and time.time() - self.session.get('last_token_send', 0) > 1200:
                self.session['last_token_send'] = time.time()
                try:
                    token = await loop.run_in_executor(None, _get_cvp_token)
                    if token:
                        self.write_message(json.dumps({
                            'type': 'token_refresh',
                            'data': {'cvp_token': token}
                        }))
                except Exception:
                    pass

            # Update active session data snapshot
            if hasattr(self, 'session'):
                active_session_data[self.session['id']] = {
                    'session_id': self.session['id'],
                    'client_ip': self.session['client_ip'],
                    'connected_at': str(self.session['connected_at']),
                    'missed_pongs': self.session['missed_pongs'],
                    'last_rtt': self.session['last_rtt'],
                    'reconnect_count': self.session['reconnect_count']
                }
        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.keepalive: {e}', event='error', handler='topoDataHandler')
            pS("ERROR sending update")
        finally:
            if not getattr(self, '_closed', True):
                self.schedule_update()

    def on_close(self):
        self._closed = True
        duration = 0
        session_id = 'unknown'
        try:
            duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
            session_id = self.session['id']
            active_sessions.discard(session_id)
            active_session_data.pop(session_id, None)

            # Store in recent_sessions for reconnect detection
            recent_sessions[self.session['client_ip']] = {
                'session_id': session_id,
                'closed_at': datetime.utcnow(),
                'reconnect_count': self.session['reconnect_count']
            }

            safe_log('info', 'WebSocket session ended',
                event='connectivity', action='session_end',
                session_id=session_id,
                client_id=str(self.session.get('client_id', '')),
                client_ip=str(self.session['client_ip']),
                duration_seconds=str(round(duration, 1)),
                missed_pongs=str(self.session['missed_pongs']),
                reconnect_count=str(self.session['reconnect_count']))
        except AttributeError:
            safe_log('info', 'WebSocket connection closed (no session)',
                event='websocket', action='disconnect')
        try:
            tornado.ioloop.IOLoop.current().remove_timeout(self.timeout)
            if hasattr(self, 'summary_timeout'):
                tornado.ioloop.IOLoop.current().remove_timeout(self.summary_timeout)
            pS('connection closed')
        except Exception:
            safe_log('warning', 'Timeout already removed on close', event='websocket', action='timeout_cleanup')
 
    def check_origin(self, origin):
        """Validate origin matches the request host to prevent cross-site WebSocket hijacking."""
        host = self.request.headers.get('Host', '')
        if not host:
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(origin)
            return parsed.netloc == host or parsed.netloc.split(':')[0] == host.split(':')[0]
        except Exception:
            return False
    
    def sendData(self, mtype):
        instance_data = {
            'cvp': self.cvp_status,
            'tasks': self.cvp_tasks,
            'uptime': self.uptime,
            'endexamtime' : EXAM_END_TIME, 
            'startExamTime' : EXAM_START_TIME         
        }
        self.write_message(json.dumps({
            'type': mtype,
            'data': instance_data
        }))

    def send_session_info(self):
        """Send session metadata to the frontend for diagnostics panel"""
        try:
            cvp_token = _get_cvp_token()
            self.write_message(json.dumps({
                'type': 'session_info',
                'data': {
                    'session_id': self.session['id'],
                    'client_id': self.session.get('client_id', ''),
                    'reconnect_count': self.session['reconnect_count'],
                    'debug_mode': self.session['debug_mode'],
                    'cvp_token': cvp_token or ''
                }
            }))
        except Exception:
            safe_log('error', 'Error sending session info',
                event='error', handler='topoDataHandler')

    def handle_connectivity_event(self, data):
        """Process connectivity events from the frontend"""
        if not hasattr(self, 'session'):
            return

        event = data.get('event', '')
        session_id = self.session['id']

        if event == 'periodic_summary':
            safe_log('info', 'Client connectivity summary',
                event='connectivity', action='periodic_summary',
                source='client',
                session_id=session_id,
                client_id=str(self.session.get('client_id', '')),
                client_ip=str(self.session['client_ip']),
                ws_latency_ms=str(data.get('wsRoundTrip', '')),
                grpc_status=str(data.get('grpcStatus', '')),
                grpc_failures=str(data.get('grpcFailures', '')),
                event_count=str(data.get('eventCount', '')),
                session_uptime_s=str(data.get('sessionUptime', '')),
                external_check=str(data.get('externalCheck', '')),
                external_rtt_ms=str(data.get('externalRttMs', '')),
                network_type=str(data.get('networkType', '')),
                effective_type=str(data.get('effectiveType', '')),
                downlink_mbps=str(data.get('downlinkMbps', '')),
                browser_rtt_ms=str(data.get('browserRttMs', '')),
                uptime_percent=str(data.get('uptimePercent', '')))

        elif event == 'reconnect_report':
            safe_log('warning', 'Client reconnected after outage',
                event='connectivity', action='reconnect_report',
                source='client',
                session_id=session_id,
                client_ip=str(self.session['client_ip']),
                offline_duration_ms=str(data.get('offlineDuration', '')),
                offline_from=str(data.get('offlineFrom', '')),
                offline_to=str(data.get('offlineTo', '')),
                buffered_event_count=str(len(data.get('bufferedEvents', []))))

            if self.session.get('debug_mode'):
                for evt in data.get('bufferedEvents', [])[:100]:
                    safe_log('debug', 'Buffered client event',
                        event='connectivity', action='buffered_event',
                        source='client',
                        session_id=session_id,
                        event_type=str(evt.get('type', '')),
                        event_ts=str(evt.get('ts', '')),
                        event_data=str(evt.get('data', '')))

        elif event == 'grpc_check':
            grpc_status = data.get('status', 'unknown')
            log_level = 'info' if grpc_status == 'ok' else 'warning'
            safe_log(log_level, 'Client gRPC check: ' + grpc_status,
                event='connectivity', action='grpc_check',
                source='client',
                session_id=session_id,
                client_id=str(self.session.get('client_id', '')),
                client_ip=str(self.session['client_ip']),
                status=str(grpc_status),
                detail=str(data.get('detail', ''))[:200])

        elif event == 'state_change':
            safe_log('info', 'Client connectivity state change',
                event='connectivity', action='state_change',
                source='client',
                session_id=session_id,
                client_ip=str(self.session['client_ip']),
                change_type=str(data.get('changeType', '')),
                detail=str(data.get('detail', '')))

    def schedule_summary(self):
        """Schedule periodic session summary logging (every 5 minutes)"""
        try:
            self.summary_timeout = tornado.ioloop.IOLoop.current().add_timeout(
                timedelta(seconds=300), self.log_session_summary)
        except Exception:
            pass

    def log_session_summary(self):
        """Log a summary of the current session state"""
        if getattr(self, '_closed', True):
            return
        try:
            if hasattr(self, 'session'):
                duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                safe_log('info', 'Active session summary',
                    event='connectivity', action='session_summary',
                    session_id=self.session['id'],
                    client_id=str(self.session.get('client_id', '')),
                    client_ip=str(self.session['client_ip']),
                    duration_seconds=str(round(duration, 1)),
                    missed_pongs=str(self.session['missed_pongs']),
                    last_rtt_ms=str(self.session['last_rtt'] if self.session['last_rtt'] else ''),
                    reconnect_count=str(self.session['reconnect_count']),
                    debug_mode=str(self.session['debug_mode']))
        except Exception:
            pass
        finally:
            if not getattr(self, '_closed', True):
                self.schedule_summary()


# device utility functions (invalidate_devices_cache, get_all_devices, get_device_ip_from_sources)
# and their cache globals have been moved to handlers/topology_api.py


def update_hubspot_handler(email, action, project):
    """
    Call the HubSpot Cloud Function to update exam properties

    Args:
        email: Email address of the user
        action: Action to perform ('update_exam_start' or 'update_exam_submit')
        project: GCP project name

    Returns:
        dict: Response from the Cloud Function or error dict
    """
    print(f"Updating HubSpot: {action} for {email} in project {project}")
    hubspot_url = f"https://us-central1-{project}.cloudfunctions.net/api-hl-hubspot-handler"
    headers = {'Content-Type': 'application/json'}

    payload = {
        "action": action,
        "email": email
    }

    try:
        response = requests.post(url=hubspot_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            print(f"Successfully updated HubSpot: {action} for {email}")
            return response.json()
        else:
            error_msg = f"HubSpot update failed with status {response.status_code}"
            print(error_msg)
            try:
                error_detail = response.json()
                print(f"Error details: {error_detail}")
                return error_detail
            except Exception:
                return {"error": error_msg, "status_code": response.status_code}

    except requests.exceptions.Timeout:
        error_msg = "HubSpot request timed out"
        safe_log('error', f'Error in update_hubspot_handler: {error_msg}', event='error', handler='update_hubspot_handler')
        print(error_msg)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"HubSpot update error: {str(e)}"
        safe_log('error', f'Error in update_hubspot_handler: {error_msg}', event='error', handler='update_hubspot_handler')
        print(error_msg)
        return {"error": error_msg}

# ToolsHandler, ViewConfigHandler, BaseUrlHandler, UptimeWithRuntimeHandler,
# TerminalPageHandler, ConsolePageHandler — moved to handlers/pages.py



# TopologyAPIHandler, DevicesAPIHandler, DeviceTypesAPIHandler,
# InterfaceStatsAPIHandler, DeviceStatusAPIHandler, RunningConfigAPIHandler
# — moved to handlers/topology_api.py

# ===============================
# Packet Capture / Latency / Impairment Handlers
# Imported from handlers.capture, handlers.latency, handlers.impairments
# ===============================

# ClientLogHandler, ConnectivityStatusHandler — moved to handlers/pages.py


if __name__ == "__main__":
    settings = {
        'cookie_secret': genCookieSecret(),
        'login_url': "/login",
        'xheaders': True
    }

    # Shared mutable dict for exam timing — written by ExamStatusHandler,
    # read by UptimeWithRuntimeHandler so both handlers stay in sync.
    EXAM_STATE = {'start_time': EXAM_START_TIME, 'end_time': EXAM_END_TIME}

    # Config dict for exam handlers — groups all path/project/Honorlock settings.
    _exam_config = {
        'base_path': BASE_PATH,
        'atd_access_path': ATD_ACCESS_PATH,
        'project': PROJECT,
        'honorlock_client_id': HonorLockClientID,
        'honorlock_secret': HonorLockSecret,
    }
    _exam_kwargs = {
        'config': _exam_config,
        'docker_client': DOCKER_CLIENT,
        'exam_state': EXAM_STATE,
    }

    # Config dict for page handlers — base paths and title
    _page_config = {
        'base_path': BASE_PATH,
        'atd_access_path': ATD_ACCESS_PATH,
        'title': TITLE,
    }

    # Topo config dict for topoRequestHandler
    _topo_config = {
        'topo': TOPO,
        'nomenuoptionfile': NOMENUOPTIONFILE,
        'menu_items': MENU_ITEMS if not NOMENUOPTIONFILE else {},
        'default_menu_file_value': DEFAULT_MENU_FILE_VALUE if not NOMENUOPTIONFILE else '',
        'mod_yaml': MOD_YAML,
        'eos_type': EOS_TYPE,
    }

    # Session state shared with ConnectivityStatusHandler (mutable references).
    # active_sessions/active_session_data/recent_sessions are mutated in-place,
    # so the handler sees live data. _grpc_state is also a mutable dict updated
    # by check_cvp_grpc_internal.
    _session_state = {
        'active_sessions': active_sessions,
        'active_session_data': active_session_data,
        'recent_sessions': recent_sessions,
        'grpc_state': _grpc_state,
    }

    app = tornado.web.Application([
        (r'/td-api/client-log', ClientLogHandler),
        (r'/exam-submitted', ExamSubmittedRedirectHandler, _exam_kwargs),
        (r'/exam-already-running', ExamAlreadyRunningHandler, _exam_kwargs),
        (r'/exam-redo', ExamRedoRedirectHandler, _exam_kwargs),
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "js/"}),
        (r'/css/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "css/"}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "images/"}),
        (r'/topo/(.*)', tornado.web.StaticFileHandler, {'path': ArBASE_PATH}),
        (r'/', topoRequestHandler, {'config': _page_config, 'topo_config': _topo_config}),
        (r'/td-ws', topoDataHandler),
        (r'/login', LoginHandler, {'accounts': accounts, 'salt': salt, 'base_path': BASE_PATH}),
        (r'/lab', LabHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        (r'/labStaus', LabStausHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        #(r'/tools', ToolsHandler),
        (r'/viewConfig', ViewConfigHandler),
        (r'/resetLab', ResetLabHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        (r'/examStatus', ExamStatusHandler, _exam_kwargs),
        (r'/examSubmit', ExamSubmitHandler, _exam_kwargs),
        (r'/exam-authentication', ExamAuthenticationHandler, _exam_kwargs),
        (r'/getAccessInfo', GetAccessInfoHandler, _exam_kwargs),
        (r'/getClientId', GetClientIdHandler, _exam_kwargs),
        (r'/getExamInstructions', GetExamInstructionsHandler, _exam_kwargs),
        (r'/getUserSessionId', GetUserSessionIdHandler, _exam_kwargs),
        (r'/beginExam', BeginExamHandler, _exam_kwargs),
        (r'/endExam', EndExamHandler, _exam_kwargs),
        (r'/baseUrl', BaseUrlHandler, {'config': _page_config}),
        (r'/uptimeWithRuntime', UptimeWithRuntimeHandler, {'exam_state': EXAM_STATE, 'topo_data': None}),
        (r'/terminal', TerminalPageHandler, {'config': _page_config}),
        (r'/console/?', ConsolePageHandler, {'config': _page_config}),  # /? makes trailing slash optional
        (r'/td-api/devices', DevicesAPIHandler),
        (r'/td-api/device-types', DeviceTypesAPIHandler),
        (r'/td-api/topology', TopologyAPIHandler),
        (r'/td-api/interface-stats', InterfaceStatsAPIHandler),
        (r'/td-api/device-status', DeviceStatusAPIHandler),
        (r'/td-api/running-config', RunningConfigAPIHandler),
        # Packet capture endpoints
        (r'/capture-ws', CaptureWebSocketHandler),
        (r'/td-api/capture/bridges', CaptureBridgesAPIHandler),
        (r'/td-api/capture/status', CaptureStatusAPIHandler),
        (r'/td-api/capture/start', CaptureStartAPIHandler),
        (r'/td-api/capture/stop', CaptureStopAPIHandler),
        # Latency injection endpoints (legacy, kept for backwards compatibility)
        (r'/td-api/latency/bridges', LatencyBridgesAPIHandler),
        (r'/td-api/latency/enable', LatencyEnableAPIHandler),
        (r'/td-api/latency/disable', LatencyDisableAPIHandler),
        (r'/td-api/latency/disable-all', LatencyDisableAllAPIHandler),
        # Impairment injection endpoints (unified control)
        (r'/td-api/impairments/bridges', ImpairmentsBridgesAPIHandler),
        (r'/td-api/impairments/configure', ImpairmentsConfigureAPIHandler),
        (r'/td-api/impairments/clear', ImpairmentsClearAPIHandler),
        (r'/td-api/impairments/clear-all', ImpairmentsClearAllAPIHandler),
        # Topology Converter endpoints
        (r'/topology-converter', TopologyConverterPageHandler),
        (r'/td-api/topology-converter/current', TopologyConverterCurrentHandler),
        (r'/td-api/topology-converter/available', TopologyConverterAvailableHandler),
        (r'/td-api/topology-converter/info', TopologyConverterInfoHandler),
        (r'/td-api/topology-converter/convert', TopologyConverterConvertHandler),
        (r'/td-api/topology-converter/status', TopologyConverterStatusHandler),
        # Connectivity status endpoint
        (r'/td-api/connectivity-status', ConnectivityStatusHandler, {'session_state': _session_state}),
        # Nodebuilder endpoints (dynamic node addition for KVM labs)
        (r'/td-api/nodes/(.*)', NodeBuilderProxyHandler),
    ], **settings)
    app.listen(PORT)
    safe_log('info', 'UILanding server started', port='80', topology=TOPO)
    print('*** Websocket Server Started on {} ***'.format(PORT))
    try:
        TOPO_DATA = getEventStatus(NAME, ZONE, FUNC_STATE, SCHEMA)

        # Global internal gRPC health check — runs once every 30 seconds, not per-connection
        def _grpc_check_tick():
            try:
                if active_sessions:
                    tornado.ioloop.IOLoop.current().run_in_executor(None, check_cvp_grpc_internal)
            except Exception:
                pass
        grpc_check_timer = tornado.ioloop.PeriodicCallback(_grpc_check_tick, 30000)
        grpc_check_timer.start()

        tornado.ioloop.IOLoop.current().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.current().stop()
        print("*** Websocked Server Stopped ***")
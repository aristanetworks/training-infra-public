#!/usr/bin/env python3

from ruamel.yaml import YAML
from time import sleep
import tornado.ioloop
import tornado.web
import requests
import hashlib, uuid
import docker
import urllib3
import os
import time
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
    safe_log, getEventStatus, genCookieSecret,
)
from handlers.auth import LoginHandler
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
    ViewConfigHandler,
    BaseUrlHandler,
    UptimeWithRuntimeHandler,
    TerminalPageHandler,
    ConsolePageHandler,
    ClientLogHandler,
    ConnectivityStatusHandler,
)
from handlers.websocket import topoDataHandler
from handlers.topology_api import (
    TopologyAPIHandler,
    DevicesAPIHandler,
    DeviceTypesAPIHandler,
    InterfaceStatsAPIHandler,
    DeviceStatusAPIHandler,
    RunningConfigAPIHandler,
    initialize as initialize_topology_api,
)

# Disable any TLS Warnings when getting instance Uptime
urllib3.disable_warnings()


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
MENU_ITEMS = {}
DEFAULT_MENU_FILE_VALUE = ''
if str(default_menu_info['default_menu']).lower() == 'ssh':
    NOMENUOPTIONFILE = True
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
        # Only log token failures on state transition (not every check during CVP startup)
        if _grpc_state.get('status') != 'skipped':
            safe_log('warning', 'Failed to get CVP token for internal gRPC check',
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
        prev_status = _grpc_state.get('status')
        _grpc_state['status'] = 'skipped'
        _grpc_state['last_check'] = time.time()
        if prev_status != 'skipped':
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
            prev_status = _grpc_state.get('status')
            if grpc_status is not None and int(grpc_status) == 14:
                _grpc_state['status'] = 'unavailable'
                if prev_status != 'unavailable':
                    safe_log('warning', 'Internal gRPC check: CVP unavailable',
                        event='connectivity', action='grpc_check', source='internal',
                        status='unavailable', http_status=str(status_code),
                        grpc_status=str(grpc_status))
            else:
                _grpc_state['status'] = 'ok'
                if prev_status != 'ok':
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

# topoDataHandler — moved to handlers/websocket.py


# device utility functions (invalidate_devices_cache, get_all_devices, get_device_ip_from_sources)
# and their cache globals have been moved to handlers/topology_api.py


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

    # Session state shared with ConnectivityStatusHandler and topoDataHandler
    # (mutable references). active_sessions/active_session_data/recent_sessions
    # are mutated in-place, so all handlers see live data. _grpc_state is also a
    # mutable dict updated by check_cvp_grpc_internal.
    _session_state = {
        'active_sessions': active_sessions,
        'active_session_data': active_session_data,
        'recent_sessions': recent_sessions,
        'grpc_state': _grpc_state,
    }

    # WebSocket handler kwargs — injects session state, exam state, and CVP token fn.
    _ws_kwargs = {
        'session_state': _session_state,
        'exam_state': EXAM_STATE,
        'cvp_token_fn': _get_cvp_token,
    }

    # ===== App 1: UI Frontend (port 8080) =====
    # Pages, authentication, static files, lab operations, topology converter
    ui_app = tornado.web.Application([
        (r'/td-api/client-log', ClientLogHandler),
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH + "js/"}),
        (r'/css/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH + "css/"}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH + "images/"}),
        (r'/topo/(.*)', tornado.web.StaticFileHandler, {'path': ArBASE_PATH}),
        (r'/', topoRequestHandler, {'config': _page_config, 'topo_config': _topo_config}),
        (r'/login', LoginHandler, {'accounts': accounts, 'salt': salt, 'base_path': BASE_PATH}),
        (r'/lab', LabHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        (r'/labStaus', LabStausHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        (r'/viewConfig', ViewConfigHandler),
        (r'/resetLab', ResetLabHandler, {'docker_client': DOCKER_CLIENT, 'default_menu_file_value': DEFAULT_MENU_FILE_VALUE}),
        (r'/baseUrl', BaseUrlHandler, {'config': _page_config}),
        (r'/uptimeWithRuntime', UptimeWithRuntimeHandler, {'exam_state': EXAM_STATE, 'topo_data': None}),
        (r'/terminal', TerminalPageHandler, {'config': _page_config}),
        (r'/console/?', ConsolePageHandler, {'config': _page_config}),
        # Topology Converter endpoints
        (r'/topology-converter', TopologyConverterPageHandler),
        (r'/td-api/topology-converter/current', TopologyConverterCurrentHandler),
        (r'/td-api/topology-converter/available', TopologyConverterAvailableHandler),
        (r'/td-api/topology-converter/info', TopologyConverterInfoHandler),
        (r'/td-api/topology-converter/convert', TopologyConverterConvertHandler),
        (r'/td-api/topology-converter/status', TopologyConverterStatusHandler),
        # Connectivity status endpoint
        (r'/td-api/connectivity-status', ConnectivityStatusHandler, {'session_state': _session_state}),
    ], **settings)
    ui_app.listen(8080)
    safe_log('info', 'ui-frontend app started', port='8080')

    # ===== App 2: API (port 8081) =====
    # Topology data, device management, eAPI queries
    api_app = tornado.web.Application([
        (r'/td-api/devices', DevicesAPIHandler),
        (r'/td-api/device-types', DeviceTypesAPIHandler),
        (r'/td-api/topology', TopologyAPIHandler),
        (r'/td-api/interface-stats', InterfaceStatsAPIHandler),
        (r'/td-api/device-status', DeviceStatusAPIHandler),
        (r'/td-api/running-config', RunningConfigAPIHandler),
    ], **settings)
    api_app.listen(8081)
    safe_log('info', 'api app started', port='8081')

    # ===== App 3: WebSocket (port 8082) =====
    # Real-time topology updates, connectivity monitoring
    ws_app = tornado.web.Application([
        (r'/td-ws', topoDataHandler, _ws_kwargs),
    ], **settings)
    ws_app.listen(8082)
    safe_log('info', 'websocket app started', port='8082')

    # ===== App 4: Exam (port 8083) =====
    # Exam lifecycle, Honorlock proctoring, HubSpot integration
    exam_app = tornado.web.Application([
        (r'/exam-submitted', ExamSubmittedRedirectHandler, _exam_kwargs),
        (r'/exam-already-running', ExamAlreadyRunningHandler, _exam_kwargs),
        (r'/exam-redo', ExamRedoRedirectHandler, _exam_kwargs),
        (r'/exam-authentication', ExamAuthenticationHandler, _exam_kwargs),
        (r'/examStatus', ExamStatusHandler, _exam_kwargs),
        (r'/examSubmit', ExamSubmitHandler, _exam_kwargs),
        (r'/getAccessInfo', GetAccessInfoHandler, _exam_kwargs),
        (r'/getClientId', GetClientIdHandler, _exam_kwargs),
        (r'/getExamInstructions', GetExamInstructionsHandler, _exam_kwargs),
        (r'/getUserSessionId', GetUserSessionIdHandler, _exam_kwargs),
        (r'/beginExam', BeginExamHandler, _exam_kwargs),
        (r'/endExam', EndExamHandler, _exam_kwargs),
    ], **settings)
    exam_app.listen(8083)
    safe_log('info', 'exam app started', port='8083')

    # ===== App 5: Proxy (port 8084) =====
    # Nodebuilder proxy, packet capture, impairments/latency
    proxy_app = tornado.web.Application([
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
        # Nodebuilder endpoints (dynamic node addition for KVM labs)
        (r'/td-api/nodes/(.*)', NodeBuilderProxyHandler),
    ], **settings)
    proxy_app.listen(8084)
    safe_log('info', 'proxy app started', port='8084')

    print('*** UILanding Multi-App Server Started ***')
    print('  ui-frontend: 8080 | api: 8081 | websocket: 8082 | exam: 8083 | proxy: 8084')

    # Log lab session metadata once at startup
    from handlers.session_logger import log_lab_session
    log_lab_session(host_yaml)

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
        print("*** UILanding Multi-App Server Stopped ***")
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

# Cloud Logging Setup
try:
    from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error
    logger = setup_cloud_logging('uilanding')
except Exception:
    import logging as _logging
    logger = _logging.getLogger('uilanding')
    logger.addHandler(_logging.StreamHandler())
    logger.setLevel(_logging.INFO)

def safe_log(level, message, **kwargs):
    """Log safely - never crash the application due to logging errors"""
    try:
        labels = {k: str(v) for k, v in kwargs.items()}
        getattr(logger, level)(message, extra={'labels': labels} if labels else {})
    except Exception:
        pass

# Disable any TLS Warnings when getting instance Uptime
urllib3.disable_warnings()


PORT = 80
TOPO_API = 'atd-conftopo'
BASE_PATH = '/opt/topo/html/'
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'

ArBASE_PATH = '/opt/modules/'
MODULE_FILE = ArBASE_PATH + 'modules.yaml'
MENU_BASE_PATH = '/opt/menus/'
EXAM_END_TIME = 0
EXAM_START_TIME = 0
# Open yaml for the default yaml and read what file to lookup for default menu
default_menu_file_generated_flag = (os.path.join(MENU_BASE_PATH, 'labguides-done.txt'))
print ("Waiting for labguides-done.txt file existance to start the server")
while True:
    if os.path.exists(default_menu_file_generated_flag):
        print("Deleting labguides-done.txt file to start the server")
        os.remove(default_menu_file_generated_flag)
        break
    else:
        print("labguides-done.txt file does not exist yet, waiting for 1 sec")
        sleep(1)
default_menu_file = open(MENU_BASE_PATH+'default.yaml')
default_menu_info = YAML().load(default_menu_file)
default_menu_file.close()
if str(default_menu_info['default_menu']).lower() == 'ssh':
    NOMENUOPTIONFILE =True
else:
    # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
    NOMENUOPTIONFILE = False
    menu_file = open('/opt/menus/{0}'.format(default_menu_info['default_menu']))
    MENU_ITEMS = YAML().load(menu_file)  
    menu_file.close()
    DEFAULT_MENU_FILE_VALUE = default_menu_info['default_menu'].replace('.yaml', '')
    

with open(MODULE_FILE, 'r') as mf:
    MOD_YAML = YAML().load(mf)

# Add in check to make sure arista password has been updated
while True:
    host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
    if host_yaml['login_info']['jump_host']['pw'] == 'REPLACE_PWD':
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

def get_metadata_extract(attribute):
    try:
        metadata_url = "http://169.254.169.254/computeMetadata/v1/project/attributes/{}".format(attribute)
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers)
        if response.status_code == 200:
            return response.text
        else:
            return None
    except requests.exceptions.RequestException as e:
        safe_log('error', f'Error in get_metadata_extract: {e}', event='error', handler='get_metadata_extract')
        print(f"Error fetching metadata: {e}")
        return None

HonorLockClientID = get_metadata_extract('honorlockClientID')
HonorLockSecret = get_metadata_extract('honorlockClientSecret')


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return(self.get_secure_cookie("user"))
class ExamSubmittedRedirectHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")  # Set the correct content type for HTML
        try:
            with open(BASE_PATH + 'exam-submitted.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)  # Write the HTML content to the response
        except FileNotFoundError:
            safe_log('error', 'Error in ExamSubmittedRedirectHandler: exam-submitted.html not found', event='error', handler='ExamSubmittedRedirectHandler')
            self.set_status(404)
            self.write("Error: exam-submitted.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamSubmittedRedirectHandler: {e}', event='error', handler='ExamSubmittedRedirectHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")

class ExamAlreadyRunningHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(BASE_PATH + 'exam-already-running.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)
        except FileNotFoundError:
            safe_log('error', 'Error in ExamAlreadyRunningHandler: exam-already-running.html not found', event='error', handler='ExamAlreadyRunningHandler')
            self.set_status(404)
            self.write("Error: exam-already-running.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamAlreadyRunningHandler: {e}', event='error', handler='ExamAlreadyRunningHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")
class ExamAuthenticationHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")  # Set the correct content type for HTML
        try:
            with open(BASE_PATH + 'honorlock-index.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)  # Write the HTML content to the response
        except FileNotFoundError:
            safe_log('error', 'Error in ExamAuthenticationHandler: honorlock-index.html not found', event='error', handler='ExamAuthenticationHandler')
            self.set_status(404)
            self.write("Error: honorlock-index.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamAuthenticationHandler: {e}', event='error', handler='ExamAuthenticationHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")
class LoginHandler(BaseHandler):
    def _validate_credentials(self, username: str, password: str) -> bool:
        """
        Validate username and password using constant-time comparison.

        Security: Uses secrets.compare_digest to prevent timing attacks.
        Always computes both hashes regardless of username validity.
        """
        tmp_username_hash = hashlib.sha512((username + salt).encode('utf-8')).hexdigest()
        tmp_pwd_hash = hashlib.sha512((password + salt).encode('utf-8')).hexdigest()

        # Get stored password hash, or use a dummy value if username not found
        # This ensures constant-time behavior regardless of username validity
        stored_pwd_hash = accounts.get(tmp_username_hash, 'invalid_user_dummy_hash')

        # Use constant-time comparison to prevent timing attacks
        return secrets.compare_digest(tmp_pwd_hash, stored_pwd_hash)

    def get(self):
        safe_log('info', 'Login page accessed', event='page_view', page='login')
        AUTH = False
        decoded_cred = None
        if 'auth' in self.request.arguments:
            try:
                decoded_cred = decodeID(self.get_argument('auth'))
                AUTH = self._validate_credentials(decoded_cred['user'], decoded_cred['pwd'])
            except:
                pass
        if AUTH and decoded_cred:
            self.set_secure_cookie("user", decoded_cred['user'])
            self.redirect('/')
        else:
            self.render(
                BASE_PATH + 'login.html',
                LOGIN_MESSAGE=""
            )

    def post(self):
        username = self.get_argument("name")
        password = self.get_argument("pwd")

        if self._validate_credentials(username, password):
            safe_log('info', 'Login successful', event='auth', action='login_success', username=username)
            self.set_secure_cookie("user", username)
            self.redirect("/")
        else:
            safe_log('warning', 'Login failed', event='auth', action='login_failure', username=username)
            self.render(
                BASE_PATH + 'login.html',
                LOGIN_MESSAGE="Wrong username and/or password."
            )

class topoRequestHandler(BaseHandler):
    def get(self):
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        lab_type = host_yaml.get('customer_details', {}).get('lab_type', 'Lab')

        # For Exam labs: ALWAYS require Honorlock authentication flow
        # This prevents bypass via direct URL access or cached sessions
        if lab_type == "Exam":
            # Check if user accessed via proper Honorlock flow (has 'honorlock' parameter)
            if 'honorlock' in self.request.arguments:
                # Valid Honorlock flow - proceed with authentication
                if not self.current_user:
                    # Redirect to login with auth credentials
                    if 'auth' in self.request.arguments:
                        self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
                    else:
                        self.redirect('/login')
                    return
                # else: User authenticated via Honorlock - allow access (continue to line 232+)
            else:
                # No 'honorlock' parameter - force Honorlock authentication
                # This blocks:
                #   1. Direct URL access: https://lab.com/
                #   2. Cached session access without Honorlock
                #   3. Manual auth parameter manipulation
                self.redirect('/exam-authentication')
                return

        # Handle non-authenticated users for regular (non-Exam) labs
        # Note: Exam labs are already handled above (lines 210-229)
        if not self.current_user:
            # Regular lab authentication
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return
        else:
            safe_log('info', 'Topology page accessed', event='page_view', page='topology', lab_type=str(lab_type))
            _topo_cvp = False
            if 'disabled_links' in host_yaml:
                disable_links = host_yaml['disabled_links']
            else:
                disable_links = []
            menu={}
            if NOMENUOPTIONFILE:
                disable_links.append('lab_menu')
            else:
                for lab in MENU_ITEMS['lab_list']:
                    menu[lab] = MENU_ITEMS['lab_list'][lab]['description']

            # Disable lab_menu for Exam type labs
            if lab_type == "Exam" and 'lab_menu' not in disable_links:
                disable_links.append('lab_menu')
            if 'labguides' in host_yaml:
                if host_yaml['labguides'] == 'self':
                    labguides = '/labguides/index.html'
                else:
                    labguides = host_yaml['labguides']
            else:
                labguides = '/labguides/index.html'
            if 'cvp' in host_yaml:
                if host_yaml['cvp'] != "none":
                    _topo_cvp = True       
            gui_urls,servers =[],[]
            if host_yaml['eos_type'] == 'container-labs':
                try:
                    servers =  MOD_YAML['topology']['servers']
                    if servers is None:
                        servers = [] 
                    external_ip_url = "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"
                    headers = {"Metadata-Flavor": "Google"}
                    response = requests.get(external_ip_url, headers=headers)
                    for server in servers:
                        gui_urls.append(f'http://{response.text}:{servers[server]["port"]}')
                except Exception as e:
                    safe_log('error', f'Error in topoRequestHandler: {e}', event='error', handler='topoRequestHandler')
                    pS(f"Error while looking for servers in GUI {e}")
            self.render(
                BASE_PATH + 'index.html',
                NODES = MOD_YAML['topology']['nodes'],
                SERVERS = servers,
                GUI_URLS= gui_urls,
                ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
                topo_title = TITLE,
                disable_links = disable_links,
                labguides = labguides,
                topo_cvp = _topo_cvp,
                menu_options = menu,
                lab_type = lab_type
            )
    
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
                safe_log('warning', 'Internal gRPC check: CVP unavailable',
                    event='connectivity', action='grpc_check', source='internal',
                    status='unavailable', http_status=str(status_code),
                    grpc_status=str(grpc_status))
            else:
                safe_log('info', 'Internal gRPC check passed',
                    event='connectivity', action='grpc_check', source='internal',
                    status='ok', http_status=str(status_code),
                    grpc_status=str(grpc_status) if grpc_status else '')
        elif status_code in (401, 403, 405):
            # Token may be stale, clear it
            global _cvp_grpc_token
            _cvp_grpc_token = None
            safe_log('warning', 'Internal gRPC check: auth rejected',
                event='connectivity', action='grpc_check', source='internal',
                status='auth_rejected', http_status=str(status_code))
        elif status_code in (502, 503, 504):
            safe_log('warning', 'Internal gRPC check: CVP unreachable',
                event='connectivity', action='grpc_check', source='internal',
                status='unreachable', http_status=str(status_code))
        else:
            safe_log('warning', 'Internal gRPC check: unexpected response',
                event='connectivity', action='grpc_check', source='internal',
                status='unexpected', http_status=str(status_code))
    except requests.exceptions.Timeout:
        safe_log('warning', 'Internal gRPC check: timeout',
            event='connectivity', action='grpc_check', source='internal',
            status='timeout')
    except Exception as e:
        safe_log('error', 'Internal gRPC check failed',
            event='connectivity', action='grpc_check', source='internal',
            status='error', error=str(e))

# ===============================
# Connectivity Session Tracking
# ===============================

# Track recently closed sessions for reconnect detection
# Key: client_ip, Value: {'session_id': str, 'closed_at': datetime, 'reconnect_count': int}
recent_sessions = {}
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
        client_ip = self.request.remote_ip
        session_id = str(uuid.uuid4())

        # Check for reconnect
        prune_recent_sessions()
        reconnect_count = 0
        reconnect_gap = None
        if client_ip in recent_sessions:
            prev = recent_sessions[client_ip]
            reconnect_count = prev['reconnect_count'] + 1
            reconnect_gap = (datetime.utcnow() - prev['closed_at']).total_seconds()

        self.session = {
            'id': session_id,
            'connected_at': datetime.utcnow(),
            'reconnect_count': reconnect_count,
            'missed_pongs': 0,
            'last_pong': None,
            'last_rtt': None,
            'client_ip': client_ip,
            'debug_mode': False
        }

        safe_log('info', 'WebSocket session started',
            event='connectivity', action='session_start',
            session_id=session_id,
            client_ip=client_ip,
            reconnect_count=str(reconnect_count))

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
    
    def on_message(self,message):
        pS("Message Received")
        try:
            recv = json.loads(message)
            cdata = recv['data']
            if recv['type'] == 'hello':
                # Grab current uptime of topology
                self.uptime = getUptime('192.168.0.1')
                # Get initial topology status
                self.cvp_status = getAPI("cvp_status")
                self.endexamtime = EXAM_END_TIME    
                self.startExamTime = EXAM_START_TIME            
                if self.cvp_status['status'] == 'UP':
                    self.cvp_tasks = getAPI("cvp_tasks")
                else:
                    self.cvp_tasks = ''
                self.sendData('status')
                self.send_session_info()
                self.schedule_update()
            elif recv['type'] == 'pong':
                if hasattr(self, 'session'):
                    server_ts = cdata.get('server_ts', 0)
                    now_ms = int(time.time() * 1000)
                    rtt = now_ms - server_ts if server_ts else None
                    self.session['last_pong'] = datetime.utcnow()
                    self.session['last_rtt'] = rtt
                    self.session['missed_pongs'] = 0

                    if self.session.get('debug_mode'):
                        safe_log('debug', 'Pong received',
                            event='connectivity', action='pong',
                            session_id=self.session['id'],
                            rtt_ms=str(rtt) if rtt else 'unknown')

            elif recv['type'] == 'connectivity':
                self.handle_connectivity_event(cdata)

            elif recv['type'] == 'debug_toggle':
                if hasattr(self, 'session'):
                    self.session['debug_mode'] = not self.session['debug_mode']
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
        except:
            safe_log('error', 'Error in topoDataHandler.on_message', event='error', handler='topoDataHandler')
            pS("WS ERROR")

    def schedule_update(self):
        try:
            self.timeout = tornado.ioloop.IOLoop.instance().add_timeout(timedelta(seconds=30),self.keepalive)
        except:
            safe_log('error', 'Error in topoDataHandler.schedule_update', event='error', handler='topoDataHandler')
            pS("Error with timeout call")
        
    def keepalive(self):
        try:
            self.uptime = getUptime('192.168.0.1')
            self.endexamtime = EXAM_END_TIME
            self.startExamTime = EXAM_START_TIME
            self.cvp_status = getAPI("cvp_status")
            if self.cvp_status['status'] == 'UP':
                self.cvp_tasks = getAPI("cvp_tasks")
            else:
                self.cvp_tasks = ''
            self.sendData('status')

            # Internal gRPC check to CVP (only when CVP is UP)
            if self.cvp_status.get('status') == 'UP':
                check_cvp_grpc_internal()

            # Send timestamped ping for latency measurement
            self.write_message(json.dumps({
                'type': 'ping',
                'data': {'ts': int(time.time() * 1000)}
            }))

            # Check for missed pongs
            if hasattr(self, 'session'):
                if self.session['last_pong'] is not None:
                    pong_age = (datetime.utcnow() - self.session['last_pong']).total_seconds()
                    if pong_age > 60:
                        self.session['missed_pongs'] += 1
                        if self.session['missed_pongs'] >= 3:
                            safe_log('warning', 'Client missing pong responses',
                                event='connectivity', action='missed_pongs',
                                session_id=self.session['id'],
                                missed_pongs=str(self.session['missed_pongs']),
                                last_pong_age_seconds=str(round(pong_age, 1)))
                elif self.session['connected_at']:
                    conn_age = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                    if conn_age > 90:
                        self.session['missed_pongs'] += 1
        except:
            safe_log('error', 'Error in topoDataHandler.keepalive',
                event='error', handler='topoDataHandler')
            pS("ERROR sending update")
        finally:
            self.schedule_update()

    def on_close(self):
        duration = 0
        session_id = 'unknown'
        try:
            duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
            session_id = self.session['id']

            # Store in recent_sessions for reconnect detection
            recent_sessions[self.session['client_ip']] = {
                'session_id': session_id,
                'closed_at': datetime.utcnow(),
                'reconnect_count': self.session['reconnect_count']
            }

            safe_log('info', 'WebSocket session ended',
                event='connectivity', action='session_end',
                session_id=session_id,
                client_ip=str(self.session['client_ip']),
                duration_seconds=str(round(duration, 1)),
                missed_pongs=str(self.session['missed_pongs']),
                reconnect_count=str(self.session['reconnect_count']))
        except AttributeError:
            safe_log('info', 'WebSocket connection closed (no session)',
                event='websocket', action='disconnect')
        try:
            tornado.ioloop.IOLoop.instance().remove_timeout(self.timeout)
            if hasattr(self, 'summary_timeout'):
                tornado.ioloop.IOLoop.instance().remove_timeout(self.summary_timeout)
            pS('connection closed')
        except:
            pS('connection already closed')
 
    def check_origin(self, origin):
        return(True)
    
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
            self.write_message(json.dumps({
                'type': 'session_info',
                'data': {
                    'session_id': self.session['id'],
                    'reconnect_count': self.session['reconnect_count'],
                    'debug_mode': self.session['debug_mode']
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
                browser_rtt_ms=str(data.get('browserRttMs', '')))

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
                for evt in data.get('bufferedEvents', []):
                    safe_log('debug', 'Buffered client event',
                        event='connectivity', action='buffered_event',
                        source='client',
                        session_id=session_id,
                        event_type=str(evt.get('type', '')),
                        event_ts=str(evt.get('ts', '')),
                        event_data=str(evt.get('data', '')))

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
            self.summary_timeout = tornado.ioloop.IOLoop.instance().add_timeout(
                timedelta(seconds=300), self.log_session_summary)
        except:
            pass

    def log_session_summary(self):
        """Log a summary of the current session state"""
        try:
            if hasattr(self, 'session'):
                duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                safe_log('info', 'Active session summary',
                    event='connectivity', action='session_summary',
                    session_id=self.session['id'],
                    client_ip=str(self.session['client_ip']),
                    duration_seconds=str(round(duration, 1)),
                    missed_pongs=str(self.session['missed_pongs']),
                    last_rtt_ms=str(self.session['last_rtt'] if self.session['last_rtt'] else ''),
                    reconnect_count=str(self.session['reconnect_count']),
                    debug_mode=str(self.session['debug_mode']))
        except:
            pass
        finally:
            self.schedule_summary()


# ===============================
# Utility Functions
# ===============================

def getAPI(action):
    try:
        _action = encodeID(action)
        response = requests.get(f"http://{TOPO_API}:50010/td-api/conftopo?action={_action}")
        return(json.loads(response.text))
    except Exception as e:
        safe_log('error', f'Error in getAPI: {e}', event='error', handler='getAPI')
        pS("Error calling backend API.")
        traceback.print_exc()
        print("Message: {err}".format(
            err = str(e),
        ))


def encodeID(tmp_data):
    tmp_str = json.dumps(tmp_data).encode()
    enc_str = b64encode(tmp_str).decode()
    return(enc_str)

def decodeID(tmp_data):
    decrypt_str = b64decode(tmp_data.encode()).decode()
    tmp_json = json.loads(decrypt_str)
    return(tmp_json)

def genCookieSecret():
    """
    Function to generate a cookie_secret
    """
    return(secrets.token_hex(16))

def getUptime(instanceIP):
    """
    Function to get response from instances /uptime.
    instanceIP = IP/URL for instance (str)
    """
    try:
        response = requests.get(f"https://{instanceIP}/uptime", verify=False, timeout=0.5)
        instance_data = json.loads(response.text)
        if instance_data['status'] == 'init':
            instance_data['runtime'] = int(TOPO_DATA['labels']['runtime'])
        else:
            instance_data['runtime'] = 12
        return(instance_data)
    except:
        return({
            'boottime': 0,
            'uptime': 0,
            'runtime': 12,
            'status': 'init'
        })

def getEventStatus(instanceName, instanceZone):
    """
    Function to get the currnet status of an instance.
    """
    try:
        if SCHEMA == 2:
            response = requests.get(FUNC_STATE + "?function=state&instance={0}-eos&zone={1}".format(instanceName, instanceZone))
        else:
            response = requests.get(FUNC_STATE + "?function=state&instance={0}&zone={1}".format(instanceName, instanceZone))
        return(response.json())
    except ValueError:
        safe_log('error', f'Error in getEventStatus: ValueError for {instanceName}', event='error', handler='getEventStatus')
        pS("Value Error retrieving status for {0}".format(instanceName))
        return(False)
    except requests.exceptions.ConnectionError:
        safe_log('error', f'Error in getEventStatus: ConnectionError for {instanceName}', event='error', handler='getEventStatus')
        pS("Connection Error retrieving status for {0}".format(instanceName))
        return(False)
    except:
        safe_log('error', f'Error in getEventStatus: Unknown error for {instanceName}', event='error', handler='getEventStatus')
        pS("Error retrieving status for {0}".format(instanceName))
        return(False)


def pS(mtype):
    """
    Function to send output from service file to Syslog
    """
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


# Cache for topo_build.yml data (loaded once on first use)
_TOPO_BUILD_CACHE = None
# Cache for merged device list from both sources
_ALL_DEVICES_CACHE = None
# Track user_nodes.yaml modification time for cache invalidation
_USER_NODES_MTIME = 0
# Track user_hosts.yaml modification time for cache invalidation
_USER_HOSTS_MTIME = 0
# Track user_firewalls.yaml modification time for cache invalidation
_USER_FIREWALLS_MTIME = 0
# Track user_velo.yaml modification time for cache invalidation
_USER_VELO_MTIME = 0


def invalidate_devices_cache():
    """
    Invalidate the devices cache.
    Called when user nodes/hosts/firewalls/velo devices are added/removed to ensure fresh data.
    """
    global _ALL_DEVICES_CACHE, _USER_NODES_MTIME, _USER_HOSTS_MTIME, _USER_FIREWALLS_MTIME, _USER_VELO_MTIME
    _ALL_DEVICES_CACHE = None
    _USER_NODES_MTIME = 0
    _USER_HOSTS_MTIME = 0
    _USER_FIREWALLS_MTIME = 0
    _USER_VELO_MTIME = 0
    pS("Devices cache invalidated")


def _get_topo_build_data():
    """
    Load and cache topo_build.yml data.
    Returns cached data on subsequent calls.
    """
    global _TOPO_BUILD_CACHE

    if _TOPO_BUILD_CACHE is not None:
        return _TOPO_BUILD_CACHE

    topo_path = f"/opt/atd/topologies/{TOPO}/topo_build.yml"
    try:
        with open(topo_path, 'r') as f:
            _TOPO_BUILD_CACHE = YAML().load(f)
        pS(f"Cached topo_build.yml from {topo_path}")
    except Exception as e:
        safe_log('error', f'Error in _get_topo_build_data: {e}', event='error', handler='_get_topo_build_data')
        pS(f"Error reading topo_build.yml: {e}")
        _TOPO_BUILD_CACHE = {}  # Empty dict to avoid repeated failures

    return _TOPO_BUILD_CACHE


def get_device_ip_from_sources(device_name):
    """
    Look up device IP using cached get_all_devices() with case-insensitive matching.

    Args:
        device_name: Name of the device to look up

    Returns:
        str: IP address if found, None otherwise
    """
    if not device_name:
        return None

    all_devices = get_all_devices()
    device_name_lower = device_name.lower()

    # Case-insensitive lookup in cached devices
    for name, info in all_devices.items():
        if name.lower() == device_name_lower:
            ip = info.get('ip', '')
            return ip if ip else None

    return None


def normalize_device_name(name):
    """
    Normalize device name to consistent capitalization.
    E.g., 'leaf5' -> 'Leaf5', 'memleaf1' -> 'Memleaf1', 'spine1-dc1' -> 'Spine1-DC1'

    Handles patterns like:
    - Simple: leaf1 -> Leaf1, spine2 -> Spine2
    - With suffix: spine1-dc1 -> Spine1-DC1, leaf2-dc2 -> Leaf2-DC2
    - Compound: memleaf1 -> Memleaf1, borderleaf1 -> Borderleaf1
    - Abbreviations: PE1 -> PE1, P3 -> P3 (preserve uppercase abbreviations)
    """
    import re

    if not name:
        return name

    # Split on hyphens to handle suffixes like -DC1, -DC2
    parts = name.split('-')
    result_parts = []

    for part in parts:
        # Check if this part is a datacenter suffix (DC1, DC2, etc.)
        if re.match(r'^[dD][cC]\d+$', part):
            # Uppercase the DC suffix
            result_parts.append(part.upper())
        # Check if this part is an uppercase abbreviation followed by numbers (PE1, P3, CE1, etc.)
        elif re.match(r'^[A-Z]+\d*$', part):
            # Preserve uppercase abbreviations like PE1, P3, CE1
            result_parts.append(part)
        else:
            # Capitalize first letter, keep rest of case
            # This turns 'leaf5' -> 'Leaf5', 'memleaf1' -> 'Memleaf1'
            result_parts.append(part.capitalize())

    return '-'.join(result_parts)


def get_all_devices():
    """
    Get all devices from topo_build.yml, user_nodes.yaml, user_hosts.yaml, and user_firewalls.yaml.
    Returns a dict of {device_name: {'ip': ip_address, 'user_added': bool, 'device_category': str}}.
    Uses caching to avoid repeated lookups.
    Device names are normalized to consistent capitalization.
    Cache is auto-invalidated when any user file changes.
    """
    global _ALL_DEVICES_CACHE, _USER_NODES_MTIME, _USER_HOSTS_MTIME, _USER_FIREWALLS_MTIME, _USER_VELO_MTIME

    # Check if any user file has been modified since last cache
    user_nodes_path = '/etc/atd/user_nodes.yaml'
    user_hosts_path = '/etc/atd/user_hosts.yaml'
    user_firewalls_path = '/etc/atd/user_firewalls.yaml'
    user_velo_path = '/etc/atd/user_velo.yaml'

    try:
        if os.path.exists(user_nodes_path):
            current_mtime = os.path.getmtime(user_nodes_path)
            if current_mtime > _USER_NODES_MTIME:
                _ALL_DEVICES_CACHE = None
                _USER_NODES_MTIME = current_mtime
    except OSError:
        pass

    try:
        if os.path.exists(user_hosts_path):
            current_mtime = os.path.getmtime(user_hosts_path)
            if current_mtime > _USER_HOSTS_MTIME:
                _ALL_DEVICES_CACHE = None
                _USER_HOSTS_MTIME = current_mtime
    except OSError:
        pass

    try:
        if os.path.exists(user_firewalls_path):
            current_mtime = os.path.getmtime(user_firewalls_path)
            if current_mtime > _USER_FIREWALLS_MTIME:
                _ALL_DEVICES_CACHE = None
                _USER_FIREWALLS_MTIME = current_mtime
    except OSError:
        pass

    try:
        if os.path.exists(user_velo_path):
            current_mtime = os.path.getmtime(user_velo_path)
            if current_mtime > _USER_VELO_MTIME:
                _ALL_DEVICES_CACHE = None
                _USER_VELO_MTIME = current_mtime
    except OSError:
        pass

    if _ALL_DEVICES_CACHE is not None:
        return _ALL_DEVICES_CACHE

    devices = {}

    # Get devices from topo_build.yml (the authoritative topology source)
    topo_data = _get_topo_build_data()
    if topo_data and 'nodes' in topo_data:
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for name, info in node_entry.items():
                    ip = info.get('ip_addr', '')
                    if ip == 'N/A':
                        ip = ''
                    # Normalize device name to consistent capitalization
                    display_name = normalize_device_name(name)
                    # Store original name for virsh console (VM names match topo_build.yml)
                    devices[display_name] = {'ip': ip, 'user_added': False, 'vm_name': name, 'device_category': 'node'}

    # Merge user-added nodes from user_nodes.yaml (for dynamically added nodes)
    try:
        if os.path.exists(user_nodes_path):
            with open(user_nodes_path, 'r') as f:
                user_data = YAML().load(f)
            if user_data and 'nodes' in user_data and user_data['nodes']:
                for node_entry in user_data['nodes']:
                    if isinstance(node_entry, dict):
                        for name, info in node_entry.items():
                            ip = info.get('ip_addr', '')
                            if ip == 'N/A':
                                ip = ''
                            display_name = normalize_device_name(name)
                            devices[display_name] = {
                                'ip': ip,
                                'user_added': True,
                                'device_type': info.get('device_type', 'other'),
                                'vm_name': name,
                                'device_category': 'node'
                            }
                pS(f"Merged {len(user_data['nodes'])} user-added nodes into device list")
    except Exception as e:
        pS(f"Warning: Error loading user_nodes.yaml for devices: {e}")

    # Merge user-added hosts from user_hosts.yaml (Linux desktop VMs)
    try:
        if os.path.exists(user_hosts_path):
            with open(user_hosts_path, 'r') as f:
                hosts_data = YAML().load(f)
            if hosts_data and 'hosts' in hosts_data and hosts_data['hosts']:
                for host_entry in hosts_data['hosts']:
                    if isinstance(host_entry, dict):
                        for name, info in host_entry.items():
                            ip = info.get('ip_addr', info.get('mgmt_ip', ''))
                            if ip == 'N/A':
                                ip = ''
                            display_name = normalize_device_name(name)
                            devices[display_name] = {
                                'ip': ip,
                                'user_added': True,
                                'device_type': 'linux_host',
                                'vm_name': name,
                                'device_category': 'host',
                                'supports_novnc': True  # Linux hosts have noVNC desktop access
                            }
                pS(f"Merged {len(hosts_data['hosts'])} user-added hosts into device list")
    except Exception as e:
        pS(f"Warning: Error loading user_hosts.yaml for devices: {e}")

    # Merge user-added firewalls from user_firewalls.yaml (VyOS firewalls)
    try:
        if os.path.exists(user_firewalls_path):
            with open(user_firewalls_path, 'r') as f:
                firewalls_data = YAML().load(f)
            if firewalls_data and 'firewalls' in firewalls_data and firewalls_data['firewalls']:
                for fw_entry in firewalls_data['firewalls']:
                    if isinstance(fw_entry, dict):
                        for name, info in fw_entry.items():
                            ip = info.get('ip_addr', info.get('mgmt_ip', ''))
                            if ip == 'N/A':
                                ip = ''
                            display_name = normalize_device_name(name)
                            devices[display_name] = {
                                'ip': ip,
                                'user_added': True,
                                'device_type': 'firewall',
                                'vm_name': name,
                                'device_category': 'firewall'
                            }
                pS(f"Merged {len(firewalls_data['firewalls'])} user-added firewalls into device list")
    except Exception as e:
        pS(f"Warning: Error loading user_firewalls.yaml for devices: {e}")

    # Merge user-added VeloCloud devices from user_velo.yaml
    try:
        if os.path.exists(user_velo_path):
            with open(user_velo_path, 'r') as f:
                velo_data = YAML().load(f)
            if velo_data and 'devices' in velo_data and velo_data['devices']:
                for velo_entry in velo_data['devices']:
                    if isinstance(velo_entry, dict):
                        for name, info in velo_entry.items():
                            ip = info.get('mgmt_ip', '')
                            if ip == 'N/A':
                                ip = ''
                            device_type = info.get('device_type', 'edge')
                            display_name = normalize_device_name(name)
                            devices[display_name] = {
                                'ip': ip,
                                'user_added': True,
                                'device_type': f'velo_{device_type}',
                                'vm_name': name,
                                'device_category': 'velocloud',
                                # VCO web UI requires embedded Firefox (proxy abandoned)
                                # Access via: https://<mgmt_ip>/operator/ with admin@velocloud.local
                                'supports_webui': False
                            }
                pS(f"Merged {len(velo_data['devices'])} user-added VeloCloud devices into device list")
    except Exception as e:
        pS(f"Warning: Error loading user_velo.yaml for devices: {e}")

    _ALL_DEVICES_CACHE = devices
    pS(f"Cached {len(devices)} devices from topo_build.yml + user files")
    return devices


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
            except:
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

class GetClientIdHandler(tornado.web.RequestHandler):
    def get(self):
        """
        Handler to fetch client ID from Honorlock API.
        """
        url = "https://app.honorlock.com/api/en/v1/token"
        payload = json.dumps({
            "client_id": HonorLockClientID,
            "client_secret": HonorLockSecret
        })
        headers = {'Content-Type': 'application/json'}

        try:
            response = requests.post(url, headers=headers, data=payload)
            if response.status_code in [200, 201]:
                 self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetClientIdHandler: {e}', event='error', handler='GetClientIdHandler')
            self.set_status(500)
            self.write({"error": str(e)})

class GetExamInstructionsHandler(tornado.web.RequestHandler):
    def post(self):
        """
        Handler to fetch exam instructions from Honorlock API.
        """
        safe_log('info', 'Exam instructions requested', event='exam', action='get_instructions')
        try:
            payload = json.loads(self.request.body)
            url = f"https://app.honorlock.com/api/en/v1/exams/{payload['external_exam_id']}/instructions"
            auth_header = self.request.headers.get('Authorization')

            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetExamInstructionsHandler: {e}', event='error', handler='GetExamInstructionsHandler')
            self.set_status(500)
            self.write({"error": str(e)})



class GetUserSessionIdHandler(tornado.web.RequestHandler):
    def post(self):
        """
        Handler to create a user session in Honorlock API.
        """
        safe_log('info', 'User session ID requested', event='exam', action='create_session')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/exams/sessions/create"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 201:
                self.set_status(201)
                self.write(response.json())
            elif response.status_code == 200:
                self.set_status(200)
                self.write(response.json())
                return
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetUserSessionIdHandler: {e}', event='error', handler='GetUserSessionIdHandler')
            self.set_status(500)
            self.write({"error": str(e)})

class LabHandler(tornado.web.RequestHandler):
    def get(self):
        safe_log('info', 'Lab configuration started', event='lab', action='start', lab_value=str(self.get_argument('lab_value', 'unknown')))
        self.set_header("Access-Control-Allow-Origin", "*")
        selected_lab_option = self.get_argument('lab_value')
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        login_container.exec_run(f'python3 /usr/local/bin/callConfigTopo.py  {DEFAULT_MENU_FILE_VALUE} {selected_lab_option}', detach=True)
        print(f'python3 /usr/local/bin/callConfigTopo.py  {DEFAULT_MENU_FILE_VALUE} {selected_lab_option}')
        # print(container_output)
        # log_file = open('log.txt','w')
        # log_file.write(str(container_output.output.decode("utf-8")))
        # log_file.close()
        # with open("log.txt", "r") as txt_file:
        #     response =  txt_file.readlines()
        self.write({
            'response':'Configuration is being applied. Check in CVP that all tasks have been applied'
        })

class LabStausHandler(tornado.web.RequestHandler):
    def get(self):
        safe_log('info', 'Lab status queried', event='lab', action='status_check')
        self.set_header("Access-Control-Allow-Origin", "*")
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        container_output=login_container.exec_run(f'sudo lab_status.py')

        # Filter output to only include lines with format "name,status"
        # Skip log lines that contain timestamps or log levels (INFO, WARNING, ERROR, DEBUG)
        response = []
        output_text = container_output.output.decode("utf-8")

        for line in output_text.splitlines():
            # Only include lines that match the switch status format (contain comma)
            # and don't contain log-related keywords
            if ',' in line and not any(keyword in line for keyword in ['INFO', 'WARNING', 'ERROR', 'DEBUG', ' - ', 'Checking', 'completed']):
                response.append(line.strip())

        print(f"Filtered lab status response: {response}")
        self.write({
            'response':response
        })


class ResetLabHandler(tornado.web.RequestHandler):
    def get(self):
        safe_log('info', 'Lab reset initiated', event='lab', action='reset')
        self.set_header("Access-Control-Allow-Origin", "*")
        lab_names = self.get_argument('lab_names')
        self.write({
            'response':lab_names
        })
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        login_container.exec_run(f'sudo python3 /usr/local/bin/resetVMs.py')

class ExamStatusHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            self.write({
                'response':"startExamButtonNeeded" if host_yaml['examButtonNeeded'] else "startExamButtonNotNeeded",
                'examStartTime': host_yaml.get('startExamTime', 0),
            })
        except Exception as e:
            safe_log('error', f'Error in ExamStatusHandler.get: {e}', event='error', handler='ExamStatusHandler')
            self.set_status(500)
            self.write({"error": str(e)})

    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            exam_duration = host_yaml.get("exam_duration", 0)
            safe_log('info', 'Exam started', event='exam', action='start', duration_minutes=str(exam_duration))
            current_time = int(time.time())
            global EXAM_END_TIME
            global EXAM_START_TIME
            EXAM_START_TIME = current_time
            EXAM_END_TIME = current_time + (exam_duration * 60)
            host_yaml['startExamTime'] = EXAM_START_TIME
            host_yaml['endExamTime'] = EXAM_END_TIME
            host_yaml['examButtonNeeded'] = False
            yaml = YAML()
            with open(ATD_ACCESS_PATH, "w") as file:
                yaml.dump(host_yaml, file)

            # Call HubSpot to update exam start time
            try:
                customer_email = host_yaml.get('customer_details', {}).get('exam_taker_email', '')
                if customer_email and customer_email != 'arista-test-taker@arista.com':
                    print(f"Calling HubSpot to update exam start time for {customer_email}")
                    hubspot_response = update_hubspot_handler(customer_email, 'update_exam_start', PROJECT)
                    print(f"HubSpot response: {hubspot_response}")
                else:
                    print(f"Skipping HubSpot update - no valid customer email found")
            except Exception as hubspot_error:
                # Don't fail the exam start if HubSpot update fails
                safe_log('error', f'Error in ExamStatusHandler HubSpot update: {hubspot_error}', event='error', handler='ExamStatusHandler')
                print(f"Warning: HubSpot update failed but exam started successfully: {hubspot_error}")

            self.write({
                'response':f'Status updated to ExamButtonNotNeeded'
                    })
        except Exception as e:
            safe_log('error', f'Error in ExamStatusHandler.post: {e}', event='error', handler='ExamStatusHandler')
            self.set_status(500)
            self.write({"error": str(e)})

class ExamSubmitHandler(tornado.web.RequestHandler):
    def get(self):
        safe_log('info', 'Exam submitted', event='exam', action='submit')
        self.set_header("Access-Control-Allow-Origin", "*")
        try:
            docker_conn= docker.from_env()
            login_container = docker_conn.containers.get('atd-login') 
            login_container.exec_run(f'sudo python3 -m exam_upload_v2.main', detach=True)
            self.write({
                'response':f'Exam has been submitted'
                    })
        except Exception as e:
            safe_log('error', f'Error in ExamSubmitHandler: {e}', event='error', handler='ExamSubmitHandler')
            self.set_status(500)
            self.write({"error": str(e)})

class ToolsHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)            
            # Extract the three parameters
            changeLatency = data.get('changeLatency', False)
            devices = data.get('devices', [])
            score = data.get('score', 0)
            result = subprocess.run(f'please update code {"ENABLE" if changeLatency else "DISABLE"} -d {score} -i {",".join(devices)}"',
                shell=True,
                capture_output=True,
                text=True
            )
            
            # Prepare the response
            response = {
                "changeLatency": changeLatency,
                "devices": devices,
                "score": score,
                "result" : result.stdout,
                "message": "Parameters received successfully"
            }
            
            # Send the response
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(response))
        
        except json.JSONDecodeError:
            safe_log('error', 'Error in ToolsHandler: Invalid JSON in request body', event='error', handler='ToolsHandler')
            self.set_status(400)
            self.write({"error": "Invalid JSON in request body"})
        except ValueError as e:
            safe_log('error', f'Error in ToolsHandler: {e}', event='error', handler='ToolsHandler')
            self.set_status(400)
            self.write({"error": str(e)})
        except Exception as e:
            safe_log('error', f'Error in ToolsHandler: {e}', event='error', handler='ToolsHandler')
            self.set_status(500)
            self.write({"error": "Internal server error"})

class ViewConfigHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)
            
            # Extract the three parameters
            devices = data.get('devices', False)
            result = subprocess.run(f'please update code "sudo -S python3 /home/atdadmin/change-latency.py "SHOW" -i {",".join(devices)}"',
                shell=True,
                capture_output=True,
                text=True
            )
            # Prepare the response
            response = {
                "devices": devices,
                "result" : result.stdout,
                "message": "Parameters received successfully"
            }
            
            # Send the response
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(response))
        
        except json.JSONDecodeError:
            safe_log('error', 'Error in ViewConfigHandler: Invalid JSON in request body', event='error', handler='ViewConfigHandler')
            self.set_status(400)
            self.write({"error": "Invalid JSON in request body"})
        except ValueError as e:
            safe_log('error', f'Error in ViewConfigHandler: {e}', event='error', handler='ViewConfigHandler')
            self.set_status(400)
            self.write({"error": str(e)})
        except Exception as e:
            safe_log('error', f'Error in ViewConfigHandler: {e}', event='error', handler='ViewConfigHandler')
            self.set_status(500)
            self.write({"error": "Internal server error"})
class ExamRedoRedirectHandler(BaseHandler):
    def get(self):
        try:
            # Load access info to get customer details
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            
            # Get customer name
            exam_taker_name = host_yaml.get('customer_details', {}).get('exam_taker_full_name', 'Student')
            
            # Get start exam time and convert to readable format
            start_exam_time = host_yaml.get('startExamTime', 0)
            if start_exam_time:
                session_start_time = datetime.fromtimestamp(start_exam_time).strftime('%Y-%m-%d %H:%M:%S UTC')
            else:
                session_start_time = 'Unknown time'
            
            self.render(
                BASE_PATH + 'exam-redo.html',
                exam_taker_name=exam_taker_name,
                session_start_time=session_start_time
            )
        except Exception as e:
            safe_log('error', f'Error in ExamRedoRedirectHandler: {e}', event='error', handler='ExamRedoRedirectHandler')
            print(f"Error in ExamRedoRedirectHandler: {e}")
            # Fallback rendering with default values
            self.render(
                BASE_PATH + 'exam-redo.html',
                exam_taker_name='Student',
                session_start_time='Unknown time'
            )
class BeginExamHandler(tornado.web.RequestHandler):
    def post(self):

        """
        Handler to create a user Begin Exam in Honorlock API.
        """
        safe_log('info', 'Exam begin requested', event='exam', action='begin')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/session/start"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                self.write(response.json())
            elif response.status_code == 409:
                self.set_status(409)
                self.write(response.json())
                return
            else:
                self.set_status(response.status_code)
                self.write(response.json())
        except Exception as e:
            safe_log('error', f'Error in BeginExamHandler: {e}', event='error', handler='BeginExamHandler')
            self.set_status(500)
            self.write({"error": str(e)})
class BaseUrlHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            login_info = host_yaml.get('login_info', {}).get('jump_host', {})
            response = {
                "pwd": login_info.get('pw', ''),
                "user": login_info.get('user', '')
            }
            encoded_response = b64encode(json.dumps(response).encode()).decode()
            self.write({"response": encoded_response})
        except Exception as e:
            safe_log('error', f'Error in BaseUrlHandler: {e}', event='error', handler='BaseUrlHandler')
            self.set_status(500)
            self.write({"error": str(e)})

class UptimeWithRuntimeHandler(tornado.web.RequestHandler):
    def get(self):
        """
        Handler to provide uptime data with runtime information for timer widget
        """
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Content-Type", "application/json")

            # Get uptime data directly from atd-uptime service
            try:
                response = requests.get("http://atd-uptime:50010/uptime", timeout=1)
                instance_data = response.json()

                # Add runtime from topology metadata
                if instance_data.get('status') == 'init' and TOPO_DATA and 'labels' in TOPO_DATA and 'runtime' in TOPO_DATA['labels']:
                    instance_data['runtime'] = int(TOPO_DATA['labels']['runtime'])
                else:
                    instance_data['runtime'] = 12

                # Add exam time information if available
                instance_data['exam_end_time'] = EXAM_END_TIME
                instance_data['exam_start_time'] = EXAM_START_TIME

                self.write(json.dumps(instance_data))
            except:
                # If uptime service is not ready, return default values
                self.write(json.dumps({
                    'boottime': 0,
                    'uptime': 0,
                    'runtime': 12,
                    'status': 'init',
                    'exam_end_time': EXAM_END_TIME,
                    'exam_start_time': EXAM_START_TIME
                }))
        except Exception as e:
            safe_log('error', f'Error in UptimeWithRuntimeHandler: {e}', event='error', handler='UptimeWithRuntimeHandler')
            self.set_status(500)
            self.write(json.dumps({
                "error": str(e),
                "boottime": 0,
                "uptime": 0,
                "runtime": 12,
                "status": "error",
                "exam_end_time": 0,
                "exam_start_time": 0
            }))

class GetAccessInfoHandler(tornado.web.RequestHandler):
    def validate_field(self, customer_details, field_name, default_value, validated_details, defaulted_fields):
        """
        Validate a single field and add to validated_details with default if needed
        """
        field_value = customer_details.get(field_name)
        if field_value is None or str(field_value).strip() == '':
            validated_details[field_name] = default_value
            defaulted_fields.append(field_name)
            print(f"Field '{field_name}' is empty or missing, using default: {default_value}")
        else:
            validated_details[field_name] = str(field_value)

    def get(self):
        """
        Handler to fetch user details from ACCESS_INFO.yaml file.
        """
        self.set_header("Access-Control-Allow-Origin", "*")
        try:
            # Check authorization
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            # Read the YAML file
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            
            # Extract customer details
            customer_details = host_yaml.get('customer_details', {})

            default_values = {
                "exam_taker_id": "Arista-test-taker-ID",
                "exam_taker_email": "arista-test-taker@arista.com", 
                "exam_taker_full_name": "Arista Test Taker",
                "external_exam_id": "default-training-exam",
                "exam_taker_attempt_id": "1",
                "exam_hours": "240",
                "lab_type": "Lab",
                "exam_code": "001"
            }
            validated_details = {}
            defaulted_fields = []
            # Validate each field using the helper method
            for field_name, default_value in default_values.items():
                self.validate_field(customer_details, field_name, default_value, validated_details, defaulted_fields)
            self.write({
                    "customer_details": validated_details
                })

        except Exception as e:
            safe_log('error', f'Error in GetAccessInfoHandler: {e}', event='error', handler='GetAccessInfoHandler')
            print(f"Error in GetAccessInfoHandler: {str(e)}")
            self.set_status(500)
            self.write({
                "error": str(e),
                    "customer_details": default_values
            })

class TerminalPageHandler(BaseHandler):
    """Handler for the tabbed terminal page."""

    def get(self):
        safe_log('info', 'Terminal page accessed', event='page_view', page='terminal')
        if not self.current_user:
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return

        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        self.render(
            BASE_PATH + 'terminal.html',
            topo_title=TITLE,
            ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
        )


class ConsolePageHandler(BaseHandler):
    """Handler for the serial console page (virsh console access)."""

    def get(self):
        safe_log('info', 'Console page accessed', event='page_view', page='console')
        if not self.current_user:
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return

        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        self.render(
            BASE_PATH + 'console.html',
            topo_title=TITLE,
            ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
        )


class TopologyAPIHandler(BaseHandler):
    """API endpoint to return topology data for interactive Cytoscape.js diagram."""

    # Thread-safe cache for parsed topology data (30 second TTL)
    _cache = {}
    _cache_time = 0
    _cache_lock = threading.Lock()
    CACHE_TTL = 30
    # Track user files modification time for cache invalidation
    _user_nodes_mtime = 0
    _user_hosts_mtime = 0
    _user_firewalls_mtime = 0
    _user_velo_mtime = 0
    _user_cloudeos_mtime = 0
    _user_links_mtime = 0

    @staticmethod
    def classify_device_type(device_name):
        """Classify device type based on naming pattern. Uses shared DeviceTypeConfig."""
        return DeviceTypeConfig.classify_device(device_name)

    @staticmethod
    def extract_datacenter(device_name):
        """
        Extract datacenter identifier from device name.
        E.g., 'spine1-DC1' -> 'DC1', 'leaf2-DC2' -> 'DC2', 'host1' -> ''
        Also handles WAN Gateway naming: 'GW11' -> 'DC1', 'GW21' -> 'DC2', 'GW31' -> 'DC3'
        """
        import re
        # Match -DC followed by number or letter at end of name
        match = re.search(r'-?(DC\d+|dc\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        # Handle GW device naming: GW11, GW12 -> DC1, GW21, GW22 -> DC2, GW31 -> DC3
        # First digit after GW is the DC number
        if device_name.startswith('GW') and len(device_name) >= 3 and device_name[2].isdigit():
            dc_num = device_name[2]
            return f'DC{dc_num}'

        return ''  # No datacenter suffix

    @staticmethod
    def extract_isp_provider(device_name):
        """
        Extract ISP provider identifier from device name.
        E.g., 'core1-ISP1' -> 'ISP1', 'core2-ISP2' -> 'ISP2', 'internet' -> ''
        Used for grouping ISP devices by provider in the topology layout.
        """
        import re
        # Match -ISP followed by number
        match = re.search(r'-?(ISP\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return ''  # No ISP suffix

    @staticmethod
    def get_sort_key(device_name):
        """
        Generate a sort key for natural ordering of device names.
        E.g., spine1, spine2, spine10 sorts correctly (not spine1, spine10, spine2)
        """
        import re
        # Split name into text and number parts
        parts = re.split(r'(\d+)', device_name)
        result = []
        for part in parts:
            if part.isdigit():
                result.append(int(part))
            else:
                result.append(part.lower())
        return result

    @staticmethod
    def detect_topology_type(nodes_data):
        """
        Detect the type of topology based on device types present.
        Returns: 'wan' for P-router mesh topologies, 'datacenter' for spine-leaf
        """
        device_types = set(node['data']['device_type'] for node in nodes_data)

        has_p_routers = 'p' in device_types
        has_pe_routers = 'pe' in device_types
        has_spines = 'spine' in device_types
        has_leaves = 'leaf' in device_types

        # WAN topology: has P and PE routers, no spines
        if has_p_routers and has_pe_routers and not has_spines:
            return 'wan'

        # Datacenter topology: has spines or leaves
        if has_spines or has_leaves:
            return 'datacenter'

        # Default to datacenter layout
        return 'datacenter'

    @staticmethod
    def extract_site_number(device_name):
        """
        Extract site number from device name for WAN topology positioning.
        E.g., 'PE1' -> 1, 'A2' -> 2, 'PE-1' -> 1, 'SiteA-1' -> 1
        Returns 1 for "left" side, 2 for "right" side, 0 for center (P routers)
        """
        import re

        # Look for trailing number
        match = re.search(r'[-_]?(\d+)$', device_name)
        if match:
            num = int(match.group(1))
            # Odd numbers = site 1 (left), Even numbers = site 2 (right)
            return 1 if num % 2 == 1 else 2

        return 0  # No number found - treat as center

    @staticmethod
    def calculate_wan_positions(nodes_data, edges_data):
        """
        Calculate positions for WAN topology with P-router mesh in center.
        Layout: Left customers -> Left PEs -> P mesh -> Right PEs -> Right customers
        """
        import re

        NODE_SPACING_X = 185   # Horizontal spacing
        NODE_SPACING_Y = 135   # Vertical spacing
        COLUMN_SPACING = 260   # Extra spacing between columns
        PADDING = 100

        # Build adjacency for graph analysis
        adjacency = {}
        for node in nodes_data:
            adjacency[node['data']['id']] = set()
        for edge in edges_data:
            src = edge['data']['source']
            tgt = edge['data']['target']
            if src in adjacency and tgt in adjacency:
                adjacency[src].add(tgt)
                adjacency[tgt].add(src)

        # Categorize nodes
        p_routers = []
        pe_routers = []
        customer_devices = []  # CE, hosts, or other edge devices
        other_devices = []

        for node in nodes_data:
            dtype = node['data']['device_type']
            if dtype == 'p':
                p_routers.append(node)
            elif dtype == 'pe':
                pe_routers.append(node)
            elif DeviceTypeConfig.is_wan_customer_device(dtype) or dtype == 'leaf':
                # Customer/endpoint devices go to left/right columns in WAN layout
                # Also includes leafs which often connect to customer equipment
                customer_devices.append(node)
            else:
                other_devices.append(node)

        # Determine which side each PE is on using graph analysis
        # PEs are on "left" if their connected customers have odd site numbers
        pe_sides = {}
        for pe in pe_routers:
            pe_id = pe['data']['id']
            pe_neighbors = adjacency.get(pe_id, set())

            # Check connected devices (excluding P routers)
            side_votes = []
            for neighbor in pe_neighbors:
                neighbor_node = next((n for n in nodes_data if n['data']['id'] == neighbor), None)
                if neighbor_node and neighbor_node['data']['device_type'] != 'p':
                    site = TopologyAPIHandler.extract_site_number(neighbor)
                    if site > 0:
                        side_votes.append(site)

            # Also consider the PE's own name
            pe_site = TopologyAPIHandler.extract_site_number(pe_id)
            if pe_site > 0:
                side_votes.append(pe_site)

            # Majority vote, default to name-based
            if side_votes:
                pe_sides[pe_id] = 1 if side_votes.count(1) >= side_votes.count(2) else 2
            else:
                pe_sides[pe_id] = pe_site if pe_site > 0 else 1

        # Determine customer sides based on their PE connections
        customer_sides = {}
        for cust in customer_devices:
            cust_id = cust['data']['id']
            cust_neighbors = adjacency.get(cust_id, set())

            # Find connected PEs
            for neighbor in cust_neighbors:
                if neighbor in pe_sides:
                    customer_sides[cust_id] = pe_sides[neighbor]
                    break

            # Fallback to name-based
            if cust_id not in customer_sides:
                site = TopologyAPIHandler.extract_site_number(cust_id)
                customer_sides[cust_id] = site if site > 0 else 1

        # Split into columns (left to right)
        left_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 1]
        left_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 1) == 1]
        right_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 2) == 2]
        right_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 2]

        # Sort each group by name
        for group in [left_customers, left_pes, p_routers, right_pes, right_customers]:
            group.sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        # Calculate column X positions
        columns = [left_customers, left_pes, p_routers, right_pes, right_customers]
        column_names = ['left_cust', 'left_pe', 'p_mesh', 'right_pe', 'right_cust']

        # Find max height (most nodes in any column)
        max_height = max(len(col) for col in columns) if columns else 1

        # Position each column
        current_x = PADDING
        for col_idx, column in enumerate(columns):
            if not column:
                continue

            # Center column vertically
            col_height = len(column) * NODE_SPACING_Y
            start_y = PADDING + (max_height * NODE_SPACING_Y - col_height) / 2

            for row_idx, node in enumerate(column):
                node['position'] = {
                    'x': current_x,
                    'y': start_y + row_idx * NODE_SPACING_Y
                }
                node['data']['wan_column'] = column_names[col_idx]

            current_x += COLUMN_SPACING

        # Position any other devices at the top
        if other_devices:
            other_devices.sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))
            for idx, node in enumerate(other_devices):
                node['position'] = {
                    'x': PADDING + idx * NODE_SPACING_X,
                    'y': PADDING / 2
                }
                node['data']['wan_column'] = 'other'

        return nodes_data

    @staticmethod
    def calculate_positions(nodes_data, edges_data=None):
        """
        Calculate x,y positions for nodes based on topology type.
        Automatically detects WAN vs datacenter topologies.
        """
        import re

        # Detect topology type
        topo_type = TopologyAPIHandler.detect_topology_type(nodes_data)

        # Use WAN layout for P-router mesh topologies
        if topo_type == 'wan' and edges_data:
            return TopologyAPIHandler.calculate_wan_positions(nodes_data, edges_data)

        # Standard datacenter layout (tier-based)
        # Group nodes by tier, then by grouping key (datacenter or ISP provider)
        tiers = {}
        ISP_TIER = DeviceTypeConfig.get_tier('isp')

        for node in nodes_data:
            device_type = node['data']['device_type']
            tier = DeviceTypeConfig.get_tier(device_type)

            # For ISP tier, group by ISP provider (ISP1, ISP2) instead of datacenter
            if tier == ISP_TIER:
                group_key = TopologyAPIHandler.extract_isp_provider(node['data']['id'])
            else:
                group_key = TopologyAPIHandler.extract_datacenter(node['data']['id'])

            if tier not in tiers:
                tiers[tier] = {}
            if group_key not in tiers[tier]:
                tiers[tier][group_key] = []
            tiers[tier][group_key].append(node)

        # Sort nodes within each tier/group by name (natural sort)
        for tier in tiers:
            for group_key in tiers[tier]:
                tiers[tier][group_key].sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        # Calculate positions
        NODE_SPACING_X = 170   # Horizontal spacing between nodes
        NODE_SPACING_Y = 185   # Vertical spacing between tiers
        DC_SPACING = 120       # Extra spacing between datacenter groups
        PADDING = 100          # Left padding

        # Calculate max width considering groups and spacing
        # Groups can be DCs (DC1, DC2) or ISP providers (ISP1, ISP2) depending on tier
        max_width = 0
        for tier in tiers:
            tier_width = 0
            group_keys = sorted(tiers[tier].keys())  # Sort: '', 'DC1', 'DC2' or 'ISP1', 'ISP2'
            for i, gk in enumerate(group_keys):
                tier_width += len(tiers[tier][gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:  # Add spacing between groups
                    tier_width += DC_SPACING
            max_width = max(max_width, tier_width)

        if max_width == 0:
            max_width = NODE_SPACING_X

        # Position nodes by tier (use row_index to skip empty tiers)
        row_index = 0
        for tier_num in sorted(tiers.keys()):
            tier_groups = tiers[tier_num]
            group_keys = sorted(tier_groups.keys())  # Sort: '', 'DC1', 'DC2' or 'ISP1', 'ISP2'

            # Calculate this tier's total width
            tier_width = 0
            for i, gk in enumerate(group_keys):
                tier_width += len(tier_groups[gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:
                    tier_width += DC_SPACING

            # Center this tier
            start_x = PADDING + (max_width - tier_width) / 2
            current_x = start_x

            for i, group_key in enumerate(group_keys):
                group_nodes = tier_groups[group_key]

                for node in group_nodes:
                    node['position'] = {
                        'x': current_x,
                        'y': PADDING + row_index * NODE_SPACING_Y
                    }
                    # Store grouping info for potential UI use
                    if tier_num == ISP_TIER:
                        node['data']['isp_provider'] = group_key if group_key else 'default'
                        node['data']['datacenter'] = 'shared'  # ISP devices are shared across DCs
                    else:
                        node['data']['datacenter'] = group_key if group_key else 'default'
                    current_x += NODE_SPACING_X

                # Add spacing after each group (except last)
                if i < len(group_keys) - 1:
                    current_x += DC_SPACING

            # Increment row index for next tier that has nodes
            row_index += 1

        return nodes_data

    def parse_topology(self, topo_path):
        """
        Parse topo_build.yml and return Cytoscape.js formatted data.

        Returns:
            dict: Success with 'data' key containing topology
            dict: Error with 'error' and 'error_type' keys
        """
        # Try to open and parse the file
        try:
            with open(topo_path, 'r') as f:
                topo_data = YAML().load(f)
        except FileNotFoundError:
            return {'error': f'Topology file not found: {topo_path}', 'error_type': 'not_found'}
        except PermissionError:
            return {'error': f'Permission denied accessing: {topo_path}', 'error_type': 'permission'}
        except Exception as e:
            pS(f"Error parsing topology file: {e}")
            return {'error': f'Failed to parse topology file: {str(e)}', 'error_type': 'parse_error'}

        # Merge user-added nodes from user_nodes.yaml (for dynamically added nodes)
        user_nodes_path = '/etc/atd/user_nodes.yaml'
        try:
            if os.path.exists(user_nodes_path):
                with open(user_nodes_path, 'r') as f:
                    user_data = YAML().load(f)
                if user_data and 'nodes' in user_data and user_data['nodes']:
                    # Ensure topo_data has nodes list
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    # Append user-added nodes
                    topo_data['nodes'].extend(user_data['nodes'])
                    pS(f"Merged {len(user_data['nodes'])} user-added nodes from {user_nodes_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_nodes.yaml: {e}")
            # Continue without user nodes - don't fail the whole topology load

        # Merge user-added hosts from user_hosts.yaml (Linux desktop VMs)
        user_hosts_path = '/etc/atd/user_hosts.yaml'
        try:
            if os.path.exists(user_hosts_path):
                with open(user_hosts_path, 'r') as f:
                    hosts_data = YAML().load(f)
                if hosts_data and 'hosts' in hosts_data and hosts_data['hosts']:
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    # Convert hosts to node format for topology
                    for host_entry in hosts_data['hosts']:
                        if isinstance(host_entry, dict):
                            for name, info in host_entry.items():
                                # Create node entry with linux_host device_type
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', info.get('ip_addr', 'N/A')),
                                    'device_type': 'linux_host',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged {len(hosts_data['hosts'])} user-added hosts from {user_hosts_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_hosts.yaml: {e}")

        # Merge user-added firewalls from user_firewalls.yaml (VyOS firewalls)
        user_firewalls_path = '/etc/atd/user_firewalls.yaml'
        try:
            if os.path.exists(user_firewalls_path):
                with open(user_firewalls_path, 'r') as f:
                    firewalls_data = YAML().load(f)
                if firewalls_data and 'firewalls' in firewalls_data and firewalls_data['firewalls']:
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    # Convert firewalls to node format for topology
                    for fw_entry in firewalls_data['firewalls']:
                        if isinstance(fw_entry, dict):
                            for name, info in fw_entry.items():
                                # Create node entry with firewall device_type
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', info.get('ip_addr', 'N/A')),
                                    'device_type': 'firewall',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged {len(firewalls_data['firewalls'])} user-added firewalls from {user_firewalls_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_firewalls.yaml: {e}")

        # Merge user-added VeloCloud devices from user_velo.yaml
        user_velo_path = '/etc/atd/user_velo.yaml'
        try:
            if os.path.exists(user_velo_path):
                with open(user_velo_path, 'r') as f:
                    velo_data = YAML().load(f)
                if velo_data and 'devices' in velo_data and velo_data['devices']:
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    # Convert VeloCloud devices to node format for topology
                    for velo_entry in velo_data['devices']:
                        if isinstance(velo_entry, dict):
                            for name, info in velo_entry.items():
                                device_type = info.get('device_type', 'edge')
                                # Create node entry with velo_* device_type
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', 'N/A'),
                                    'device_type': f'velo_{device_type}',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                # Add connections as neighbors for edge drawing
                                connections = info.get('connections', [])
                                for conn in connections:
                                    target = conn.get('target_device', '')
                                    if target:
                                        node_info['neighbors'].append({
                                            'neighborDevice': target,
                                            'neighborPort': conn.get('target_port', ''),
                                            'port': conn.get('local_port', '')
                                        })
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged {len(velo_data['devices'])} user-added VeloCloud devices from {user_velo_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_velo.yaml: {e}")

        # Merge user-added CloudEOS devices from user_cloudeos.yaml
        user_cloudeos_path = '/etc/atd/user_cloudeos.yaml'
        try:
            if os.path.exists(user_cloudeos_path):
                with open(user_cloudeos_path, 'r') as f:
                    cloudeos_data = YAML().load(f)
                if cloudeos_data and 'devices' in cloudeos_data and cloudeos_data['devices']:
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    for device_entry in cloudeos_data['devices']:
                        if isinstance(device_entry, dict):
                            for name, info in device_entry.items():
                                if isinstance(info, dict) and info.get('status') == 'creating':
                                    continue
                                node_info = {
                                    'ip_addr': info.get('ip_addr', 'N/A'),
                                    'device_type': info.get('device_type', 'other'),
                                    'device_category': 'cloudeos',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged user-added CloudEOS devices from {user_cloudeos_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_cloudeos.yaml: {e}")

        # Merge user-added links from user_links.yaml
        # These add neighbor entries to existing topology nodes
        user_links_path = '/etc/atd/user_links.yaml'
        try:
            if os.path.exists(user_links_path):
                with open(user_links_path, 'r') as f:
                    links_data = YAML().load(f)
                if links_data and 'links' in links_data and links_data['links']:
                    links_merged = 0
                    for link in links_data['links']:
                        source = link.get('source_device', '')
                        source_port = link.get('source_port', '')
                        target = link.get('target_device', '')
                        target_port = link.get('target_port', '')
                        if source and target:
                            # Add neighbor entry to source node
                            for node_entry in topo_data['nodes']:
                                if isinstance(node_entry, dict):
                                    for node_name in node_entry:
                                        if node_name.lower() == source.lower():
                                            neighbors = node_entry[node_name].setdefault('neighbors', [])
                                            neighbors.append({
                                                'neighborDevice': target,
                                                'neighborPort': target_port,
                                                'port': source_port,
                                                'user_added': True
                                            })
                                            links_merged += 1
                    if links_merged > 0:
                        pS(f"Merged {links_merged} user-added links from {user_links_path}")
        except Exception as e:
            pS(f"Warning: Error loading user_links.yaml: {e}")

        # Validate topo_data structure
        if topo_data is None:
            return {'error': 'Topology file is empty', 'error_type': 'empty_file'}

        if 'nodes' not in topo_data:
            return {'error': 'Topology file missing "nodes" key', 'error_type': 'invalid_format'}

        if not topo_data['nodes']:
            # Empty nodes list - return empty topology (valid case)
            pS("Warning: Topology file has no nodes")
            return {
                'data': {
                    'metadata': {
                        'topology_name': TOPO,
                        'eos_type': EOS_TYPE,
                        'node_count': 0,
                        'edge_count': 0,
                        'generated_at': datetime.now().isoformat()
                    },
                    'nodes': [],
                    'edges': []
                }
            }

        nodes = []
        edges = []
        edge_set = set()  # Track edges to avoid duplicates

        # First pass: collect all valid node names and build normalization mapping
        # Maps raw_name (from YAML) -> normalized_name (for display and API consistency)
        valid_node_names = set()
        name_mapping = {}  # raw_name -> normalized_name
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for raw_name in node_entry.keys():
                    normalized = normalize_device_name(raw_name)
                    valid_node_names.add(raw_name)
                    name_mapping[raw_name] = normalized

        # Second pass: build nodes and edges
        for node_entry in topo_data['nodes']:
            # Validate node entry is a dict
            if not isinstance(node_entry, dict):
                pS(f"Warning: Invalid node entry format (not a dict): {node_entry}")
                continue

            # Each entry is a dict with device name as key
            for raw_device_name, device_info in node_entry.items():
                # Get normalized display name for API consistency
                display_name = name_mapping.get(raw_device_name, raw_device_name)

                # Validate device_info is a dict
                if not isinstance(device_info, dict):
                    pS(f"Warning: Invalid device info for {raw_device_name} (not a dict)")
                    continue

                # Use explicit device_type if provided (for user-added nodes),
                # otherwise classify from device name
                device_type = device_info.get('device_type') or self.classify_device_type(raw_device_name)
                ip_addr = device_info.get('ip_addr', 'N/A')
                sys_mac = device_info.get('sys_mac', 'N/A')
                neighbors = device_info.get('neighbors', [])
                # Check if this is a user-added node (from user_nodes.yaml)
                user_added = device_info.get('user_added', False)

                # Validate neighbors is a list
                if not isinstance(neighbors, list):
                    pS(f"Warning: Invalid neighbors format for {raw_device_name}")
                    neighbors = []

                # Build port info for tooltip
                ports = []
                for neighbor in neighbors:
                    if not isinstance(neighbor, dict):
                        continue

                    neighbor_device_raw = neighbor.get('neighborDevice', '')
                    # Get normalized neighbor name for display
                    neighbor_device_display = name_mapping.get(neighbor_device_raw, neighbor_device_raw)

                    ports.append({
                        'port': neighbor.get('port', ''),
                        'neighbor': neighbor_device_display,  # Use normalized name for display
                        'neighbor_port': neighbor.get('neighborPort', '')
                    })

                    # Create edge only if both nodes exist (prevents Cytoscape.js errors)
                    if neighbor_device_raw and neighbor_device_raw in valid_node_names:
                        # Get port values with None-safety
                        device_port = neighbor.get('port') or ''
                        neighbor_port = neighbor.get('neighborPort') or ''

                        # Create edge key that includes ports to support multiple links
                        # between the same device pair (e.g., MLAG, port-channel, redundancy)
                        # Use normalized names for edge keys
                        port_pair = tuple(sorted([device_port, neighbor_port]))
                        edge_key = (tuple(sorted([display_name, neighbor_device_display])), port_pair)

                        if edge_key not in edge_set:
                            edge_set.add(edge_key)

                            # Use alphabetically sorted order for source/target to ensure
                            # consistent port assignment regardless of processing order.
                            # edge_key[0][0] is the alphabetically first device name.
                            sorted_devices = edge_key[0]
                            if display_name == sorted_devices[0]:
                                # Current device is alphabetically first, so it's the source.
                                # Ports stay as-is: device_port -> source, neighbor_port -> target
                                source_node = display_name
                                target_node = neighbor_device_display
                                source_port = device_port
                                target_port = neighbor_port
                            else:
                                # Neighbor device is alphabetically first, so it becomes source.
                                # Since we're processing from device's perspective, swap ports:
                                # neighbor_port belongs to the alphabetically-first (source) node
                                # device_port belongs to the alphabetically-second (target) node
                                source_node = neighbor_device_display
                                target_node = display_name
                                source_port = neighbor_port
                                target_port = device_port

                            # Use unique edge ID that includes ports to support parallel links
                            edge_id = f"{source_node}-{target_node}-{source_port}-{target_port}"
                            edge_data = {
                                'id': edge_id,
                                'source': source_node,
                                'target': target_node,
                                'source_port': source_port,
                                'target_port': target_port
                            }
                            # Mark user-added links so the UI can show Remove Link option
                            # and style them differently on the diagram
                            edge_entry = {'data': edge_data}
                            if neighbor.get('user_added'):
                                edge_data['user_added'] = True
                                edge_entry['classes'] = 'edge-user-added'
                            edges.append(edge_entry)
                    elif neighbor_device_raw:
                        pS(f"Warning: Skipping edge {display_name}->{neighbor_device_display}: target node not in topology")

                # Create node with normalized display name as ID
                # Keep vm_name for virsh console access (uses original name from YAML)
                # Include device_category so the UI can route delete requests correctly
                device_category = device_info.get('device_category', 'node')
                nodes.append({
                    'data': {
                        'id': display_name,
                        'label': display_name,
                        'ip': ip_addr,
                        'sys_mac': sys_mac,
                        'device_type': device_type,
                        'device_category': device_category,
                        'status': 'unknown',
                        'ports': ports,
                        'user_added': user_added,
                        'vm_name': raw_device_name  # Original name for virsh console
                    },
                    'classes': f"device-type-{device_type} status-unknown"
                })

        # Calculate positions based on device type tiers and natural name sorting
        # Pass edges for WAN topology detection which uses graph adjacency
        nodes = self.calculate_positions(nodes, edges)

        return {
            'data': {
                'metadata': {
                    'topology_name': TOPO,
                    'eos_type': EOS_TYPE,
                    'node_count': len(nodes),
                    'edge_count': len(edges),
                    'generated_at': datetime.now().isoformat()
                },
                'nodes': nodes,
                'edges': edges
            }
        }

    def options(self):
        """Handle CORS preflight requests."""
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.set_header("Access-Control-Max-Age", "86400")  # Cache preflight for 24 hours
        self.set_status(204)
        self.finish()

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Topology API requested', event='api', endpoint='topology')
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            current_time = time.time()

            # Check if any user file was modified (invalidates cache)
            user_nodes_path = '/etc/atd/user_nodes.yaml'
            user_hosts_path = '/etc/atd/user_hosts.yaml'
            user_firewalls_path = '/etc/atd/user_firewalls.yaml'
            user_velo_path = '/etc/atd/user_velo.yaml'
            user_cloudeos_path = '/etc/atd/user_cloudeos.yaml'
            user_links_path = '/etc/atd/user_links.yaml'

            user_nodes_mtime = os.path.getmtime(user_nodes_path) if os.path.exists(user_nodes_path) else 0
            user_hosts_mtime = os.path.getmtime(user_hosts_path) if os.path.exists(user_hosts_path) else 0
            user_firewalls_mtime = os.path.getmtime(user_firewalls_path) if os.path.exists(user_firewalls_path) else 0
            user_velo_mtime = os.path.getmtime(user_velo_path) if os.path.exists(user_velo_path) else 0
            user_cloudeos_mtime = os.path.getmtime(user_cloudeos_path) if os.path.exists(user_cloudeos_path) else 0
            user_links_mtime = os.path.getmtime(user_links_path) if os.path.exists(user_links_path) else 0

            # Thread-safe cache check - invalidate if any user file changed
            with TopologyAPIHandler._cache_lock:
                cache_valid = (
                    TopologyAPIHandler._cache and
                    current_time - TopologyAPIHandler._cache_time < TopologyAPIHandler.CACHE_TTL and
                    user_nodes_mtime <= TopologyAPIHandler._user_nodes_mtime and
                    user_hosts_mtime <= TopologyAPIHandler._user_hosts_mtime and
                    user_firewalls_mtime <= TopologyAPIHandler._user_firewalls_mtime and
                    user_velo_mtime <= TopologyAPIHandler._user_velo_mtime and
                    user_cloudeos_mtime <= TopologyAPIHandler._user_cloudeos_mtime and
                    user_links_mtime <= TopologyAPIHandler._user_links_mtime
                )
                if cache_valid:
                    self.write(json.dumps(TopologyAPIHandler._cache))
                    return

            # Parse topology file (outside lock to avoid blocking)
            topo_path = f"/opt/atd/topologies/{TOPO}/topo_build.yml"
            result = self.parse_topology(topo_path)

            # Check for errors
            if 'error' in result:
                error_type = result.get('error_type', 'unknown')
                if error_type == 'not_found':
                    self.set_status(404)
                elif error_type == 'permission':
                    self.set_status(403)
                else:
                    self.set_status(500)
                self.write(json.dumps({'error': result['error']}))
                return

            topology_data = result['data']

            # Thread-safe cache update (include user file mtimes for invalidation)
            with TopologyAPIHandler._cache_lock:
                TopologyAPIHandler._cache = topology_data
                TopologyAPIHandler._cache_time = current_time
                TopologyAPIHandler._user_nodes_mtime = user_nodes_mtime
                TopologyAPIHandler._user_hosts_mtime = user_hosts_mtime
                TopologyAPIHandler._user_firewalls_mtime = user_firewalls_mtime
                TopologyAPIHandler._user_velo_mtime = user_velo_mtime
                TopologyAPIHandler._user_cloudeos_mtime = user_cloudeos_mtime
                TopologyAPIHandler._user_links_mtime = user_links_mtime

            self.write(json.dumps(topology_data))

        except Exception as e:
            safe_log('error', f'Error in TopologyAPIHandler: {e}', event='error', handler='TopologyAPIHandler')
            pS(f"Error in TopologyAPIHandler: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': f'Internal server error: {str(e)}'}))


class DevicesAPIHandler(BaseHandler):
    """API endpoint to return device list grouped by type for terminal page."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Devices API requested', event='api', endpoint='devices')
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get devices from topo_build.yml, user_nodes.yaml, user_hosts.yaml, user_firewalls.yaml
            nodes = get_all_devices()

            # Group devices using shared DeviceTypeConfig
            # User-added items go to separate groups by category at the end
            groups = {}
            user_nodes_group = []
            user_hosts_group = []
            user_firewalls_group = []
            user_velocloud_group = []

            for device_name, device_info in nodes.items():
                is_user_added = device_info.get('user_added', False)
                device_category = device_info.get('device_category', 'node')

                # Build device entry with new flags
                # Console only supported for KVM labs (virsh console), not cEOS
                supports_console = EOS_TYPE != 'container-labs'
                # Include original VM name for virsh console connections
                vm_name = device_info.get('vm_name', device_name)
                # Linux hosts support noVNC desktop access
                supports_novnc = device_info.get('supports_novnc', False)
                # VeloCloud Orchestrator supports web UI access
                supports_webui = device_info.get('supports_webui', False)

                device_entry = {
                    'name': device_name,
                    'vmName': vm_name,  # Original name for virsh console
                    'ip': device_info.get('ip', ''),
                    'userAdded': is_user_added,
                    'supportsConsole': supports_console,
                    'supportsNoVnc': supports_novnc,
                    'supportsWebUI': supports_webui,
                }

                if is_user_added:
                    # User-added devices go to category-specific groups
                    if device_category == 'host':
                        user_hosts_group.append(device_entry)
                    elif device_category == 'firewall':
                        user_firewalls_group.append(device_entry)
                    elif device_category == 'velocloud':
                        user_velocloud_group.append(device_entry)
                    else:
                        user_nodes_group.append(device_entry)
                else:
                    # Regular nodes grouped by device type
                    device_type = device_info.get('device_type') or DeviceTypeConfig.classify_device(device_name)
                    group_name = DeviceTypeConfig.get_group_name(device_type)

                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(device_entry)

            # Sort devices within each group and format result
            # Order groups by tier (using first device type that maps to each group)
            group_order = DeviceTypeConfig.get_all_group_names()

            result = []
            for group_name in group_order:
                if group_name in groups and groups[group_name]:
                    devices = sorted(groups[group_name], key=lambda x: x['name'])
                    result.append({
                        'group': group_name,
                        'devices': devices
                    })

            # Add any remaining groups not in the predefined order
            for group_name in sorted(groups.keys()):
                if group_name not in group_order and groups[group_name]:
                    devices = sorted(groups[group_name], key=lambda x: x['name'])
                    result.append({
                        'group': group_name,
                        'devices': devices
                    })

            # Add User Nodes group if there are any
            if user_nodes_group:
                result.append({
                    'group': 'User Nodes',
                    'devices': sorted(user_nodes_group, key=lambda x: x['name'])
                })

            # Add User Hosts group if there are any (Linux desktop VMs)
            if user_hosts_group:
                result.append({
                    'group': 'Linux Hosts',
                    'devices': sorted(user_hosts_group, key=lambda x: x['name'])
                })

            # Add User Firewalls group if there are any (VyOS firewalls)
            if user_firewalls_group:
                result.append({
                    'group': 'Firewalls',
                    'devices': sorted(user_firewalls_group, key=lambda x: x['name'])
                })

            # Add VeloCloud group if there are any (VeloCloud Edge/Gateway/Orchestrator)
            if user_velocloud_group:
                result.append({
                    'group': 'VeloCloud',
                    'devices': sorted(user_velocloud_group, key=lambda x: x['name'])
                })

            self.write(json.dumps({
                'topology': TITLE,
                'eosType': EOS_TYPE,
                'groups': result
            }))

        except FileNotFoundError as e:
            safe_log('error', f'Error in DevicesAPIHandler: {e}', event='error', handler='DevicesAPIHandler')
            pS(f"DevicesAPIHandler: Configuration file not found: {e}")
            self.set_status(503)
            self.write(json.dumps({
                'error': 'Device configuration not available',
                'detail': 'The topology configuration file could not be found. Please wait for the lab to finish initializing.',
                'retry': True
            }))

        except (yaml.YAMLError, json.JSONDecodeError) as e:
            safe_log('error', f'Error in DevicesAPIHandler: {e}', event='error', handler='DevicesAPIHandler')
            pS(f"DevicesAPIHandler: Configuration parse error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({
                'error': 'Configuration error',
                'detail': 'The device configuration could not be parsed.',
                'retry': False
            }))

        except Exception as e:
            safe_log('error', f'Error in DevicesAPIHandler: {e}', event='error', handler='DevicesAPIHandler')
            pS(f"DevicesAPIHandler: Unexpected error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({
                'error': 'Internal server error',
                'detail': str(e),
                'retry': True
            }))


class DeviceTypesAPIHandler(BaseHandler):
    """API endpoint to return device type metadata for frontend."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            metadata = DeviceTypeConfig.export_for_frontend()
            self.write(json.dumps(metadata))
        except Exception as e:
            safe_log('error', f'Error in DeviceTypesAPIHandler: {e}', event='error', handler='DeviceTypesAPIHandler')
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class InterfaceStatsAPIHandler(BaseHandler):
    """API endpoint for interface statistics via eAPI."""

    # Cache: {device_interface: (timestamp, data)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 10  # seconds

    # Rate calculation: store previous readings for rate computation
    _previous_counters = {}
    _previous_lock = threading.Lock()

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Interface stats requested', event='api', endpoint='interface_stats', device=str(self.get_argument('device', 'unknown')))
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)
        interface = self.get_argument('interface', None)

        if not device or not interface:
            self.set_status(400)
            self.write(json.dumps({'error': 'device and interface parameters required'}))
            return

        try:
            stats = self.get_interface_stats(device, interface)
            self.write(json.dumps(stats))
        except Exception as e:
            error_str = str(e)
            # Check for authentication failures - device is up but not configured
            if 'Unauthorized' in error_str or 'Bad username' in error_str or 'authentication' in error_str.lower():
                pS(f"InterfaceStatsAPIHandler: Auth failed for {device} (unconfigured)")
                self.write(json.dumps({
                    'device': device,
                    'interface': interface,
                    'status': 'unconfigured',
                    'error': 'Device reachable but authentication failed (not yet configured)'
                }))
            else:
                pS(f"InterfaceStatsAPIHandler error: {e}")
                traceback.print_exc()
                self.set_status(500)
                self.write(json.dumps({'error': error_str}))

    def get_interface_stats(self, device_name, interface_name):
        """Query EOS device for interface counters via eAPI."""
        cache_key = f"{device_name}:{interface_name}"
        current_time = time.time()

        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        # Get device IP from topology
        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        # Get credentials from ACCESS_INFO
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        # Connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=10
            )

            # Execute show interfaces command
            result = connection.execute([f"show interfaces {interface_name}"])

            # Parse interface data
            interfaces = result.get('result', [{}])[0].get('interfaces', {})
            intf_data = interfaces.get(interface_name, {})

            if not intf_data:
                raise ValueError(f"Interface {interface_name} not found on {device_name}")

            counters = intf_data.get('interfaceCounters', {})
            bandwidth = intf_data.get('bandwidth', 0)

            # Calculate rates from counter deltas
            rates = self.calculate_rates(cache_key, counters, current_time)

            # Calculate utilization percentage
            if bandwidth > 0:
                utilization_in = (rates['in_rate_bps'] / bandwidth) * 100
                utilization_out = (rates['out_rate_bps'] / bandwidth) * 100
            else:
                utilization_in = 0
                utilization_out = 0

            stats = {
                'device': device_name,
                'interface': interface_name,
                'stats': {
                    'in_octets': counters.get('inOctets', 0),
                    'out_octets': counters.get('outOctets', 0),
                    'in_rate_bps': rates['in_rate_bps'],
                    'out_rate_bps': rates['out_rate_bps'],
                    'in_packets': counters.get('inUcastPkts', 0) + counters.get('inMulticastPkts', 0) + counters.get('inBroadcastPkts', 0),
                    'out_packets': counters.get('outUcastPkts', 0) + counters.get('outMulticastPkts', 0) + counters.get('outBroadcastPkts', 0),
                    'in_errors': counters.get('inErrors', 0) + counters.get('inputErrorsDetail', {}).get('crcErrors', 0),
                    'out_errors': counters.get('outErrors', 0),
                    'in_discards': counters.get('inDiscards', 0),
                    'out_discards': counters.get('outDiscards', 0),
                    'speed_bps': bandwidth,
                    'utilization_in': round(utilization_in, 2),
                    'utilization_out': round(utilization_out, 2),
                    'operational_status': intf_data.get('interfaceStatus', 'unknown'),
                    'line_protocol': intf_data.get('lineProtocolStatus', 'unknown'),
                    'description': intf_data.get('description', ''),
                    'last_updated': datetime.now().isoformat()
                }
            }

            # Update cache
            with self._cache_lock:
                self._cache[cache_key] = (current_time, stats)

            return stats

        except pyeapi.eapilib.ConnectionError as e:
            # Preserve auth failure info for upstream handler to detect 'unconfigured' status
            error_str = str(e)
            if 'Unauthorized' in error_str or 'Bad username' in error_str:
                raise ValueError(f"Unauthorized: Cannot authenticate to {device_name} ({device_ip}): {e}")
            raise ValueError(f"Cannot connect to {device_name} ({device_ip}): {e}")
        except pyeapi.eapilib.CommandError as e:
            raise ValueError(f"Command error on {device_name}: {e}")

    def calculate_rates(self, cache_key, current_counters, current_time):
        """Calculate bit rates from counter deltas."""
        with self._previous_lock:
            if cache_key in self._previous_counters:
                prev_time, prev_counters = self._previous_counters[cache_key]
                time_delta = current_time - prev_time

                if time_delta > 0:
                    in_octet_delta = current_counters.get('inOctets', 0) - prev_counters.get('inOctets', 0)
                    out_octet_delta = current_counters.get('outOctets', 0) - prev_counters.get('outOctets', 0)

                    # Handle counter wrap (unlikely but possible)
                    if in_octet_delta < 0:
                        in_octet_delta = current_counters.get('inOctets', 0)
                    if out_octet_delta < 0:
                        out_octet_delta = current_counters.get('outOctets', 0)

                    in_rate = (in_octet_delta * 8) / time_delta
                    out_rate = (out_octet_delta * 8) / time_delta
                else:
                    in_rate = out_rate = 0
            else:
                # First reading, no rate available yet
                in_rate = out_rate = 0

            # Store current reading for next calculation
            self._previous_counters[cache_key] = (current_time, {
                'inOctets': current_counters.get('inOctets', 0),
                'outOctets': current_counters.get('outOctets', 0)
            })

            return {'in_rate_bps': round(in_rate, 2), 'out_rate_bps': round(out_rate, 2)}


class DeviceStatusAPIHandler(BaseHandler):
    """API endpoint to check device reachability via eAPI."""

    # Cache: {device: (timestamp, status)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 30  # seconds - longer cache for status checks

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Device status check requested', event='api', endpoint='device_status')
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)

        # If no device specified, check all devices
        if device:
            try:
                status = self.check_device_status(device)
                self.write(json.dumps(status))
            except Exception as e:
                self.write(json.dumps({
                    'device': device,
                    'status': 'error',
                    'error': str(e)
                }))
        else:
            # Check all devices in topology
            statuses = self.check_all_devices()
            self.write(json.dumps({'devices': statuses}))

    def check_device_status(self, device_name):
        """Check if a single device is reachable. Uses eAPI for EOS devices, ping for hosts/firewalls."""
        cache_key = device_name
        current_time = time.time()

        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        # Get device info from topology (includes device_category for hosts/firewalls)
        all_devices = get_all_devices()
        device_info = all_devices.get(device_name, {})
        device_ip = device_info.get('ip', '')
        device_category = device_info.get('device_category', 'node')

        # Fallback to old method if not found in get_all_devices
        if not device_ip:
            device_ip = get_device_ip_from_sources(device_name)

        pS(f"[DeviceStatus] Checking {device_name} -> IP: {device_ip}, category: {device_category}")
        if not device_ip:
            result = {
                'device': device_name,
                'status': 'unknown',
                'error': 'Device not found in topology'
            }
            return result

        # For hosts and firewalls, use ping instead of eAPI
        if device_category in ('host', 'firewall'):
            result = self._check_device_via_ping(device_name, device_ip, device_category)
        else:
            # For EOS devices, use eAPI
            result = self._check_device_via_eapi(device_name, device_ip)

        # Update cache
        with self._cache_lock:
            self._cache[cache_key] = (current_time, result)

        return result

    def _check_device_via_ping(self, device_name, device_ip, device_category):
        """Check if a host or firewall is reachable via ping or TCP check."""
        import subprocess
        import socket

        device_type_label = 'Linux Host' if device_category == 'host' else 'VyOS Firewall'

        # Try ping first
        try:
            # Quick ping with 1 second timeout
            result = subprocess.run(
                ['ping', '-c', '1', '-W', '1', device_ip],
                capture_output=True,
                timeout=3
            )

            if result.returncode == 0:
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'up',
                    'version': device_type_label,
                    'last_check': datetime.now().isoformat()
                }
            else:
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'down',
                    'error': 'Ping failed',
                    'last_check': datetime.now().isoformat()
                }
        except subprocess.TimeoutExpired:
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': 'Ping timeout',
                'last_check': datetime.now().isoformat()
            }
        except FileNotFoundError:
            # ping command not available, fallback to TCP check on port 22 (SSH)
            pS(f"[DeviceStatus] Ping not available, trying TCP check for {device_name}")
            pass
        except Exception as e:
            # Log the error but try TCP fallback
            pS(f"[DeviceStatus] Ping failed for {device_name}: {e}, trying TCP check")
            pass

        # Fallback: TCP check on SSH port (22)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex((device_ip, 22))
            sock.close()

            if result == 0:
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'up',
                    'version': device_type_label,
                    'last_check': datetime.now().isoformat()
                }
            else:
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'down',
                    'error': f'TCP port 22 not responding (code {result})',
                    'last_check': datetime.now().isoformat()
                }
        except Exception as e:
            pS(f"[DeviceStatus] TCP check also failed for {device_name}: {e}")
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': f'Unreachable: {str(e)}',
                'last_check': datetime.now().isoformat()
            }

    def _check_device_via_eapi(self, device_name, device_ip):
        """Check if an EOS device is reachable via eAPI."""
        # Get credentials from ACCESS_INFO
        try:
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            username = host_yaml['login_info']['jump_host']['user']
            password = host_yaml['login_info']['jump_host']['pw']
        except Exception as e:
            return {
                'device': device_name,
                'status': 'error',
                'error': f'Cannot read credentials: {e}'
            }

        # Try to connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=5  # Short timeout for status check
            )

            # Simple command to verify connectivity
            result_cmd = connection.execute(['show version'])
            version = result_cmd.get('result', [{}])[0].get('version', 'unknown')

            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'up',
                'version': version,
                'last_check': datetime.now().isoformat()
            }

        except pyeapi.eapilib.ConnectionError as e:
            # pyeapi raises ConnectionError for auth failures with "Unauthorized" message
            error_str = str(e)
            if 'Unauthorized' in error_str or 'Bad username' in error_str or 'authentication' in error_str.lower():
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'unconfigured',
                    'error': 'Device reachable but authentication failed (not yet configured)',
                    'last_check': datetime.now().isoformat()
                }
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': 'Connection failed',
                'last_check': datetime.now().isoformat()
            }
        except Exception as e:
            error_str = str(e)
            # Fallback check for authentication failures from other exception types
            if 'Unauthorized' in error_str or 'Bad username' in error_str or 'authentication' in error_str.lower():
                return {
                    'device': device_name,
                    'ip': device_ip,
                    'status': 'unconfigured',
                    'error': 'Device reachable but authentication failed (not yet configured)',
                    'last_check': datetime.now().isoformat()
                }
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'error',
                'error': error_str,
                'last_check': datetime.now().isoformat()
            }

    def check_all_devices(self):
        """Check status of all devices from both modules.yaml and topo_build.yml."""
        # Get devices from both sources
        nodes = get_all_devices()
        statuses = {}

        # Debug logging
        pS(f"[DeviceStatus] Found {len(nodes)} devices from all sources")

        # Use thread pool for parallel checks
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.check_device_status, device_name): device_name
                for device_name in nodes.keys()
            }

            for future in as_completed(futures, timeout=30):
                device_name = futures[future]
                try:
                    result = future.result()
                    statuses[device_name] = result
                except Exception as e:
                    statuses[device_name] = {
                        'device': device_name,
                        'status': 'error',
                        'error': str(e)
                    }

        return statuses


class RunningConfigAPIHandler(BaseHandler):
    """API endpoint to fetch running config from a device via eAPI."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Running config requested', event='api', endpoint='running_config', device=str(self.get_argument('device', 'unknown')))
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)

        if not device:
            self.set_status(400)
            self.write(json.dumps({'error': 'device parameter required'}))
            return

        try:
            config = self.get_running_config(device)
            self.write(json.dumps(config))
        except Exception as e:
            safe_log('error', f'Error in RunningConfigAPIHandler: {e}', event='error', handler='RunningConfigAPIHandler')
            pS(f"RunningConfigAPIHandler error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def get_running_config(self, device_name):
        """Query EOS device for running config via eAPI."""
        # Get device IP from topology
        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        # Get credentials from ACCESS_INFO
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        # Connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=15
            )

            # Execute show running-config command with text encoding
            result = connection.execute(['show running-config'], encoding='text')

            # Get the config output from the text response
            config_output = result.get('result', [{}])[0].get('output', '')

            return {
                'device': device_name,
                'config': config_output,
                'timestamp': datetime.now().isoformat()
            }

        except pyeapi.eapilib.ConnectionError as e:
            # Preserve auth failure info for upstream handler to detect 'unconfigured' status
            error_str = str(e)
            if 'Unauthorized' in error_str or 'Bad username' in error_str:
                raise ValueError(f"Unauthorized: Cannot authenticate to {device_name} ({device_ip}): {e}")
            raise ValueError(f"Cannot connect to {device_name} ({device_ip}): {e}")
        except pyeapi.eapilib.CommandError as e:
            raise ValueError(f"Command error on {device_name}: {e}")


class EndExamHandler(tornado.web.RequestHandler):
    def post(self):

        """
        Handler to create a user Begin Exam in Honorlock API.
        """
        safe_log('info', 'Exam end requested', event='exam', action='end')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/session/complete"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            try:
                print("Calling exam_upload_v2 module to upload exam")
                docker_conn = docker.from_env()
                login_container = docker_conn.containers.get('atd-login')
                login_container.exec_run(f'sudo python3 -m exam_upload_v2.main', detach=True)
            except Exception as e:
                safe_log('error', f'Error in EndExamHandler upload_exam: {e}', event='error', handler='EndExamHandler')
                print(f"Error running exam_upload_v2: {e}")
                self.write({
                    'honorlock_response': response.json(),
                    'exam_submit': 'Exam has been submitted but error running exam_upload_v2',
                })
            if response.status_code in [200, 201]:
                try:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit': 'Exam has been submitted'
                    })
                except Exception as e:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit_error': str(e)
                    })
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in EndExamHandler: {e}', event='error', handler='EndExamHandler')
            self.set_status(500)
            self.write({"error": str(e)})


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

    # Capture service URL (running on host network, accessible via Docker host IP)
    CAPTURE_SERVICE_URL = "ws://host.docker.internal:8089/ws"
    # Fallback for Linux Docker (host.docker.internal not always available)
    CAPTURE_SERVICE_URL_FALLBACK = "ws://172.17.0.1:8089/ws"

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
            pS("[Capture WS Proxy] Unauthenticated connection - closing")
            self.close(code=1008, reason="Authentication required")
            return

        self.current_user = user.decode() if isinstance(user, bytes) else str(user)
        self.client_id = str(uuid.uuid4())[:8]
        safe_log('info', 'Capture WebSocket opened', event='capture', action='ws_connect',
                 client_id=self.client_id, user=str(self.current_user))
        pS(f"[Capture WS Proxy] Client {self.client_id} connected (user: {self.current_user})")

        # Connect to upstream capture service
        await self.connect_upstream()

    async def connect_upstream(self):
        """Connect to the capture service WebSocket."""
        from tornado.websocket import websocket_connect
        import asyncio

        pS(f"[Capture WS Proxy] Attempting upstream connection...")

        try:
            # Try primary URL first (works on Docker Desktop)
            pS(f"[Capture WS Proxy] Trying primary: {self.CAPTURE_SERVICE_URL}")
            self.upstream_ws = await asyncio.wait_for(
                websocket_connect(
                    self.CAPTURE_SERVICE_URL,
                    on_message_callback=self.on_upstream_message
                ),
                timeout=5.0
            )
            self.is_connected = True
            pS(f"[Capture WS Proxy] Connected to capture service at {self.CAPTURE_SERVICE_URL}")
        except Exception as e:
            pS(f"[Capture WS Proxy] Primary connection failed: {e}, trying fallback...")
            try:
                # Try fallback URL (works on Linux Docker)
                pS(f"[Capture WS Proxy] Trying fallback: {self.CAPTURE_SERVICE_URL_FALLBACK}")
                self.upstream_ws = await asyncio.wait_for(
                    websocket_connect(
                        self.CAPTURE_SERVICE_URL_FALLBACK,
                        on_message_callback=self.on_upstream_message
                    ),
                    timeout=5.0
                )
                self.is_connected = True
                pS(f"[Capture WS Proxy] Connected to capture service at {self.CAPTURE_SERVICE_URL_FALLBACK}")
            except Exception as e2:
                pS(f"[Capture WS Proxy] Fallback connection also failed: {e2}")
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
            pS(f"[Capture WS Proxy] Upstream connection closed")
            self.is_connected = False
            if self.ws_connection:
                self.write_message(json.dumps({
                    'type': 'error',
                    'message': 'Capture service connection lost'
                }))
            return

        # Debug: log first few messages
        try:
            msg_data = json.loads(message)
            if msg_data.get('type') == 'packet':
                pkt_num = msg_data.get('data', {}).get('number', 0)
                if pkt_num <= 3:
                    pS(f"[Capture WS Proxy] Received packet {pkt_num} from upstream, relaying to browser")
        except:
            pass

        # Relay message to browser client
        try:
            if self.ws_connection:
                self.write_message(message)
        except Exception as e:
            pS(f"[Capture WS Proxy] Error relaying to browser: {e}")

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
            pS(f"[Capture WS Proxy] Error relaying to upstream: {e}")
            self.write_message(json.dumps({
                'type': 'error',
                'message': f'Failed to send to capture service: {e}'
            }))

    def on_close(self):
        """Handle WebSocket close from browser."""
        safe_log('info', 'Capture WebSocket closed', event='capture', action='ws_disconnect',
                 client_id=self.client_id)
        pS(f"[Capture WS Proxy] Client {self.client_id} disconnected")

        # Close upstream connection
        if self.upstream_ws:
            self.upstream_ws.close()
            self.upstream_ws = None
            self.is_connected = False


class CaptureBridgesAPIHandler(BaseHandler):
    """API endpoint to list available OVS bridges for capture."""

    # Capture service URLs (same as WebSocket handler)
    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            from tornado.httpclient import AsyncHTTPClient
            http_client = AsyncHTTPClient()

            # Check for refresh parameter
            refresh = self.get_argument('refresh', '0')
            refresh_param = f"?refresh={refresh}" if refresh == '1' else ""

            # Try to fetch bridges from capture service
            bridges = []
            try:
                response = await http_client.fetch(
                    f"{self.CAPTURE_SERVICE_URL}/bridges{refresh_param}",
                    request_timeout=5
                )
                data = json.loads(response.body.decode('utf-8'))
                bridges = data.get('bridges', [])
            except Exception as e:
                pS(f"[CaptureBridges] Primary service failed: {e}, trying fallback...")
                try:
                    response = await http_client.fetch(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/bridges{refresh_param}",
                        request_timeout=5
                    )
                    data = json.loads(response.body.decode('utf-8'))
                    bridges = data.get('bridges', [])
                except Exception as e2:
                    pS(f"[CaptureBridges] Fallback also failed: {e2}")
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
            pS(f"[CaptureBridges] Error: {e}")
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
                pS(f"Warning: Error loading {user_file_path} for bridge enrichment: {e}")

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


# Latency API Handlers (proxy to captureservice)

class LatencyBridgesAPIHandler(BaseHandler):
    """API endpoint to list bridges with latency status."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            from tornado.httpclient import AsyncHTTPClient
            http_client = AsyncHTTPClient()

            bridges = []
            try:
                response = await http_client.fetch(
                    f"{self.CAPTURE_SERVICE_URL}/latency/bridges",
                    request_timeout=5
                )
                data = json.loads(response.body.decode('utf-8'))
                bridges = data.get('bridges', [])
            except Exception as e:
                pS(f"[LatencyBridges] Primary service failed: {e}, trying fallback...")
                try:
                    response = await http_client.fetch(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/bridges",
                        request_timeout=5
                    )
                    data = json.loads(response.body.decode('utf-8'))
                    bridges = data.get('bridges', [])
                except Exception as e2:
                    pS(f"[LatencyBridges] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({
                        'error': 'Latency service unavailable',
                        'bridges': []
                    }))
                    return

            self.write(json.dumps({
                'bridges': bridges,
                'count': len(bridges)
            }))

        except Exception as e:
            safe_log('error', f'Error in LatencyBridgesAPIHandler: {e}', event='error', handler='LatencyBridgesAPIHandler')
            pS(f"[LatencyBridges] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyEnableAPIHandler(BaseHandler):
    """API endpoint to enable latency on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON'}))
            return

        try:
            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            request_body = json.dumps(body)

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/enable",
                    method="POST",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                    request_timeout=10
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[LatencyEnable] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/enable",
                        method="POST",
                        body=request_body,
                        headers={"Content-Type": "application/json"},
                        request_timeout=10
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[LatencyEnable] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyEnableAPIHandler: {e}', event='error', handler='LatencyEnableAPIHandler')
            pS(f"[LatencyEnable] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAPIHandler(BaseHandler):
    """API endpoint to disable latency on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON'}))
            return

        try:
            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            request_body = json.dumps(body)

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/disable",
                    method="POST",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                    request_timeout=10
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[LatencyDisable] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/disable",
                        method="POST",
                        body=request_body,
                        headers={"Content-Type": "application/json"},
                        request_timeout=10
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[LatencyDisable] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyDisableAPIHandler: {e}', event='error', handler='LatencyDisableAPIHandler')
            pS(f"[LatencyDisable] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAllAPIHandler(BaseHandler):
    """API endpoint to disable latency on all bridges."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/disable-all",
                    method="POST",
                    body="{}",
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[LatencyDisableAll] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/disable-all",
                        method="POST",
                        body="{}",
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[LatencyDisableAll] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyDisableAllAPIHandler: {e}', event='error', handler='LatencyDisableAllAPIHandler')
            pS(f"[LatencyDisableAll] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# Impairment API Handlers (unified control for latency, loss, duplication, corruption)

class ImpairmentsBridgesAPIHandler(BaseHandler):
    """API endpoint to get bridges with all impairment status."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/bridges",
                    method="GET",
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[ImpairmentsBridges] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/bridges",
                        method="GET",
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[ImpairmentsBridges] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in ImpairmentsBridgesAPIHandler: {e}', event='error', handler='ImpairmentsBridgesAPIHandler')
            pS(f"[ImpairmentsBridges] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsConfigureAPIHandler(BaseHandler):
    """API endpoint to configure impairments on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment configured', event='impairment', action='configure',
                     bridge=str(_body.get('bridge', '')), latency=str(_body.get('latency', '')),
                     loss=str(_body.get('loss', '')))
        except Exception:
            safe_log('info', 'Network impairment configured', event='impairment', action='configure')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/configure",
                    method="POST",
                    body=json.dumps(request_data),
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[ImpairmentsConfigure] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/configure",
                        method="POST",
                        body=json.dumps(request_data),
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[ImpairmentsConfigure] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except json.JSONDecodeError:
            safe_log('error', 'Error in ImpairmentsConfigureAPIHandler: Invalid JSON', event='error', handler='ImpairmentsConfigureAPIHandler')
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON in request body'}))
        except Exception as e:
            safe_log('error', f'Error in ImpairmentsConfigureAPIHandler: {e}', event='error', handler='ImpairmentsConfigureAPIHandler')
            pS(f"[ImpairmentsConfigure] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsClearAPIHandler(BaseHandler):
    """API endpoint to clear all impairments on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear',
                     bridge=str(_body.get('bridge', '')))
        except Exception:
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/clear",
                    method="POST",
                    body=json.dumps(request_data),
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[ImpairmentsClear] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear",
                        method="POST",
                        body=json.dumps(request_data),
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[ImpairmentsClear] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except json.JSONDecodeError:
            safe_log('error', 'Error in ImpairmentsClearAPIHandler: Invalid JSON', event='error', handler='ImpairmentsClearAPIHandler')
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON in request body'}))
        except Exception as e:
            safe_log('error', f'Error in ImpairmentsClearAPIHandler: {e}', event='error', handler='ImpairmentsClearAPIHandler')
            pS(f"[ImpairmentsClear] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsClearAllAPIHandler(BaseHandler):
    """API endpoint to clear all impairments on all bridges."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        safe_log('info', 'All network impairments cleared', event='impairment', action='clear_all')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            from tornado.httpclient import AsyncHTTPClient, HTTPRequest
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/clear-all",
                    method="POST",
                    body="{}",
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                pS(f"[ImpairmentsClearAll] Primary service failed: {e}, trying fallback...")
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear-all",
                        method="POST",
                        body="{}",
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    pS(f"[ImpairmentsClearAll] Fallback also failed: {e2}")
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in ImpairmentsClearAllAPIHandler: {e}', event='error', handler='ImpairmentsClearAllAPIHandler')
            pS(f"[ImpairmentsClearAll] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


# ===============================
# Nodebuilder Proxy Handlers
# ===============================

class NodeBuilderProxyHandler(BaseHandler):
    """
    Proxy handler for nodebuilder service API calls.

    The nodebuilder service runs on port 8090 with host network mode
    for libvirt/virsh access. This handler proxies requests from the
    UI to the nodebuilder service.
    """

    NODEBUILDER_URL = "http://host.docker.internal:8090"
    NODEBUILDER_URL_FALLBACK = "http://172.17.0.1:8090"

    async def get(self, path):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        _nb_path = self.request.uri.replace('/nodebuilder/', '')
        safe_log('info', f'Node builder GET: {_nb_path}', event='nodebuilder', method='GET', path=_nb_path)
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        await self._proxy_request('GET', path)

    async def post(self, path):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        _nb_path = self.request.uri.replace('/nodebuilder/', '')
        try:
            _nb_body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            _nb_action = _nb_body.get('action', _nb_body.get('type', 'unknown'))
            _nb_name = _nb_body.get('name', _nb_body.get('hostname', ''))
            safe_log('info', f'Node builder POST: {_nb_path}', event='nodebuilder', method='POST', path=_nb_path,
                     action=str(_nb_action), node_name=str(_nb_name))
        except Exception:
            safe_log('info', f'Node builder POST: {_nb_path}', event='nodebuilder', method='POST', path=_nb_path)
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        await self._proxy_request('POST', path, self.request.body)

    async def options(self, path):
        """Handle CORS preflight requests."""
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.set_header("Access-Control-Max-Age", "86400")
        self.set_status(204)
        self.finish()

    async def _proxy_request(self, method, path, body=None):
        """Proxy request to nodebuilder service."""
        from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPClientError

        http_client = AsyncHTTPClient()
        url = f"/{path}" if path else ""

        # Longer timeout for operations that may take a while
        timeout = 180 if 'reset-all' in path or 'cleanup' in path else 60

        async def try_fetch(base_url):
            """Attempt to fetch from a URL, returning (response, error)."""
            request = HTTPRequest(
                f"{base_url}{url}",
                method=method,
                body=body if method == 'POST' else None,
                headers={"Content-Type": "application/json"} if body else {},
                request_timeout=timeout
            )
            try:
                response = await http_client.fetch(request, raise_error=False)
                return response, None
            except Exception as e:
                return None, e

        try:
            # Try primary URL first (Docker Desktop)
            response, error = await try_fetch(self.NODEBUILDER_URL)

            if error:
                # Connection error - try fallback
                pS(f"[NodeBuilderProxy] Primary connection failed: {error}, trying fallback...")
                response, error = await try_fetch(self.NODEBUILDER_URL_FALLBACK)

            if error:
                # Both failed to connect
                pS(f"[NodeBuilderProxy] Fallback also failed: {error}")
                self.set_status(503)
                self.write(json.dumps({
                    'error': 'Nodebuilder service unavailable',
                    'detail': str(error)
                }))
                return

            # Forward the response (including non-2xx status codes like 400)
            self.set_status(response.code)
            if response.body:
                self.write(response.body)

        except Exception as e:
            safe_log('error', f'Error in NodeBuilderProxyHandler: {e}', event='error', handler='NodeBuilderProxyHandler')
            pS(f"[NodeBuilderProxy] Error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


if __name__ == "__main__":
    settings = {
        'cookie_secret': genCookieSecret(),
        'login_url': "/login"
    }

    app = tornado.web.Application([
        (r'/exam-submitted', ExamSubmittedRedirectHandler),
        (r'/exam-already-running', ExamAlreadyRunningHandler),
        (r'/exam-redo', ExamRedoRedirectHandler),
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "js/"}),
        (r'/css/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "css/"}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "images/"}),
        (r'/topo/(.*)', tornado.web.StaticFileHandler, {'path': ArBASE_PATH}),
        (r'/', topoRequestHandler),
        (r'/td-ws', topoDataHandler),
        (r'/login', LoginHandler),
        (r'/lab', LabHandler),
        (r'/labStaus', LabStausHandler),
        #(r'/tools', ToolsHandler),
        (r'/viewConfig', ViewConfigHandler),
        (r'/resetLab', ResetLabHandler),
        (r'/examStatus', ExamStatusHandler),
        (r'/examSubmit', ExamSubmitHandler),
        (r'/exam-authentication', ExamAuthenticationHandler),     
        (r'/getAccessInfo', GetAccessInfoHandler),
        (r'/getClientId', GetClientIdHandler),
        (r'/getExamInstructions', GetExamInstructionsHandler),
        (r'/getUserSessionId', GetUserSessionIdHandler),
        (r'/beginExam', BeginExamHandler),
        (r'/endExam', EndExamHandler),
        (r'/baseUrl', BaseUrlHandler),
        (r'/uptimeWithRuntime', UptimeWithRuntimeHandler),
        (r'/terminal', TerminalPageHandler),
        (r'/console/?', ConsolePageHandler),  # /? makes trailing slash optional
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
        # Nodebuilder endpoints (dynamic node addition for KVM labs)
        (r'/td-api/nodes/(.*)', NodeBuilderProxyHandler),
    ], **settings)
    app.listen(PORT)
    safe_log('info', 'UILanding server started', port='80', topology=TOPO)
    print('*** Websocket Server Started on {} ***'.format(PORT))
    try:
        TOPO_DATA = getEventStatus(NAME, ZONE)
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        print("*** Websocked Server Stopped ***")
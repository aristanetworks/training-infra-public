"""Page and utility handlers for UILanding.

Extracted from uilanding.py. All handlers use initialize() for dependency injection.

Dependency groups:
  config         — base_path, atd_access_path, title
  topo_config    — topo, nomenuoptionfile, menu_items, default_menu_file_value,
                   mod_yaml, eos_type
  exam_state     — shared mutable dict: {'start_time': …, 'end_time': …}
  session_state  — shared mutable dict with active_sessions, active_session_data,
                   recent_sessions, last_grpc_status, last_grpc_time
  topo_data      — topology metadata dict (may be None before first fetch)
"""

import json
import re
import subprocess
from base64 import b64encode

import requests
import tornado.web
from ruamel.yaml import YAML

from handlers.auth import BaseHandler
from utils import safe_log


# ---------------------------------------------------------------------------
# topoRequestHandler
# ---------------------------------------------------------------------------

class topoRequestHandler(BaseHandler):
    """Main topology page render.

    Dependencies (via initialize):
        config      — base_path, atd_access_path, title
        topo_config — topo, nomenuoptionfile, menu_items, default_menu_file_value,
                      mod_yaml, eos_type
    """

    def initialize(self, config, topo_config):
        self.base_path = config['base_path']
        self.atd_access_path = config['atd_access_path']
        self.title = config['title']
        self.topo = topo_config['topo']
        self.nomenuoptionfile = topo_config['nomenuoptionfile']
        self.menu_items = topo_config.get('menu_items', {})
        self.default_menu_file_value = topo_config.get('default_menu_file_value', '')
        self.mod_yaml = topo_config['mod_yaml']
        self.eos_type = topo_config.get('eos_type', 'veos')

    def get(self):
        with open(self.atd_access_path, 'r') as f:
            host_yaml = YAML().load(f)
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
                # else: User authenticated via Honorlock - allow access (continue below)
            else:
                # No 'honorlock' parameter - force Honorlock authentication
                # This blocks:
                #   1. Direct URL access: https://lab.com/
                #   2. Cached session access without Honorlock
                #   3. Manual auth parameter manipulation
                self.redirect('/exam-authentication')
                return

        # Handle non-authenticated users for regular (non-Exam) labs
        # Note: Exam labs are already handled above
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
            menu = {}
            if self.nomenuoptionfile:
                disable_links.append('lab_menu')
            else:
                for lab in self.menu_items.get('lab_list', {}):
                    menu[lab] = self.menu_items['lab_list'][lab]['description']

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
            gui_urls, servers = [], []
            if host_yaml.get('eos_type') == 'container-labs':
                try:
                    servers = self.mod_yaml['topology']['servers']
                    if servers is None:
                        servers = []
                    external_ip_url = "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"
                    headers = {"Metadata-Flavor": "Google"}
                    response = requests.get(external_ip_url, headers=headers, timeout=5)
                    for server in servers:
                        gui_urls.append(f'http://{response.text}:{servers[server]["port"]}')
                except Exception as e:
                    safe_log('error', f'Error in topoRequestHandler: {e}', event='error', handler='topoRequestHandler')
            try:
                student_name = host_yaml.get('customer_details', {}).get('exam_taker_full_name', '').strip()
            except Exception:
                student_name = ''
            self.render(
                self.base_path + 'index.html',
                NODES=self.mod_yaml['topology']['nodes'],
                SERVERS=servers,
                GUI_URLS=gui_urls,
                ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
                topo_title=self.title,
                disable_links=disable_links,
                labguides=labguides,
                topo_cvp=_topo_cvp,
                menu_options=menu,
                lab_type=lab_type,
                student_name=student_name
            )


# ---------------------------------------------------------------------------
# ToolsHandler
# ---------------------------------------------------------------------------

class ToolsHandler(tornado.web.RequestHandler):
    """Latency tool handler (dormant — route commented out in main app).

    Uses subprocess; device names are validated to prevent injection.
    """

    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)
            # Extract the three parameters
            changeLatency = data.get('changeLatency', False)
            devices = data.get('devices', [])
            score = data.get('score', 0)
            # Validate device names to prevent command injection
            for d in devices:
                if not re.match(r'^[a-zA-Z0-9_-]+$', str(d)):
                    self.set_status(400)
                    self.write({"error": f"Invalid device name: {d}"})
                    return
            result = subprocess.run(
                ['please', 'update', 'code',
                 'ENABLE' if changeLatency else 'DISABLE',
                 '-d', str(int(score)), '-i', ','.join(devices)],
                capture_output=True, text=True, timeout=30
            )

            # Prepare the response
            response = {
                "changeLatency": changeLatency,
                "devices": devices,
                "score": score,
                "result": result.stdout,
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


# ---------------------------------------------------------------------------
# ViewConfigHandler
# ---------------------------------------------------------------------------

class ViewConfigHandler(tornado.web.RequestHandler):
    """View device config handler.

    Uses subprocess; device names are validated to prevent injection.
    """

    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)

            # Extract the parameters
            devices = data.get('devices', [])
            # Validate device names to prevent command injection
            for d in devices:
                if not re.match(r'^[a-zA-Z0-9_-]+$', str(d)):
                    self.set_status(400)
                    self.write({"error": f"Invalid device name: {d}"})
                    return
            result = subprocess.run(
                ['sudo', '-S', 'python3', '/home/atdadmin/change-latency.py',
                 'SHOW', '-i', ','.join(devices)],
                capture_output=True, text=True, timeout=30
            )
            # Prepare the response
            response = {
                "devices": devices,
                "result": result.stdout,
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


# ---------------------------------------------------------------------------
# BaseUrlHandler
# ---------------------------------------------------------------------------

class BaseUrlHandler(tornado.web.RequestHandler):
    """Return base64-encoded jump-host credentials.

    Dependencies (via initialize):
        config — atd_access_path
    """

    def initialize(self, config):
        self.atd_access_path = config['atd_access_path']

    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            with open(self.atd_access_path, 'r') as f:
                host_yaml = YAML().load(f)
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


# ---------------------------------------------------------------------------
# UptimeWithRuntimeHandler
# ---------------------------------------------------------------------------

class UptimeWithRuntimeHandler(tornado.web.RequestHandler):
    """Uptime with runtime handler for the timer widget.

    Dependencies (via initialize):
        exam_state — shared mutable dict {'start_time': …, 'end_time': …}
        topo_data  — topology metadata dict (may be None)
    """

    def initialize(self, exam_state, topo_data):
        self.exam_state = exam_state
        self.topo_data = topo_data

    def get(self):
        """Handler to provide uptime data with runtime information for timer widget."""
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            self.set_header("Content-Type", "application/json")

            exam_end_time = self.exam_state.get('end_time', 0)
            exam_start_time = self.exam_state.get('start_time', 0)

            # Get uptime data directly from atd-uptime service
            try:
                response = requests.get("http://atd-uptime:50010/uptime", timeout=1)
                instance_data = response.json()

                # Add runtime from topology metadata
                if instance_data.get('status') == 'init' and self.topo_data and 'labels' in self.topo_data and 'runtime' in self.topo_data['labels']:
                    instance_data['runtime'] = int(self.topo_data['labels']['runtime'])
                else:
                    instance_data['runtime'] = 12

                # Add exam time information if available
                instance_data['exam_end_time'] = exam_end_time
                instance_data['exam_start_time'] = exam_start_time

                self.write(json.dumps(instance_data))
            except Exception as e:
                safe_log('warning', f'Uptime service not ready: {e}', event='uptime', handler='UptimeWithRuntimeHandler')
                # If uptime service is not ready, return default values
                self.write(json.dumps({
                    'boottime': 0,
                    'uptime': 0,
                    'runtime': 12,
                    'status': 'init',
                    'exam_end_time': exam_end_time,
                    'exam_start_time': exam_start_time
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


# ---------------------------------------------------------------------------
# TerminalPageHandler
# ---------------------------------------------------------------------------

class TerminalPageHandler(BaseHandler):
    """Handler for the tabbed terminal page.

    Dependencies (via initialize):
        config — base_path, atd_access_path, title
    """

    def initialize(self, config):
        self.base_path = config['base_path']
        self.atd_access_path = config['atd_access_path']
        self.title = config['title']

    def get(self):
        safe_log('info', 'Terminal page accessed', event='page_view', page='terminal')
        if not self.current_user:
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return

        with open(self.atd_access_path, 'r') as f:
            host_yaml = YAML().load(f)
        self.render(
            self.base_path + 'terminal.html',
            topo_title=self.title,
            ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
        )


# ---------------------------------------------------------------------------
# ConsolePageHandler
# ---------------------------------------------------------------------------

class ConsolePageHandler(BaseHandler):
    """Handler for the serial console page (virsh console access).

    Dependencies (via initialize):
        config — base_path, atd_access_path, title
    """

    def initialize(self, config):
        self.base_path = config['base_path']
        self.atd_access_path = config['atd_access_path']
        self.title = config['title']

    def get(self):
        safe_log('info', 'Console page accessed', event='page_view', page='console')
        if not self.current_user:
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return

        with open(self.atd_access_path, 'r') as f:
            host_yaml = YAML().load(f)
        self.render(
            self.base_path + 'console.html',
            topo_title=self.title,
            ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
        )


# ---------------------------------------------------------------------------
# ClientLogHandler
# ---------------------------------------------------------------------------

class ClientLogHandler(tornado.web.RequestHandler):
    """Receive client-side log events from browser JS and forward to Cloud Logging."""
    VALID_LEVELS = {'info', 'warning', 'error'}

    def post(self):
        try:
            data = json.loads(self.request.body)
            level = data.get('level', 'info')
            if level not in self.VALID_LEVELS:
                level = 'info'
            message = str(data.get('message', ''))[:500]
            source = str(data.get('source', 'unknown'))[:50]
            action = str(data.get('action', ''))[:50]
            kwargs = {'event': 'client', 'source': source}
            if action:
                kwargs['action'] = action
            for key in ('device', 'topology', 'session_id', 'client_id', 'page', 'context', 'parent_page', 'section'):
                if key in data:
                    kwargs[key] = str(data[key])[:100]
            safe_log(level, message, **kwargs)
            self.set_status(204)
        except Exception:
            self.set_status(204)


# ---------------------------------------------------------------------------
# ConnectivityStatusHandler
# ---------------------------------------------------------------------------

class ConnectivityStatusHandler(BaseHandler):
    """REST endpoint returning current connectivity state for live session health.

    Requires authentication (extends BaseHandler) — returns 401 without cookie.

    Dependencies (via initialize):
        session_state — dict with keys:
            active_sessions      set of session IDs currently connected
            active_session_data  dict keyed by session_id
            recent_sessions      dict of recently closed sessions
            grpc_state           mutable dict {'status': str|None, 'last_check': float|None}
    """

    def initialize(self, session_state):
        self.session_state = session_state

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return
        self.set_header('Content-Type', 'application/json')
        grpc = self.session_state.get('grpc_state', {})
        self.write(json.dumps({
            'active_sessions': len(self.session_state['active_sessions']),
            'active_session_data': list(self.session_state['active_session_data'].values()),
            'recent_disconnects': len(self.session_state['recent_sessions']),
            'internal_grpc': {
                'last_status': grpc.get('status'),
                'last_check': grpc.get('last_check')
            }
        }))

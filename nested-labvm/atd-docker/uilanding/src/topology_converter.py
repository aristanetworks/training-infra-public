"""
Topology Converter API Handlers

Provides REST API endpoints for switching between lab topologies.
Extracted from uilanding.py to reduce file size.
"""

import json
import os
import subprocess
import threading
from datetime import datetime

import tornado.web
from ruamel.yaml import YAML

# Constants
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'
BASE_PATH = '/opt/topo/html/'


def pS(mtype):
    """Function to send output from service file to Syslog."""
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


# Global variables for conversion status
conversion_status = {
    'in_progress': False,
    'phase': None,
    'status': 'Idle',
    'log': [],
    'completed': False,
    'success': False
}


class BaseHandler(tornado.web.RequestHandler):
    """Base handler with authentication support."""
    def get_current_user(self):
        return self.get_secure_cookie("user")


class TopologyConverterCurrentHandler(BaseHandler):
    """API endpoint to get current topology information."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Read ACCESS_INFO.yaml
            with open(ATD_ACCESS_PATH, 'r') as f:
                access_info = YAML().load(f)

            topology_name = access_info.get('topology', 'Unknown')
            topo_path = f'/opt/atd/topologies/{topology_name}'

            # Read topo_build.yml if it exists
            topo_build_path = f'{topo_path}/topo_build.yml'
            node_count = 0
            nodes = []

            if os.path.exists(topo_build_path):
                with open(topo_build_path, 'r') as f:
                    topo_build = YAML().load(f)
                    if 'nodes' in topo_build:
                        nodes = [list(node.keys())[0] for node in topo_build['nodes']]
                        node_count = len(nodes)

            # Count configlets
            configlet_dir = f'{topo_path}/configlets'
            configlet_count = 0
            if os.path.exists(configlet_dir):
                configlet_count = len([f for f in os.listdir(configlet_dir)
                                      if os.path.isfile(os.path.join(configlet_dir, f))])

            response = {
                'name': topology_name,
                'node_count': node_count,
                'nodes': nodes,
                'eos_type': access_info.get('eos_type', 'veos'),
                'configlet_count': configlet_count
            }

            self.write(json.dumps(response))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterAvailableHandler(BaseHandler):
    """API endpoint to get list of available topologies."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            topo_dir = '/opt/atd/topologies'
            topologies = []

            if os.path.exists(topo_dir):
                for item in os.listdir(topo_dir):
                    item_path = os.path.join(topo_dir, item)
                    topo_build = os.path.join(item_path, 'topo_build.yml')

                    # Only include if it has topo_build.yml
                    if os.path.isdir(item_path) and os.path.exists(topo_build):
                        topologies.append(item)

            self.write(json.dumps({'topologies': sorted(topologies)}))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterInfoHandler(BaseHandler):
    """API endpoint to get information about a specific topology."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            topology_name = self.get_argument('topology', None)

            if not topology_name:
                self.set_status(400)
                self.write(json.dumps({'error': 'topology parameter required'}))
                return

            topo_path = f'/opt/atd/topologies/{topology_name}'

            if not os.path.exists(topo_path):
                self.set_status(404)
                self.write(json.dumps({'error': 'Topology not found'}))
                return

            # Read topo_build.yml
            topo_build_path = f'{topo_path}/topo_build.yml'
            node_count = 0
            nodes = []

            if os.path.exists(topo_build_path):
                with open(topo_build_path, 'r') as f:
                    topo_build = YAML().load(f)
                    if 'nodes' in topo_build:
                        nodes = [list(node.keys())[0] for node in topo_build['nodes']]
                        node_count = len(nodes)

            # Count configlets
            configlet_dir = f'{topo_path}/configlets'
            configlet_count = 0
            if os.path.exists(configlet_dir):
                configlet_count = len([f for f in os.listdir(configlet_dir)
                                      if os.path.isfile(os.path.join(configlet_dir, f))])

            response = {
                'name': topology_name,
                'node_count': node_count,
                'nodes': nodes,
                'configlet_count': configlet_count
            }

            self.write(json.dumps(response))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterConvertHandler(BaseHandler):
    """API endpoint to start topology conversion."""

    def post(self):
        global conversion_status

        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Check if conversion is already in progress
            if conversion_status['in_progress']:
                self.set_status(409)
                self.write(json.dumps({'error': 'Conversion already in progress'}))
                return

            # Parse request body
            body = json.loads(self.request.body.decode('utf-8'))
            target_topology = body.get('target_topology')

            if not target_topology:
                self.set_status(400)
                self.write(json.dumps({'error': 'target_topology required'}))
                return

            # Validate target topology exists
            topo_path = f'/opt/atd/topologies/{target_topology}'
            if not os.path.exists(topo_path):
                self.set_status(404)
                self.write(json.dumps({'error': 'Target topology not found'}))
                return

            # Check if target is same as current topology
            try:
                with open(ATD_ACCESS_PATH, 'r') as f:
                    access_info = YAML().load(f)
                    current_topology = access_info.get('topology', '')
                    if current_topology == target_topology:
                        self.set_status(400)
                        self.write(json.dumps({
                            'error': f'Target topology "{target_topology}" is the same as current topology. No conversion needed.'
                        }))
                        return
            except Exception as e:
                pS(f"Warning: Could not check current topology: {e}")

            # Start conversion in background thread
            def run_conversion():
                global conversion_status
                conversion_status = {
                    'in_progress': True,
                    'phase': 'starting',
                    'status': 'Starting conversion...',
                    'log': ['Conversion initiated'],
                    'completed': False,
                    'success': False
                }

                try:
                    # Run the conversion script on HOST using docker
                    # We use nsenter via a privileged container to run on the host
                    script_path = '/opt/atd/scripts/topology_converter_v2.py'

                    # Use docker to run a privileged container that executes on host
                    cmd = [
                        'docker', 'run', '--rm',
                        '--privileged',
                        '--pid=host',
                        '--network=host',
                        '-v', '/:/host',
                        '-v', '/var/run/docker.sock:/var/run/docker.sock',
                        '-v', '/var/run/libvirt:/var/run/libvirt',
                        'python:3.9-slim',
                        'nsenter', '--target', '1', '--mount', '--uts', '--ipc', '--net', '--pid', '--',
                        'python3', script_path, target_topology,
                        '--no-monitoring',  # Don't wait for CVP
                        '--force'  # Skip confirmation prompt
                    ]

                    conversion_status['log'].append(f'Executing conversion on host via privileged container...')

                    # Execute with real-time output capture
                    process = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT,
                        text=True,
                        bufsize=1
                    )

                    # Read output line by line
                    for line in iter(process.stdout.readline, ''):
                        line = line.strip()
                        if line:
                            conversion_status['log'].append(line)

                            # Parse phase from log (matches topology_converter_v2.py phases)
                            if 'Phase 1' in line or 'Pre-flight' in line:
                                conversion_status['phase'] = 'validate'
                            elif 'Phase 2' in line or 'Backup Current' in line:
                                conversion_status['phase'] = 'backup'
                            elif 'Phase 3' in line or 'CVP Cleanup' in line:
                                conversion_status['phase'] = 'destroy'
                            elif 'Phase 4' in line or 'Destroy Current VMs' in line:
                                conversion_status['phase'] = 'destroy'
                            elif 'Phase 5' in line or 'Destroy OVS' in line:
                                conversion_status['phase'] = 'destroy'
                            elif 'Phase 6' in line or 'Update Configuration' in line:
                                conversion_status['phase'] = 'update'
                            elif 'Phase 7' in line or 'Libvirtd' in line:
                                conversion_status['phase'] = 'build'
                            elif 'Phase 8' in line or 'atdStartup' in line:
                                conversion_status['phase'] = 'build'
                            elif 'Phase 9' in line or 'KVM Builder' in line:
                                conversion_status['phase'] = 'build'
                            elif 'Phase 10' in line or 'Create OVS' in line:
                                conversion_status['phase'] = 'build'
                            elif 'Phase 11' in line or 'Create and Start VMs' in line:
                                conversion_status['phase'] = 'build'
                            elif 'Phase 12' in line or 'Reconfigure CVP' in line:
                                conversion_status['phase'] = 'cvp'
                            elif 'Phase 13' in line or 'Monitor CVP' in line:
                                conversion_status['phase'] = 'cvp'
                            elif 'COMPLETED' in line:
                                conversion_status['phase'] = 'completed'

                            conversion_status['status'] = line

                    process.wait()

                    # Check result
                    if process.returncode == 0:
                        conversion_status['success'] = True
                        conversion_status['status'] = 'Conversion completed successfully!'
                        conversion_status['log'].append('SUCCESS: Conversion completed')
                    else:
                        conversion_status['success'] = False
                        conversion_status['status'] = f'Conversion failed with exit code {process.returncode}'
                        conversion_status['log'].append(f'ERROR: Conversion failed (exit code {process.returncode})')

                except Exception as e:
                    conversion_status['success'] = False
                    conversion_status['status'] = f'Error: {str(e)}'
                    conversion_status['log'].append(f'ERROR: {str(e)}')

                finally:
                    conversion_status['in_progress'] = False
                    conversion_status['completed'] = True

            # Start thread
            thread = threading.Thread(target=run_conversion)
            thread.daemon = True
            thread.start()

            self.write(json.dumps({
                'status': 'started',
                'message': f'Conversion to {target_topology} started in background'
            }))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterStatusHandler(BaseHandler):
    """API endpoint to get conversion status."""

    def get(self):
        global conversion_status

        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Return last 50 log lines
            recent_logs = conversion_status['log'][-50:] if conversion_status['log'] else []

            response = {
                'in_progress': conversion_status['in_progress'],
                'phase': conversion_status['phase'],
                'status': conversion_status['status'],
                'log': '\n'.join(recent_logs),
                'completed': conversion_status['completed'],
                'success': conversion_status['success']
            }

            self.write(json.dumps(response))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterPageHandler(BaseHandler):
    """Handler for the topology converter HTML page."""

    @tornado.web.authenticated
    def get(self):
        self.set_header("Content-Type", "text/html")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            with open(BASE_PATH + 'topology-converter.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)
        except FileNotFoundError:
            self.set_status(404)
            self.write("Error: topology-converter.html not found")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error: {str(e)}")

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

import docker
import tornado.web
from ruamel.yaml import YAML

from cloud_logging_utils import (
    setup_cloud_logging,
    log_operation_start,
    log_operation_success,
    log_operation_error,
)

# Constants
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'
BASE_PATH = '/opt/topo/html/'

# Setup cloud logging for topology converter
logger = setup_cloud_logging('topology-converter')


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
    """API endpoint to get list of available topologies.

    Returns only topologies listed in the 'topology-switcher' section of
    ACCESS_INFO.yaml. If that section is missing or empty, returns an
    error so the frontend can prompt the user to fix the file.
    """

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            with open(ATD_ACCESS_PATH, 'r') as f:
                access_info = YAML().load(f)

            switcher_config = access_info.get('topology-switcher')

            if not switcher_config:
                logger.warning("No topology-switcher config in ACCESS_INFO.yaml")  # LOG 1
                self.write(json.dumps({
                    'topologies': [],
                    'error': 'No topology-switcher configuration found in ACCESS_INFO.yaml'
                }))
                return

            # Keys of topology-switcher dict are the allowed topology names
            topologies = sorted(switcher_config.keys())

            self.write(json.dumps({'topologies': topologies}))

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


def _update_labguides_modules(target_topology, status):
    """Update labguides_modules in ACCESS_INFO.yaml from topology-switcher config.

    Reads the topology-switcher section, finds the modules list for the
    target topology, and writes it to the labguides_modules key.
    """
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(ATD_ACCESS_PATH, 'r') as f:
            access_info = yaml.load(f)

        switcher_config = access_info.get('topology-switcher')
        if not switcher_config or target_topology not in switcher_config:
            msg = (f'No labguides modules configured for {target_topology} '
                   f'in topology-switcher — labguides_modules unchanged')
            logger.warning(msg)  # LOG 2
            status['log'].append(f'WARNING: {msg}')
            return

        new_modules = switcher_config[target_topology]
        if not new_modules:
            msg = (f'topology-switcher entry for {target_topology} is empty '
                   f'— labguides_modules unchanged')
            logger.warning(msg)  # LOG 3
            status['log'].append(f'WARNING: {msg}')
            return

        old_modules = access_info.get('labguides_modules', [])
        access_info['labguides_modules'] = list(new_modules)

        with open(ATD_ACCESS_PATH, 'w') as f:
            yaml.dump(access_info, f)

        log_operation_success(logger, 'update-labguides-modules',  # LOG 4
                              target_topology=target_topology,
                              old_modules=str(old_modules),
                              new_modules=str(list(new_modules)))
        status['log'].append(f'Updated labguides_modules for {target_topology}: {list(new_modules)}')

    except Exception as e:
        log_operation_error(logger, 'update-labguides-modules', str(e),  # LOG 5
                            target_topology=target_topology)
        status['log'].append(f'WARNING: Failed to update labguides_modules: {e}')


def _restart_labguides_container(status):
    """Restart atd-labguides-v2 container so it rebuilds with updated modules."""
    try:
        client = docker.from_env()
        container = client.containers.get('atd-labguides-v2')
        status['log'].append('Restarting atd-labguides-v2 to rebuild labguides...')
        container.restart(timeout=10)
        log_operation_success(logger, 'restart-labguides-container')  # LOG 6
        status['log'].append('atd-labguides-v2 restarted — labguides will rebuild')
    except docker.errors.NotFound:
        log_operation_error(logger, 'restart-labguides-container',  # LOG 7
                            'atd-labguides-v2 container not found')
        status['log'].append('WARNING: atd-labguides-v2 container not found — labguides not rebuilt')
    except Exception as e:
        log_operation_error(logger, 'restart-labguides-container', str(e))  # LOG 8
        status['log'].append(f'WARNING: Failed to restart labguides container: {e}')


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
                logger.warning("Conversion rejected: already in progress")  # LOG 9
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
                logger.warning(f"Conversion rejected: topology not found: {target_topology}")  # LOG 10
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
                logger.warning(f"Could not check current topology: {e}")

            # Get user for logging
            user = self.current_user.decode('utf-8') if self.current_user else 'unknown'

            log_operation_start(logger, 'topology-conversion',  # LOG 11
                                target_topology=target_topology,
                                current_topology=current_topology,
                                user=user)

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
                    # Update labguides_modules in ACCESS_INFO.yaml before conversion
                    # so the labguides container rebuilds with correct modules
                    _update_labguides_modules(target_topology, conversion_status)

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

                    logger.info(f"Executing host conversion for {target_topology}")  # LOG 12
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
                    prev_phase = None
                    for line in iter(process.stdout.readline, ''):
                        line = line.strip()
                        if line:
                            conversion_status['log'].append(line)

                            # Parse phase from log (matches topology_converter_v2.py phases)
                            if 'Phase 1' in line or 'Pre-flight' in line:
                                conversion_status['phase'] = 'validate'
                            elif 'Phase 2' in line or 'Backup Current' in line:
                                conversion_status['phase'] = 'backup'
                            elif 'Phase 3' in line or 'Destroy Current VMs' in line:
                                conversion_status['phase'] = 'destroy'
                            elif 'Phase 4' in line or 'CVP Device Cleanup' in line:
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

                            # Log phase transitions to Cloud Logging
                            if conversion_status['phase'] != prev_phase:
                                logger.info(  # LOG 13 (per phase change, ~7 unique phases)
                                    f"Phase: {conversion_status['phase']}",
                                    extra={'labels': {
                                        'operation': 'topology-conversion',
                                        'phase': conversion_status['phase'],
                                        'target_topology': target_topology,
                                    }}
                                )
                                prev_phase = conversion_status['phase']

                            conversion_status['status'] = line

                    process.wait()

                    # Check result
                    if process.returncode == 0:
                        conversion_status['success'] = True
                        conversion_status['status'] = 'Conversion completed successfully!'
                        conversion_status['log'].append('SUCCESS: Conversion completed')

                        log_operation_success(logger, 'topology-conversion',  # LOG 14
                                              target_topology=target_topology,
                                              current_topology=current_topology)

                        # Restart labguides container to rebuild with new modules
                        _restart_labguides_container(conversion_status)
                    else:
                        conversion_status['success'] = False
                        conversion_status['status'] = f'Conversion failed with exit code {process.returncode}'
                        conversion_status['log'].append(f'ERROR: Conversion failed (exit code {process.returncode})')

                        log_operation_error(logger, 'topology-conversion',  # LOG 15
                                            f'Exit code {process.returncode}',
                                            target_topology=target_topology)

                except Exception as e:
                    conversion_status['success'] = False
                    conversion_status['status'] = f'Error: {str(e)}'
                    conversion_status['log'].append(f'ERROR: {str(e)}')

                    log_operation_error(logger, 'topology-conversion',  # LOG 16
                                        str(e),
                                        target_topology=target_topology)

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

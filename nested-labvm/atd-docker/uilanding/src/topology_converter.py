"""
Topology Converter API Handlers

Provides REST API endpoints for switching between lab topologies.
Extracted from uilanding.py to reduce file size.
"""

import json
import os
import random
import re
import subprocess
import threading
from collections import deque
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
from handlers.auth import BaseHandler
from utils import getAPI, safe_log

try:
    from google.cloud import firestore
    FIRESTORE_AVAILABLE = True
except ImportError:
    FIRESTORE_AVAILABLE = False

# Constants
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'
BASE_PATH = '/opt/topo/html/'
LABGUIDES_COLLECTION = 'labGuides'

# Setup cloud logging for topology converter
logger = setup_cloud_logging('topology-converter')


# =============================================================================
# Labguide module expansion (mirrors api-deploy/main.py:modularGuides)
# =============================================================================

def _mod_select_item(data):
    """Recursively select random items from compatibility tree."""
    if isinstance(data, dict):
        compatible_items = data.get('compatible')
        if compatible_items:
            return _mod_select_item(compatible_items)
        key = random.choice(list(data.keys()))
        return [key] + _mod_select_item(data[key])
    elif isinstance(data, list):
        return [random.choice(data[0][item]) for item in data[0]]
    return []


def _modular_selection(data):
    """Build module list for mod-exam type with random selection."""
    index = list(data.keys())
    first_mandatory = data[index[0]]['first_mandatory']
    last_mandatory = data[index[0]]['last_mandatory']
    selected_items = _mod_select_item(data[index[0]])
    return index + first_mandatory + selected_items + last_mandatory


def expand_labguide_modules(lab_names):
    """
    Expand labguide IDs into full module list via Firestore lookup.

    Mirrors modularGuides() in cloud-functions/api-deploy/src/main.py.
    For each lab_name:
      - If found in Firestore labGuides collection:
          - type 'mod-exam' -> random selection from modules tree
          - type 'class'/'exam'/'nugget' -> use full modules list
      - If not found: keep raw lab_name (legacy/manual modules)
    """
    if not FIRESTORE_AVAILABLE:
        logger.warning("google-cloud-firestore not installed, returning raw labguide list")
        return list(lab_names)

    try:
        db = firestore.Client()
    except Exception as e:
        logger.warning(f"Firestore client init failed: {e}, returning raw labguide list")
        return list(lab_names)

    modules = []
    for lab in lab_names:
        try:
            doc = db.collection(LABGUIDES_COLLECTION).document(lab).get()
            from_db = doc.to_dict() if doc.exists else None
            if from_db:
                info = from_db.get('metadata', {})
                lg_type = info.get('type')
                if lg_type == 'mod-exam':
                    modules.extend(_modular_selection(from_db['modules']))
                elif lg_type in ('class', 'exam', 'nugget'):
                    modules.extend(from_db.get('modules', []))
                else:
                    logger.warning(f"Labguide '{lab}' has unknown type '{lg_type}', skipping")
            else:
                # No Firestore doc - treat as raw module name (legacy behavior)
                modules.append(lab)
        except Exception as e:
            logger.warning(f"Firestore lookup failed for '{lab}': {e}, using raw value")
            modules.append(lab)

    return modules


# Global variables for conversion status
_conversion_lock = threading.Lock()
conversion_status = {
    'in_progress': False,
    'phase': None,
    'status': 'Idle',
    'log': deque(maxlen=500),
    'completed': False,
    'success': False
}


class TopologyConverterCurrentHandler(BaseHandler):
    """API endpoint to get current topology information."""

    def get(self):
        safe_log('info', 'Current topology info requested',
                 event='api_request', handler='TopologyConverterCurrentHandler', method='GET')

        if not self.current_user:
            safe_log('warning', 'Unauthenticated request to current topology endpoint',
                     event='auth', handler='TopologyConverterCurrentHandler', action='denied')
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Read ACCESS_INFO.yaml
            safe_log('info', f'Reading ACCESS_INFO from {ATD_ACCESS_PATH}',
                     event='file_read', handler='TopologyConverterCurrentHandler', file=ATD_ACCESS_PATH)
            with open(ATD_ACCESS_PATH, 'r') as f:
                access_info = YAML().load(f)

            topology_name = access_info.get('topology', 'Unknown')
            topo_path = f'/opt/atd/topologies/{topology_name}'
            safe_log('info', f'Current topology identified: {topology_name}',
                     event='topology_info', handler='TopologyConverterCurrentHandler', topology=topology_name)

            # Read topo_build.yml if it exists
            topo_build_path = f'{topo_path}/topo_build.yml'
            node_count = 0
            nodes = []

            if os.path.exists(topo_build_path):
                safe_log('info', f'Reading topo_build.yml from {topo_build_path}',
                         event='file_read', handler='TopologyConverterCurrentHandler', file=topo_build_path)
                with open(topo_build_path, 'r') as f:
                    topo_build = YAML().load(f)
                    if topo_build and 'nodes' in topo_build:
                        nodes = [list(node.keys())[0] for node in topo_build['nodes']]
                        node_count = len(nodes)
                        safe_log('info', f'Found {node_count} nodes in topology {topology_name}: {", ".join(nodes)}',
                                 event='topology_info', handler='TopologyConverterCurrentHandler',
                                 topology=topology_name, node_count=node_count)
            else:
                safe_log('warning', f'topo_build.yml not found at {topo_build_path}',
                         event='file_missing', handler='TopologyConverterCurrentHandler', file=topo_build_path)

            # Count configlets
            configlet_dir = f'{topo_path}/configlets'
            configlet_count = 0
            if os.path.exists(configlet_dir):
                configlet_count = len([f for f in os.listdir(configlet_dir)
                                      if os.path.isfile(os.path.join(configlet_dir, f))])
                safe_log('info', f'Found {configlet_count} configlets for topology {topology_name}',
                         event='topology_info', handler='TopologyConverterCurrentHandler',
                         topology=topology_name, configlet_count=configlet_count)

            response = {
                'name': topology_name,
                'node_count': node_count,
                'nodes': nodes,
                'eos_type': access_info.get('eos_type', 'veos'),
                'configlet_count': configlet_count
            }

            safe_log('info', f'Successfully returned current topology info: {topology_name}',
                     event='api_response', handler='TopologyConverterCurrentHandler',
                     topology=topology_name, status_code=200)
            self.write(json.dumps(response))

        except Exception as e:
            safe_log('error', f'Error fetching current topology info: {e}',
                     event='error', handler='TopologyConverterCurrentHandler', error=str(e))
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterAvailableHandler(BaseHandler):
    """API endpoint to get list of available topologies.

    Returns only topologies listed in the 'topology-switcher' section of
    ACCESS_INFO.yaml. If that section is missing or empty, returns an
    error so the frontend can prompt the user to fix the file.
    """

    def get(self):
        safe_log('info', 'Available topologies list requested',
                 event='api_request', handler='TopologyConverterAvailableHandler', method='GET')

        if not self.current_user:
            safe_log('warning', 'Unauthenticated request to available topologies endpoint',
                     event='auth', handler='TopologyConverterAvailableHandler', action='denied')
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
            safe_log('error', f'Error listing available topologies: {e}',
                     event='error', handler='TopologyConverterAvailableHandler', error=str(e))
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterInfoHandler(BaseHandler):
    """API endpoint to get information about a specific topology."""

    def get(self):
        topology_name = self.get_argument('topology', None)
        safe_log('info', f'Topology info requested for: {topology_name}',
                 event='api_request', handler='TopologyConverterInfoHandler', method='GET',
                 requested_topology=str(topology_name))

        if not self.current_user:
            safe_log('warning', 'Unauthenticated request to topology info endpoint',
                     event='auth', handler='TopologyConverterInfoHandler', action='denied')
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            if not topology_name:
                safe_log('warning', 'Topology info request missing topology parameter',
                         event='validation_error', handler='TopologyConverterInfoHandler',
                         reason='missing_parameter')
                self.set_status(400)
                self.write(json.dumps({'error': 'topology parameter required'}))
                return

            # Validate topology name to prevent path traversal
            if not re.match(r'^[a-zA-Z0-9_-]+$', topology_name):
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid topology name'}))
                return

            topo_path = f'/opt/atd/topologies/{topology_name}'

            if not os.path.exists(topo_path):
                safe_log('warning', f'Topology not found: {topology_name} (path: {topo_path})',
                         event='topology_not_found', handler='TopologyConverterInfoHandler',
                         topology=topology_name, path=topo_path)
                self.set_status(404)
                self.write(json.dumps({'error': 'Topology not found'}))
                return

            # Read topo_build.yml
            topo_build_path = f'{topo_path}/topo_build.yml'
            node_count = 0
            nodes = []

            if os.path.exists(topo_build_path):
                safe_log('info', f'Reading topo_build.yml for topology: {topology_name}',
                         event='file_read', handler='TopologyConverterInfoHandler', file=topo_build_path)
                with open(topo_build_path, 'r') as f:
                    topo_build = YAML().load(f)
                    if topo_build and 'nodes' in topo_build:
                        nodes = [list(node.keys())[0] for node in topo_build['nodes']]
                        node_count = len(nodes)
                        safe_log('info', f'Topology {topology_name}: {node_count} nodes found: {", ".join(nodes)}',
                                 event='topology_info', handler='TopologyConverterInfoHandler',
                                 topology=topology_name, node_count=node_count)
                    else:
                        safe_log('warning', f'topo_build.yml for {topology_name} has no nodes section',
                                 event='topology_warning', handler='TopologyConverterInfoHandler',
                                 topology=topology_name, reason='no_nodes_section')
            else:
                safe_log('warning', f'topo_build.yml not found for topology: {topology_name}',
                         event='file_missing', handler='TopologyConverterInfoHandler',
                         topology=topology_name, file=topo_build_path)

            # Count configlets
            configlet_dir = f'{topo_path}/configlets'
            configlet_count = 0
            if os.path.exists(configlet_dir):
                configlet_count = len([f for f in os.listdir(configlet_dir)
                                      if os.path.isfile(os.path.join(configlet_dir, f))])
                safe_log('info', f'Topology {topology_name}: {configlet_count} configlets found',
                         event='topology_info', handler='TopologyConverterInfoHandler',
                         topology=topology_name, configlet_count=configlet_count)

            response = {
                'name': topology_name,
                'node_count': node_count,
                'nodes': nodes,
                'configlet_count': configlet_count
            }

            safe_log('info', f'Successfully returned info for topology: {topology_name}',
                     event='api_response', handler='TopologyConverterInfoHandler',
                     topology=topology_name, status_code=200)
            self.write(json.dumps(response))

        except Exception as e:
            safe_log('error', f'Error fetching topology info for {topology_name}: {e}',
                     event='error', handler='TopologyConverterInfoHandler',
                     topology=str(topology_name), error=str(e))
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


def _update_labguides_modules(target_topology, status):
    """Update labguides_modules in ACCESS_INFO.yaml from topology-switcher config.

    Reads the topology-switcher section, finds the labguide IDs for the
    target topology, expands them via Firestore lookup (mirrors api-deploy's
    modularGuides), and writes the expanded module list to labguides_modules.
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
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        raw_entries = switcher_config[target_topology]
        if not raw_entries:
            msg = (f'topology-switcher entry for {target_topology} is empty '
                   f'— labguides_modules unchanged')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        # Expand labguide IDs via Firestore lookup (e.g. Foundations_Track -> module list)
        expanded_modules = expand_labguide_modules(list(raw_entries))
        if not expanded_modules:
            msg = (f'Labguide expansion returned empty list for {target_topology}'
                   f' — labguides_modules unchanged')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        old_modules = access_info.get('labguides_modules', [])
        access_info['labguides_modules'] = expanded_modules

        with open(ATD_ACCESS_PATH, 'w') as f:
            yaml.dump(access_info, f)

        log_operation_success(logger, 'update-labguides-modules',
                              target_topology=target_topology,
                              raw_entries=str(list(raw_entries)),
                              expanded_count=len(expanded_modules),
                              old_modules=str(old_modules))
        status['log'].append(
            f'Updated labguides_modules for {target_topology}: '
            f'{list(raw_entries)} -> {len(expanded_modules)} modules'
        )

    except Exception as e:
        log_operation_error(logger, 'update-labguides-modules', str(e),
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

        safe_log('info', 'Topology conversion request received',
                 event='api_request', handler='TopologyConverterConvertHandler', method='POST')

        if not self.current_user:
            safe_log('warning', 'Unauthenticated conversion request blocked',
                     event='auth', handler='TopologyConverterConvertHandler', action='denied')
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
            safe_log('info', f'Conversion requested to target topology: {target_topology}',
                     event='conversion_request', handler='TopologyConverterConvertHandler',
                     target_topology=str(target_topology))

            if not target_topology:
                safe_log('warning', 'Conversion request missing target_topology parameter',
                         event='validation_error', handler='TopologyConverterConvertHandler',
                         reason='missing_target_topology')
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': 'target_topology required'}))
                return

            # TC-3: Validate topology name to prevent path traversal
            if not re.match(r'^[a-zA-Z0-9_-]+$', target_topology):
                safe_log('warning', f'Invalid topology name rejected: {target_topology}',
                         event='validation_error', handler='TopologyConverterConvertHandler',
                         reason='invalid_topology_name')
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid topology name'}))
                return

            # Validate target topology exists
            topo_path = f'/opt/atd/topologies/{target_topology}'
            if not os.path.exists(topo_path):
                logger.warning(f"Conversion rejected: topology not found: {target_topology}")  # LOG 10
                self.set_status(404)
                self.write(json.dumps({'error': 'Target topology not found'}))
                return

            # Check if target is same as current topology
            current_topology = None
            try:
                with open(ATD_ACCESS_PATH, 'r') as f:
                    access_info = YAML().load(f)
                    current_topology = access_info.get('topology', '')
                    safe_log('info', f'Current topology: {current_topology}, target: {target_topology}',
                             event='conversion_validation', handler='TopologyConverterConvertHandler',
                             current_topology=current_topology, target_topology=target_topology)
                    if current_topology == target_topology:
                        safe_log('warning', f'Conversion rejected: target same as current ({target_topology})',
                                 event='conversion_rejected', handler='TopologyConverterConvertHandler',
                                 reason='same_topology', topology=target_topology)
                        conversion_status['in_progress'] = False
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

            # Log conversion initiation with full context
            log_operation_start(logger, 'topology_conversion',
                                current_topology=str(current_topology),
                                target_topology=target_topology,
                                user=str(self.current_user))

            # Start conversion in background thread
            def run_conversion():
                global conversion_status
                conversion_start_time = datetime.now()
                safe_log('info', f'Conversion thread started: {current_topology} -> {target_topology}',
                         event='conversion_thread_start', handler='TopologyConverterConvertHandler',
                         current_topology=str(current_topology), target_topology=target_topology,
                         start_time=conversion_start_time.isoformat())

                conversion_status = {
                    'in_progress': True,
                    'phase': 'starting',
                    'status': 'Starting conversion...',
                    'log': deque(['Conversion initiated'], maxlen=500),
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

                    safe_log('info', f'Conversion subprocess started with PID {process.pid}',
                             event='conversion_subprocess_start', handler='TopologyConverterConvertHandler',
                             target_topology=target_topology, pid=process.pid)

                    previous_phase = None
                    line_count = 0

                    # Read output line by line
                    prev_phase = None
                    for line in iter(process.stdout.readline, ''):
                        line = line.strip()
                        if line:
                            line_count += 1
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

                    try:
                        process.wait(timeout=3600)  # 1 hour max
                    except subprocess.TimeoutExpired:
                        safe_log('error', 'Conversion subprocess timed out after 1 hour, killing process',
                                 event='conversion_timeout', handler='TopologyConverterConvertHandler',
                                 target_topology=target_topology, pid=process.pid)
                        process.kill()
                        process.wait()
                        conversion_status['success'] = False
                        conversion_status['status'] = 'Conversion timed out after 1 hour'
                        conversion_status['log'].append('ERROR: Conversion timed out after 1 hour and was killed')
                        raise Exception('Conversion subprocess timed out after 1 hour')
                    elapsed = (datetime.now() - conversion_start_time).total_seconds()

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
                        log_operation_error(logger, 'topology_conversion',
                                            f'Process exited with code {process.returncode}',
                                            current_topology=str(current_topology),
                                            target_topology=target_topology,
                                            elapsed_seconds=elapsed,
                                            total_log_lines=line_count,
                                            exit_code=process.returncode,
                                            last_phase=str(conversion_status.get('phase', 'unknown')))

                        log_operation_error(logger, 'topology-conversion',  # LOG 15
                                            f'Exit code {process.returncode}',
                                            target_topology=target_topology)

                except Exception as e:
                    elapsed = (datetime.now() - conversion_start_time).total_seconds()
                    conversion_status['success'] = False
                    conversion_status['status'] = f'Error: {str(e)}'
                    conversion_status['log'].append(f'ERROR: {str(e)}')
                    log_operation_error(logger, 'topology_conversion',
                                        str(e),
                                        current_topology=str(current_topology),
                                        target_topology=target_topology,
                                        elapsed_seconds=elapsed,
                                        last_phase=str(conversion_status.get('phase', 'unknown')),
                                        exception_type=type(e).__name__)

                    log_operation_error(logger, 'topology-conversion',  # LOG 16
                                        str(e),
                                        target_topology=target_topology)

                finally:
                    conversion_status['in_progress'] = False
                    conversion_status['completed'] = True
                    elapsed = (datetime.now() - conversion_start_time).total_seconds()
                    safe_log('info', f'Conversion thread finished. Success: {conversion_status["success"]}, '
                             f'Duration: {elapsed:.1f}s, Final phase: {conversion_status.get("phase", "unknown")}',
                             event='conversion_thread_end', handler='TopologyConverterConvertHandler',
                             target_topology=target_topology,
                             success=conversion_status['success'],
                             elapsed_seconds=elapsed,
                             final_phase=str(conversion_status.get('phase', 'unknown')))

            # Start thread
            thread = threading.Thread(target=run_conversion)
            thread.daemon = True
            thread.start()

            safe_log('info', f'Conversion thread launched for target: {target_topology}',
                     event='conversion_started', handler='TopologyConverterConvertHandler',
                     target_topology=target_topology, thread_name=thread.name)
            self.write(json.dumps({
                'status': 'started',
                'message': f'Conversion to {target_topology} started in background'
            }))

        except json.JSONDecodeError as e:
            safe_log('error', f'Invalid JSON in conversion request body: {e}',
                     event='validation_error', handler='TopologyConverterConvertHandler',
                     error=str(e), reason='invalid_json')
            conversion_status['in_progress'] = False
            self.set_status(400)
            self.write(json.dumps({'error': f'Invalid JSON: {str(e)}'}))
        except Exception as e:
            safe_log('error', f'Unexpected error in conversion handler: {e}',
                     event='error', handler='TopologyConverterConvertHandler',
                     error=str(e), exception_type=type(e).__name__)
            conversion_status['in_progress'] = False
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterStatusHandler(BaseHandler):
    """API endpoint to get conversion status."""

    def get(self):
        global conversion_status

        if not self.current_user:
            safe_log('warning', 'Unauthenticated request to conversion status endpoint',
                     event='auth', handler='TopologyConverterStatusHandler', action='denied')
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        # Log only for active conversions (status polling is frequent — every 5s)
        if conversion_status['in_progress']:
            safe_log('info', f'Conversion status polled - Phase: {conversion_status.get("phase", "unknown")}',
                     event='conversion_status_poll', handler='TopologyConverterStatusHandler',
                     phase=str(conversion_status.get('phase', 'unknown')),
                     in_progress='true')

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Return last 50 log lines (log is a deque, convert to list to slice)
            recent_logs = list(conversion_status['log'])[-50:] if conversion_status['log'] else []

            response = {
                'in_progress': conversion_status['in_progress'],
                'phase': conversion_status['phase'],
                'status': conversion_status['status'],
                'log': '\n'.join(recent_logs),
                'completed': conversion_status['completed'],
                'success': conversion_status['success']
            }

            # Log when conversion has just completed (transition moment)
            if conversion_status['completed'] and not conversion_status['in_progress']:
                safe_log('info', f'Conversion status returned: completed={conversion_status["completed"]}, '
                         f'success={conversion_status["success"]}',
                         event='conversion_status_final', handler='TopologyConverterStatusHandler',
                         completed=conversion_status['completed'],
                         success=conversion_status['success'],
                         final_phase=str(conversion_status.get('phase', 'unknown')))

            self.write(json.dumps(response))

        except Exception as e:
            safe_log('error', f'Error returning conversion status: {e}',
                     event='error', handler='TopologyConverterStatusHandler', error=str(e))
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class TopologyConverterCvpStatusHandler(BaseHandler):
    """Proxy endpoint returning CVP readiness status from conftopo.

    Returns the dict from getAPI('cvp_status'), shape:
        {'status': 'UP'|'DOWN'|other, 'version': str (optional), ...}
    Used by topology-converter.html overlay gate to unlock the page only
    when CVP is online.
    """

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps({'status': 'DOWN', 'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")
        try:
            result = getAPI('cvp_status')
            if not isinstance(result, dict):
                result = {'status': 'DOWN', 'error': 'invalid response'}
            self.write(json.dumps(result))
        except Exception as e:
            safe_log('error', f'Error fetching cvp_status: {e}',
                     event='error', handler='TopologyConverterCvpStatusHandler', error=str(e))
            self.set_status(500)
            self.write(json.dumps({'status': 'DOWN', 'error': str(e)}))


class TopologyConverterPageHandler(BaseHandler):
    """Handler for the topology converter HTML page."""

    @tornado.web.authenticated
    def get(self):
        safe_log('info', 'Topology converter page accessed',
                 event='page_view', handler='TopologyConverterPageHandler',
                 page='topology-converter', user=str(self.current_user))

        self.set_header("Content-Type", "text/html")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            html_path = BASE_PATH + 'topology-converter.html'
            safe_log('info', f'Serving topology converter page from {html_path}',
                     event='file_read', handler='TopologyConverterPageHandler', file=html_path)
            with open(html_path, 'r') as file:
                html_content = file.read()
            self.write(html_content)
        except FileNotFoundError:
            safe_log('error', f'topology-converter.html not found at {BASE_PATH}',
                     event='file_missing', handler='TopologyConverterPageHandler',
                     file=BASE_PATH + 'topology-converter.html')
            self.set_status(404)
            self.write("Error: topology-converter.html not found")
        except Exception as e:
            safe_log('error', f'Error serving topology converter page: {e}',
                     event='error', handler='TopologyConverterPageHandler', error=str(e))
            self.set_status(500)
            self.write(f"Error: {str(e)}")

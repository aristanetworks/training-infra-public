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


def validate_lab_labguides(raw_labguides):
    """Pre-flight validation of a lab's `labguides` list.

    Returns (is_valid, error_message). Treats a `labguides` list as valid
    when either:
      - it contains a single Firestore class/exam/nugget/mod-exam id that
        expands to >= 2 modules (so lgbuild's `len < 2` guard passes), or
      - it contains >= 2 raw module ids (legacy "detailed" form, passed to
        lgbuild verbatim with no Firestore lookup needed).

    A single raw id that does not exist in Firestore is rejected here —
    that is what causes lgbuild.py:285-288 to raise an empty exception
    downstream, surfacing as "Failed to load ACCESS_INFO data" and a
    blank labguide.pdf in the user's browser.
    """
    raw = list(raw_labguides or [])
    if not raw:
        return False, 'No labguides configured for this lab.'

    # Detailed form: 2+ raw module ids. lgbuild accepts as-is.
    if len(raw) >= 2:
        return True, ''

    # Expandable form: single Firestore id.
    expanded = expand_labguide_modules(raw)
    if len(expanded) >= 2:
        return True, ''

    return False, (
        f'Labguide "{raw[0]}" unavailable: not found in Firestore '
        f'(or expands to fewer than 2 modules). Use a valid Firestore '
        f'class/exam id, or provide 2+ raw module ids in the labguides list.'
    )


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

# Persist conversion_status across uilanding restarts/recreations. The
# labguides-only path runs `atdUpdate.sh` which calls `atdStartup.sh`,
# and that does `docker compose up -d` — recreating atd-uilanding from
# scratch (not just restarting it). A recreate wipes the container's
# writable overlay, so /tmp does NOT survive. /etc/atd is the only
# host-bind-mounted writable directory available inside this container,
# so the snapshot lives there. Reloaded on import; if a prior conversion
# was in_progress when uilanding was killed, it is promoted to completed
# (atdStartup only returns after success, so by the time we are reading
# this file again the conversion has effectively finished).
STATUS_PERSIST_PATH = '/etc/atd/topo_conversion_status.json'


def _persist_conversion_status():
    try:
        snapshot = {
            'in_progress': conversion_status['in_progress'],
            'phase': conversion_status['phase'],
            'status': conversion_status['status'],
            'log': list(conversion_status['log'])[-100:],
            'completed': conversion_status['completed'],
            'success': conversion_status['success'],
        }
        tmp = STATUS_PERSIST_PATH + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(snapshot, f)
        os.replace(tmp, STATUS_PERSIST_PATH)
    except Exception as e:
        try:
            logger.warning(f'Failed to persist conversion_status: {e}')
        except Exception:
            pass


def _load_persisted_conversion_status():
    """Restore conversion_status from disk on module import.

    If a previous conversion was in_progress when uilanding died (atdUpdate
    triggered atdStartup → restart), promote it to completed/success here
    so the UI's next /status poll sees the terminal state. atdUpdate.sh
    only finishes successfully if everything downstream did, and uilanding
    only comes back up after atdStartup returns, so by the time this code
    runs the conversion has effectively succeeded.
    """
    try:
        if not os.path.exists(STATUS_PERSIST_PATH):
            return
        with open(STATUS_PERSIST_PATH, 'r') as f:
            snap = json.load(f)
        if snap.get('in_progress'):
            snap['in_progress'] = False
            snap['completed'] = True
            snap['success'] = True
            snap['phase'] = 'completed'
            snap['status'] = (snap.get('status') or '') + ' (resumed after uilanding restart)'
        conversion_status['in_progress'] = snap.get('in_progress', False)
        conversion_status['phase'] = snap.get('phase')
        conversion_status['status'] = snap.get('status', 'Idle')
        conversion_status['completed'] = snap.get('completed', False)
        conversion_status['success'] = snap.get('success', False)
        conversion_status['log'].clear()
        for line in snap.get('log', []):
            conversion_status['log'].append(line)
        _persist_conversion_status()
    except Exception as e:
        try:
            logger.warning(f'Failed to load persisted conversion_status: {e}')
        except Exception:
            pass


_load_persisted_conversion_status()


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

            # Resolve the user-facing display name (lab label) for the
            # currently-active topology. Prefer the explicitly-stored
            # `topology-switcher-active` field; otherwise fall back to the
            # first display name in topology-switcher whose `topology`
            # matches the active topology id.
            display_name = access_info.get('topology-switcher-active')
            if not display_name:
                switcher_config = access_info.get('topology-switcher') or {}
                for d_name, entry in switcher_config.items():
                    if isinstance(entry, dict) and entry.get('topology') == topology_name:
                        display_name = d_name
                        break

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
                'topology': topology_name,
                'display_name': display_name,
                'node_count': node_count,
                'nodes': nodes,
                'eos_type': access_info.get('eos_type', 'veos'),
                'configlet_count': configlet_count
            }

            safe_log('info', f'Successfully returned current topology info: '
                     f'topology={topology_name!r}, display_name={display_name!r}',
                     event='api_response', handler='TopologyConverterCurrentHandler',
                     topology=topology_name, display_name=str(display_name), status_code=200)
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
    """API endpoint to get information about a specific topology.

    Accepts either `display_name` (preferred — the lab label keyed under
    `topology-switcher` in ACCESS_INFO.yaml) or `topology` (legacy — raw
    topology id). When a display_name is given it is resolved via the
    topology-switcher map to its underlying topology id, and the
    associated labguides list is included in the response.
    """

    def get(self):
        display_name = self.get_argument('display_name', None)
        topology_name = self.get_argument('topology', None)
        requested = display_name or topology_name
        safe_log('info', f'Topology info requested for: {requested}',
                 event='api_request', handler='TopologyConverterInfoHandler', method='GET',
                 requested_topology=str(requested))

        if not self.current_user:
            safe_log('warning', 'Unauthenticated request to topology info endpoint',
                     event='auth', handler='TopologyConverterInfoHandler', action='denied')
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        labguides = []
        try:
            if not requested:
                safe_log('warning', 'Topology info request missing display_name/topology parameter',
                         event='validation_error', handler='TopologyConverterInfoHandler',
                         reason='missing_parameter')
                self.set_status(400)
                self.write(json.dumps({'error': 'display_name or topology parameter required'}))
                return

            # Display names allow spaces; topology ids do not. Validate the
            # incoming string with the looser rule, then re-validate the
            # resolved topology id strictly before any filesystem use.
            if not re.match(r'^[a-zA-Z0-9_ .-]+$', requested):
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid name'}))
                return

            # Resolve display_name → topology id via topology-switcher map.
            if display_name:
                try:
                    with open(ATD_ACCESS_PATH, 'r') as f:
                        access_info = YAML().load(f)
                    switcher_config = access_info.get('topology-switcher') or {}
                    entry = switcher_config.get(display_name)
                    if not entry or not isinstance(entry, dict):
                        self.set_status(404)
                        self.write(json.dumps({'error': f'Lab "{display_name}" not found in topology-switcher'}))
                        return
                    topology_name = entry.get('topology')
                    labguides = list(entry.get('labguides') or [])
                    if not topology_name:
                        self.set_status(400)
                        self.write(json.dumps({'error': f'Lab "{display_name}" has no topology configured'}))
                        return
                except Exception as e:
                    safe_log('error', f'Failed to resolve display_name {display_name}: {e}',
                             event='error', handler='TopologyConverterInfoHandler', error=str(e))
                    self.set_status(500)
                    self.write(json.dumps({'error': str(e)}))
                    return

            # Strict path-traversal guard on the topology id we are about to use.
            if not topology_name or not re.match(r'^[a-zA-Z0-9_-]+$', topology_name):
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid topology name'}))
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

            # Pre-flight labguide validation. Surfaces as `labguides_valid`
            # + `labguides_warning` in the response so the UI can warn the
            # user before they trigger a conversion that would crash
            # lgbuild downstream.
            lg_valid, lg_error = validate_lab_labguides(labguides)

            response = {
                'name': display_name or topology_name,
                'display_name': display_name,
                'topology': topology_name,
                'node_count': node_count,
                'nodes': nodes,
                'configlet_count': configlet_count,
                'labguides': labguides,
                'labguides_valid': lg_valid,
                'labguides_warning': lg_error if not lg_valid else '',
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


def _update_labguides_modules(display_name, status):
    """Update labguides_modules in ACCESS_INFO.yaml for the selected lab.

    Looks up the topology-switcher entry by display_name (the lab label),
    expands the labguide IDs via Firestore lookup, writes the resulting
    module list to labguides_modules, and records the active display_name
    as `topology-switcher-active` so the UI can resolve the current lab.
    """
    try:
        yaml = YAML()
        yaml.preserve_quotes = True
        with open(ATD_ACCESS_PATH, 'r') as f:
            access_info = yaml.load(f)

        switcher_config = access_info.get('topology-switcher')
        if not switcher_config or display_name not in switcher_config:
            msg = (f'No topology-switcher entry for lab "{display_name}" '
                   f'— labguides_modules unchanged')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        entry = switcher_config[display_name]
        if not isinstance(entry, dict):
            msg = (f'topology-switcher entry for "{display_name}" must be a mapping '
                   f'with `topology` and `labguides` — got {type(entry).__name__}')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        raw_entries = list(entry.get('labguides') or [])
        if not raw_entries:
            msg = (f'No labguides configured for lab "{display_name}" '
                   f'— labguides_modules unchanged')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        # Expand labguide IDs via Firestore lookup (e.g. Foundations_Track -> module list)
        expanded_modules = expand_labguide_modules(raw_entries)
        if not expanded_modules:
            msg = (f'Labguide expansion returned empty list for lab "{display_name}"'
                   f' — labguides_modules unchanged')
            logger.warning(msg)
            status['log'].append(f'WARNING: {msg}')
            return

        old_modules = access_info.get('labguides_modules', [])
        access_info['labguides_modules'] = expanded_modules
        # Persist the active lab label so subsequent UI loads can show it
        # without guessing from the topology id alone.
        access_info['topology-switcher-active'] = display_name

        with open(ATD_ACCESS_PATH, 'w') as f:
            yaml.dump(access_info, f)

        log_operation_success(logger, 'update-labguides-modules',
                              display_name=display_name,
                              raw_entries=str(raw_entries),
                              expanded_count=len(expanded_modules),
                              old_modules=str(old_modules))
        status['log'].append(
            f'Updated labguides_modules for "{display_name}": '
            f'{raw_entries} -> {len(expanded_modules)} modules'
        )

    except Exception as e:
        log_operation_error(logger, 'update-labguides-modules', str(e),
                            display_name=display_name)
        status['log'].append(f'WARNING: Failed to update labguides_modules: {e}')


def _run_atd_update_on_host(status, timeout=900):
    """Execute /usr/local/bin/atdUpdate.sh on the host via privileged nsenter.

    Used by the labguides-only conversion path — when source and target
    labs share a topology, no VM rebuild is needed; atdUpdate.sh pulls the
    latest repo and runs atdStartup.sh, which reconciles services against
    the updated ACCESS_INFO.yaml (labguides_modules + topology-switcher-active).
    """
    cmd = [
        'docker', 'run', '--rm',
        '--privileged',
        '--pid=host',
        '--network=host',
        '-v', '/:/host',
        '-v', '/var/run/docker.sock:/var/run/docker.sock',
        'python:3.9-slim',
        'nsenter', '--target', '1', '--mount', '--uts', '--ipc', '--net', '--pid', '--',
        'bash', '/usr/local/bin/atdUpdate.sh',
    ]

    logger.info('Executing host atdUpdate.sh for labguides-only refresh')
    status['log'].append('Executing atdUpdate.sh on host via privileged container...')

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    for line in iter(process.stdout.readline, ''):
        line = line.strip()
        if line:
            status['log'].append(line)
            status['status'] = line

    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise Exception(f'atdUpdate.sh timed out after {timeout}s')

    if process.returncode != 0:
        raise Exception(f'atdUpdate.sh exited with code {process.returncode}')


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

            # Parse request body. Prefer `target_display_name` (the lab label
            # from the topology-switcher dropdown); accept legacy
            # `target_topology` for backward compatibility — when only the
            # legacy field is given, treat its value as a display_name too.
            body = json.loads(self.request.body.decode('utf-8'))
            target_display_name = body.get('target_display_name') or body.get('target_topology')
            safe_log('info', f'Conversion requested for lab: {target_display_name}',
                     event='conversion_request', handler='TopologyConverterConvertHandler',
                     target_display_name=str(target_display_name))

            if not target_display_name:
                safe_log('warning', 'Conversion request missing target_display_name parameter',
                         event='validation_error', handler='TopologyConverterConvertHandler',
                         reason='missing_target_display_name')
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': 'target_display_name required'}))
                return

            # Display names allow spaces; reject anything outside the
            # alphanumeric + space/_/-/. set before using them as a YAML key.
            if not re.match(r'^[a-zA-Z0-9_ .-]+$', target_display_name):
                safe_log('warning', f'Invalid display name rejected: {target_display_name}',
                         event='validation_error', handler='TopologyConverterConvertHandler',
                         reason='invalid_display_name')
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid lab name'}))
                return

            # Resolve display_name → underlying topology id via topology-switcher.
            target_topology = None
            current_topology = None
            current_display_name = None
            try:
                with open(ATD_ACCESS_PATH, 'r') as f:
                    access_info = YAML().load(f)
                switcher_config = access_info.get('topology-switcher') or {}
                entry = switcher_config.get(target_display_name)
                if entry and isinstance(entry, dict):
                    target_topology = entry.get('topology')
                else:
                    # Legacy fallback: caller passed a raw topology id and
                    # no display name matches it. Use it as-is.
                    target_topology = target_display_name
                current_topology = access_info.get('topology', '')
                # Prefer the explicit `topology-switcher-active` marker. If
                # absent (fresh lab, never converted), fall back to the
                # first display_name in topology-switcher whose `topology`
                # matches the active topology id — mirrors the resolution
                # done by TopologyConverterCurrentHandler so the UI and
                # backend agree on what the "current lab" is.
                current_display_name = access_info.get('topology-switcher-active')
                if not current_display_name:
                    for d_name, sw_entry in switcher_config.items():
                        if isinstance(sw_entry, dict) and sw_entry.get('topology') == current_topology:
                            current_display_name = d_name
                            break
            except Exception as e:
                logger.warning(f"Could not read ACCESS_INFO.yaml: {e}")

            if not target_topology:
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': f'Lab "{target_display_name}" has no topology configured'}))
                return

            # Path-traversal guard on the resolved topology id.
            if not re.match(r'^[a-zA-Z0-9_-]+$', target_topology):
                safe_log('warning', f'Invalid topology name rejected: {target_topology}',
                         event='validation_error', handler='TopologyConverterConvertHandler',
                         reason='invalid_topology_name')
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': 'Invalid topology name'}))
                return

            # Validate resolved topology exists on disk
            topo_path = f'/opt/atd/topologies/{target_topology}'
            if not os.path.exists(topo_path):
                logger.warning(f"Conversion rejected: topology not found: {target_topology}")
                self.set_status(404)
                self.write(json.dumps({'error': 'Target topology not found'}))
                return

            # Pre-flight labguide validation. lgbuild crashes on labguide
            # lists with fewer than 2 effective modules (Foundations_Track
            # → 1 raw entry when Firestore doesn't have the doc), leaving
            # the user with a blank labguide.pdf and no obvious error.
            # Reject the conversion up-front so the operator sees the
            # real failure ("labguide X unavailable") instead of a stuck
            # progress bar.
            entry = (switcher_config or {}).get(target_display_name)
            target_labguides = list((entry or {}).get('labguides') or [])
            lg_valid, lg_error = validate_lab_labguides(target_labguides)
            if not lg_valid:
                safe_log('warning', f'Conversion rejected: invalid labguides for {target_display_name}: {lg_error}',
                         event='conversion_rejected', handler='TopologyConverterConvertHandler',
                         reason='invalid_labguides', display_name=target_display_name,
                         labguides=str(target_labguides))
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({'error': lg_error}))
                return

            # Reject re-selecting the currently-active lab. Compare strictly
            # on display_name — multiple labs share a topology id, so a
            # topology-id match alone does not mean the same lab. When the
            # current display_name is unknown, allow the conversion (the
            # dropdown already hides the resolved-current lab on the UI).
            same_as_current = bool(current_display_name) and current_display_name == target_display_name
            if same_as_current:
                safe_log('warning', f'Conversion rejected: target same as current ({target_display_name})',
                         event='conversion_rejected', handler='TopologyConverterConvertHandler',
                         reason='same_lab', display_name=target_display_name)
                conversion_status['in_progress'] = False
                self.set_status(400)
                self.write(json.dumps({
                    'error': f'Lab "{target_display_name}" is already the active lab. No conversion needed.'
                }))
                return

            # Fast-path detection: when the source and target labs map to the
            # same underlying topology id, the VMs do not need to be torn
            # down and rebuilt — only ACCESS_INFO (labguides_modules +
            # topology-switcher-active) changes, then atdUpdate.sh applies it.
            labguides_only = bool(current_topology) and current_topology == target_topology

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
                _persist_conversion_status()

                try:
                    # Update labguides_modules in ACCESS_INFO.yaml before conversion
                    # so the labguides container rebuilds with correct modules.
                    # Also persists `topology-switcher-active` = display_name.
                    _update_labguides_modules(target_display_name, conversion_status)

                    # Labguides-only fast path: same topology id, only the
                    # lab label and labguide module list change. Skip the
                    # VM destroy/build pipeline and just run atdUpdate.sh —
                    # ACCESS_INFO is already updated above.
                    if labguides_only:
                        conversion_status['phase'] = 'update'
                        conversion_status['status'] = (
                            f'Same topology ({target_topology}) — running labguides-only update'
                        )
                        conversion_status['log'].append(
                            f'Labguides-only switch: {current_display_name or current_topology} '
                            f'-> {target_display_name} (topology {target_topology} unchanged)'
                        )
                        _persist_conversion_status()
                        _run_atd_update_on_host(conversion_status)
                        _persist_conversion_status()
                        _restart_labguides_container(conversion_status)
                        conversion_status['phase'] = 'completed'
                        conversion_status['success'] = True
                        conversion_status['status'] = 'Labguides update completed successfully!'
                        conversion_status['log'].append('SUCCESS: Labguides-only update completed')
                        _persist_conversion_status()
                        log_operation_success(logger, 'topology-conversion',
                                              target_topology=target_topology,
                                              current_topology=current_topology,
                                              mode='labguides-only')
                        return

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
                    _persist_conversion_status()
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
                'display_name': target_display_name,
                'topology': target_topology,
                'mode': 'labguides-only' if labguides_only else 'full-conversion',
                'message': (
                    f'Labguides-only update to "{target_display_name}" started in background'
                    if labguides_only else
                    f'Conversion to "{target_display_name}" (topology {target_topology}) started in background'
                ),
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

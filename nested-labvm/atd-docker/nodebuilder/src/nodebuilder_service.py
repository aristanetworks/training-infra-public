#!/usr/bin/env python3
"""
Nodebuilder Service - Dynamic node addition for ATL labs

This service provides a REST API for dynamically adding VMs
to running KVM-based ATL labs. It runs on port 8090 with host network
mode for libvirt access.

Endpoints:
- GET  /health              - Health check
- GET  /available-ips       - List unused IPs from dnsmasq
- GET  /existing-nodes      - List all nodes (topo_build + user_nodes)
- GET  /target-devices      - List devices available as connection targets
- GET  /user-nodes-status   - Get status of user-added nodes (for restore button)
- POST /validate-node       - Validate node config before creation
- POST /add-node            - Create new vEOS VM
- POST /restore-user-nodes  - Start all user-added VMs after reboot
- POST /reset-all-user-nodes - Remove all user-added nodes and restore original topology

Linux Host Endpoints:
- GET  /host-status         - Get host count and availability
- POST /add-host            - Create new Linux host VM
- POST /delete-host         - Delete a Linux host
- GET  /novnc-token/{name}  - Get noVNC access token for a host

Firewall Endpoints:
- GET  /firewall-status     - Get firewall count and availability
- POST /add-firewall        - Create new VyOS firewall VM
- POST /edit-firewall       - Edit firewall interface IPs
- POST /delete-firewall     - Delete a firewall

VeloCloud Endpoints:
- GET  /velo-status         - Get VeloCloud device count and availability
- GET  /velo-devices        - List all VeloCloud devices
- POST /add-velo-device     - Create new VeloCloud device (Edge, Gateway, Orchestrator)
- POST /delete-velo-device  - Delete a VeloCloud device
- GET/POST/PUT/DELETE /vco-proxy/{path} - Proxy requests to VCO web UI

Bridge Utilities Endpoints (Single Source of Truth):
- GET  /bridge/parse/{name} - Parse a bridge name to device/port info
- POST /bridge/parse        - Batch parse multiple bridge names
- GET  /bridge/abbreviations - Get device abbreviation mapping
"""

import logging
import os
import subprocess
from aiohttp import web

from config import (
    SERVICE_PORT,
    SERVICE_HOST,
    MAX_TOTAL_NODES,
    MAX_CONNECTIONS_PER_NODE,
    VALID_DEVICE_TYPES,
    DEFAULT_DEVICE_TYPE,
    MAX_HOSTS_PER_TOPOLOGY,
    MAX_FIREWALLS_PER_TOPOLOGY,
    USER_HOSTS_PATH,
    USER_FIREWALLS_PATH,
    HOST_DATA_PORT,
    FIREWALL_INSIDE_PORT,
    FIREWALL_OUTSIDE_PORT,
    SUBPROCESS_TIMEOUT_SHORT,
    SUBPROCESS_TIMEOUT_DEFAULT,
    DEFAULT_NETWORK_LATENCY_MS
)

from cloudeos_manager import create_cloudeos, delete_cloudeos, get_cloudeos_status
from link_manager import add_link, remove_link, get_user_links, get_available_ports

# Configure logging (level configurable via environment variable)
LOG_LEVEL = os.environ.get('NODEBUILDER_LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Cloud Logging Setup — routes existing logger.info/error/warning calls to GCP
try:
    from cloud_logging_utils import setup_cloud_logging
    logger = setup_cloud_logging('nodebuilder')
except Exception:
    logger = logging.getLogger('nodebuilder')

routes = web.RouteTableDef()


def sanitize_error(error: Exception) -> str:
    """
    Sanitize error messages for client responses.

    Returns a safe error message without exposing internal details
    like file paths, stack traces, or system information.
    """
    error_str = str(error)

    # Known safe error patterns that can be shown to users
    safe_patterns = [
        'Device name is required',
        'IP address is required',
        'Invalid device name',
        'already exists',
        'not available',
        'not found',
        'is not a user-added node',
        'No changes specified',
        'Maximum',
        'must be',
        'Invalid JSON',
        'Timeout',
        'Failed to',
    ]

    # Check if error matches a known safe pattern
    for pattern in safe_patterns:
        if pattern.lower() in error_str.lower():
            return error_str

    # For unknown errors, return generic message
    return 'An internal error occurred. Check server logs for details.'


@routes.get('/health')
async def health(request):
    """Health check endpoint"""
    return web.json_response({
        'status': 'ok',
        'service': 'nodebuilder',
        'version': '1.0.0'
    })


@routes.get('/debug')
async def debug(request):
    """Debug endpoint to check file access and parsing"""
    import os
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path
    from validation import parse_dnsmasq_config, get_topo_nodes, get_user_nodes

    topo_build_path = get_topo_build_path()

    debug_info = {
        'dnsmasq_path': DNSMASQ_PATH,
        'dnsmasq_exists': os.path.exists(DNSMASQ_PATH),
        'topo_build_path': topo_build_path,
        'topo_build_exists': os.path.exists(topo_build_path),
        'user_nodes_path': USER_NODES_PATH,
        'user_nodes_exists': os.path.exists(USER_NODES_PATH),
    }

    # Try to read dnsmasq file
    if os.path.exists(DNSMASQ_PATH):
        try:
            with open(DNSMASQ_PATH, 'r') as f:
                lines = f.readlines()[:10]  # First 10 lines
            debug_info['dnsmasq_first_lines'] = [l.strip() for l in lines]
            debug_info['dnsmasq_total_lines'] = len(open(DNSMASQ_PATH).readlines())
        except Exception as e:
            debug_info['dnsmasq_read_error'] = str(e)

    # Try to parse dnsmasq
    try:
        entries = parse_dnsmasq_config(DNSMASQ_PATH)
        debug_info['dnsmasq_entries_count'] = len(entries)
        debug_info['dnsmasq_first_entries'] = entries[:5] if entries else []
    except Exception as e:
        debug_info['dnsmasq_parse_error'] = str(e)

    # Try to get topo nodes
    try:
        topo_nodes = get_topo_nodes(topo_build_path)
        debug_info['topo_nodes_count'] = len(topo_nodes)
        debug_info['topo_node_ips'] = [n.get('ip_addr') for n in topo_nodes[:5]]
    except Exception as e:
        debug_info['topo_parse_error'] = str(e)

    # Try to get user nodes
    try:
        user_nodes = get_user_nodes(USER_NODES_PATH)
        debug_info['user_nodes_count'] = len(user_nodes)
    except Exception as e:
        debug_info['user_nodes_error'] = str(e)

    return web.json_response(debug_info)


@routes.get('/available-ips')
async def available_ips(request):
    """Return IPs from dnsmasq not already in use"""
    # Import here to avoid circular imports and allow module development
    from validation import get_available_ips
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        topo_build_path = get_topo_build_path()
        ips = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
        return web.json_response({'available_ips': ips})
    except Exception as e:
        logger.error(f"Error getting available IPs: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/existing-nodes')
async def existing_nodes(request):
    """Return all nodes from topo_build + user_nodes"""
    from validation import get_all_nodes
    from config import USER_NODES_PATH, get_topo_build_path

    try:
        topo_build_path = get_topo_build_path()
        nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)
        return web.json_response({'nodes': nodes})
    except Exception as e:
        logger.error(f"Error getting existing nodes: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/topology/unified')
async def unified_topology(request):
    """
    Return complete unified topology with ALL device types.

    This endpoint provides a consistent view of all devices:
    - Original topology vEOS nodes
    - User-added vEOS nodes
    - User-added Linux hosts
    - User-added VyOS firewalls

    Response includes:
    - devices: List of all devices with standardized structure
    - connections: List of all inter-device connections
    - summary: Count statistics by device type

    Query parameters:
    - include_connections: bool (default true) - include connection list
    - device_type: filter by device type (veos, linux_host, firewall)
    - user_added: filter by user_added status (true, false)
    """
    from unified_topology import get_unified_topology, DeviceType
    from config import (
        USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH, USER_VELO_PATH,
        get_topo_build_path
    )

    try:
        topo_build_path = get_topo_build_path()
        topology = get_unified_topology(
            topo_build_path, USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH,
            USER_VELO_PATH
        )

        # Apply filters if specified
        devices = topology['devices']

        # Filter by device_type
        device_type_filter = request.query.get('device_type')
        if device_type_filter:
            devices = [d for d in devices if d['device_type'] == device_type_filter]

        # Filter by user_added
        user_added_filter = request.query.get('user_added')
        if user_added_filter is not None:
            user_added_bool = user_added_filter.lower() == 'true'
            devices = [d for d in devices if d['user_added'] == user_added_bool]

        # Optionally exclude connections
        include_connections = request.query.get('include_connections', 'true').lower() != 'false'

        response = {
            'devices': devices,
            'summary': {
                **topology['summary'],
                'filtered_count': len(devices)
            }
        }

        if include_connections:
            response['connections'] = topology['connections']

        return web.json_response(response)

    except Exception as e:
        logger.error(f"Error getting unified topology: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/target-devices')
async def target_devices(request):
    """Return devices available as connection targets with available ports"""
    from interface_manager import get_target_devices_with_ports

    try:
        devices = get_target_devices_with_ports()
        return web.json_response({'devices': devices})
    except Exception as e:
        logger.error(f"Error getting target devices: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/validate-node')
async def validate_node(request):
    """Validate node config before creation"""
    from validation import validate_device_name, get_available_ips
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    errors = []
    topo_build_path = get_topo_build_path()

    # Validate name (check against ALL device types: nodes, hosts, firewalls)
    # Normalize to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    name_valid, name_error = validate_device_name(
        name, topo_build_path, USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH
    )
    if not name_valid:
        errors.append(name_error)

    # Validate IP is in available list (only if provided - IP is selected in step 2)
    ip = data.get('ip', '')
    if ip:
        available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
        if not any(entry['ip'] == ip for entry in available):
            errors.append(f"IP {ip} is not available or already in use")
    # Note: IP is not required for name-only validation in step 1

    return web.json_response({
        'valid': len(errors) == 0,
        'errors': errors
    })


@routes.post('/add-node')
async def add_node(request):
    """Create new vEOS VM"""
    from validation import get_mac_for_ip, validate_device_name, get_available_ips
    from vm_manager import create_veos_node
    from persistence import save_user_node_pending, update_user_node_status, remove_user_node
    from interface_manager import creation_lock
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    ip = data.get('ip', '')
    device_type = data.get('device_type', DEFAULT_DEVICE_TYPE)  # Device type for diagram positioning
    connections = data.get('connections', [])

    # Validate device_type against known types
    if device_type and device_type not in VALID_DEVICE_TYPES:
        return web.json_response({
            'error': f"Invalid device_type '{device_type}'. Must be one of: {', '.join(sorted(VALID_DEVICE_TYPES))}"
        }, status=400)

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)
    if not ip:
        return web.json_response({'error': 'IP address is required'}, status=400)
    # Note: Connections are optional - nodes can be created without connections
    # for testing purposes or later connection via edit-node

    # Security: Validate connections count
    if not isinstance(connections, list):
        return web.json_response({'error': 'Connections must be a list'}, status=400)

    if len(connections) > MAX_CONNECTIONS_PER_NODE:
        return web.json_response({
            'error': f'Maximum {MAX_CONNECTIONS_PER_NODE} connections per node'
        }, status=400)

    try:
        # Acquire creation lock to prevent concurrent creates from racing
        # This serializes the entire validation + creation flow
        with creation_lock(f'add-node:{name}'):
            topo_build_path = get_topo_build_path()

            # Security: Check total node count limit (topology + user-added)
            from validation import get_all_nodes
            all_nodes = get_all_nodes(topo_build_path, USER_NODES_PATH)
            if len(all_nodes) >= MAX_TOTAL_NODES:
                return web.json_response({
                    'error': f'Maximum of {MAX_TOTAL_NODES} total nodes reached (topology has {len(all_nodes)} nodes)'
                }, status=400)

            # Validate name (check against ALL device types)
            name_valid, name_error = validate_device_name(
                name, topo_build_path, USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH
            )
            if not name_valid:
                return web.json_response({'error': name_error}, status=400)

            # Validate IP is available
            available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
            if not any(entry['ip'] == ip for entry in available):
                return web.json_response({'error': f'IP {ip} is not available or already in use'}, status=400)

            # Get MAC from dnsmasq
            mac = get_mac_for_ip(ip, DNSMASQ_PATH)
            if not mac:
                return web.json_response({'error': f'No MAC found for IP {ip}'}, status=400)

            logger.info(f"Creating vEOS node: {name} with IP {ip}, MAC {mac}")

            # SAVE-BEFORE-CREATE: Save pending entry BEFORE VM creation
            # This prevents zombie VMs if service crashes during creation
            pending_entry = {
                'ip_addr': ip,
                'sys_mac': mac,
                'platform': 'veos',
                'device_type': device_type or DEFAULT_DEVICE_TYPE,
                'neighbors': []  # Will be updated after creation
            }
            save_user_node_pending(name, pending_entry, USER_NODES_PATH)
            logger.debug(f"Saved pending node entry for {name}")

            try:
                # Create the VM (uses fixed CPU/RAM from config)
                result = create_veos_node(name, ip, mac, connections)

                # Update persistence with actual connection info
                neighbors = [
                    {
                        'neighborDevice': c['target_device'],
                        'neighborPort': c['target_port'],
                        'port': c['local_port']
                    } for c in result['connections']
                ]
                update_user_node_status(name, 'active', {'neighbors': neighbors}, USER_NODES_PATH)

                logger.info(f"Successfully created node: {name}")

                return web.json_response({
                    'status': 'created',
                    'node': {
                        'name': name,
                        'ip': ip,
                        'mac': mac,
                        'connections': result['connections']
                    },
                    # Include reboot information so UI knows which targets need rebooting
                    # Targets that reused orphaned slots don't need a reboot
                    'targets_reused_slots': result.get('targets_reused_slots', []),
                    'targets_need_reboot': result.get('targets_need_reboot', [])
                })

            except Exception as e:
                # VM creation failed - clean up any partially created resources
                logger.error(f"VM creation failed for {name}: {e}")

                # Try to clean up VM if it was created (prevents zombie VMs)
                from resource_manager import get_resource_manager
                rm = get_resource_manager()
                try:
                    if rm.vm_exists(name):
                        logger.info(f"Cleaning up partially created VM: {name}")
                        rm.destroy_vm(name, force=True)
                        rm.undefine_vm(name, force=True)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up VM {name}: {cleanup_error}")

                # Remove the pending entry from persistence
                remove_user_node(name, USER_NODES_PATH)
                raise

    except TimeoutError as e:
        logger.warning(f"Concurrent creation in progress, request queued timeout: {e}")
        return web.json_response({'error': 'Server busy with another creation request, please retry'}, status=503)
    except Exception as e:
        logger.error(f"Error creating node {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/user-nodes-status')
async def user_nodes_status(request):
    """
    Get status of user-added nodes.

    Returns whether there are user nodes, their current state,
    and whether restoration is needed (VMs defined but not running).
    """
    from vm_manager import get_user_nodes_status

    try:
        status = get_user_nodes_status()
        return web.json_response(status)
    except Exception as e:
        logger.error(f"Error getting user nodes status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/restore-user-nodes')
async def restore_user_nodes(request):
    """
    Restore all user-added nodes.

    This starts VMs that are defined but not running, and ensures
    their OVS bridges exist. Called after the original topology
    is up and running.
    """
    from vm_manager import restore_all_user_nodes
    from interface_manager import creation_lock

    try:
        # Acquire creation lock to prevent concurrent creates from racing
        with creation_lock('restore-all'):
            result = restore_all_user_nodes()
        return web.json_response(result)
    except TimeoutError:
        return web.json_response(
            {'error': 'Server busy - another operation in progress. Please try again.'},
            status=503
        )
    except Exception as e:
        logger.error(f"Error restoring user nodes: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/reset-all-user-nodes')
async def reset_all_user_nodes(request):
    """
    Reset to original topology by removing all user-added nodes.

    This removes all user-added VMs (vEOS nodes, Linux hosts, VyOS firewalls),
    deletes their OVS bridges, and clears persistence files.

    Returns detailed results of each phase of the reset process.
    """
    from resource_manager import get_resource_manager
    from interface_manager import creation_lock

    try:
        # Acquire creation lock to prevent concurrent operations from racing
        with creation_lock('reset-all'):
            logger.info("Initiating full reset of user-added nodes")
            resource_mgr = get_resource_manager()
            result = resource_mgr.reset_all_user_nodes()

            # Log summary
            summary = result.get('summary', {})
            logger.info(
                f"Reset complete: {summary.get('nodes', 0)} nodes, "
                f"{summary.get('hosts', 0)} hosts, "
                f"{summary.get('firewalls', 0)} firewalls, "
                f"{summary.get('bridges', 0)} bridges cleaned"
            )

        return web.json_response(result)
    except TimeoutError:
        return web.json_response(
            {'error': 'Server busy - another operation in progress. Please try again.'},
            status=503
        )
    except Exception as e:
        logger.error(f"Error resetting user nodes: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/delete-node')
async def delete_node(request):
    """
    Delete a user-added node completely.

    Request body: { "name": "leaf5" }

    This will:
    1. Stop and undefine the VM
    2. Delete the disk image
    3. Delete OVS bridges
    4. Detach interfaces from connected VMs
    5. Remove from user_nodes.yaml
    6. Clean up neighbor references in other user-added nodes
       (prevents orphaned connections when deleting a node that
       is connected to other user-added nodes)
    """
    from persistence import get_user_node, remove_user_node, remove_all_device_references
    from resource_manager import get_resource_manager
    from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)

    # Security: Validate device name format (alphanumeric + underscore)
    import re
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', name):
        return web.json_response({'error': 'Invalid device name format'}, status=400)

    # Validate: must be a user-added node
    user_node = get_user_node(name, USER_NODES_PATH)
    if not user_node:
        return web.json_response({
            'error': f"Node '{name}' is not a user-added node or does not exist"
        }, status=400)

    # Get node info for cleanup
    node_info = user_node.get(name, {})

    try:
        logger.info(f"Deleting user-added node: {name}")

        # Delete completely using ResourceManager
        resource_mgr = get_resource_manager()
        result = resource_mgr.delete_node_completely(name, node_info)

        # Remove from persistence
        remove_user_node(name, USER_NODES_PATH)

        # Clean up references in ALL device types (nodes, hosts, firewalls)
        # (prevents orphaned references when a node connected to other devices is deleted)
        cleanup_result = remove_all_device_references(
            name, USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH
        )
        if cleanup_result['total'] > 0:
            result['orphaned_references_removed'] = cleanup_result

        logger.info(f"Successfully deleted node: {name}")

        return web.json_response({
            'status': 'deleted',
            'node': name,
            'details': result
        })

    except Exception as e:
        logger.error(f"Error deleting node {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/edit-node')
async def edit_node(request):
    """
    Edit connections for a user-added node.

    Request body: {
        "name": "leaf5",
        "add_connections": [
            {"target_device": "spine3", "target_port": "Ethernet7"}
        ],
        "remove_connections": [
            {"local_port": "Ethernet1", "target_device": "spine1", "target_port": "Ethernet5"}
        ]
    }

    Note: IP/MAC cannot be changed as that would break ZTP.
    """
    from persistence import get_user_node, load_user_nodes, save_user_nodes
    from connection_manager import get_connection_manager, Connection
    from transactions import NodeEditTransaction
    from validation import validate_connection_unique, validate_target_device_exists
    from interface_manager import find_next_available_port
    from config import USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    add_connections = data.get('add_connections', [])
    remove_connections = data.get('remove_connections', [])

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)

    # Security: Validate device name format
    import re
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', name):
        return web.json_response({'error': 'Invalid device name format'}, status=400)

    # Validate: must be a user-added node
    user_node = get_user_node(name, USER_NODES_PATH)
    if not user_node:
        return web.json_response({
            'error': f"Node '{name}' is not a user-added node or does not exist"
        }, status=400)

    # Validate inputs
    if not add_connections and not remove_connections:
        return web.json_response({'error': 'No changes specified'}, status=400)

    topo_build_path = get_topo_build_path()

    # Validate add_connections
    for conn in add_connections:
        target_device = conn.get('target_device')
        if not target_device:
            return web.json_response({'error': 'target_device is required for add_connections'}, status=400)

        # Validate target device exists
        valid, error = validate_target_device_exists(target_device, topo_build_path, USER_NODES_PATH)
        if not valid:
            return web.json_response({'error': error}, status=400)

    # Validate remove_connections
    for conn in remove_connections:
        if not conn.get('local_port'):
            return web.json_response({'error': 'local_port is required for remove_connections'}, status=400)

    try:
        logger.info(f"Editing connections for node: {name}")

        conn_mgr = get_connection_manager()
        node_info = user_node.get(name, {})

        # Use transaction for atomic operation
        with NodeEditTransaction(name) as txn:
            # First: remove old connections
            for conn_spec in remove_connections:
                local_port = conn_spec.get('local_port')
                target_device = conn_spec.get('target_device')
                target_port = conn_spec.get('target_port')

                conn = Connection(
                    source_device=name,
                    source_port=local_port,
                    target_device=target_device,
                    target_port=target_port
                )
                txn.add_remove_connection(conn_mgr, conn)

            # Second: add new connections
            added_connections = []
            for conn_spec in add_connections:
                target_device = conn_spec.get('target_device')
                target_port = conn_spec.get('target_port') or find_next_available_port(target_device)

                # Find next available local port
                local_port = conn_spec.get('local_port') or find_next_available_port(name)

                # Validate connection is unique
                valid, error = validate_connection_unique(
                    name, local_port, target_device, target_port,
                    topo_build_path, USER_NODES_PATH
                )
                if not valid:
                    return web.json_response({'error': error}, status=400)

                conn = Connection(
                    source_device=name,
                    source_port=local_port,
                    target_device=target_device,
                    target_port=target_port
                )
                txn.add_create_connection(conn_mgr, conn)
                added_connections.append({
                    'local_port': local_port,
                    'target_device': target_device,
                    'target_port': target_port
                })

            # Execute all actions with rollback on failure
            txn.execute()

        # Update persistence
        all_user_nodes = load_user_nodes(USER_NODES_PATH)
        for node_entry in all_user_nodes.get('nodes', []):
            for node_name, info in node_entry.items():
                if node_name.lower() == name.lower():
                    # Remove deleted connections from neighbors
                    for conn_spec in remove_connections:
                        local_port = conn_spec.get('local_port')
                        info['neighbors'] = [
                            n for n in info.get('neighbors', [])
                            if n.get('port', '').lower() != local_port.lower()
                        ]

                    # Add new connections to neighbors
                    for conn in added_connections:
                        info.setdefault('neighbors', []).append({
                            'port': conn['local_port'],
                            'neighborDevice': conn['target_device'],
                            'neighborPort': conn['target_port']
                        })
                    break

        save_user_nodes(all_user_nodes, USER_NODES_PATH)

        logger.info(f"Successfully edited node: {name}")

        return web.json_response({
            'status': 'updated',
            'node': name,
            'added': added_connections,
            'removed': [c.get('local_port') for c in remove_connections]
        })

    except Exception as e:
        logger.error(f"Error editing node {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/node-connections/{name}')
async def get_node_connections(request):
    """
    Get current connections for a user-added device (node, host, or firewall).

    Returns list of connections for deletion reboot prompt.
    """
    from persistence import get_user_node, get_user_host, get_user_firewall
    from config import USER_NODES_PATH, USER_HOSTS_PATH, USER_FIREWALLS_PATH

    name = request.match_info.get('name', '')

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)

    # Security: Validate device name format
    import re
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', name):
        return web.json_response({'error': 'Invalid device name format'}, status=400)

    connections = []
    device_ip = ''
    device_type = 'node'

    # Check vEOS nodes first
    user_node = get_user_node(name, USER_NODES_PATH)
    if user_node:
        node_info = user_node.get(name, {})
        device_ip = node_info.get('ip_addr', '')
        neighbors = node_info.get('neighbors', [])
        for neighbor in neighbors:
            connections.append({
                'local_port': neighbor.get('port', ''),
                'target_device': neighbor.get('neighborDevice', ''),
                'target_port': neighbor.get('neighborPort', '')
            })
    else:
        # Check Linux hosts
        user_host = get_user_host(name, USER_HOSTS_PATH)
        if user_host:
            host_info = user_host.get(name, {})
            device_ip = host_info.get('mgmt_ip', '')
            device_type = 'host'
            connection = host_info.get('connection', {})
            if connection and connection.get('target_device'):
                connections.append({
                    'local_port': 'eth1',
                    'target_device': connection.get('target_device', ''),
                    'target_port': connection.get('target_port', '')
                })
        else:
            # Check VyOS firewalls
            user_fw = get_user_firewall(name, USER_FIREWALLS_PATH)
            if user_fw:
                fw_info = user_fw.get(name, {})
                device_ip = fw_info.get('mgmt_ip', '')
                device_type = 'firewall'
                # Add inside interface connection
                inside = fw_info.get('inside_interface', {})
                if inside and inside.get('target_device'):
                    connections.append({
                        'local_port': 'eth1',
                        'target_device': inside.get('target_device', ''),
                        'target_port': inside.get('target_port', '')
                    })
                # Add outside interface connection
                outside = fw_info.get('outside_interface', {})
                if outside and outside.get('target_device'):
                    connections.append({
                        'local_port': 'eth2',
                        'target_device': outside.get('target_device', ''),
                        'target_port': outside.get('target_port', '')
                    })
            else:
                return web.json_response({
                    'error': f"Device '{name}' not found in user nodes, hosts, or firewalls"
                }, status=400)

    return web.json_response({
        'name': name,
        'ip': device_ip,
        'type': device_type,
        'connections': connections
    })


@routes.get('/cluster-templates')
async def cluster_templates(request):
    """
    Get available cluster templates.

    Returns list of templates with their configurations.
    """
    from cluster_templates import get_cluster_templates

    try:
        templates = get_cluster_templates()
        return web.json_response({'templates': templates})
    except Exception as e:
        logger.error(f"Error getting cluster templates: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-cluster')
async def add_cluster(request):
    """
    Create a cluster of nodes from a template.

    Request body: {
        "template_id": "internet",
        "name_prefix": "dc1",
        "external_connections": [
            {"from_node": "isp1", "target_device": "borderleaf1"},
            {"from_node": "isp2", "target_device": "borderleaf2"}
        ],
        "ip_assignments": {
            "isp1": "192.168.0.50",
            "isp2": "192.168.0.51"
        },
        "impairments": {
            "latency_ms": 25,
            "loss_percent": 0.5
        }
    }

    The cluster creation works in phases:
    1. Create all VMs with only external connections (to existing topology)
    2. After all VMs exist, create internal connections between cluster nodes
    3. Optionally apply impairments to cluster bridges
    """
    from cluster_templates import get_template_by_id, validate_cluster_request
    from validation import get_available_ips, get_mac_for_ip, validate_device_name, validate_target_device_exists, generate_unique_cluster_prefix
    from vm_manager import create_veos_node
    from persistence import save_user_node
    from connection_manager import get_connection_manager, Connection
    from interface_manager import create_ovs_bridge, attach_interface_to_vm, generate_bridge_name, creation_lock
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    template_id = data.get('template_id', '')
    # Normalize prefix to lowercase to ensure consistent VM naming
    # (libvirt may be case-insensitive, causes issues with deletion)
    name_prefix = data.get('name_prefix', '').lower()
    external_connections = data.get('external_connections', [])
    ip_assignments = data.get('ip_assignments', {})
    impairments = data.get('impairments', {})

    if not template_id:
        return web.json_response({'error': 'template_id is required'}, status=400)

    # Get template first (before lock) to fail fast on invalid template
    template = get_template_by_id(template_id)
    if not template:
        return web.json_response({'error': f"Unknown template: {template_id}"}, status=400)

    # Use creation lock to prevent race conditions on cluster prefix validation
    # Lock includes template_id and name_prefix to allow concurrent creation of different clusters
    try:
        with creation_lock(f'add-cluster:{template_id}:{name_prefix}'):
            topo_build_path = get_topo_build_path()

            # Get available IPs
            available_ips = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)

            # Validate request
            valid, error = validate_cluster_request(
                template_id,
                external_connections,
                len(available_ips)
            )
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate external connection targets exist
            for ext_conn in external_connections:
                target_device = ext_conn.get('target_device')
                if target_device:
                    valid, error = validate_target_device_exists(target_device, topo_build_path, USER_NODES_PATH)
                    if not valid:
                        return web.json_response({'error': error}, status=400)

            # Generate unique prefix to avoid name conflicts
            # If user's prefix results in conflicts, auto-increment (e.g., prefix_2, prefix_3)
            try:
                unique_prefix = generate_unique_cluster_prefix(
                    name_prefix,
                    template.nodes,
                    topo_build_path,
                    USER_NODES_PATH
                )
            except ValueError as e:
                return web.json_response({'error': sanitize_error(e)}, status=400)

            # Log if prefix was modified
            if unique_prefix != name_prefix:
                logger.info(f"Auto-adjusted prefix from '{name_prefix}' to '{unique_prefix}' to avoid conflicts")

            # Generate node names with the unique prefix
            node_names = {}
            for node_template in template.nodes:
                full_name = node_template.get_full_name(unique_prefix)
                # Validate name format (not uniqueness - that's already handled above)
                if not full_name or len(full_name) > 32:
                    return web.json_response({
                        'error': f"Invalid node name '{full_name}': must be 1-32 characters"
                    }, status=400)
                node_names[node_template.name_suffix] = full_name

            # Update name_prefix to the unique version for response
            name_prefix = unique_prefix

            # Assign IPs (use provided or auto-assign from available)
            assigned_ips = {}
            available_ip_list = list(available_ips)
            ip_index = 0

            for node_template in template.nodes:
                suffix = node_template.name_suffix
                full_name = node_names[suffix]

                if suffix in ip_assignments:
                    # Use provided IP
                    ip = ip_assignments[suffix]
                    # Validate it's available
                    if not any(entry['ip'] == ip for entry in available_ips):
                        return web.json_response({
                            'error': f"IP {ip} is not available for {full_name}"
                        }, status=400)
                    assigned_ips[suffix] = ip
                else:
                    # Auto-assign from available
                    if ip_index >= len(available_ip_list):
                        return web.json_response({'error': 'Not enough IPs available'}, status=400)
                    assigned_ips[suffix] = available_ip_list[ip_index]['ip']
                    ip_index += 1

            try:
                logger.info(f"Creating cluster from template: {template_id}")

                created_nodes = []
                internal_bridges = []  # Track bridges created for internal connections
                # Track reboot info for EXTERNAL topology targets only (not cluster nodes)
                external_targets_reused_slots = []
                external_targets_need_reboot = []
                # Track cluster nodes that need reboot (always all of them for internal connections)
                cluster_nodes_need_reboot = set()
                conn_mgr = get_connection_manager()

                # PHASE 1: Create all VMs with only external connections
                # (internal connections are added after all VMs exist)
                logger.info(f"Phase 1: Creating {len(template.nodes)} VMs")

                for node_template in template.nodes:
                    suffix = node_template.name_suffix
                    full_name = node_names[suffix]
                    ip = assigned_ips[suffix]
                    mac = get_mac_for_ip(ip, DNSMASQ_PATH)

                    if not mac:
                        # Rollback already created nodes
                        from resource_manager import get_resource_manager
                        rm = get_resource_manager()
                        for created in created_nodes:
                            try:
                                rm.delete_node_completely(created['name'], {})
                            except Exception:
                                pass
                        return web.json_response({
                            'error': f"No MAC found for IP {ip}"
                        }, status=400)

                    # Only include external connections for this node
                    # (connections to existing topology devices)
                    node_external_connections = []
                    for ext_conn in external_connections:
                        if ext_conn.get('from_node') == suffix:
                            node_external_connections.append({
                                'target_device': ext_conn['target_device'],
                                'target_port': ext_conn.get('target_port')
                            })

                    try:
                        # Create VM with only external connections
                        result = create_veos_node(full_name, ip, mac, node_external_connections)
                        created_nodes.append({
                            'name': full_name,
                            'suffix': suffix,
                            'ip': ip,
                            'mac': mac,
                            'connections': result.get('connections', [])
                        })
                        # Collect reboot info for external targets from this node
                        external_targets_reused_slots.extend(result.get('targets_reused_slots', []))
                        external_targets_need_reboot.extend(result.get('targets_need_reboot', []))
                        logger.info(f"  Created VM: {full_name}")
                    except Exception as e:
                        # Rollback all created nodes
                        logger.error(f"Failed to create {full_name}: {e}")
                        from resource_manager import get_resource_manager
                        rm = get_resource_manager()
                        for created in created_nodes:
                            try:
                                rm.delete_node_completely(created['name'], {})
                            except Exception:
                                pass
                        raise

                # PHASE 2: Create internal connections between cluster nodes
                # Now that all VMs exist, we can connect them to each other
                logger.info(f"Phase 2: Creating {len(template.internal_connections)} internal connections")
                failed_internal_connections = []
                from interface_manager import delete_ovs_bridge

                for int_conn in template.internal_connections:
                    from_suffix = int_conn.from_node
                    to_suffix = int_conn.to_node
                    from_name = node_names[from_suffix]
                    to_name = node_names[to_suffix]

                    # Find both nodes to calculate their next port numbers BEFORE adding connections
                    from_node = next((n for n in created_nodes if n['suffix'] == from_suffix), None)
                    to_node = next((n for n in created_nodes if n['suffix'] == to_suffix), None)

                    if not from_node or not to_node:
                        logger.error(f"Could not find nodes for internal connection: {from_suffix} -> {to_suffix}")
                        failed_internal_connections.append({
                            'from': from_suffix,
                            'to': to_suffix,
                            'error': 'Node not found in created_nodes'
                        })
                        continue

                    # Calculate port numbers for both ends
                    from_port = f"Ethernet{len(from_node['connections']) + 1}"
                    to_port = f"Ethernet{len(to_node['connections']) + 1}"

                    # Generate bridge name for this internal connection
                    bridge_name = generate_bridge_name(from_name, from_port, to_name, to_port)
                    bridge_created = False

                    try:
                        # Create OVS bridge
                        create_ovs_bridge(bridge_name)
                        bridge_created = True

                        # Attach interface to both VMs
                        # Note: attach_interface_to_vm requires reboot for the interface to work
                        attach_interface_to_vm(from_name, bridge_name)
                        attach_interface_to_vm(to_name, bridge_name)

                        # Track that these cluster nodes need reboot for internal connections
                        cluster_nodes_need_reboot.add(from_name)
                        cluster_nodes_need_reboot.add(to_name)

                        internal_bridges.append({
                            'bridge': bridge_name,
                            'from': from_name,
                            'to': to_name
                        })

                        # Add connection records with correct port info for BOTH ends
                        from_node['connections'].append({
                            'local_port': from_port,
                            'target_device': to_name,
                            'target_port': to_port,
                            'bridge': bridge_name,
                            'internal': True
                        })
                        to_node['connections'].append({
                            'local_port': to_port,
                            'target_device': from_name,
                            'target_port': from_port,
                            'bridge': bridge_name,
                            'internal': True
                        })

                        logger.info(f"  Connected: {from_name} <-> {to_name} (bridge: {bridge_name})")

                    except Exception as e:
                        logger.error(f"Failed to create internal connection {from_name} <-> {to_name}: {e}")
                        failed_internal_connections.append({
                            'from': from_name,
                            'to': to_name,
                            'error': str(e)
                        })
                        # Rollback: delete the bridge if it was created
                        if bridge_created:
                            try:
                                delete_ovs_bridge(bridge_name)
                                logger.info(f"  Rolled back bridge {bridge_name} after connection failure")
                            except Exception as rollback_err:
                                logger.warning(f"  Failed to rollback bridge {bridge_name}: {rollback_err}")

                # PHASE 3: Apply impairments to cluster bridges if specified
                applied_impairments = []
                if impairments:
                    logger.info(f"Phase 3: Applying impairments to {len(internal_bridges)} internal bridges")

                    # Note: Impairments are applied via captureservice API
                    # We'll return the bridge names so the frontend can apply impairments
                    for bridge_info in internal_bridges:
                        applied_impairments.append({
                            'bridge': bridge_info['bridge'],
                            'from': bridge_info['from'],
                            'to': bridge_info['to'],
                            'impairments': impairments
                        })

                # Save all nodes to persistence
                for node_info in created_nodes:
                    neighbors = []
                    for c in node_info['connections']:
                        neighbors.append({
                            'neighborDevice': c.get('target_device', ''),
                            'neighborPort': c.get('target_port', ''),
                            'port': c.get('local_port', ''),
                            'bridge': c.get('bridge', ''),
                            'internal': c.get('internal', False)
                        })

                    node_data = {
                        node_info['name']: {
                            'ip_addr': node_info['ip'],
                            'sys_mac': node_info['mac'],
                            'platform': 'veos',
                            'user_added': True,
                            'cluster': template_id,
                            'neighbors': neighbors
                        }
                    }
                    save_user_node(node_data, USER_NODES_PATH)

                logger.info(f"Successfully created cluster: {template_id} ({len(created_nodes)} nodes)")

                # Apply mutual exclusivity for EXTERNAL targets only:
                # If an external target needs reboot for ANY reason, it should only appear
                # in targets_need_reboot (not in targets_reused_slots)
                ext_reused_set = set(external_targets_reused_slots)
                ext_reboot_set = set(external_targets_need_reboot)
                final_ext_reused = ext_reused_set - ext_reboot_set

                # Combine external targets needing reboot with cluster nodes needing reboot
                # (cluster nodes are tracked separately and always need reboot for internal connections)
                all_need_reboot = ext_reboot_set | cluster_nodes_need_reboot

                return web.json_response({
                    'status': 'created',
                    'cluster': template_id,
                    'prefix': name_prefix,
                    'nodes': created_nodes,
                    'internal_bridges': internal_bridges,
                    'internal_connections_failed': failed_internal_connections,
                    'impairments_to_apply': applied_impairments,
                    'targets_reused_slots': list(final_ext_reused),
                    'targets_need_reboot': list(all_need_reboot)
                })

            except Exception as e:
                logger.error(f"Error creating cluster {template_id}: {e}", exc_info=True)
                return web.json_response({'error': sanitize_error(e)}, status=500)

    except TimeoutError as e:
        logger.warning(f"Concurrent cluster creation in progress, request queued timeout: {e}")
        return web.json_response({'error': 'Server busy with another cluster creation, please retry'}, status=503)


@routes.post('/save-config')
async def save_config(request):
    """
    Save running config to startup config on a device via pyeAPI.

    Request body: { "device": "spine1", "ip": "192.168.0.11" }
    """
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    device = data.get('device', '')
    ip = data.get('ip', '')

    if not device:
        return web.json_response({'error': 'Device name is required'}, status=400)
    if not ip:
        return web.json_response({'error': 'Device IP is required'}, status=400)

    try:
        import pyeapi
        from config import get_device_credentials

        # Get credentials from ACCESS_INFO.yaml
        creds = get_device_credentials()

        # Connect to device using eAPI
        connection = pyeapi.connect(
            transport='https',
            host=ip,
            username=creds['username'],
            password=creds['password'],
            return_node=True
        )

        # Execute write memory command
        result = connection.enable(['write memory'])

        logger.info(f"Saved config on {device} ({ip})")

        return web.json_response({
            'status': 'saved',
            'device': device,
            'ip': ip,
            'result': result
        })

    except Exception as e:
        logger.error(f"Error saving config on {device}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/reboot-devices')
async def reboot_devices(request):
    """
    Reboot one or more devices via virsh.

    Request body: { "devices": ["spine1", "leaf1"] }

    Note: This reboots the VMs, not graceful EOS reload.
    Target devices need to be rebooted to see newly attached interfaces.
    """
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    devices = data.get('devices', [])

    if not devices:
        return web.json_response({'error': 'At least one device is required'}, status=400)

    if not isinstance(devices, list):
        return web.json_response({'error': 'Devices must be a list'}, status=400)

    results = []
    errors = []

    for device in devices:
        try:
            import subprocess

            result = subprocess.run(
                ['virsh', 'reboot', device],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_DEFAULT
            )

            if result.returncode != 0:
                errors.append({'device': device, 'error': result.stderr.strip()})
            else:
                results.append({'device': device, 'status': 'rebooting'})
                logger.info(f"Rebooted device: {device}")

        except subprocess.TimeoutExpired:
            errors.append({'device': device, 'error': 'Timeout rebooting device'})
        except Exception as e:
            logger.error(f"Error rebooting device {device}: {e}", exc_info=True)
            errors.append({'device': device, 'error': 'Failed to reboot device'})

    return web.json_response({
        'status': 'completed',
        'rebooted': results,
        'errors': errors
    })


# ============================================================================
# Linux Host Endpoints
# ============================================================================

@routes.get('/host-status')
async def host_status(request):
    """
    Get current Linux host count and availability.

    Returns count of existing hosts and whether more can be added.
    """
    from persistence import list_user_hosts

    try:
        hosts = list_user_hosts(USER_HOSTS_PATH)
        current_count = len(hosts)

        return web.json_response({
            'current_count': current_count,
            'max_allowed': MAX_HOSTS_PER_TOPOLOGY,
            'can_add_more': current_count < MAX_HOSTS_PER_TOPOLOGY,
            'hosts': [
                {'name': list(h.keys())[0], 'info': list(h.values())[0]}
                for h in hosts
            ]
        })
    except Exception as e:
        logger.error(f"Error getting host status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-host')
async def add_host(request):
    """
    Create a new Linux desktop host VM.

    Request body: {
        "name": "desktop1",
        "ip": "192.168.0.50",
        "connection": {
            "target_device": "leaf1",
            "target_port": "Ethernet5"
        },
        "data_ip": "10.1.1.100/24"  // Optional: IP for data interface
    }
    """
    from host_manager import create_host
    from persistence import save_user_host_pending, update_user_host_status, remove_user_host
    from interface_manager import creation_lock
    from validation import (
        validate_host_name, validate_host_limit,
        get_mac_for_ip, get_available_ips, validate_cidr_ip
    )
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    ip = data.get('ip', '')
    connection = data.get('connection')
    data_ip = data.get('data_ip')

    if not name:
        return web.json_response({'error': 'Host name is required'}, status=400)
    if not ip:
        return web.json_response({'error': 'Management IP is required'}, status=400)

    # Validate data_ip format if provided (can do before lock)
    if data_ip:
        valid, error = validate_cidr_ip(data_ip)
        if not valid:
            return web.json_response({'error': error}, status=400)

    try:
        # Acquire creation lock to prevent concurrent creates from racing
        with creation_lock(f'add-host:{name}'):
            topo_build_path = get_topo_build_path()

            # Validate host limit
            valid, error = validate_host_limit(USER_HOSTS_PATH, MAX_HOSTS_PER_TOPOLOGY)
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate name across all device types
            valid, error = validate_host_name(
                name, topo_build_path, USER_NODES_PATH,
                USER_HOSTS_PATH, USER_FIREWALLS_PATH
            )
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate IP is available
            available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
            if not any(entry['ip'] == ip for entry in available):
                return web.json_response({
                    'error': f'IP {ip} is not available or already in use'
                }, status=400)

            logger.info(f"Creating Linux host: {name} (IP: {ip})")

            # SAVE-BEFORE-CREATE: Save pending entry BEFORE VM creation
            pending_entry = {
                'mgmt_ip': ip,
                'data_ip': data_ip,
                'connection': connection,
                'neighbors': []  # Will be updated after creation
            }
            save_user_host_pending(name, pending_entry, USER_HOSTS_PATH)
            logger.debug(f"Saved pending host entry for {name}")

            try:
                # Create the host VM
                result = create_host(name, ip, connection, data_ip)

                # Build neighbors list for topology diagram connections
                neighbors = []
                conn = result.get('connection')
                if conn:
                    if conn.get('target_device'):
                        neighbors.append({
                            'neighborDevice': conn['target_device'],
                            'neighborPort': conn.get('target_port', ''),
                            'port': HOST_DATA_PORT
                        })
                    else:
                        logger.warning(f"Host {name} connection missing target_device")

                # Update persistence with actual info
                update_info = {
                    'vnc_port': result.get('vnc_port'),
                    'connection': conn,
                    'neighbors': neighbors
                }
                update_user_host_status(name, 'active', update_info, USER_HOSTS_PATH)

                logger.info(f"Successfully created Linux host: {name}")

                return web.json_response({
                    'status': 'created',
                    'host': result,
                    'targets_reused_slots': result.get('targets_reused_slots', []),
                    'targets_need_reboot': result.get('targets_need_reboot', [])
                })

            except Exception as e:
                # VM creation failed - clean up any partially created resources
                logger.error(f"Host VM creation failed for {name}: {e}")

                # Try to clean up VM if it was created (prevents zombie VMs)
                from resource_manager import get_resource_manager
                rm = get_resource_manager()
                try:
                    if rm.vm_exists(name):
                        logger.info(f"Cleaning up partially created host VM: {name}")
                        rm.destroy_vm(name, force=True)
                        rm.undefine_vm(name, force=True)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up host VM {name}: {cleanup_error}")

                # Remove the pending entry from persistence
                remove_user_host(name, USER_HOSTS_PATH)
                raise

    except TimeoutError as e:
        logger.warning(f"Concurrent creation in progress, request queued timeout: {e}")
        return web.json_response({'error': 'Server busy with another creation request, please retry'}, status=503)
    except ValueError as e:
        logger.warning(f"Validation error creating host {name}: {e}")
        return web.json_response({'error': sanitize_error(e)}, status=400)
    except FileNotFoundError as e:
        logger.error(f"Required file not found for host {name}: {e}")
        return web.json_response({'error': 'Required file not found'}, status=500)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed for host {name}: {e}")
        return web.json_response({'error': f'VM operation failed: {sanitize_error(e)}'}, status=500)
    except Exception as e:
        logger.error(f"Unexpected error creating host {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/delete-host')
async def delete_host_endpoint(request):
    """
    Delete a Linux host VM.

    Request body: { "name": "desktop1" }
    """
    from host_manager import delete_host
    from persistence import get_user_host, remove_user_host
    from novnc_manager import revoke_tokens_for_host

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()

    if not name:
        return web.json_response({'error': 'Host name is required'}, status=400)

    # Validate: must be a user-added host
    host = get_user_host(name, USER_HOSTS_PATH)
    if not host:
        return web.json_response({
            'error': f"Host '{name}' not found"
        }, status=400)

    try:
        logger.info(f"Deleting Linux host: {name}")

        # Delete the VM
        result = delete_host(name)

        # Remove from persistence
        remove_user_host(name, USER_HOSTS_PATH)

        # Revoke any noVNC tokens
        revoke_tokens_for_host(name)

        logger.info(f"Successfully deleted Linux host: {name}")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error deleting host {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/novnc-token/{name}')
async def get_novnc_token(request):
    """
    Get a noVNC access token for a Linux host.

    Returns a token and WebSocket URL for browser-based VNC access.
    """
    from novnc_manager import create_vnc_token
    from persistence import get_user_host

    name = request.match_info.get('name', '')

    if not name:
        return web.json_response({'error': 'Host name is required'}, status=400)

    # Validate: must be a user-added host
    host = get_user_host(name, USER_HOSTS_PATH)
    if not host:
        return web.json_response({
            'error': f"Host '{name}' not found"
        }, status=400)

    try:
        token_info = create_vnc_token(name)
        if not token_info:
            return web.json_response({
                'error': f"VNC not available for host '{name}'"
            }, status=400)

        return web.json_response(token_info)

    except Exception as e:
        logger.error(f"Error getting VNC token for {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# ============================================================================
# Firewall Endpoints
# ============================================================================

@routes.get('/firewall-status')
async def firewall_status(request):
    """
    Get current VyOS firewall count and availability.

    Returns count of existing firewalls and whether more can be added.
    """
    from persistence import list_user_firewalls

    try:
        firewalls = list_user_firewalls(USER_FIREWALLS_PATH)
        current_count = len(firewalls)

        return web.json_response({
            'current_count': current_count,
            'max_allowed': MAX_FIREWALLS_PER_TOPOLOGY,
            'can_add_more': current_count < MAX_FIREWALLS_PER_TOPOLOGY,
            'firewalls': [
                {'name': list(fw.keys())[0], 'info': list(fw.values())[0]}
                for fw in firewalls
            ]
        })
    except Exception as e:
        logger.error(f"Error getting firewall status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-firewall')
async def add_firewall(request):
    """
    Create a new VyOS firewall VM.

    Request body: {
        "name": "fw1",
        "mgmt_ip": "192.168.0.51",
        "inside_interface": {
            "ip": "10.1.1.1/24",
            "target_device": "leaf1",
            "target_port": "Ethernet6"
        },
        "outside_interface": {
            "ip": "10.2.2.1/24",
            "target_device": "spine1",
            "target_port": "Ethernet7"
        }
    }
    """
    from firewall_manager import create_firewall
    from persistence import save_user_firewall_pending, update_user_firewall_status, remove_user_firewall
    from interface_manager import creation_lock
    from validation import (
        validate_firewall_name, validate_firewall_limit,
        validate_cidr_ip, get_available_ips
    )
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    mgmt_ip = data.get('mgmt_ip', '')
    inside_interface = data.get('inside_interface', {})
    outside_interface = data.get('outside_interface', {})

    if not name:
        return web.json_response({'error': 'Firewall name is required'}, status=400)
    if not mgmt_ip:
        return web.json_response({'error': 'Management IP is required'}, status=400)
    # Validate target devices are specified (IPs are configured in VyOS after boot)
    if not inside_interface.get('target_device'):
        return web.json_response({'error': 'Inside interface target device is required'}, status=400)
    if not outside_interface.get('target_device'):
        return web.json_response({'error': 'Outside interface target device is required'}, status=400)

    try:
        # Acquire creation lock to prevent concurrent creates from racing
        with creation_lock(f'add-firewall:{name}'):
            topo_build_path = get_topo_build_path()

            # Validate firewall limit
            valid, error = validate_firewall_limit(USER_FIREWALLS_PATH, MAX_FIREWALLS_PER_TOPOLOGY)
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate name across all device types
            valid, error = validate_firewall_name(
                name, topo_build_path, USER_NODES_PATH,
                USER_HOSTS_PATH, USER_FIREWALLS_PATH
            )
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate management IP is available
            available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
            if not any(entry['ip'] == mgmt_ip for entry in available):
                return web.json_response({
                    'error': f'Management IP {mgmt_ip} is not available or already in use'
                }, status=400)

            logger.info(f"Creating VyOS firewall: {name} (Mgmt IP: {mgmt_ip})")

            # SAVE-BEFORE-CREATE: Save pending entry BEFORE VM creation
            pending_entry = {
                'mgmt_ip': mgmt_ip,
                'inside_interface': inside_interface,
                'outside_interface': outside_interface,
                'neighbors': []  # Will be updated after creation
            }
            save_user_firewall_pending(name, pending_entry, USER_FIREWALLS_PATH)
            logger.debug(f"Saved pending firewall entry for {name}")

            try:
                # Create the firewall VM
                result = create_firewall(name, mgmt_ip, inside_interface, outside_interface)

                # Build neighbors list for topology diagram connections
                neighbors = []
                inside_iface = result.get('inside_interface')
                outside_iface = result.get('outside_interface')

                if inside_iface:
                    if inside_iface.get('target_device'):
                        neighbors.append({
                            'neighborDevice': inside_iface['target_device'],
                            'neighborPort': inside_iface.get('target_port', ''),
                            'port': FIREWALL_INSIDE_PORT
                        })
                    else:
                        logger.warning(f"Firewall {name} inside interface missing target_device")

                if outside_iface:
                    if outside_iface.get('target_device'):
                        neighbors.append({
                            'neighborDevice': outside_iface['target_device'],
                            'neighborPort': outside_iface.get('target_port', ''),
                            'port': FIREWALL_OUTSIDE_PORT
                        })
                    else:
                        logger.warning(f"Firewall {name} outside interface missing target_device")

                # Update persistence with actual info
                update_info = {
                    'inside_interface': inside_iface,
                    'outside_interface': outside_iface,
                    'neighbors': neighbors
                }
                update_user_firewall_status(name, 'active', update_info, USER_FIREWALLS_PATH)

                logger.info(f"Successfully created VyOS firewall: {name}")

                return web.json_response({
                    'status': 'created',
                    'firewall': result,
                    'targets_reused_slots': result.get('targets_reused_slots', []),
                    'targets_need_reboot': result.get('targets_need_reboot', [])
                })

            except Exception as e:
                # VM creation failed - clean up any partially created resources
                logger.error(f"Firewall VM creation failed for {name}: {e}")

                # Try to clean up VM if it was created (prevents zombie VMs)
                from resource_manager import get_resource_manager
                rm = get_resource_manager()
                try:
                    if rm.vm_exists(name):
                        logger.info(f"Cleaning up partially created firewall VM: {name}")
                        rm.destroy_vm(name, force=True)
                        rm.undefine_vm(name, force=True)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up firewall VM {name}: {cleanup_error}")

                # Remove the pending entry from persistence
                remove_user_firewall(name, USER_FIREWALLS_PATH)
                raise

    except TimeoutError as e:
        logger.warning(f"Concurrent creation in progress, request queued timeout: {e}")
        return web.json_response({'error': 'Server busy with another creation request, please retry'}, status=503)
    except ValueError as e:
        logger.warning(f"Validation error creating firewall {name}: {e}")
        return web.json_response({'error': sanitize_error(e)}, status=400)
    except FileNotFoundError as e:
        logger.error(f"Required file not found for firewall {name}: {e}")
        return web.json_response({'error': 'Required file not found'}, status=500)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed for firewall {name}: {e}")
        return web.json_response({'error': f'VM operation failed: {sanitize_error(e)}'}, status=500)
    except Exception as e:
        logger.error(f"Unexpected error creating firewall {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/edit-firewall')
async def edit_firewall_endpoint(request):
    """
    Edit firewall interface IPs.

    Request body: {
        "name": "fw1",
        "inside_interface": {"ip": "10.1.1.2/24"},
        "outside_interface": {"ip": "10.2.2.2/24"}
    }

    Note: This requires a firewall reboot to apply changes.
    """
    from firewall_manager import edit_firewall
    from persistence import get_user_firewall
    from validation import validate_cidr_ip

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    inside_interface = data.get('inside_interface')
    outside_interface = data.get('outside_interface')

    if not name:
        return web.json_response({'error': 'Firewall name is required'}, status=400)

    if not inside_interface and not outside_interface:
        return web.json_response({'error': 'No changes specified'}, status=400)

    # Validate: must be a user-added firewall
    firewall = get_user_firewall(name, USER_FIREWALLS_PATH)
    if not firewall:
        return web.json_response({
            'error': f"Firewall '{name}' not found"
        }, status=400)

    # Validate IP formats if provided
    if inside_interface and inside_interface.get('ip'):
        valid, error = validate_cidr_ip(inside_interface['ip'])
        if not valid:
            return web.json_response({'error': f'Inside interface: {error}'}, status=400)

    if outside_interface and outside_interface.get('ip'):
        valid, error = validate_cidr_ip(outside_interface['ip'])
        if not valid:
            return web.json_response({'error': f'Outside interface: {error}'}, status=400)

    try:
        logger.info(f"Editing VyOS firewall: {name}")

        result = edit_firewall(name, inside_interface, outside_interface)

        logger.info(f"Successfully edited VyOS firewall: {name}")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error editing firewall {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/delete-firewall')
async def delete_firewall_endpoint(request):
    """
    Delete a VyOS firewall VM.

    Request body: { "name": "fw1" }
    """
    from firewall_manager import delete_firewall
    from persistence import get_user_firewall, remove_user_firewall

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()

    if not name:
        return web.json_response({'error': 'Firewall name is required'}, status=400)

    # Validate: must be a user-added firewall
    firewall = get_user_firewall(name, USER_FIREWALLS_PATH)
    if not firewall:
        return web.json_response({
            'error': f"Firewall '{name}' not found"
        }, status=400)

    try:
        logger.info(f"Deleting VyOS firewall: {name}")

        # Delete the VM
        result = delete_firewall(name)

        # Remove from persistence
        remove_user_firewall(name, USER_FIREWALLS_PATH)

        logger.info(f"Successfully deleted VyOS firewall: {name}")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error deleting firewall {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# ============================================================================
# VeloCloud Endpoints
# ============================================================================

@routes.get('/velo-status')
async def velo_status(request):
    """
    Get current VeloCloud device count and availability.

    Returns count of existing devices by type and whether more can be added.
    Also returns whether VeloCloud feature is enabled.
    """
    from velo_manager import get_velo_status

    try:
        status = get_velo_status()
        return web.json_response(status)
    except Exception as e:
        logger.error(f"Error getting VeloCloud status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/velo-devices')
async def velo_devices(request):
    """
    List all VeloCloud devices with their status.

    Query parameters:
        device_type: Optional filter by device type (edge, gateway, orchestrator)
    """
    from velo_manager import list_velo_devices

    try:
        devices = list_velo_devices()

        # Apply filter if specified
        device_type_filter = request.query.get('device_type')
        if device_type_filter:
            devices = [d for d in devices if d.get('device_type') == device_type_filter.lower()]

        return web.json_response({'devices': devices})
    except Exception as e:
        logger.error(f"Error listing VeloCloud devices: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-velo-device')
async def add_velo_device(request):
    """
    Create a new VeloCloud device VM.

    Request body: {
        "name": "edge1",
        "device_type": "edge",  # edge, gateway, or orchestrator
        "mgmt_ip": "192.168.0.60",
        "connections": [
            {
                "local_port": "wan1",
                "target_device": "spine1",
                "target_port": "Ethernet8"
            }
        ],
        "interface_ips": {
            "wan1": "10.1.1.1/24",
            "lan": "10.2.2.1/24"
        }
    }
    """
    from velo_manager import create_velo_device
    from persistence import (
        save_user_velo_device_pending,
        update_user_velo_device_status,
        remove_user_velo_device
    )
    from interface_manager import creation_lock
    from validation import (
        validate_velo_name, validate_velo_limit, validate_velo_enabled,
        validate_velo_device_type, validate_velo_device_type_enabled,
        validate_cidr_ip, get_available_ips
    )
    from config import (
        DNSMASQ_PATH, USER_NODES_PATH, USER_HOSTS_PATH,
        USER_FIREWALLS_PATH, USER_VELO_PATH, get_topo_build_path,
        MAX_VELO_EDGE_PER_TOPOLOGY, MAX_VELO_GATEWAY_PER_TOPOLOGY,
        MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY
    )

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()
    device_type = data.get('device_type', '')
    mgmt_ip = data.get('mgmt_ip', '')
    connections = data.get('connections', [])
    interface_ips = data.get('interface_ips', {})
    gateway_config = data.get('gateway_config', {})  # Gateway-specific: vco, activation_code, eth0/eth1 config
    edge_config = data.get('edge_config', {})  # Edge-specific: vco, activation_code, GE1-GE8 interface config
    orchestrator_config = data.get('orchestrator_config', {})  # Orchestrator-specific: eth0/eth1 network config

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)
    if not device_type:
        return web.json_response({'error': 'Device type is required'}, status=400)
    if not mgmt_ip:
        return web.json_response({'error': 'Management IP is required'}, status=400)

    # Validate device type before proceeding
    valid, error = validate_velo_device_type(device_type)
    if not valid:
        return web.json_response({'error': error}, status=400)

    # Validate interface IPs format if provided
    for iface_name, ip in interface_ips.items():
        valid, error = validate_cidr_ip(ip)
        if not valid:
            return web.json_response({
                'error': f'{iface_name} interface: {error}'
            }, status=400)

    try:
        # Acquire creation lock to prevent concurrent creates from racing
        with creation_lock(f'add-velo:{name}'):
            topo_build_path = get_topo_build_path()

            # Validate VeloCloud is enabled
            valid, error = validate_velo_enabled()
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate this device type is enabled
            valid, error = validate_velo_device_type_enabled(device_type)
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate device limit for this type
            valid, error = validate_velo_limit(
                device_type, USER_VELO_PATH,
                MAX_VELO_EDGE_PER_TOPOLOGY,
                MAX_VELO_GATEWAY_PER_TOPOLOGY,
                MAX_VELO_ORCHESTRATOR_PER_TOPOLOGY
            )
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate name across all device types including VeloCloud
            valid, error = validate_velo_name(
                name, topo_build_path, USER_NODES_PATH,
                USER_HOSTS_PATH, USER_FIREWALLS_PATH, USER_VELO_PATH
            )
            if not valid:
                return web.json_response({'error': error}, status=400)

            # Validate management IP is available
            # VeloCloud Orchestrator uses a fixed reserved IP (192.168.0.6)
            # which is excluded from the normal available pool
            from config import VELO_ORCHESTRATOR_MGMT_IP
            if device_type.lower() == 'orchestrator' and mgmt_ip == VELO_ORCHESTRATOR_MGMT_IP:
                # For orchestrator with fixed IP, just check it's not already in use
                # by an existing orchestrator
                from persistence import list_user_velo_devices
                existing_velo = list_user_velo_devices(USER_VELO_PATH)
                for velo_entry in existing_velo:
                    if isinstance(velo_entry, dict):
                        for _, info in velo_entry.items():
                            if info.get('mgmt_ip') == mgmt_ip and info.get('status') == 'active':
                                return web.json_response({
                                    'error': f'Management IP {mgmt_ip} is already in use by another orchestrator'
                                }, status=400)
            else:
                # Normal IP validation for edge/gateway devices
                available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
                if not any(entry['ip'] == mgmt_ip for entry in available):
                    return web.json_response({
                        'error': f'Management IP {mgmt_ip} is not available or already in use'
                    }, status=400)

            logger.info(f"Creating VeloCloud {device_type}: {name} (Mgmt IP: {mgmt_ip})")

            # SAVE-BEFORE-CREATE: Save pending entry BEFORE VM creation
            pending_entry = {
                'device_type': device_type.lower(),
                'mgmt_ip': mgmt_ip,
                'interface_ips': interface_ips,
                'connections': connections,
                'neighbors': []  # Will be updated after creation
            }
            save_user_velo_device_pending(name, device_type, pending_entry, USER_VELO_PATH)
            logger.debug(f"Saved pending VeloCloud entry for {name}")

            try:
                # Create the VeloCloud device VM
                result = create_velo_device(
                    name, device_type, mgmt_ip, connections, interface_ips,
                    gateway_config=gateway_config if device_type.lower() == 'gateway' else None,
                    edge_config=edge_config if device_type.lower() == 'edge' else None,
                    orchestrator_config=orchestrator_config if device_type.lower() == 'orchestrator' else None
                )

                # Build neighbors list for topology diagram connections
                neighbors = []
                for conn in result.get('connections', []):
                    if conn.get('target_device'):
                        neighbors.append({
                            'neighborDevice': conn['target_device'],
                            'neighborPort': conn.get('target_port', ''),
                            'port': conn.get('local_port', '')
                        })

                # Update persistence with actual info
                update_info = {
                    'connections': result.get('connections', []),
                    'neighbors': neighbors
                }
                update_user_velo_device_status(name, 'active', update_info, USER_VELO_PATH)

                logger.info(f"Successfully created VeloCloud {device_type}: {name}")

                return web.json_response({
                    'status': 'created',
                    'device': result,
                    'targets_reused_slots': result.get('targets_reused_slots', []),
                    'targets_need_reboot': result.get('targets_need_reboot', [])
                })

            except Exception as e:
                # VM creation failed - clean up any partially created resources
                logger.error(f"VeloCloud VM creation failed for {name}: {e}")

                # Try to clean up VM if it was created (prevents zombie VMs)
                from resource_manager import get_resource_manager
                rm = get_resource_manager()
                try:
                    if rm.vm_exists(name):
                        logger.info(f"Cleaning up partially created VeloCloud VM: {name}")
                        rm.destroy_vm(name, force=True)
                        rm.undefine_vm(name, force=True)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to clean up VeloCloud VM {name}: {cleanup_error}")

                # Remove the pending entry from persistence
                remove_user_velo_device(name, USER_VELO_PATH)
                raise

    except TimeoutError as e:
        logger.warning(f"Concurrent creation in progress, request queued timeout: {e}")
        return web.json_response({'error': 'Server busy with another creation request, please retry'}, status=503)
    except ValueError as e:
        logger.warning(f"Validation error creating VeloCloud device {name}: {e}")
        return web.json_response({'error': sanitize_error(e)}, status=400)
    except FileNotFoundError as e:
        logger.error(f"Required file not found for VeloCloud device {name}: {e}")
        return web.json_response({'error': 'Required file not found'}, status=500)
    except subprocess.CalledProcessError as e:
        logger.error(f"Command failed for VeloCloud device {name}: {e}")
        return web.json_response({'error': f'VM operation failed: {sanitize_error(e)}'}, status=500)
    except Exception as e:
        logger.error(f"Unexpected error creating VeloCloud device {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/delete-velo-device')
async def delete_velo_device_endpoint(request):
    """
    Delete a VeloCloud device VM.

    Request body: { "name": "edge1" }
    """
    from velo_manager import delete_velo_device
    from persistence import get_user_velo_device, remove_user_velo_device
    from config import USER_VELO_PATH

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    # Normalize name to lowercase for consistent VM naming
    name = data.get('name', '').lower()

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)

    # Security: Validate device name format
    import re
    if not re.match(r'^[a-zA-Z][a-zA-Z0-9_]*$', name):
        return web.json_response({'error': 'Invalid device name format'}, status=400)

    # Validate: must be a user-added VeloCloud device
    device = get_user_velo_device(name, USER_VELO_PATH)
    if not device:
        return web.json_response({
            'error': f"VeloCloud device '{name}' not found"
        }, status=400)

    try:
        logger.info(f"Deleting VeloCloud device: {name}")

        # Delete the VM
        result = delete_velo_device(name)

        # Remove from persistence
        remove_user_velo_device(name, USER_VELO_PATH)

        logger.info(f"Successfully deleted VeloCloud device: {name}")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error deleting VeloCloud device {name}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/cleanup-orphaned-bridges')
async def cleanup_orphaned_bridges(request):
    """
    Detect and clean up orphaned OVS bridges.

    An orphaned bridge is one that:
    - Matches user-created bridge naming patterns
    - Has 0-1 ports attached (meaning one or both VMs were deleted)

    This endpoint safely scans and removes only orphaned bridges,
    not system bridges or healthy connections.

    Returns:
        JSON with cleanup statistics and list of deleted bridges
    """
    from resource_manager import get_resource_manager

    try:
        logger.info("Starting orphaned bridge cleanup")
        resource_mgr = get_resource_manager()
        result = resource_mgr.cleanup_all_orphaned_bridges()

        logger.info(f"Orphaned bridge cleanup completed: {len(result.get('deleted', []))} bridges removed")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error cleaning orphaned bridges: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/bridge-status')
async def bridge_status(request):
    """
    Get status of all OVS bridges for diagnostics.

    Returns list of bridges with port counts to help identify orphans.
    """
    import subprocess

    try:
        # Get all bridges
        result = subprocess.run(
            ['ovs-vsctl', 'list-br'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            return web.json_response({'error': 'Failed to list bridges'}, status=500)

        bridges = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]

        system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}
        bridge_info = []

        for bridge in bridges:
            # Get port count
            ports_result = subprocess.run(
                ['ovs-vsctl', 'list-ports', bridge],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SHORT
            )

            ports = []
            if ports_result.returncode == 0:
                ports = [p.strip() for p in ports_result.stdout.split('\n') if p.strip()]

            is_system = bridge in system_bridges

            bridge_info.append({
                'name': bridge,
                'port_count': len(ports),
                'ports': ports,
                'is_system': is_system,
                'status': 'healthy' if len(ports) >= 2 or is_system else 'orphaned'
            })

        return web.json_response({
            'total_bridges': len(bridges),
            'bridges': bridge_info
        })

    except Exception as e:
        logger.error(f"Error getting bridge status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# =============================================================================
# Bridge Parsing API - Single Source of Truth for Bridge Name Parsing
# =============================================================================
# These endpoints expose bridge_utils.py functionality to other services
# (captureservice, uilanding) so they don't need duplicate parsing logic.


@routes.get('/bridge/parse/{bridge_name}')
async def parse_bridge_name_endpoint(request):
    """
    Parse a bridge name to extract device and port information.

    This endpoint exposes the bridge_utils.parse_bridge_name() function
    as an API for other services (captureservice, uilanding) to use.

    Path Parameters:
        bridge_name: OVS bridge name (e.g., 'le5x1-sp4x9')

    Returns:
        JSON with parsed device/port information:
        {
            "bridge_name": "le5x1-sp4x9",
            "source_device": "le5",
            "source_port": "1",
            "source_device_name": "leaf5",
            "source_port_name": "Ethernet1",
            "target_device": "sp4",
            "target_port": "9",
            "target_device_name": "spine4",
            "target_port_name": "Ethernet9"
        }
    """
    from bridge_utils import parse_bridge_name

    bridge_name = request.match_info.get('bridge_name', '')

    if not bridge_name:
        return web.json_response({'error': 'Bridge name is required'}, status=400)

    try:
        result = parse_bridge_name(bridge_name)
        result['bridge_name'] = bridge_name
        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error parsing bridge name '{bridge_name}': {e}")
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/bridge/parse')
async def parse_bridge_names_batch(request):
    """
    Parse multiple bridge names in a single request.

    This batch endpoint is more efficient when parsing many bridge names
    (e.g., for the capture panel which may have dozens of links).

    Request Body:
        {
            "bridge_names": ["le5x1-sp4x9", "fi1x1-bo1x5", ...]
        }

    Returns:
        JSON with parsed results for each bridge:
        {
            "results": {
                "le5x1-sp4x9": {
                    "source_device_name": "leaf5",
                    "source_port_name": "Ethernet1",
                    ...
                },
                "fi1x1-bo1x5": {
                    ...
                }
            }
        }
    """
    from bridge_utils import parse_bridge_name

    try:
        data = await request.json()
    except Exception:
        return web.json_response({'error': 'Invalid JSON'}, status=400)

    bridge_names = data.get('bridge_names', [])

    if not bridge_names:
        return web.json_response({'error': 'bridge_names array is required'}, status=400)

    if not isinstance(bridge_names, list):
        return web.json_response({'error': 'bridge_names must be an array'}, status=400)

    # Limit batch size to prevent abuse
    MAX_BATCH_SIZE = 100
    if len(bridge_names) > MAX_BATCH_SIZE:
        return web.json_response(
            {'error': f'Maximum batch size is {MAX_BATCH_SIZE}'},
            status=400
        )

    try:
        results = {}
        for bridge_name in bridge_names:
            if isinstance(bridge_name, str) and bridge_name:
                results[bridge_name] = parse_bridge_name(bridge_name)

        return web.json_response({'results': results})

    except Exception as e:
        logger.error(f"Error in batch bridge parsing: {e}")
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/bridge/abbreviations')
async def get_device_abbreviations(request):
    """
    Get the canonical device abbreviation mapping.

    This endpoint returns the DEVICE_ABBREVIATIONS dictionary from
    bridge_utils.py - the single source of truth for device abbreviations.

    Useful for:
    - Debugging bridge name issues
    - Documentation
    - Client-side validation

    Returns:
        JSON with abbreviation mapping:
        {
            "abbreviations": {
                "sp": "spine",
                "le": "leaf",
                "bo": "borderleaf",
                ...
            },
            "legacy": ["bl", "gw", "fw"]
        }
    """
    from bridge_utils import get_abbreviation_mapping, is_legacy_abbreviation

    try:
        abbreviations = get_abbreviation_mapping()

        # Separate legacy abbreviations for clarity
        legacy = [abbrev for abbrev in abbreviations if is_legacy_abbreviation(abbrev)]

        return web.json_response({
            'abbreviations': abbreviations,
            'legacy': legacy,
            'note': 'Legacy abbreviations are supported for parsing but not generated'
        })

    except Exception as e:
        logger.error(f"Error getting abbreviations: {e}")
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/bridges')
async def get_all_bridges_parsed(request):
    """
    Get all OVS bridges with parsed device/port information.

    This is the primary endpoint for captureservice and UILanding to get
    bridge information for the packet capture panel. Returns all bridges
    with pre-parsed device and port names.

    System bridges (oob_mgmt, br0, br-mgmt, vmgmt, etc.) are excluded.

    Returns:
        JSON with bridges array:
        {
            "bridges": [
                {
                    "name": "le5x1-sp4x9",
                    "source_device": "le5",
                    "source_port": "1",
                    "source_device_name": "leaf5",
                    "source_port_name": "Ethernet1",
                    "target_device": "sp4",
                    "target_port": "9",
                    "target_device_name": "spine4",
                    "target_port_name": "Ethernet9"
                },
                ...
            ],
            "count": 42
        }
    """
    import subprocess
    from bridge_utils import parse_bridge_name

    try:
        # Get all bridges from OVS
        result = subprocess.run(
            ['ovs-vsctl', 'list-br'],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_DEFAULT
        )

        if result.returncode != 0:
            return web.json_response({'error': 'Failed to list bridges'}, status=500)

        all_bridges = [b.strip() for b in result.stdout.strip().split('\n') if b.strip()]

        # Filter out system bridges
        system_bridges = {'oob_mgmt', 'br0', 'br1', 'br-mgmt', 'br-ext', 'vmgmt'}
        link_bridges = [b for b in all_bridges if b not in system_bridges]

        # Parse each bridge and include parsed info
        bridges = []
        for bridge_name in link_bridges:
            parsed = parse_bridge_name(bridge_name)
            parsed['name'] = bridge_name
            bridges.append(parsed)

        return web.json_response({
            'bridges': bridges,
            'count': len(bridges)
        })

    except Exception as e:
        logger.error(f"Error getting bridges: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/reconcile')
async def reconcile_resources_dry_run(request):
    """
    Check for resource inconsistencies (dry run - no changes made).

    Returns a report of:
    - Orphan entries: Persistence records without corresponding VMs
    - Zombie VMs: VMs stuck in 'creating' status
    - Orphan bridges: OVS bridges with missing VM attachments

    Use POST /reconcile to actually fix the issues.
    """
    from resource_manager import get_resource_manager

    try:
        logger.info("Running resource reconciliation (dry run)")
        resource_mgr = get_resource_manager()
        result = resource_mgr.reconcile_resources(dry_run=True)

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error during reconciliation: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/reconcile')
async def reconcile_resources_fix(request):
    """
    Fix resource inconsistencies.

    This will:
    - Remove orphan persistence entries (records without VMs)
    - Clean up orphaned bridges

    Note: Zombie VMs (stuck in 'creating') are reported but require manual
    intervention as they may need investigation.

    Returns a report of fixed issues.
    """
    from resource_manager import get_resource_manager

    try:
        logger.info("Running resource reconciliation (fixing issues)")
        resource_mgr = get_resource_manager()
        result = resource_mgr.reconcile_resources(dry_run=False)

        logger.info(f"Reconciliation complete: {len(result.get('fixed', []))} issues fixed")

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error during reconciliation: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# ============================================================================
# Orphaned Interface Slots Endpoints
# ============================================================================

@routes.get('/orphaned-slots')
async def get_orphaned_slots(request):
    """
    Get all orphaned interface slots.

    Orphaned slots are interfaces that were preserved when user devices
    were deleted, to prevent vEOS interface renumbering. They can be
    reused when adding new devices.

    Query parameters:
        device: Optional filter by device name

    Returns:
        JSON with orphaned slots by device and summary counts
    """
    from orphaned_interfaces import (
        list_all_orphaned_slots,
        get_orphaned_slots_for_device,
        count_orphaned_slots
    )

    try:
        device_filter = request.query.get('device')

        # Validate device name if provided (security: prevent injection attacks)
        if device_filter:
            from validation import validate_device_name
            is_valid, error = validate_device_name(device_filter)
            if not is_valid:
                return web.json_response({'error': f'Invalid device name: {error}'}, status=400)

            # Get slots for specific device
            slots = get_orphaned_slots_for_device(device_filter)
            result = {
                'success': True,
                'device': device_filter,
                'orphaned_slots': slots,
                'count': len(slots)
            }
        else:
            # Get all slots
            all_slots = list_all_orphaned_slots()
            counts = count_orphaned_slots()

            result = {
                'success': True,
                'orphaned_slots': all_slots,
                'summary': counts
            }

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error getting orphaned slots: {e}", exc_info=True)
        return web.json_response({
            'success': False,
            'error': sanitize_error(e)
        }, status=500)


@routes.post('/cleanup-orphaned-slots')
async def cleanup_orphaned_slots(request):
    """
    Clean up orphaned interface slots.

    This endpoint can:
    1. Clear all orphaned slots (for full cleanup)
    2. Clear slots for a specific device
    3. Optionally detach the interfaces from VMs before clearing

    Request body (optional):
        {
            "device_name": "spine1",  # Optional: clear specific device only
            "truly_detach": false     # Optional: detach interfaces before clearing
        }

    Returns:
        JSON with cleanup results
    """
    from orphaned_interfaces import (
        clear_all_orphaned_slots,
        clear_orphaned_slots_for_device,
        get_orphaned_slots_for_device,
        list_all_orphaned_slots
    )
    from interface_manager import detach_interface_from_vm

    try:
        # Parse request body (may be empty)
        try:
            data = await request.json()
        except Exception:
            data = {}

        device_name = data.get('device_name')
        truly_detach = data.get('truly_detach', False)

        # Validate device name if provided (security: prevent injection attacks)
        if device_name:
            from validation import validate_device_name
            is_valid, error = validate_device_name(device_name)
            if not is_valid:
                return web.json_response({'error': f'Invalid device name: {error}'}, status=400)

        result = {
            'success': True,
            'slots_cleared': 0,
            'interfaces_detached': 0,
            'errors': []
        }

        if device_name:
            # Clean specific device
            logger.info(f"Cleaning orphaned slots for device: {device_name}")

            if truly_detach:
                slots = get_orphaned_slots_for_device(device_name)
                for slot in slots:
                    try:
                        mac = slot.get('mac_address')
                        if mac:
                            detach_interface_from_vm(device_name, mac)
                            result['interfaces_detached'] += 1
                    except Exception as e:
                        result['errors'].append({
                            'device': device_name,
                            'mac': slot.get('mac_address'),
                            'error': str(e)
                        })

            cleared = clear_orphaned_slots_for_device(device_name)
            result['slots_cleared'] = cleared
            result['device'] = device_name

        else:
            # Clean all devices
            logger.info("Cleaning all orphaned slots")

            if truly_detach:
                all_slots = list_all_orphaned_slots()
                for dev_name, slots in all_slots.items():
                    for slot in slots:
                        try:
                            mac = slot.get('mac_address')
                            if mac:
                                detach_interface_from_vm(dev_name, mac)
                                result['interfaces_detached'] += 1
                        except Exception as e:
                            result['errors'].append({
                                'device': dev_name,
                                'mac': slot.get('mac_address'),
                                'error': str(e)
                            })

            cleared = clear_all_orphaned_slots()
            result['slots_cleared'] = cleared

        logger.info(
            f"Orphaned slots cleanup complete: {result['slots_cleared']} slots cleared, "
            f"{result['interfaces_detached']} interfaces detached"
        )

        return web.json_response(result)

    except Exception as e:
        logger.error(f"Error cleaning orphaned slots: {e}", exc_info=True)
        return web.json_response({
            'success': False,
            'error': sanitize_error(e)
        }, status=500)


@routes.get('/orphaned-slots/validate')
async def validate_orphaned_slots(request):
    """
    Validate orphaned slots against actual VM state.

    Checks:
    1. Devices exist in libvirt
    2. Interfaces with recorded MACs exist on devices
    3. No duplicate slot numbers per device

    Returns:
        JSON with validation results and any issues found
    """
    from orphaned_interfaces import validate_orphaned_slots as do_validate

    try:
        logger.info("Validating orphaned interface slots")
        result = do_validate()

        return web.json_response({
            'success': True,
            'validation': result
        })

    except Exception as e:
        logger.error(f"Error validating orphaned slots: {e}", exc_info=True)
        return web.json_response({
            'success': False,
            'error': sanitize_error(e)
        }, status=500)


# VeloCloud Orchestrator Web UI Proxy
@routes.get('/vco-proxy')
@routes.get('/vco-proxy/{path:.*}')
async def vco_proxy_get(request):
    """Proxy GET requests to VeloCloud Orchestrator web UI."""
    return await _vco_proxy(request, 'GET')


@routes.post('/vco-proxy')
@routes.post('/vco-proxy/{path:.*}')
async def vco_proxy_post(request):
    """Proxy POST requests to VeloCloud Orchestrator web UI."""
    return await _vco_proxy(request, 'POST')


@routes.put('/vco-proxy/{path:.*}')
async def vco_proxy_put(request):
    """Proxy PUT requests to VeloCloud Orchestrator web UI."""
    return await _vco_proxy(request, 'PUT')


@routes.delete('/vco-proxy/{path:.*}')
async def vco_proxy_delete(request):
    """Proxy DELETE requests to VeloCloud Orchestrator web UI."""
    return await _vco_proxy(request, 'DELETE')


async def _vco_proxy(request, method: str):
    """
    Forward requests to VeloCloud Orchestrator.

    Finds the VCO from user_velo.yaml, gets its management IP,
    and proxies requests to https://<mgmt_ip>/<path>.
    """
    import aiohttp
    import ssl
    from persistence import list_user_velo_devices
    from config import USER_VELO_PATH

    try:
        # Find the VCO from user_velo.yaml
        devices = list_user_velo_devices(USER_VELO_PATH)
        vco_ip = None

        for device_entry in devices:
            if isinstance(device_entry, dict):
                for name, info in device_entry.items():
                    if info.get('device_type', '').lower() == 'orchestrator':
                        vco_ip = info.get('mgmt_ip')
                        if vco_ip and vco_ip != 'N/A':
                            break
            if vco_ip:
                break

        if not vco_ip:
            return web.json_response({
                'error': 'No VeloCloud Orchestrator found. Please add one first.'
            }, status=404)

        # Get path from request
        path = request.match_info.get('path', '')
        query_string = request.query_string

        # Build target URL
        target_url = f"https://{vco_ip}/{path}"
        if query_string:
            target_url = f"{target_url}?{query_string}"

        logger.info(f"VCO Proxy: {method} {target_url}")

        # Create SSL context that doesn't verify certificates.
        # SECURITY NOTE: This is acceptable for training environments where VCO uses
        # self-signed certificates. In production, proper certificate validation should
        # be used. The VCO is on an isolated lab network not exposed to the internet.
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Prepare headers (forward relevant ones)
        headers = {}
        for header in ['Content-Type', 'Accept', 'Authorization', 'Cookie']:
            if header in request.headers:
                headers[header] = request.headers[header]

        # Get request body for POST/PUT
        body = None
        if method in ('POST', 'PUT'):
            body = await request.read()

        # Create connector with SSL context
        connector = aiohttp.TCPConnector(ssl=ssl_context)

        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.request(
                method,
                target_url,
                headers=headers,
                data=body,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                # Read response
                content = await resp.read()

                # Build response with same status and relevant headers
                response = web.Response(
                    body=content,
                    status=resp.status
                )

                # Forward relevant response headers
                for header in ['Content-Type', 'Set-Cookie', 'Location']:
                    if header in resp.headers:
                        response.headers[header] = resp.headers[header]

                return response

    except aiohttp.ClientError as e:
        logger.error(f"VCO Proxy error: {e}")
        return web.json_response({
            'error': f'Failed to connect to VCO: {str(e)}'
        }, status=502)
    except Exception as e:
        logger.error(f"VCO Proxy error: {e}", exc_info=True)
        return web.json_response({
            'error': sanitize_error(e)
        }, status=500)


# ============================================================================
# Helper functions
# ============================================================================

def get_available_ips_internal():
    """Return available IPs for use by other handlers without going through HTTP."""
    from validation import get_available_ips
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path
    topo_build_path = get_topo_build_path()
    return get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)


# ============================================================================
# CloudEOS Endpoints
# ============================================================================

@routes.get('/cloudeos-status')
async def cloudeos_status(request):
    """GET /cloudeos-status - Return CloudEOS device count and availability."""
    try:
        status = get_cloudeos_status()
        return web.json_response(status)
    except Exception as e:
        logger.error(f"Error getting CloudEOS status: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-cloudeos')
async def add_cloudeos(request):
    """POST /add-cloudeos - Create a CloudEOS VM."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    name = data.get('name')
    ip = data.get('ip')
    device_type = data.get('device_type', 'other')
    connections = data.get('connections', [])

    if not name or not ip:
        return web.json_response({'error': 'name and ip are required'}, status=400)

    try:
        result = create_cloudeos(name=name, ip=ip, device_type=device_type, connections=connections)
        if result.get('status') == 'error':
            return web.json_response(result, status=400)
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Error creating CloudEOS: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/delete-cloudeos')
async def delete_cloudeos_endpoint(request):
    """POST /delete-cloudeos - Delete a CloudEOS VM."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    name = data.get('name')
    if not name:
        return web.json_response({'error': 'name is required'}, status=400)

    try:
        result = delete_cloudeos(name=name)
        if result.get('status') == 'error':
            return web.json_response(result, status=400)
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Error deleting CloudEOS: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# ============================================================================
# WAN CloudEOS Endpoints
# ============================================================================

@routes.get('/wan-cloudeos-preview')
async def wan_cloudeos_preview(request):
    """GET /wan-cloudeos-preview - Preview D1/D2 deployment details."""
    from validation import get_topo_nodes
    from interface_manager import find_next_available_port
    from persistence import get_user_cloudeos_device
    from config import get_topo_build_path

    try:
        topo_build_path = get_topo_build_path()
        topo_nodes = get_topo_nodes(topo_build_path)

        # Find PE1 and PE2 nodes (returned as flat dicts with 'name' key)
        pe1 = None
        pe2 = None
        for node in topo_nodes:
            if node.get('name', '').upper() == 'PE1':
                pe1 = node
            elif node.get('name', '').upper() == 'PE2':
                pe2 = node

        if not pe1 or not pe2:
            return web.json_response({'error': 'PE1 and PE2 not found in topology'}, status=400)

        # Check if D1/D2 already exist
        d1_exists = get_user_cloudeos_device('D1') is not None
        d2_exists = get_user_cloudeos_device('D2') is not None

        if d1_exists or d2_exists:
            existing = []
            if d1_exists:
                existing.append('D1')
            if d2_exists:
                existing.append('D2')
            return web.json_response({
                'error': f'{", ".join(existing)} already deployed',
                'd1_exists': d1_exists,
                'd2_exists': d2_exists
            }, status=400)

        # Find available ports on PE1/PE2
        pe1_port = find_next_available_port('PE1')
        pe2_port = find_next_available_port('PE2')

        # Get available IPs
        available_ips = get_available_ips_internal()
        d1_ip = available_ips[0]['ip'] if len(available_ips) > 0 else None
        d2_ip = available_ips[1]['ip'] if len(available_ips) > 1 else None

        return web.json_response({
            'status': 'ready',
            'd1': {'name': 'D1', 'ip': d1_ip, 'target': 'PE1', 'target_port': pe1_port},
            'd2': {'name': 'D2', 'ip': d2_ip, 'target': 'PE2', 'target_port': pe2_port},
            'targets_need_reboot': ['PE1', 'PE2']
        })
    except Exception as e:
        logger.error(f"Error previewing WAN CloudEOS: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-wan-cloudeos')
async def add_wan_cloudeos(request):
    """POST /add-wan-cloudeos - Deploy D1 and D2 CloudEOS nodes connected to PE1 and PE2."""
    from validation import get_topo_nodes
    from interface_manager import find_next_available_port
    from persistence import get_user_cloudeos_device
    from config import get_topo_build_path

    try:
        topo_build_path = get_topo_build_path()
        topo_nodes = get_topo_nodes(topo_build_path)
        node_names = {n.get('name', '').upper() for n in topo_nodes}

        if 'PE1' not in node_names or 'PE2' not in node_names:
            return web.json_response({'error': 'PE1 and PE2 not found in topology'}, status=400)

        # Check D1/D2 don't already exist
        if get_user_cloudeos_device('D1') is not None:
            return web.json_response({'error': 'D1 already deployed'}, status=400)
        if get_user_cloudeos_device('D2') is not None:
            return web.json_response({'error': 'D2 already deployed'}, status=400)

        # Get available IPs
        available_ips = get_available_ips_internal()
        if len(available_ips) < 2:
            return web.json_response({'error': 'Not enough IPs available'}, status=400)

        d1_ip = available_ips[0]['ip']
        d2_ip = available_ips[1]['ip']
        pe1_port = find_next_available_port('PE1')
        pe2_port = find_next_available_port('PE2')

        # Create D1 connected to PE1
        d1_result = create_cloudeos(
            name='D1', ip=d1_ip, device_type='pe',
            connections=[{'target_device': 'PE1', 'target_port': pe1_port, 'local_port': 'Ethernet1'}]
        )

        if d1_result.get('status') == 'error':
            return web.json_response(
                {'error': f'Failed to create D1: {d1_result.get("message")}'},
                status=500
            )

        # Create D2 connected to PE2
        d2_result = create_cloudeos(
            name='D2', ip=d2_ip, device_type='pe',
            connections=[{'target_device': 'PE2', 'target_port': pe2_port, 'local_port': 'Ethernet1'}]
        )

        if d2_result.get('status') == 'error':
            # Rollback D1
            logger.warning("D2 creation failed, rolling back D1")
            delete_cloudeos('D1')
            return web.json_response({
                'error': f'Failed to create D2 (D1 rolled back): {d2_result.get("message")}'
            }, status=500)

        # Merge reboot lists from both deployments
        all_need_reboot = list(set(
            d1_result.get('targets_need_reboot', []) + d2_result.get('targets_need_reboot', [])
        ))
        all_reused = list(set(
            d1_result.get('targets_reused_slots', []) + d2_result.get('targets_reused_slots', [])
        ))

        return web.json_response({
            'status': 'success',
            'd1': d1_result,
            'd2': d2_result,
            'targets_need_reboot': all_need_reboot,
            'targets_reused_slots': all_reused
        })

    except Exception as e:
        logger.error(f"Error deploying WAN CloudEOS: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


# ============================================================================
# Link Management Endpoints
# ============================================================================

@routes.get('/user-links')
async def get_user_links_endpoint(request):
    """GET /user-links - List user-added links between topology nodes."""
    try:
        links = get_user_links()
        return web.json_response({'links': links})
    except Exception as e:
        logger.error(f"Error getting user links: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/add-link')
async def add_link_endpoint(request):
    """POST /add-link - Add a link between two original topology nodes."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    source_device = data.get('source_device')
    source_port = data.get('source_port')
    target_device = data.get('target_device')
    target_port = data.get('target_port')

    if not all([source_device, source_port, target_device, target_port]):
        return web.json_response(
            {'error': 'source_device, source_port, target_device, target_port required'},
            status=400
        )

    try:
        result = add_link(source_device, source_port, target_device, target_port)
        if result.get('status') == 'error':
            return web.json_response(result, status=400)
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Error adding link: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.post('/remove-link')
async def remove_link_endpoint(request):
    """POST /remove-link - Remove a user-added link between topology nodes."""
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    source_device = data.get('source_device')
    source_port = data.get('source_port')
    target_device = data.get('target_device')
    target_port = data.get('target_port')

    if not all([source_device, source_port, target_device, target_port]):
        return web.json_response(
            {'error': 'source_device, source_port, target_device, target_port required'},
            status=400
        )

    try:
        result = remove_link(source_device, source_port, target_device, target_port)
        if result.get('status') == 'error':
            return web.json_response(result, status=400)
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Error removing link: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


@routes.get('/available-ports/{device}')
async def available_ports(request):
    """GET /available-ports/{device} - List free ports on a topology device."""
    try:
        device = request.match_info['device']
        ports = get_available_ports(device)
        return web.json_response({'device': device, 'ports': ports})
    except Exception as e:
        logger.error(f"Error getting available ports: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


async def on_startup(app):
    """Called when the aiohttp server starts.

    Performs startup cleanup and initiates background image pre-staging.
    """
    from image_prestage import start_background_prestaging
    from persistence import (
        cleanup_stale_user_hosts,
        cleanup_stale_user_firewalls,
        cleanup_stale_velo_devices
    )
    from config import USER_HOSTS_PATH, USER_FIREWALLS_PATH, USER_VELO_PATH
    from orphaned_interfaces import cleanup_stale_orphaned_interfaces

    # Clean up any stale device entries from crashed creations
    # This prevents orphaned 'creating' entries from blocking new device creation
    total_cleaned = 0

    try:
        cleaned = cleanup_stale_user_hosts(USER_HOSTS_PATH)
        total_cleaned += cleaned
    except Exception as e:
        logger.warning(f"Nodebuilder startup: Failed to clean stale hosts: {e}")

    try:
        cleaned = cleanup_stale_user_firewalls(USER_FIREWALLS_PATH)
        total_cleaned += cleaned
    except Exception as e:
        logger.warning(f"Nodebuilder startup: Failed to clean stale firewalls: {e}")

    try:
        cleaned = cleanup_stale_velo_devices(USER_VELO_PATH)
        total_cleaned += cleaned
    except Exception as e:
        logger.warning(f"Nodebuilder startup: Failed to clean stale VeloCloud devices: {e}")

    if total_cleaned > 0:
        logger.info(f"Nodebuilder startup: Cleaned up {total_cleaned} stale device entry/entries")

    # Clean up interfaces pointing to deleted OVS bridges
    # This prevents VMs from failing to start with "Cannot get interface MTU" errors
    try:
        orphan_result = cleanup_stale_orphaned_interfaces()
        if orphan_result['detached_count'] > 0:
            logger.info(
                f"Nodebuilder startup: Detached {orphan_result['detached_count']} stale interface(s) "
                f"from {orphan_result['devices_cleaned']}"
            )
        if orphan_result['errors']:
            for err in orphan_result['errors']:
                logger.warning(f"Nodebuilder startup: Orphan cleanup issue: {err}")
    except Exception as e:
        logger.warning(f"Nodebuilder startup: Failed to clean stale orphaned interfaces: {e}")

    logger.info("Nodebuilder startup: Initiating background image pre-staging")
    await start_background_prestaging()


async def on_cleanup(app):
    """Called when the aiohttp server is shutting down.

    Cancels any in-progress image downloads gracefully.
    """
    from image_prestage import cancel_prestaging

    await cancel_prestaging()


def create_app():
    """Create and configure the application"""
    app = web.Application()
    app.add_routes(routes)

    # Register lifecycle hooks for background image pre-staging
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    # Security: No CORS headers needed - this service is only accessed
    # via the uilanding proxy (NodeBuilderProxyHandler), not directly from browsers.
    # Removing permissive CORS prevents direct browser access to this service.

    return app


def main():
    """Main entry point"""
    from config import log_gcp_config

    logger.info(f"Starting Nodebuilder Service on {SERVICE_HOST}:{SERVICE_PORT}")

    # Log GCP bucket configuration for debugging
    log_gcp_config()

    app = create_app()
    # Bind to configured host (0.0.0.0 for Docker bridge access)
    web.run_app(app, host=SERVICE_HOST, port=SERVICE_PORT)


if __name__ == '__main__':
    main()

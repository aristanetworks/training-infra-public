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
    FIREWALL_OUTSIDE_PORT
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
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

    # Validate name
    name = data.get('name', '')
    name_valid, name_error = validate_device_name(name, topo_build_path, USER_NODES_PATH)
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

    name = data.get('name', '')
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

            # Validate name
            name_valid, name_error = validate_device_name(name, topo_build_path, USER_NODES_PATH)
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
                    }
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
    from persistence import get_user_node, remove_user_node, remove_neighbor_references
    from resource_manager import get_resource_manager
    from config import USER_NODES_PATH

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    name = data.get('name', '')

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

        # Clean up neighbor references in other user nodes
        # (prevents orphaned references when a node connected to other user nodes is deleted)
        orphaned_refs_removed = remove_neighbor_references(name, USER_NODES_PATH)
        if orphaned_refs_removed > 0:
            result['orphaned_references_removed'] = orphaned_refs_removed

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

    name = data.get('name', '')
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
    from interface_manager import create_ovs_bridge, attach_interface_to_vm, generate_bridge_name
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    template_id = data.get('template_id', '')
    name_prefix = data.get('name_prefix', '')
    external_connections = data.get('external_connections', [])
    ip_assignments = data.get('ip_assignments', {})
    impairments = data.get('impairments', {})

    if not template_id:
        return web.json_response({'error': 'template_id is required'}, status=400)

    # Get template
    template = get_template_by_id(template_id)
    if not template:
        return web.json_response({'error': f"Unknown template: {template_id}"}, status=400)

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
        return web.json_response({'error': str(e)}, status=400)

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
                continue

            # Calculate port numbers for both ends
            from_port = f"Ethernet{len(from_node['connections']) + 1}"
            to_port = f"Ethernet{len(to_node['connections']) + 1}"

            # Generate bridge name for this internal connection
            bridge_name = generate_bridge_name(from_name, from_port, to_name, to_port)

            try:
                # Create OVS bridge
                create_ovs_bridge(bridge_name)

                # Attach interface to both VMs
                attach_interface_to_vm(from_name, bridge_name)
                attach_interface_to_vm(to_name, bridge_name)

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
                # Continue with other connections - partial cluster is still useful

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

        return web.json_response({
            'status': 'created',
            'cluster': template_id,
            'prefix': name_prefix,
            'nodes': created_nodes,
            'internal_bridges': internal_bridges,
            'impairments_to_apply': applied_impairments
        })

    except Exception as e:
        logger.error(f"Error creating cluster {template_id}: {e}", exc_info=True)
        return web.json_response({'error': sanitize_error(e)}, status=500)


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
                timeout=30
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

    name = data.get('name', '')
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
                    'host': result
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
        return web.json_response({'error': str(e)}, status=400)
    except FileNotFoundError as e:
        logger.error(f"Required file not found for host {name}: {e}")
        return web.json_response({'error': f'Required file not found: {e}'}, status=500)
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

    name = data.get('name', '')

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

    name = data.get('name', '')
    mgmt_ip = data.get('mgmt_ip', '')
    inside_interface = data.get('inside_interface', {})
    outside_interface = data.get('outside_interface', {})

    if not name:
        return web.json_response({'error': 'Firewall name is required'}, status=400)
    if not mgmt_ip:
        return web.json_response({'error': 'Management IP is required'}, status=400)
    if not inside_interface.get('ip'):
        return web.json_response({'error': 'Inside interface IP is required'}, status=400)
    if not outside_interface.get('ip'):
        return web.json_response({'error': 'Outside interface IP is required'}, status=400)

    # Validate interface IPs (CIDR format) - can do before lock
    for iface_name, iface in [('inside', inside_interface), ('outside', outside_interface)]:
        valid, error = validate_cidr_ip(iface.get('ip', ''))
        if not valid:
            return web.json_response({
                'error': f'{iface_name.capitalize()} interface: {error}'
            }, status=400)

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
                    'firewall': result
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
        return web.json_response({'error': str(e)}, status=400)
    except FileNotFoundError as e:
        logger.error(f"Required file not found for firewall {name}: {e}")
        return web.json_response({'error': f'Required file not found: {e}'}, status=500)
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

    name = data.get('name', '')
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

    name = data.get('name', '')

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
            timeout=30
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
                timeout=10
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


def create_app():
    """Create and configure the application"""
    app = web.Application()
    app.add_routes(routes)

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

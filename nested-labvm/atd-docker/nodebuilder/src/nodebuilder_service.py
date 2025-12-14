#!/usr/bin/env python3
"""
Nodebuilder Service - Dynamic vEOS node addition for ATD labs

This service provides a REST API for dynamically adding vEOS nodes
to running KVM-based ATD labs. It runs on port 8090 with host network
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
"""

import logging
import os
from aiohttp import web

from config import (
    SERVICE_PORT,
    SERVICE_HOST,
    MAX_TOTAL_NODES,
    MAX_CONNECTIONS_PER_NODE
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('nodebuilder')

routes = web.RouteTableDef()


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
        logger.error(f"Error getting available IPs: {e}")
        return web.json_response({'error': str(e)}, status=500)


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
        logger.error(f"Error getting existing nodes: {e}")
        return web.json_response({'error': str(e)}, status=500)


@routes.get('/target-devices')
async def target_devices(request):
    """Return devices available as connection targets with available ports"""
    from interface_manager import get_target_devices_with_ports

    try:
        devices = get_target_devices_with_ports()
        return web.json_response({'devices': devices})
    except Exception as e:
        logger.error(f"Error getting target devices: {e}")
        return web.json_response({'error': str(e)}, status=500)


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

    # Validate IP is in available list
    ip = data.get('ip', '')
    if ip:
        available = get_available_ips(DNSMASQ_PATH, topo_build_path, USER_NODES_PATH)
        if not any(entry['ip'] == ip for entry in available):
            errors.append(f"IP {ip} is not available or already in use")
    else:
        errors.append("IP address is required")

    return web.json_response({
        'valid': len(errors) == 0,
        'errors': errors
    })


@routes.post('/add-node')
async def add_node(request):
    """Create new vEOS VM"""
    from validation import get_mac_for_ip, validate_device_name, get_available_ips
    from vm_manager import create_veos_node
    from persistence import save_user_node
    from config import DNSMASQ_PATH, USER_NODES_PATH, get_topo_build_path

    try:
        data = await request.json()
    except Exception as e:
        return web.json_response({'error': f'Invalid JSON: {e}'}, status=400)

    name = data.get('name', '')
    ip = data.get('ip', '')
    connections = data.get('connections', [])

    if not name:
        return web.json_response({'error': 'Device name is required'}, status=400)
    if not ip:
        return web.json_response({'error': 'IP address is required'}, status=400)
    if not connections:
        return web.json_response({'error': 'At least one connection is required'}, status=400)

    # Security: Validate connections count
    if not isinstance(connections, list):
        return web.json_response({'error': 'Connections must be a list'}, status=400)

    if len(connections) > MAX_CONNECTIONS_PER_NODE:
        return web.json_response({
            'error': f'Maximum {MAX_CONNECTIONS_PER_NODE} connections per node'
        }, status=400)

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

    try:
        # Get MAC from dnsmasq
        mac = get_mac_for_ip(ip, DNSMASQ_PATH)
        if not mac:
            return web.json_response({'error': f'No MAC found for IP {ip}'}, status=400)

        logger.info(f"Creating vEOS node: {name} with IP {ip}, MAC {mac}")

        # Create the VM (uses fixed CPU/RAM from config)
        result = create_veos_node(name, ip, mac, connections)

        # Save to persistence
        node_data = {
            name: {
                'ip_addr': ip,
                'sys_mac': mac,
                'platform': 'veos',
                'user_added': True,
                'neighbors': [
                    {
                        'neighborDevice': c['target_device'],
                        'neighborPort': c['target_port'],
                        'port': c['local_port']
                    } for c in result['connections']
                ]
            }
        }
        save_user_node(node_data, USER_NODES_PATH)

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
        logger.error(f"Error creating node {name}: {e}")
        return web.json_response({'error': str(e)}, status=500)


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
        logger.error(f"Error getting user nodes status: {e}")
        return web.json_response({'error': str(e)}, status=500)


@routes.post('/restore-user-nodes')
async def restore_user_nodes(request):
    """
    Restore all user-added nodes.

    This starts VMs that are defined but not running, and ensures
    their OVS bridges exist. Called after the original topology
    is up and running.
    """
    from vm_manager import restore_all_user_nodes

    try:
        result = restore_all_user_nodes()
        return web.json_response(result)
    except Exception as e:
        logger.error(f"Error restoring user nodes: {e}")
        return web.json_response({'error': str(e)}, status=500)


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
    logger.info(f"Starting Nodebuilder Service on {SERVICE_HOST}:{SERVICE_PORT}")
    app = create_app()
    # Bind to configured host (0.0.0.0 for Docker bridge access)
    web.run_app(app, host=SERVICE_HOST, port=SERVICE_PORT)


if __name__ == '__main__':
    main()

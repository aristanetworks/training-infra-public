"""
Topology and device API handlers for UILanding.

Handlers:
  - TopologyAPIHandler       — Cytoscape.js topology graph (topo_build.yml + user files)
  - DevicesAPIHandler        — Device list grouped by type
  - DeviceTypesAPIHandler    — Device type metadata export for frontend
  - InterfaceStatsAPIHandler — Interface statistics via eAPI with rate calculation
  - DeviceStatusAPIHandler   — Device reachability via eAPI/ping (thread pool)
  - RunningConfigAPIHandler      — Running config via eAPI (single device)
  - BulkRunningConfigAPIHandler  — Bulk running config download as zip (all EOS devices)

Utility functions (moved from uilanding.py, only used by these handlers):
  - _get_topo_build_data()
  - get_device_ip_from_sources()
  - get_all_devices()
  - invalidate_devices_cache()

Module-level globals are set via initialize() to receive config values from uilanding.py.
"""

from datetime import datetime
import json
import os
import socket
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
import io
import zipfile

import pyeapi
from ruamel.yaml import YAML

from device_types import DeviceTypeConfig
from handlers.auth import BaseHandler
from utils import safe_log, normalize_device_name, pS

# ---------------------------------------------------------------------------
# Module-level config — set by initialize() at startup
# ---------------------------------------------------------------------------
TOPO = ''
EOS_TYPE = 'veos'
TITLE = 'Arista Training Lab'
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'


def initialize(topo, eos_type, title, atd_access_path):
    """Set module-level configuration values.  Call once at startup from uilanding.py."""
    global TOPO, EOS_TYPE, TITLE, ATD_ACCESS_PATH
    TOPO = topo
    EOS_TYPE = eos_type
    TITLE = title
    ATD_ACCESS_PATH = atd_access_path


# ---------------------------------------------------------------------------
# Module-level device caches (private to this module)
# ---------------------------------------------------------------------------

# Cache for topo_build.yml data (loaded once on first use)
_TOPO_BUILD_CACHE = None
# Cache for merged device list from all sources
_ALL_DEVICES_CACHE = None
# Track user file modification times for cache invalidation
_USER_NODES_MTIME = 0
_USER_HOSTS_MTIME = 0
_USER_FIREWALLS_MTIME = 0
_USER_VELO_MTIME = 0


# ---------------------------------------------------------------------------
# Device utility functions
# ---------------------------------------------------------------------------

def invalidate_devices_cache():
    """
    Invalidate the devices cache.
    Call when user nodes/hosts/firewalls/velo devices are added or removed.
    """
    global _ALL_DEVICES_CACHE, _USER_NODES_MTIME, _USER_HOSTS_MTIME
    global _USER_FIREWALLS_MTIME, _USER_VELO_MTIME
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

    for name, info in all_devices.items():
        if name.lower() == device_name_lower:
            ip = info.get('ip', '')
            return ip if ip else None

    return None


def get_all_devices():
    """
    Get all devices from topo_build.yml, user_nodes.yaml, user_hosts.yaml,
    user_firewalls.yaml, and user_velo.yaml.

    Returns:
        dict: {device_name: {'ip': str, 'user_added': bool, 'device_category': str, ...}}

    Device names are normalized to consistent capitalization.
    Cache is auto-invalidated when any user file changes.
    """
    global _ALL_DEVICES_CACHE, _USER_NODES_MTIME, _USER_HOSTS_MTIME
    global _USER_FIREWALLS_MTIME, _USER_VELO_MTIME
    # Note: _ALL_DEVICES_CACHE also assigned below after building devices dict

    user_nodes_path = '/etc/atd/user_nodes.yaml'
    user_hosts_path = '/etc/atd/user_hosts.yaml'
    user_firewalls_path = '/etc/atd/user_firewalls.yaml'
    user_velo_path = '/etc/atd/user_velo.yaml'

    # Auto-invalidate cache when any user file is modified
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

    # Authoritative topology source
    topo_data = _get_topo_build_data()
    if topo_data and 'nodes' in topo_data:
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for name, info in node_entry.items():
                    ip = info.get('ip_addr', '')
                    if ip == 'N/A':
                        ip = ''
                    display_name = normalize_device_name(name)
                    devices[display_name] = {
                        'ip': ip,
                        'user_added': False,
                        'vm_name': name,
                        'device_category': 'node',
                    }

    # Merge user-added nodes
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
                                'device_category': 'node',
                            }
                pS(f"Merged {len(user_data['nodes'])} user-added nodes into device list")
    except Exception as e:
        safe_log('warning', f'Error loading user_nodes.yaml: {e}', event='config', handler='get_all_devices')

    # Merge user-added Linux desktop hosts
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
                                'supports_novnc': True,
                            }
                pS(f"Merged {len(hosts_data['hosts'])} user-added hosts into device list")
    except Exception as e:
        safe_log('warning', f'Error loading user_hosts.yaml: {e}', event='config', handler='get_all_devices')

    # Merge user-added VyOS firewalls
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
                                'device_category': 'firewall',
                            }
                pS(f"Merged {len(firewalls_data['firewalls'])} user-added firewalls into device list")
    except Exception as e:
        safe_log('warning', f'Error loading user_firewalls.yaml: {e}', event='config', handler='get_all_devices')

    # Merge user-added VeloCloud devices
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
                                'supports_webui': False,
                            }
                pS(f"Merged {len(velo_data['devices'])} user-added VeloCloud devices into device list")
    except Exception as e:
        safe_log('warning', f'Error loading user_velo.yaml: {e}', event='config', handler='get_all_devices')

    _ALL_DEVICES_CACHE = devices
    pS(f"Cached {len(devices)} devices from topo_build.yml + user files")
    return devices


# ---------------------------------------------------------------------------
# TopologyAPIHandler
# ---------------------------------------------------------------------------

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
        match = re.search(r'-?(DC\d+|dc\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        if device_name.startswith('GW') and len(device_name) >= 3 and device_name[2].isdigit():
            dc_num = device_name[2]
            return f'DC{dc_num}'

        return ''

    @staticmethod
    def extract_isp_provider(device_name):
        """
        Extract ISP provider identifier from device name.
        E.g., 'core1-ISP1' -> 'ISP1', 'core2-ISP2' -> 'ISP2', 'internet' -> ''
        Used for grouping ISP devices by provider in the topology layout.
        """
        import re
        match = re.search(r'-?(ISP\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return ''

    @staticmethod
    def get_sort_key(device_name):
        """
        Generate a sort key for natural ordering of device names.
        E.g., spine1, spine2, spine10 sorts correctly (not spine1, spine10, spine2)
        """
        import re
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

        if has_p_routers and has_pe_routers and not has_spines:
            return 'wan'

        return 'datacenter'

    @staticmethod
    def extract_site_number(device_name):
        """
        Extract site number from device name for WAN topology positioning.
        Returns 1 for "left" side, 2 for "right" side, 0 for center (P routers)
        """
        import re
        match = re.search(r'[-_]?(\d+)$', device_name)
        if match:
            num = int(match.group(1))
            return 1 if num % 2 == 1 else 2
        return 0

    @staticmethod
    def calculate_wan_positions(nodes_data, edges_data):
        """
        Calculate positions for WAN topology with P-router mesh in center.
        Layout: Left customers -> Left PEs -> P mesh -> Right PEs -> Right customers
        """
        NODE_SPACING_X = 185
        NODE_SPACING_Y = 135
        COLUMN_SPACING = 260
        PADDING = 100

        adjacency = {}
        for node in nodes_data:
            adjacency[node['data']['id']] = set()
        for edge in edges_data:
            src = edge['data']['source']
            tgt = edge['data']['target']
            if src in adjacency and tgt in adjacency:
                adjacency[src].add(tgt)
                adjacency[tgt].add(src)

        p_routers = []
        pe_routers = []
        customer_devices = []
        other_devices = []

        for node in nodes_data:
            dtype = node['data']['device_type']
            if dtype == 'p':
                p_routers.append(node)
            elif dtype == 'pe':
                pe_routers.append(node)
            elif DeviceTypeConfig.is_wan_customer_device(dtype) or dtype == 'leaf':
                customer_devices.append(node)
            else:
                other_devices.append(node)

        pe_sides = {}
        for pe in pe_routers:
            pe_id = pe['data']['id']
            pe_neighbors = adjacency.get(pe_id, set())

            side_votes = []
            for neighbor in pe_neighbors:
                neighbor_node = next((n for n in nodes_data if n['data']['id'] == neighbor), None)
                if neighbor_node and neighbor_node['data']['device_type'] != 'p':
                    site = TopologyAPIHandler.extract_site_number(neighbor)
                    if site > 0:
                        side_votes.append(site)

            pe_site = TopologyAPIHandler.extract_site_number(pe_id)
            if pe_site > 0:
                side_votes.append(pe_site)

            if side_votes:
                pe_sides[pe_id] = 1 if side_votes.count(1) >= side_votes.count(2) else 2
            else:
                pe_sides[pe_id] = pe_site if pe_site > 0 else 1

        customer_sides = {}
        for cust in customer_devices:
            cust_id = cust['data']['id']
            cust_neighbors = adjacency.get(cust_id, set())

            for neighbor in cust_neighbors:
                if neighbor in pe_sides:
                    customer_sides[cust_id] = pe_sides[neighbor]
                    break

            if cust_id not in customer_sides:
                site = TopologyAPIHandler.extract_site_number(cust_id)
                customer_sides[cust_id] = site if site > 0 else 1

        left_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 1]
        left_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 1) == 1]
        right_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 2) == 2]
        right_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 2]

        for group in [left_customers, left_pes, p_routers, right_pes, right_customers]:
            group.sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        columns = [left_customers, left_pes, p_routers, right_pes, right_customers]
        column_names = ['left_cust', 'left_pe', 'p_mesh', 'right_pe', 'right_cust']

        max_height = max(len(col) for col in columns) if columns else 1

        current_x = PADDING
        for col_idx, column in enumerate(columns):
            if not column:
                continue

            col_height = len(column) * NODE_SPACING_Y
            start_y = PADDING + (max_height * NODE_SPACING_Y - col_height) / 2

            for row_idx, node in enumerate(column):
                node['position'] = {
                    'x': current_x,
                    'y': start_y + row_idx * NODE_SPACING_Y
                }
                node['data']['wan_column'] = column_names[col_idx]

            current_x += COLUMN_SPACING

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
        topo_type = TopologyAPIHandler.detect_topology_type(nodes_data)

        if topo_type == 'wan' and edges_data:
            return TopologyAPIHandler.calculate_wan_positions(nodes_data, edges_data)

        # Standard datacenter layout (tier-based)
        tiers = {}
        ISP_TIER = DeviceTypeConfig.get_tier('isp')

        for node in nodes_data:
            device_type = node['data']['device_type']
            tier = DeviceTypeConfig.get_tier(device_type)

            if tier == ISP_TIER:
                group_key = TopologyAPIHandler.extract_isp_provider(node['data']['id'])
            else:
                group_key = TopologyAPIHandler.extract_datacenter(node['data']['id'])

            if tier not in tiers:
                tiers[tier] = {}
            if group_key not in tiers[tier]:
                tiers[tier][group_key] = []
            tiers[tier][group_key].append(node)

        for tier in tiers:
            for group_key in tiers[tier]:
                tiers[tier][group_key].sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        NODE_SPACING_X = 170
        NODE_SPACING_Y = 185
        DC_SPACING = 120
        PADDING = 100

        max_width = 0
        for tier in tiers:
            tier_width = 0
            group_keys = sorted(tiers[tier].keys())
            for i, gk in enumerate(group_keys):
                tier_width += len(tiers[tier][gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:
                    tier_width += DC_SPACING
            max_width = max(max_width, tier_width)

        if max_width == 0:
            max_width = NODE_SPACING_X

        row_index = 0
        for tier_num in sorted(tiers.keys()):
            tier_groups = tiers[tier_num]
            group_keys = sorted(tier_groups.keys())

            tier_width = 0
            for i, gk in enumerate(group_keys):
                tier_width += len(tier_groups[gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:
                    tier_width += DC_SPACING

            start_x = PADDING + (max_width - tier_width) / 2
            current_x = start_x

            for i, group_key in enumerate(group_keys):
                group_nodes = tier_groups[group_key]

                for node in group_nodes:
                    node['position'] = {
                        'x': current_x,
                        'y': PADDING + row_index * NODE_SPACING_Y
                    }
                    if tier_num == ISP_TIER:
                        node['data']['isp_provider'] = group_key if group_key else 'default'
                        node['data']['datacenter'] = 'shared'
                    else:
                        node['data']['datacenter'] = group_key if group_key else 'default'
                    current_x += NODE_SPACING_X

                if i < len(group_keys) - 1:
                    current_x += DC_SPACING

            row_index += 1

        return nodes_data

    def parse_topology(self, topo_path):
        """
        Parse topo_build.yml and return Cytoscape.js formatted data.

        Returns:
            dict: Success with 'data' key containing topology
            dict: Error with 'error' and 'error_type' keys
        """
        try:
            with open(topo_path, 'r') as f:
                topo_data = YAML().load(f)
        except FileNotFoundError:
            return {'error': f'Topology file not found: {topo_path}', 'error_type': 'not_found'}
        except PermissionError:
            return {'error': f'Permission denied accessing: {topo_path}', 'error_type': 'permission'}
        except Exception as e:
            safe_log('error', f'Error parsing topology file: {e}', event='config', handler='parse_topology')
            return {'error': f'Failed to parse topology file: {str(e)}', 'error_type': 'parse_error'}

        # Merge user-added nodes from user_nodes.yaml
        user_nodes_path = '/etc/atd/user_nodes.yaml'
        try:
            if os.path.exists(user_nodes_path):
                with open(user_nodes_path, 'r') as f:
                    user_data = YAML().load(f)
                if user_data and 'nodes' in user_data and user_data['nodes']:
                    if topo_data is None:
                        topo_data = {'nodes': []}
                    if 'nodes' not in topo_data:
                        topo_data['nodes'] = []
                    topo_data['nodes'].extend(user_data['nodes'])
                    pS(f"Merged {len(user_data['nodes'])} user-added nodes from {user_nodes_path}")
        except Exception as e:
            safe_log('warning', f'Error loading user_nodes.yaml: {e}', event='config', handler='parse_topology')

        # Merge user-added hosts from user_hosts.yaml
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
                    for host_entry in hosts_data['hosts']:
                        if isinstance(host_entry, dict):
                            for name, info in host_entry.items():
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', info.get('ip_addr', 'N/A')),
                                    'device_type': 'linux_host',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged {len(hosts_data['hosts'])} user-added hosts from {user_hosts_path}")
        except Exception as e:
            safe_log('warning', f'Error loading user_hosts.yaml: {e}', event='config', handler='parse_topology')

        # Merge user-added firewalls from user_firewalls.yaml
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
                    for fw_entry in firewalls_data['firewalls']:
                        if isinstance(fw_entry, dict):
                            for name, info in fw_entry.items():
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', info.get('ip_addr', 'N/A')),
                                    'device_type': 'firewall',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
                                topo_data['nodes'].append({name: node_info})
                    pS(f"Merged {len(firewalls_data['firewalls'])} user-added firewalls from {user_firewalls_path}")
        except Exception as e:
            safe_log('warning', f'Error loading user_firewalls.yaml: {e}', event='config', handler='parse_topology')

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
                    for velo_entry in velo_data['devices']:
                        if isinstance(velo_entry, dict):
                            for name, info in velo_entry.items():
                                device_type = info.get('device_type', 'edge')
                                node_info = {
                                    'ip_addr': info.get('mgmt_ip', 'N/A'),
                                    'device_type': f'velo_{device_type}',
                                    'user_added': True,
                                    'neighbors': info.get('neighbors', [])
                                }
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
            safe_log('warning', f'Error loading user_velo.yaml: {e}', event='config', handler='parse_topology')

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
            safe_log('warning', f'Error loading user_cloudeos.yaml: {e}', event='config', handler='parse_topology')

        # Merge user-added links from user_links.yaml
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
            safe_log('warning', f'Error loading user_links.yaml: {e}', event='config', handler='parse_topology')

        if topo_data is None:
            return {'error': 'Topology file is empty', 'error_type': 'empty_file'}

        if 'nodes' not in topo_data:
            return {'error': 'Topology file missing "nodes" key', 'error_type': 'invalid_format'}

        if not topo_data['nodes']:
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
        edge_set = set()

        # Build normalization mapping: raw_name -> normalized_name
        valid_node_names = set()
        name_mapping = {}
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for raw_name in node_entry.keys():
                    normalized = normalize_device_name(raw_name)
                    valid_node_names.add(raw_name)
                    name_mapping[raw_name] = normalized

        for node_entry in topo_data['nodes']:
            if not isinstance(node_entry, dict):
                pS(f"Warning: Invalid node entry format (not a dict): {node_entry}")
                continue

            for raw_device_name, device_info in node_entry.items():
                display_name = name_mapping.get(raw_device_name, raw_device_name)

                if not isinstance(device_info, dict):
                    pS(f"Warning: Invalid device info for {raw_device_name} (not a dict)")
                    continue

                device_type = device_info.get('device_type') or self.classify_device_type(raw_device_name)
                ip_addr = device_info.get('ip_addr', 'N/A')
                sys_mac = device_info.get('sys_mac', 'N/A')
                neighbors = device_info.get('neighbors', [])
                user_added = device_info.get('user_added', False)

                if not isinstance(neighbors, list):
                    pS(f"Warning: Invalid neighbors format for {raw_device_name}")
                    neighbors = []

                ports = []
                for neighbor in neighbors:
                    if not isinstance(neighbor, dict):
                        continue

                    neighbor_device_raw = neighbor.get('neighborDevice', '')
                    neighbor_device_display = name_mapping.get(neighbor_device_raw, neighbor_device_raw)

                    ports.append({
                        'port': neighbor.get('port', ''),
                        'neighbor': neighbor_device_display,
                        'neighbor_port': neighbor.get('neighborPort', '')
                    })

                    if neighbor_device_raw and neighbor_device_raw in valid_node_names:
                        device_port = neighbor.get('port') or ''
                        neighbor_port = neighbor.get('neighborPort') or ''

                        port_pair = tuple(sorted([device_port, neighbor_port]))
                        edge_key = (tuple(sorted([display_name, neighbor_device_display])), port_pair)

                        if edge_key not in edge_set:
                            edge_set.add(edge_key)

                            sorted_devices = edge_key[0]
                            if display_name == sorted_devices[0]:
                                source_node = display_name
                                target_node = neighbor_device_display
                                source_port = device_port
                                target_port = neighbor_port
                            else:
                                source_node = neighbor_device_display
                                target_node = display_name
                                source_port = neighbor_port
                                target_port = device_port

                            edge_id = f"{source_node}-{target_node}-{source_port}-{target_port}"
                            edge_data = {
                                'id': edge_id,
                                'source': source_node,
                                'target': target_node,
                                'source_port': source_port,
                                'target_port': target_port
                            }
                            edge_entry = {'data': edge_data}
                            if neighbor.get('user_added'):
                                edge_data['user_added'] = True
                                edge_entry['classes'] = 'edge-user-added'
                            edges.append(edge_entry)
                    elif neighbor_device_raw:
                        pS(f"Warning: Skipping edge {display_name}->{neighbor_device_display}: target not in topology")

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
                        'vm_name': raw_device_name
                    },
                    'classes': f"device-type-{device_type} status-unknown"
                })

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
        self.set_header("Access-Control-Max-Age", "86400")
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

            topo_path = f"/opt/atd/topologies/{TOPO}/topo_build.yml"
            result = self.parse_topology(topo_path)

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
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': f'Internal server error: {str(e)}'}))


# ---------------------------------------------------------------------------
# DevicesAPIHandler
# ---------------------------------------------------------------------------

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
            nodes = get_all_devices()

            groups = {}
            user_nodes_group = []
            user_hosts_group = []
            user_firewalls_group = []
            user_velocloud_group = []

            for device_name, device_info in nodes.items():
                is_user_added = device_info.get('user_added', False)
                device_category = device_info.get('device_category', 'node')

                supports_console = EOS_TYPE != 'container-labs'
                vm_name = device_info.get('vm_name', device_name)
                supports_novnc = device_info.get('supports_novnc', False)
                supports_webui = device_info.get('supports_webui', False)

                device_entry = {
                    'name': device_name,
                    'vmName': vm_name,
                    'ip': device_info.get('ip', ''),
                    'userAdded': is_user_added,
                    'supportsConsole': supports_console,
                    'supportsNoVnc': supports_novnc,
                    'supportsWebUI': supports_webui,
                }

                if is_user_added:
                    if device_category == 'host':
                        user_hosts_group.append(device_entry)
                    elif device_category == 'firewall':
                        user_firewalls_group.append(device_entry)
                    elif device_category == 'velocloud':
                        user_velocloud_group.append(device_entry)
                    else:
                        user_nodes_group.append(device_entry)
                else:
                    device_type = device_info.get('device_type') or DeviceTypeConfig.classify_device(device_name)
                    group_name = DeviceTypeConfig.get_group_name(device_type)

                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(device_entry)

            group_order = DeviceTypeConfig.get_all_group_names()

            result = []
            for group_name in group_order:
                if group_name in groups and groups[group_name]:
                    devices = sorted(groups[group_name], key=lambda x: x['name'])
                    result.append({'group': group_name, 'devices': devices})

            for group_name in sorted(groups.keys()):
                if group_name not in group_order and groups[group_name]:
                    devices = sorted(groups[group_name], key=lambda x: x['name'])
                    result.append({'group': group_name, 'devices': devices})

            if user_nodes_group:
                result.append({
                    'group': 'User Nodes',
                    'devices': sorted(user_nodes_group, key=lambda x: x['name'])
                })

            if user_hosts_group:
                result.append({
                    'group': 'Linux Hosts',
                    'devices': sorted(user_hosts_group, key=lambda x: x['name'])
                })

            if user_firewalls_group:
                result.append({
                    'group': 'Firewalls',
                    'devices': sorted(user_firewalls_group, key=lambda x: x['name'])
                })

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
            safe_log('error', f'Error in DevicesAPIHandler: file not found: {e}', event='error', handler='DevicesAPIHandler')
            self.set_status(503)
            self.write(json.dumps({
                'error': 'Device configuration not available',
                'detail': 'The topology configuration file could not be found. Please wait for the lab to finish initializing.',
                'retry': True
            }))

        except json.JSONDecodeError as e:
            safe_log('error', f'Error in DevicesAPIHandler: parse error: {e}', event='error', handler='DevicesAPIHandler')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({
                'error': 'Configuration error',
                'detail': 'The device configuration could not be parsed.',
                'retry': False
            }))

        except Exception as e:
            safe_log('error', f'Error in DevicesAPIHandler: {e}', event='error', handler='DevicesAPIHandler')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({
                'error': 'Internal server error',
                'detail': str(e),
                'retry': True
            }))


# ---------------------------------------------------------------------------
# DeviceTypesAPIHandler
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# InterfaceStatsAPIHandler
# ---------------------------------------------------------------------------

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

        safe_log('info', 'Interface stats requested', event='api', endpoint='interface_stats',
                 device=str(self.get_argument('device', 'unknown')))
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
            if 'Unauthorized' in error_str or 'Bad username' in error_str or 'authentication' in error_str.lower():
                safe_log('warning', f'InterfaceStatsAPIHandler: Auth failed for {device}',
                         event='api', handler='InterfaceStatsAPIHandler', device=str(device))
                self.write(json.dumps({
                    'device': device,
                    'interface': interface,
                    'status': 'unconfigured',
                    'error': 'Device reachable but authentication failed (not yet configured)'
                }))
            elif 'Interface does not exist' in error_str or 'Invalid input' in error_str:
                self.set_status(404)
                self.write(json.dumps({
                    'device': device,
                    'interface': interface,
                    'status': 'not_found',
                    'error': f'Interface {interface} does not exist on {device}'
                }))
            elif 'timed out' in error_str.lower() or 'connection timed out' in error_str.lower():
                self.write(json.dumps({
                    'device': device,
                    'interface': interface,
                    'status': 'down',
                    'error': f'Device {device} is unreachable'
                }))
            else:
                safe_log('error', f'InterfaceStatsAPIHandler error: {e}',
                         event='api', handler='InterfaceStatsAPIHandler')
                self.set_status(500)
                self.write(json.dumps({'error': error_str}))

    def get_interface_stats(self, device_name, interface_name):
        """Query EOS device for interface counters via eAPI."""
        cache_key = f"{device_name}:{interface_name}"
        current_time = time.time()

        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        with open(ATD_ACCESS_PATH, 'r') as f:
            host_yaml = YAML().load(f)
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=10
            )

            result = connection.execute([f"show interfaces {interface_name}"])

            interfaces = result.get('result', [{}])[0].get('interfaces', {})
            intf_data = interfaces.get(interface_name, {})

            if not intf_data:
                raise ValueError(f"Interface {interface_name} not found on {device_name}")

            counters = intf_data.get('interfaceCounters', {})
            bandwidth = intf_data.get('bandwidth', 0)

            rates = self.calculate_rates(cache_key, counters, current_time)

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
                    'in_packets': (counters.get('inUcastPkts', 0) +
                                   counters.get('inMulticastPkts', 0) +
                                   counters.get('inBroadcastPkts', 0)),
                    'out_packets': (counters.get('outUcastPkts', 0) +
                                    counters.get('outMulticastPkts', 0) +
                                    counters.get('outBroadcastPkts', 0)),
                    'in_errors': (counters.get('inErrors', 0) +
                                  counters.get('inputErrorsDetail', {}).get('crcErrors', 0)),
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

            with self._cache_lock:
                self._cache[cache_key] = (current_time, stats)

            return stats

        except (socket.timeout, OSError):
            raise ValueError(f"Connection timed out to {device_name} ({device_ip})")
        except pyeapi.eapilib.ConnectionError as e:
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

                    if in_octet_delta < 0:
                        in_octet_delta = current_counters.get('inOctets', 0)
                    if out_octet_delta < 0:
                        out_octet_delta = current_counters.get('outOctets', 0)

                    in_rate = (in_octet_delta * 8) / time_delta
                    out_rate = (out_octet_delta * 8) / time_delta
                else:
                    in_rate = out_rate = 0
            else:
                in_rate = out_rate = 0

            self._previous_counters[cache_key] = (current_time, {
                'inOctets': current_counters.get('inOctets', 0),
                'outOctets': current_counters.get('outOctets', 0)
            })

            return {'in_rate_bps': round(in_rate, 2), 'out_rate_bps': round(out_rate, 2)}


# ---------------------------------------------------------------------------
# DeviceStatusAPIHandler
# ---------------------------------------------------------------------------

class DeviceStatusAPIHandler(BaseHandler):
    """API endpoint to check device reachability via eAPI."""

    # Cache: {device: (timestamp, status)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 30  # seconds

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)

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
            statuses = self.check_all_devices()
            self.write(json.dumps({'devices': statuses}))

    def check_device_status(self, device_name):
        """Check if a single device is reachable. Uses eAPI for EOS devices, ping for hosts/firewalls."""
        cache_key = device_name
        current_time = time.time()

        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        all_devices = get_all_devices()
        device_info = all_devices.get(device_name, {})
        device_ip = device_info.get('ip', '')
        device_category = device_info.get('device_category', 'node')

        if not device_ip:
            device_ip = get_device_ip_from_sources(device_name)

        # Removed per-device status check log (100 logs/device/session = ~2400 logs noise)
        if not device_ip:
            return {
                'device': device_name,
                'status': 'unknown',
                'error': 'Device not found in topology'
            }

        if device_category in ('host', 'firewall'):
            result = self._check_device_via_ping(device_name, device_ip, device_category)
        else:
            result = self._check_device_via_eapi(device_name, device_ip)

        with self._cache_lock:
            self._cache[cache_key] = (current_time, result)

        return result

    def _check_device_via_ping(self, device_name, device_ip, device_category):
        """Check if a host or firewall is reachable via ping or TCP check."""
        import subprocess

        device_type_label = 'Linux Host' if device_category == 'host' else 'VyOS Firewall'

        try:
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
            safe_log('warning', f'Ping unavailable for {device_name}, trying TCP',
                     event='api', handler='DeviceStatusAPIHandler', device=str(device_name))
        except Exception as e:
            safe_log('warning', f'Ping failed for {device_name}: {e}, trying TCP',
                     event='api', handler='DeviceStatusAPIHandler', device=str(device_name))

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
            safe_log('error', f'TCP check failed for {device_name}: {e}',
                     event='api', handler='DeviceStatusAPIHandler', device=str(device_name))
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': f'Unreachable: {str(e)}',
                'last_check': datetime.now().isoformat()
            }

    def _check_device_via_eapi(self, device_name, device_ip):
        """Check if an EOS device is reachable via eAPI."""
        try:
            with open(ATD_ACCESS_PATH, 'r') as f:
                host_yaml = YAML().load(f)
            username = host_yaml['login_info']['jump_host']['user']
            password = host_yaml['login_info']['jump_host']['pw']
        except Exception as e:
            return {
                'device': device_name,
                'status': 'error',
                'error': f'Cannot read credentials: {e}'
            }

        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=5
            )

            result_cmd = connection.execute(['show version'])
            version = result_cmd.get('result', [{}])[0].get('version', 'unknown')

            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'up',
                'version': version,
                'last_check': datetime.now().isoformat()
            }

        except (socket.timeout, OSError):
            return {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': f'Connection timed out ({device_ip})',
                'last_check': datetime.now().isoformat()
            }
        except pyeapi.eapilib.ConnectionError as e:
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
        """Check status of all devices from topology sources using a thread pool."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        nodes = get_all_devices()
        statuses = {}

        # Removed 'Found N devices' log (fires every poll cycle, ~141/session)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.check_device_status, device_name): device_name
                for device_name in nodes.keys()
            }

            try:
                for future in as_completed(futures, timeout=60):
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
            except TimeoutError:
                for future, device_name in futures.items():
                    if device_name not in statuses:
                        statuses[device_name] = {
                            'device': device_name,
                            'status': 'down',
                            'error': 'Status check timed out',
                            'last_check': datetime.now().isoformat()
                        }

        return statuses


# ---------------------------------------------------------------------------
# RunningConfigAPIHandler
# ---------------------------------------------------------------------------

class RunningConfigAPIHandler(BaseHandler):
    """API endpoint to fetch running config from a device via eAPI."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Running config requested', event='api', endpoint='running_config',
                 device=str(self.get_argument('device', 'unknown')))
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
            safe_log('error', f'Error in RunningConfigAPIHandler: {e}',
                     event='error', handler='RunningConfigAPIHandler')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def get_running_config(self, device_name):
        """Query EOS device for running config via eAPI."""
        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        with open(ATD_ACCESS_PATH, 'r') as f:
            host_yaml = YAML().load(f)
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=15
            )

            result = connection.execute(['show running-config'], encoding='text')
            config_output = result.get('result', [{}])[0].get('output', '')

            return {
                'device': device_name,
                'config': config_output,
                'timestamp': datetime.now().isoformat()
            }

        except (socket.timeout, OSError):
            raise ValueError(f"Connection timed out to {device_name} ({device_ip})")
        except pyeapi.eapilib.ConnectionError as e:
            error_str = str(e)
            if 'Unauthorized' in error_str or 'Bad username' in error_str:
                raise ValueError(f"Unauthorized: Cannot authenticate to {device_name} ({device_ip}): {e}")
            raise ValueError(f"Cannot connect to {device_name} ({device_ip}): {e}")
        except pyeapi.eapilib.CommandError as e:
            raise ValueError(f"Command error on {device_name}: {e}")


# ---------------------------------------------------------------------------
# BulkRunningConfigAPIHandler
# ---------------------------------------------------------------------------

NON_EOS_DEVICE_TYPES = frozenset([
    'firewall', 'linux_host',
    'velo_edge', 'velo_gateway', 'velo_orchestrator',
])


class BulkRunningConfigAPIHandler(BaseHandler):
    """API endpoint to fetch running configs from all EOS devices as a zip."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.set_header('Content-Type', 'application/json')
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        safe_log('info', 'Bulk running config requested', event='api',
                 endpoint='running_config_bulk')
        self.set_header('Access-Control-Allow-Origin', '*')

        all_devices = get_all_devices()
        eos_devices = {
            name: info for name, info in all_devices.items()
            if info.get('device_type', '') not in NON_EOS_DEVICE_TYPES
            and info.get('ip')
        }

        if not eos_devices:
            safe_log('warning', 'No EOS devices found for bulk config download',
                     event='api', endpoint='running_config_bulk')
            self.set_status(404)
            self.set_header('Content-Type', 'application/json')
            self.write(json.dumps({'error': 'No EOS devices found in topology'}))
            return

        try:
            with open(ATD_ACCESS_PATH, 'r') as f:
                host_yaml = YAML().load(f)
            username = host_yaml['login_info']['jump_host']['user']
            password = host_yaml['login_info']['jump_host']['pw']
        except Exception as e:
            safe_log('error', f'Cannot read credentials for bulk config: {e}',
                     event='error', handler='BulkRunningConfigAPIHandler')
            self.set_status(500)
            self.set_header('Content-Type', 'application/json')
            self.write(json.dumps({'error': 'Internal error: cannot load device credentials'}))
            return

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(
                    self._fetch_single_config, name, info['ip'], username, password
                ): name
                for name, info in eos_devices.items()
            }
            results = [f.result() for f in futures]

        configs = {}
        errors = []
        for device_name, config_text, error in results:
            if config_text is not None:
                configs[device_name] = config_text
            else:
                errors.append(f'{device_name}: {error}')

        if not configs:
            safe_log('error', 'All devices failed during bulk config download',
                     event='error', handler='BulkRunningConfigAPIHandler',
                     device_count=len(eos_devices), errors='; '.join(errors))
            self.set_status(500)
            self.set_header('Content-Type', 'application/json')
            error_detail = '; '.join(errors)
            self.write(json.dumps({
                'error': f'Failed to fetch config from all devices: {error_detail}'
            }))
            return

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for device_name, config_text in sorted(configs.items()):
                zf.writestr(device_name, config_text)
            if errors:
                zf.writestr('_errors.txt', '\n'.join(sorted(errors)))

        if errors:
            self.set_header('X-Config-Errors', 'true')
            safe_log('warning', 'Bulk config download completed with errors',
                     event='api', endpoint='running_config_bulk',
                     succeeded=len(configs), failed=len(errors),
                     failed_devices='; '.join(sorted(e.split(':')[0] for e in errors)))
        else:
            safe_log('info', 'Bulk config download completed successfully',
                     event='api', endpoint='running_config_bulk',
                     device_count=len(configs))

        self.set_header('Content-Type', 'application/zip')
        self.set_header('Content-Disposition',
                        'attachment; filename="running-configs.zip"')
        self.write(buf.getvalue())

    @staticmethod
    def _fetch_single_config(device_name, device_ip, username, password):
        """Fetch running config from one device. Returns (name, config, error)."""
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=15
            )
            result = connection.execute(['show running-config'], encoding='text')
            config_output = result.get('result', [{}])[0].get('output', '')
            return (device_name, config_output, None)
        except (socket.timeout, OSError):
            return (device_name, None,
                    f'Connection timed out to {device_name} ({device_ip})')
        except pyeapi.eapilib.ConnectionError as e:
            return (device_name, None,
                    f'Cannot connect to {device_name} ({device_ip}): {e}')
        except pyeapi.eapilib.CommandError as e:
            return (device_name, None,
                    f'Command error on {device_name}: {e}')
        except Exception as e:
            return (device_name, None, f'Unexpected error on {device_name}: {e}')

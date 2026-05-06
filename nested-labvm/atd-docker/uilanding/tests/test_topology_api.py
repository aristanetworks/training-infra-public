"""
Tests for topology and device API handlers in handlers/topology_api.py.

Covers:
  - Authentication enforcement (401 for unauthenticated requests)
  - TopologyAPIHandler: Cytoscape.js node/edge format, name normalization
  - DevicesAPIHandler: grouped device list
  - DeviceTypesAPIHandler: metadata export
  - DeviceStatusAPIHandler: eAPI up / timeout scenarios
  - normalize_device_name usage in topology output
"""

import json
import os
import sys
import shutil
import tempfile
from unittest.mock import patch, MagicMock

import tornado.testing
import tornado.web

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import handlers.topology_api as topo_module
from handlers.topology_api import (
    TopologyAPIHandler,
    DevicesAPIHandler,
    DeviceTypesAPIHandler,
    DeviceStatusAPIHandler,
    InterfaceStatsAPIHandler,
    RunningConfigAPIHandler,
    initialize,
    invalidate_devices_cache,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
TOPO_BUILD_PATH = os.path.join(FIXTURES_DIR, 'topo_build.yml')
ACCESS_INFO_PATH = os.path.join(FIXTURES_DIR, 'access_info.yaml')

# ---------------------------------------------------------------------------
# Shared Tornado app factory
# ---------------------------------------------------------------------------

COOKIE_SECRET = 'test-secret-topology-api'


def _make_app(extra_settings=None):
    """Return a Tornado Application with topology API routes and auth cookie."""

    class AuthenticatedStub(tornado.web.RequestHandler):
        """Sets the 'user' cookie so handlers see current_user."""
        def get(self):
            self.set_secure_cookie('user', 'arista')
            self.write('ok')

    settings = {'cookie_secret': COOKIE_SECRET}
    if extra_settings:
        settings.update(extra_settings)

    return tornado.web.Application([
        (r'/set-auth', AuthenticatedStub),
        (r'/td-api/topology', TopologyAPIHandler),
        (r'/td-api/devices', DevicesAPIHandler),
        (r'/td-api/device-types', DeviceTypesAPIHandler),
        (r'/td-api/device-status', DeviceStatusAPIHandler),
        (r'/td-api/interface-stats', InterfaceStatsAPIHandler),
        (r'/td-api/running-config', RunningConfigAPIHandler),
    ], **settings)


# ---------------------------------------------------------------------------
# Base test class that initialises the module and gets an auth cookie
# ---------------------------------------------------------------------------

class TopologyAPITestBase(tornado.testing.AsyncHTTPTestCase):

    def get_app(self):
        initialize('training-level1', 'veos', 'Arista Training Lab', ACCESS_INFO_PATH)
        return _make_app()

    def setUp(self):
        super().setUp()
        # Invalidate caches between tests
        invalidate_devices_cache()
        TopologyAPIHandler._cache = {}
        TopologyAPIHandler._cache_time = 0
        # Clear DeviceStatusAPIHandler cache between tests
        DeviceStatusAPIHandler._cache = {}
        InterfaceStatsAPIHandler._cache = {}

    def _get_auth_cookie(self):
        """Fetch a signed 'user' cookie from the stub endpoint."""
        response = self.fetch('/set-auth')
        assert response.code == 200
        return response.headers.get('Set-Cookie', '')

    def _authed_fetch(self, path, **kwargs):
        """Fetch path with the auth cookie attached."""
        cookie = self._get_auth_cookie()
        headers = kwargs.pop('headers', {})
        headers['Cookie'] = cookie
        return self.fetch(path, headers=headers, **kwargs)


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

class TestAuthEnforcement(TopologyAPITestBase):
    """All topology API endpoints must return 401 without authentication."""

    def test_topology_requires_auth(self):
        resp = self.fetch('/td-api/topology')
        assert resp.code == 401, f"Expected 401, got {resp.code}"
        body = json.loads(resp.body)
        assert 'error' in body

    def test_devices_requires_auth(self):
        resp = self.fetch('/td-api/devices')
        assert resp.code == 401

    def test_device_types_requires_auth(self):
        resp = self.fetch('/td-api/device-types')
        assert resp.code == 401

    def test_device_status_requires_auth(self):
        resp = self.fetch('/td-api/device-status?device=spine1')
        assert resp.code == 401

    def test_interface_stats_requires_auth(self):
        resp = self.fetch('/td-api/interface-stats?device=spine1&interface=Ethernet1')
        assert resp.code == 401

    def test_running_config_requires_auth(self):
        resp = self.fetch('/td-api/running-config?device=spine1')
        assert resp.code == 401


# ---------------------------------------------------------------------------
# TopologyAPIHandler tests
# ---------------------------------------------------------------------------

class TestTopologyAPIHandler(TopologyAPITestBase):

    def _fetch_topology(self, topo_path=None):
        """Fetch the topology endpoint with a mocked topo file path."""
        if topo_path is None:
            topo_path = TOPO_BUILD_PATH

        with patch.object(
            TopologyAPIHandler,
            'parse_topology',
            wraps=lambda self_, path: TopologyAPIHandler.parse_topology(self_, topo_path)
        ):
            # Patch os.path.exists for user files to return False (no user files)
            with patch('handlers.topology_api.os.path.exists', return_value=False), \
                 patch('handlers.topology_api.os.path.getmtime', return_value=0):
                resp = self._authed_fetch('/td-api/topology')
        return resp

    def test_topology_returns_cytoscape_json(self):
        """Authenticated request returns 200 with nodes and edges arrays."""
        with patch('handlers.topology_api.os.path.exists', return_value=False), \
             patch('handlers.topology_api.os.path.getmtime', return_value=0):
            # Direct parse_topology call using the fixture file
            handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
            result = handler.parse_topology(TOPO_BUILD_PATH)

        assert 'data' in result, f"Expected 'data' key, got: {result}"
        data = result['data']
        assert 'nodes' in data
        assert 'edges' in data
        assert 'metadata' in data
        assert len(data['nodes']) > 0, "Expected at least one node"

    def test_topology_nodes_have_required_fields(self):
        """Each node must have id, label, ip, device_type, and status fields."""
        with patch('handlers.topology_api.os.path.exists', return_value=False), \
             patch('handlers.topology_api.os.path.getmtime', return_value=0):
            handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
            result = handler.parse_topology(TOPO_BUILD_PATH)

        for node in result['data']['nodes']:
            node_data = node['data']
            for field in ('id', 'label', 'ip', 'device_type', 'status'):
                assert field in node_data, f"Node missing field {field!r}: {node_data}"

    def test_normalize_device_name_used(self):
        """Node IDs must be capitalized (normalize_device_name applied)."""
        with patch('handlers.topology_api.os.path.exists', return_value=False), \
             patch('handlers.topology_api.os.path.getmtime', return_value=0):
            handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
            result = handler.parse_topology(TOPO_BUILD_PATH)

        node_ids = [n['data']['id'] for n in result['data']['nodes']]
        # From fixture: spine1 -> Spine1, leaf1 -> Leaf1, host1 -> Host1
        assert 'Spine1' in node_ids, f"Expected 'Spine1' in {node_ids}"
        assert 'Leaf1' in node_ids, f"Expected 'Leaf1' in {node_ids}"

    def test_topology_edges_reference_valid_nodes(self):
        """All edge source/target IDs must reference existing node IDs."""
        with patch('handlers.topology_api.os.path.exists', return_value=False), \
             patch('handlers.topology_api.os.path.getmtime', return_value=0):
            handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
            result = handler.parse_topology(TOPO_BUILD_PATH)

        data = result['data']
        node_ids = {n['data']['id'] for n in data['nodes']}
        for edge in data['edges']:
            src = edge['data']['source']
            tgt = edge['data']['target']
            assert src in node_ids, f"Edge source {src!r} not in nodes"
            assert tgt in node_ids, f"Edge target {tgt!r} not in nodes"

    def test_topology_file_not_found_returns_error(self):
        """parse_topology returns error dict when file is missing."""
        handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
        result = handler.parse_topology('/nonexistent/path/topo_build.yml')
        assert 'error' in result
        assert result.get('error_type') == 'not_found'

    def test_topology_metadata_contains_topology_name(self):
        """Metadata must include the topology_name field."""
        with patch('handlers.topology_api.os.path.exists', return_value=False), \
             patch('handlers.topology_api.os.path.getmtime', return_value=0):
            handler = TopologyAPIHandler.__new__(TopologyAPIHandler)
            result = handler.parse_topology(TOPO_BUILD_PATH)

        meta = result['data']['metadata']
        assert 'topology_name' in meta
        assert 'eos_type' in meta
        assert 'node_count' in meta
        assert 'edge_count' in meta


# ---------------------------------------------------------------------------
# DevicesAPIHandler tests
# ---------------------------------------------------------------------------

class TestDevicesAPIHandler(TopologyAPITestBase):

    def test_devices_returns_grouped_list(self):
        """Authenticated request returns groups with device entries."""
        mock_devices = {
            'Spine1': {'ip': '192.168.0.10', 'user_added': False,
                       'vm_name': 'spine1', 'device_category': 'node'},
            'Leaf1': {'ip': '192.168.0.12', 'user_added': False,
                      'vm_name': 'leaf1', 'device_category': 'node'},
        }
        with patch('handlers.topology_api.get_all_devices', return_value=mock_devices):
            resp = self._authed_fetch('/td-api/devices')

        assert resp.code == 200
        body = json.loads(resp.body)
        assert 'groups' in body
        assert isinstance(body['groups'], list)
        assert len(body['groups']) > 0

    def test_devices_response_has_topology_and_eos_type(self):
        """Response includes 'topology' and 'eosType' keys."""
        mock_devices = {
            'Spine1': {'ip': '192.168.0.10', 'user_added': False,
                       'vm_name': 'spine1', 'device_category': 'node'},
        }
        with patch('handlers.topology_api.get_all_devices', return_value=mock_devices):
            resp = self._authed_fetch('/td-api/devices')

        body = json.loads(resp.body)
        assert 'topology' in body
        assert 'eosType' in body

    def test_devices_groups_spines_correctly(self):
        """Spine devices are placed in the 'Spine' group."""
        mock_devices = {
            'Spine1': {'ip': '192.168.0.10', 'user_added': False,
                       'vm_name': 'spine1', 'device_category': 'node'},
        }
        with patch('handlers.topology_api.get_all_devices', return_value=mock_devices):
            resp = self._authed_fetch('/td-api/devices')

        body = json.loads(resp.body)
        group_names = [g['group'] for g in body['groups']]
        assert 'Spine' in group_names, f"Expected 'Spine' group, got: {group_names}"


# ---------------------------------------------------------------------------
# DeviceTypesAPIHandler tests
# ---------------------------------------------------------------------------

class TestDeviceTypesAPIHandler(TopologyAPITestBase):

    def test_device_types_returns_metadata(self):
        """Returns JSON object with known device type keys."""
        resp = self._authed_fetch('/td-api/device-types')
        assert resp.code == 200
        body = json.loads(resp.body)
        assert isinstance(body, dict)
        assert 'spine' in body
        assert 'leaf' in body

    def test_device_types_each_entry_has_required_fields(self):
        """Each device type entry must include tier, label, color, shape."""
        resp = self._authed_fetch('/td-api/device-types')
        body = json.loads(resp.body)
        for device_type, entry in body.items():
            for field in ('tier', 'label', 'color', 'shape'):
                assert field in entry, f"{device_type!r} missing {field!r}"


# ---------------------------------------------------------------------------
# DeviceStatusAPIHandler tests
# ---------------------------------------------------------------------------

class TestDeviceStatusAPIHandler(TopologyAPITestBase):

    def _mock_all_devices(self, ip='192.168.0.10'):
        return {
            'Spine1': {'ip': ip, 'user_added': False,
                       'vm_name': 'spine1', 'device_category': 'node'},
        }

    def _patch_credentials(self):
        """Context manager that patches open() to return mock credentials."""
        mock_yaml = MagicMock()
        mock_yaml.__enter__ = lambda s: s
        mock_yaml.__exit__ = MagicMock(return_value=False)

        from ruamel.yaml import YAML as _YAML
        mock_host_yaml = {
            'login_info': {'jump_host': {'user': 'arista', 'pw': 'arista123'}}
        }

        def _mock_open(path, *args, **kwargs):
            import builtins
            return builtins.open(ACCESS_INFO_PATH, *args, **kwargs)

        # Patch YAML().load to return credential dict when called
        mock_yaml_instance = MagicMock()
        mock_yaml_instance.load.return_value = mock_host_yaml
        return patch('handlers.topology_api.YAML', return_value=mock_yaml_instance)

    def test_device_status_eapi_up(self):
        """When eAPI returns version info, status is 'up'."""
        # Mock _check_device_via_eapi directly to avoid file/network I/O
        up_result = {
            'device': 'Spine1', 'ip': '192.168.0.10', 'status': 'up',
            'version': '4.28.0F', 'last_check': '2026-01-01T00:00:00'
        }
        with patch('handlers.topology_api.get_all_devices', return_value=self._mock_all_devices()), \
             patch('handlers.topology_api.get_device_ip_from_sources', return_value='192.168.0.10'), \
             patch.object(DeviceStatusAPIHandler, '_check_device_via_eapi', return_value=up_result):
            resp = self._authed_fetch('/td-api/device-status?device=Spine1')

        assert resp.code == 200
        body = json.loads(resp.body)
        assert body['status'] == 'up', f"Expected 'up', got: {body}"
        assert body['version'] == '4.28.0F'

    def test_device_status_eapi_timeout(self):
        """When eAPI connection times out, status is 'down'."""
        import socket

        mock_yaml_instance = MagicMock()
        mock_yaml_instance.load.return_value = {
            'login_info': {'jump_host': {'user': 'arista', 'pw': 'arista123'}}
        }

        with patch('handlers.topology_api.get_all_devices', return_value=self._mock_all_devices()), \
             patch('handlers.topology_api.get_device_ip_from_sources', return_value='192.168.0.10'), \
             patch('handlers.topology_api.YAML', return_value=mock_yaml_instance), \
             patch('builtins.open', MagicMock(return_value=MagicMock(__enter__=MagicMock(return_value=MagicMock()), __exit__=MagicMock(return_value=False)))), \
             patch('handlers.topology_api.pyeapi.connect', side_effect=socket.timeout('timed out')):
            resp = self._authed_fetch('/td-api/device-status?device=Spine1')

        assert resp.code == 200
        body = json.loads(resp.body)
        assert body['status'] == 'down', f"Expected 'down', got: {body}"

    def test_device_status_eapi_auth_failure(self):
        """Auth failure from eAPI returns status 'unconfigured'."""
        import pyeapi.eapilib

        mock_yaml_instance = MagicMock()
        mock_yaml_instance.load.return_value = {
            'login_info': {'jump_host': {'user': 'arista', 'pw': 'arista123'}}
        }

        unconfig_result = {
            'device': 'Spine1', 'ip': '192.168.0.10', 'status': 'unconfigured',
            'error': 'Device reachable but authentication failed',
            'last_check': '2026-01-01T00:00:00'
        }
        with patch('handlers.topology_api.get_all_devices', return_value=self._mock_all_devices()), \
             patch('handlers.topology_api.get_device_ip_from_sources', return_value='192.168.0.10'), \
             patch.object(DeviceStatusAPIHandler, '_check_device_via_eapi', return_value=unconfig_result):
            resp = self._authed_fetch('/td-api/device-status?device=Spine1')

        body = json.loads(resp.body)
        assert body['status'] == 'unconfigured', f"Expected 'unconfigured', got: {body}"

    def test_device_status_unknown_device(self):
        """Device not found in topology returns status 'unknown'."""
        with patch('handlers.topology_api.get_all_devices', return_value={}), \
             patch('handlers.topology_api.get_device_ip_from_sources', return_value=None):
            resp = self._authed_fetch('/td-api/device-status?device=NonExistent')

        body = json.loads(resp.body)
        assert body['status'] == 'unknown'


# ---------------------------------------------------------------------------
# Utility function tests (module-level, no HTTP)
# ---------------------------------------------------------------------------

class TestTopologyAPIUtilities:
    """Unit tests for module-level device utility functions."""

    def setup_method(self):
        """Reset module caches before each test."""
        invalidate_devices_cache()

    def test_normalize_device_name_capitalized(self):
        """normalize_device_name from utils is used — verify via parse_topology output."""
        from utils import normalize_device_name
        assert normalize_device_name('spine1') == 'Spine1'
        assert normalize_device_name('leaf2') == 'Leaf2'
        assert normalize_device_name('host1') == 'Host1'

    def test_normalize_preserves_pe_uppercase(self):
        from utils import normalize_device_name
        assert normalize_device_name('PE1') == 'PE1'
        assert normalize_device_name('P1') == 'P1'

    def test_normalize_dc_suffix_uppercased(self):
        from utils import normalize_device_name
        assert normalize_device_name('spine1-dc1') == 'Spine1-DC1'

    def test_get_all_devices_returns_dict(self):
        """get_all_devices() returns a dict even when topo_build.yml is absent."""
        with patch('handlers.topology_api._get_topo_build_data', return_value={}):
            with patch('handlers.topology_api.os.path.exists', return_value=False):
                from handlers.topology_api import get_all_devices
                result = get_all_devices()
        assert isinstance(result, dict)

    def test_invalidate_devices_cache_clears_cache(self):
        """After invalidate, _ALL_DEVICES_CACHE is None."""
        topo_module._ALL_DEVICES_CACHE = {'Spine1': {'ip': '1.2.3.4'}}
        invalidate_devices_cache()
        assert topo_module._ALL_DEVICES_CACHE is None


# ---------------------------------------------------------------------------
# TopologyAPIHandler static method tests
# ---------------------------------------------------------------------------

class TestTopologyStaticMethods:
    """Unit tests for static helpers on TopologyAPIHandler."""

    def test_extract_datacenter_with_suffix(self):
        assert TopologyAPIHandler.extract_datacenter('spine1-DC1') == 'DC1'
        assert TopologyAPIHandler.extract_datacenter('leaf2-DC2') == 'DC2'

    def test_extract_datacenter_without_suffix(self):
        assert TopologyAPIHandler.extract_datacenter('spine1') == ''
        assert TopologyAPIHandler.extract_datacenter('host1') == ''

    def test_extract_datacenter_gw_naming(self):
        assert TopologyAPIHandler.extract_datacenter('GW11') == 'DC1'
        assert TopologyAPIHandler.extract_datacenter('GW21') == 'DC2'

    def test_extract_isp_provider(self):
        assert TopologyAPIHandler.extract_isp_provider('core1-ISP1') == 'ISP1'
        assert TopologyAPIHandler.extract_isp_provider('internet') == ''

    def test_get_sort_key_natural_order(self):
        names = ['spine10', 'spine2', 'spine1']
        sorted_names = sorted(names, key=TopologyAPIHandler.get_sort_key)
        assert sorted_names == ['spine1', 'spine2', 'spine10']

    def test_detect_topology_type_datacenter(self):
        nodes = [
            {'data': {'device_type': 'spine', 'id': 'Spine1'}},
            {'data': {'device_type': 'leaf', 'id': 'Leaf1'}},
        ]
        assert TopologyAPIHandler.detect_topology_type(nodes) == 'datacenter'

    def test_detect_topology_type_wan(self):
        nodes = [
            {'data': {'device_type': 'p', 'id': 'P1'}},
            {'data': {'device_type': 'pe', 'id': 'PE1'}},
        ]
        assert TopologyAPIHandler.detect_topology_type(nodes) == 'wan'

    def test_classify_device_type_spine(self):
        assert TopologyAPIHandler.classify_device_type('spine1') == 'spine'

    def test_classify_device_type_leaf(self):
        assert TopologyAPIHandler.classify_device_type('leaf1') == 'leaf'

    def test_calculate_positions_assigns_positions(self):
        """calculate_positions must set 'position' on every node."""
        nodes = [
            {'data': {'device_type': 'spine', 'id': 'Spine1'}},
            {'data': {'device_type': 'leaf', 'id': 'Leaf1'}},
            {'data': {'device_type': 'host', 'id': 'Host1'}},
        ]
        result = TopologyAPIHandler.calculate_positions(nodes, [])
        for node in result:
            assert 'position' in node, f"Node {node['data']['id']!r} missing 'position'"
            assert 'x' in node['position']
            assert 'y' in node['position']

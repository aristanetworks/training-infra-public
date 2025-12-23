"""
Unit tests for API response consistency.

These tests verify that API endpoints return consistent, well-structured
responses that the frontend can rely on. Tests cover:
- Response structure validation
- Required fields presence
- Data type consistency
- Error response format
"""

import os
import pytest
from unittest.mock import patch, Mock
import json

# Add src to path
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ============================================================================
# Response Schema Definitions
# ============================================================================

def validate_device_schema(device: dict, device_type: str = None) -> list:
    """
    Validate a device object has required fields.

    Returns list of validation errors (empty if valid).
    """
    errors = []

    # Required fields for all devices
    required_fields = ['name']
    for field in required_fields:
        if field not in device:
            errors.append(f"Missing required field: {field}")

    # Type-specific validation
    if device_type == 'unified':
        unified_fields = ['ip', 'device_type', 'device_category', 'user_added']
        for field in unified_fields:
            if field not in device:
                errors.append(f"Missing unified field: {field}")

        # Validate device_type values
        valid_types = ['veos', 'linux_host', 'firewall']
        if device.get('device_type') and device['device_type'] not in valid_types:
            errors.append(f"Invalid device_type: {device.get('device_type')}")

        # Validate device_category values
        valid_categories = ['node', 'host', 'firewall']
        if device.get('device_category') and device['device_category'] not in valid_categories:
            errors.append(f"Invalid device_category: {device.get('device_category')}")

        # user_added should be boolean
        if 'user_added' in device and not isinstance(device['user_added'], bool):
            errors.append(f"user_added should be boolean, got {type(device['user_added'])}")

    return errors


def validate_connection_schema(connection: dict) -> list:
    """Validate a connection object has required fields."""
    errors = []
    required_fields = ['source_device', 'target_device']
    for field in required_fields:
        if field not in connection:
            errors.append(f"Missing connection field: {field}")
    return errors


def validate_error_response(data: dict) -> list:
    """Validate error response structure."""
    errors = []
    if 'error' not in data:
        errors.append("Error response missing 'error' field")
    if 'error' in data and not isinstance(data['error'], str):
        errors.append("Error field should be a string")
    return errors


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_topo_file(tmp_path):
    """Create a mock topo_build.yml file."""
    topo_file = tmp_path / "topo_build.yml"
    topo_file.write_text("""
nodes:
  - spine1:
      ip_addr: 192.168.0.10
      sys_mac: 00:1c:73:00:00:01
      neighbors:
        - port: Ethernet1
          neighborDevice: leaf1
          neighborPort: Ethernet1
  - leaf1:
      ip_addr: 192.168.0.11
      sys_mac: 00:1c:73:00:00:02
      neighbors:
        - port: Ethernet1
          neighborDevice: spine1
          neighborPort: Ethernet1
""")
    return str(topo_file)


@pytest.fixture
def mock_user_nodes_file(tmp_path):
    """Create a mock user_nodes.yaml file."""
    nodes_file = tmp_path / "user_nodes.yaml"
    nodes_file.write_text("""
version: 1
nodes:
  - leaf5:
      ip_addr: 192.168.0.50
      sys_mac: 00:1c:73:00:00:50
      status: active
      neighbors:
        - port: Ethernet1
          neighborDevice: spine1
          neighborPort: Ethernet5
""")
    return str(nodes_file)


@pytest.fixture
def mock_user_hosts_file(tmp_path):
    """Create a mock user_hosts.yaml file."""
    hosts_file = tmp_path / "user_hosts.yaml"
    hosts_file.write_text("""
version: 1
hosts:
  - desktop1:
      mgmt_ip: 192.168.0.51
      data_ip: 10.1.1.100/24
      status: active
      vnc_port: 5901
      connection:
        target_device: leaf1
        target_port: Ethernet10
        bridge: de11-le110
""")
    return str(hosts_file)


@pytest.fixture
def mock_user_firewalls_file(tmp_path):
    """Create a mock user_firewalls.yaml file."""
    firewalls_file = tmp_path / "user_firewalls.yaml"
    firewalls_file.write_text("""
version: 1
firewalls:
  - fw1:
      mgmt_ip: 192.168.0.52
      status: active
      vnc_port: 5902
      inside_interface:
        ip: 10.1.1.1/24
        target_device: leaf1
        target_port: Ethernet11
      outside_interface:
        ip: 10.2.2.1/24
        target_device: spine1
        target_port: Ethernet10
""")
    return str(firewalls_file)


@pytest.fixture
def mock_dnsmasq_file(tmp_path):
    """Create a mock dnsmasq config file."""
    dnsmasq_file = tmp_path / "atd.conf"
    dnsmasq_file.write_text("""
dhcp-host=00:1c:73:00:00:50,192.168.0.50,eos50
dhcp-host=00:1c:73:00:00:51,192.168.0.51,eos51
dhcp-host=00:1c:73:00:00:52,192.168.0.52,eos52
dhcp-host=00:1c:73:00:00:53,192.168.0.53,eos53
""")
    return str(dnsmasq_file)


# ============================================================================
# Unified Topology Endpoint Tests
# ============================================================================

class TestUnifiedTopologyEndpoint:
    """Tests for /topology/unified endpoint response structure."""

    @pytest.mark.asyncio
    async def test_unified_topology_response_structure(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify /topology/unified returns well-structured response."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        resp = await client.get('/topology/unified')
                        assert resp.status == 200

                        data = await resp.json()

                        # Verify top-level structure
                        assert 'devices' in data, "Response missing 'devices'"
                        assert 'summary' in data, "Response missing 'summary'"
                        assert 'connections' in data, "Response missing 'connections'"

                        # Verify devices is a list
                        assert isinstance(data['devices'], list)

                        # Verify summary structure
                        summary = data['summary']
                        expected_summary_fields = [
                            'total_devices', 'topology_nodes', 'user_nodes',
                            'user_hosts', 'user_firewalls', 'total_connections'
                        ]
                        for field in expected_summary_fields:
                            assert field in summary, f"Summary missing '{field}'"

    @pytest.mark.asyncio
    async def test_unified_topology_device_schema(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify all devices in unified topology have consistent schema."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        resp = await client.get('/topology/unified')
                        data = await resp.json()

                        all_errors = []
                        for device in data['devices']:
                            errors = validate_device_schema(device, device_type='unified')
                            if errors:
                                all_errors.append(f"{device.get('name', 'unknown')}: {errors}")

                        assert not all_errors, f"Device schema errors: {all_errors}"

    @pytest.mark.asyncio
    async def test_unified_topology_connections_schema(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify all connections have consistent schema."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        resp = await client.get('/topology/unified')
                        data = await resp.json()

                        all_errors = []
                        for i, conn in enumerate(data['connections']):
                            errors = validate_connection_schema(conn)
                            if errors:
                                all_errors.append(f"Connection {i}: {errors}")

                        assert not all_errors, f"Connection schema errors: {all_errors}"

    @pytest.mark.asyncio
    async def test_unified_topology_filter_by_device_type(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify device_type filter works correctly."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        # Filter for veos devices only
                        resp = await client.get('/topology/unified?device_type=veos')
                        data = await resp.json()

                        for device in data['devices']:
                            assert device['device_type'] == 'veos', \
                                f"Device {device['name']} has wrong type"

    @pytest.mark.asyncio
    async def test_unified_topology_filter_by_user_added(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify user_added filter works correctly."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        # Filter for user-added devices only
                        resp = await client.get('/topology/unified?user_added=true')
                        data = await resp.json()

                        for device in data['devices']:
                            assert device['user_added'] is True, \
                                f"Device {device['name']} is not user_added"


# ============================================================================
# Existing Nodes Endpoint Tests
# ============================================================================

class TestExistingNodesEndpoint:
    """Tests for /existing-nodes endpoint response structure."""

    @pytest.mark.asyncio
    async def test_existing_nodes_response_structure(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file
    ):
        """Verify /existing-nodes returns expected structure."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                from nodebuilder_service import create_app
                client = await aiohttp_client(create_app())

                resp = await client.get('/existing-nodes')
                assert resp.status == 200

                data = await resp.json()

                # Verify structure
                assert 'nodes' in data, "Response missing 'nodes'"
                assert isinstance(data['nodes'], list)

                # Verify each node has basic fields
                for node in data['nodes']:
                    assert 'name' in node, f"Node missing 'name'"
                    assert 'ip_addr' in node, f"Node {node.get('name')} missing 'ip_addr'"

    @pytest.mark.asyncio
    async def test_existing_nodes_includes_topology_and_user(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file
    ):
        """Verify both topology and user nodes are included."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                from nodebuilder_service import create_app
                client = await aiohttp_client(create_app())

                resp = await client.get('/existing-nodes')
                data = await resp.json()

                node_names = [n['name'] for n in data['nodes']]

                # Topology nodes
                assert 'spine1' in node_names
                assert 'leaf1' in node_names

                # User node
                assert 'leaf5' in node_names


# ============================================================================
# Available IPs Endpoint Tests
# ============================================================================

class TestAvailableIPsEndpoint:
    """Tests for /available-ips endpoint response structure."""

    @pytest.mark.asyncio
    async def test_available_ips_response_structure(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file, mock_dnsmasq_file
    ):
        """Verify /available-ips returns expected structure."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.DNSMASQ_PATH', mock_dnsmasq_file):
                    from nodebuilder_service import create_app
                    client = await aiohttp_client(create_app())

                    resp = await client.get('/available-ips')
                    assert resp.status == 200

                    data = await resp.json()

                    # Verify structure
                    assert 'available_ips' in data, "Response missing 'available_ips'"
                    assert isinstance(data['available_ips'], list)

                    # Each entry should have ip and mac
                    for entry in data['available_ips']:
                        assert 'ip' in entry, "IP entry missing 'ip'"
                        assert 'mac' in entry, "IP entry missing 'mac'"


# ============================================================================
# Host Status Endpoint Tests
# ============================================================================

class TestHostStatusEndpoint:
    """Tests for /host-status endpoint response structure."""

    @pytest.mark.asyncio
    async def test_host_status_response_structure(
        self, aiohttp_client, mock_user_hosts_file
    ):
        """Verify /host-status returns expected structure."""
        with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
            from nodebuilder_service import create_app
            client = await aiohttp_client(create_app())

            resp = await client.get('/host-status')
            assert resp.status == 200

            data = await resp.json()

            # Verify required fields
            assert 'current_count' in data, "Response missing 'current_count'"
            assert 'max_allowed' in data, "Response missing 'max_allowed'"
            assert 'can_add_more' in data, "Response missing 'can_add_more'"
            assert 'hosts' in data, "Response missing 'hosts'"

            # Verify types
            assert isinstance(data['current_count'], int)
            assert isinstance(data['max_allowed'], int)
            assert isinstance(data['can_add_more'], bool)
            assert isinstance(data['hosts'], list)

            # Verify host entries have name and info
            for host in data['hosts']:
                assert 'name' in host, "Host entry missing 'name'"
                assert 'info' in host, "Host entry missing 'info'"


# ============================================================================
# Firewall Status Endpoint Tests
# ============================================================================

class TestFirewallStatusEndpoint:
    """Tests for /firewall-status endpoint response structure."""

    @pytest.mark.asyncio
    async def test_firewall_status_response_structure(
        self, aiohttp_client, mock_user_firewalls_file
    ):
        """Verify /firewall-status returns expected structure."""
        with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
            from nodebuilder_service import create_app
            client = await aiohttp_client(create_app())

            resp = await client.get('/firewall-status')
            assert resp.status == 200

            data = await resp.json()

            # Verify required fields
            assert 'current_count' in data, "Response missing 'current_count'"
            assert 'max_allowed' in data, "Response missing 'max_allowed'"
            assert 'can_add_more' in data, "Response missing 'can_add_more'"
            assert 'firewalls' in data, "Response missing 'firewalls'"

            # Verify types
            assert isinstance(data['current_count'], int)
            assert isinstance(data['max_allowed'], int)
            assert isinstance(data['can_add_more'], bool)
            assert isinstance(data['firewalls'], list)

            # Verify firewall entries have name and info
            for fw in data['firewalls']:
                assert 'name' in fw, "Firewall entry missing 'name'"
                assert 'info' in fw, "Firewall entry missing 'info'"


# ============================================================================
# Error Response Consistency Tests
# ============================================================================

class TestErrorResponseConsistency:
    """Tests for consistent error response format."""

    @pytest.mark.asyncio
    async def test_validation_error_format(self, aiohttp_client):
        """Verify validation errors have consistent format."""
        from nodebuilder_service import create_app
        client = await aiohttp_client(create_app())

        # Missing required field
        resp = await client.post('/add-node', json={})
        assert resp.status == 400

        data = await resp.json()
        errors = validate_error_response(data)
        assert not errors, f"Error response format issues: {errors}"

    @pytest.mark.asyncio
    async def test_invalid_json_error_format(self, aiohttp_client):
        """Verify invalid JSON errors have consistent format."""
        from nodebuilder_service import create_app
        client = await aiohttp_client(create_app())

        # Send invalid JSON
        resp = await client.post(
            '/add-node',
            data='not valid json',
            headers={'Content-Type': 'application/json'}
        )
        assert resp.status == 400

        data = await resp.json()
        errors = validate_error_response(data)
        assert not errors, f"Error response format issues: {errors}"

    @pytest.mark.asyncio
    async def test_not_found_error_format(self, aiohttp_client):
        """Verify not-found errors have consistent format."""
        with patch('config.USER_NODES_PATH', '/nonexistent/path'):
            from nodebuilder_service import create_app
            client = await aiohttp_client(create_app())

            # Try to delete non-existent node
            resp = await client.post('/delete-node', json={'name': 'nonexistent'})
            assert resp.status == 400

            data = await resp.json()
            errors = validate_error_response(data)
            assert not errors, f"Error response format issues: {errors}"


# ============================================================================
# Health Endpoint Tests
# ============================================================================

class TestHealthEndpointResponse:
    """Tests for /health endpoint response structure."""

    @pytest.mark.asyncio
    async def test_health_response_structure(self, aiohttp_client):
        """Verify /health returns expected structure."""
        from nodebuilder_service import create_app
        client = await aiohttp_client(create_app())

        resp = await client.get('/health')
        assert resp.status == 200

        data = await resp.json()

        # Must have status field
        assert 'status' in data, "Health response missing 'status'"
        assert data['status'] in ('healthy', 'ok', 'up')


# ============================================================================
# Response Type Consistency Tests
# ============================================================================

class TestResponseTypeConsistency:
    """Tests to ensure response field types are consistent."""

    @pytest.mark.asyncio
    async def test_unified_topology_types(
        self, aiohttp_client, mock_topo_file, mock_user_nodes_file,
        mock_user_hosts_file, mock_user_firewalls_file
    ):
        """Verify unified topology fields have consistent types."""
        with patch('config.get_topo_build_path', return_value=mock_topo_file):
            with patch('config.USER_NODES_PATH', mock_user_nodes_file):
                with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                    with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())

                        resp = await client.get('/topology/unified')
                        data = await resp.json()

                        for device in data['devices']:
                            # name must be string
                            assert isinstance(device['name'], str), \
                                f"Device name should be string"

                            # ip must be string
                            assert isinstance(device['ip'], str), \
                                f"Device ip should be string"

                            # user_added must be boolean
                            assert isinstance(device['user_added'], bool), \
                                f"Device user_added should be boolean"

                            # neighbors should be list or None
                            if 'neighbors' in device and device['neighbors'] is not None:
                                assert isinstance(device['neighbors'], list), \
                                    f"Device neighbors should be list"

                        # Summary fields should be integers
                        summary = data['summary']
                        int_fields = [
                            'total_devices', 'topology_nodes', 'user_nodes',
                            'user_hosts', 'user_firewalls', 'total_connections'
                        ]
                        for field in int_fields:
                            if field in summary:
                                assert isinstance(summary[field], int), \
                                    f"Summary {field} should be int"

"""
Unit tests for nodebuilder API endpoints.

Tests cover:
- Node CRUD operations (create, read, update, delete)
- Host CRUD operations
- Firewall CRUD operations
- Reconciliation endpoint
"""

import os
import pytest
from unittest.mock import patch, Mock, MagicMock, AsyncMock
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop
import json


class TestHealthEndpoint:
    """Tests for health check endpoint."""

    @pytest.fixture
    def client(self, aiohttp_client):
        """Create test client."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app
        return aiohttp_client(create_app())

    @pytest.mark.asyncio
    async def test_health_check(self, client):
        """Test health check returns 200."""
        cli = await client
        resp = await cli.get('/health')
        assert resp.status == 200
        data = await resp.json()
        assert data['status'] in ('healthy', 'ok')


class TestAddNodeEndpoint:
    """Tests for add-node endpoint."""

    @pytest.mark.asyncio
    async def test_add_node_missing_name(self, aiohttp_client):
        """Test add-node rejects missing name."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-node', json={
            'ip': '192.168.0.50',
            'connections': []
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'name' in data['error'].lower()

    @pytest.mark.asyncio
    async def test_add_node_missing_ip(self, aiohttp_client):
        """Test add-node rejects missing IP."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-node', json={
            'name': 'testnode',
            'connections': []
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'ip' in data['error'].lower()

    @pytest.mark.asyncio
    async def test_add_node_invalid_device_type(self, aiohttp_client):
        """Test add-node rejects invalid device type."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-node', json={
            'name': 'testnode',
            'ip': '192.168.0.50',
            'device_type': 'invalid_type',
            'connections': []
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'device_type' in data['error']

    @pytest.mark.asyncio
    async def test_add_node_too_many_connections(self, aiohttp_client):
        """Test add-node rejects too many connections."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        # Create more connections than allowed
        connections = [{'target_device': f'device{i}'} for i in range(50)]

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-node', json={
            'name': 'testnode',
            'ip': '192.168.0.50',
            'connections': connections
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'connection' in data['error'].lower()


class TestAddHostEndpoint:
    """Tests for add-host endpoint."""

    @pytest.mark.asyncio
    async def test_add_host_missing_name(self, aiohttp_client):
        """Test add-host rejects missing name."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-host', json={
            'ip': '192.168.0.50'
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'name' in data['error'].lower()

    @pytest.mark.asyncio
    async def test_add_host_invalid_data_ip(self, aiohttp_client):
        """Test add-host rejects invalid data IP."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-host', json={
            'name': 'testhost',
            'ip': '192.168.0.50',
            'data_ip': 'invalid-ip'  # Not CIDR format
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data


class TestAddFirewallEndpoint:
    """Tests for add-firewall endpoint."""

    @pytest.mark.asyncio
    async def test_add_firewall_missing_inside_ip(self, aiohttp_client):
        """Test add-firewall rejects missing inside interface IP."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-firewall', json={
            'name': 'testfw',
            'mgmt_ip': '192.168.0.50',
            'inside_interface': {},  # Missing IP
            'outside_interface': {'ip': '10.2.2.1/24'}
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data
        assert 'inside' in data['error'].lower()

    @pytest.mark.asyncio
    async def test_add_firewall_invalid_interface_ip(self, aiohttp_client):
        """Test add-firewall rejects invalid interface IP format."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/add-firewall', json={
            'name': 'testfw',
            'mgmt_ip': '192.168.0.50',
            'inside_interface': {'ip': '10.1.1.1'},  # Missing CIDR prefix
            'outside_interface': {'ip': '10.2.2.1/24'}
        })
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data


class TestReconcileEndpoint:
    """Tests for reconcile endpoint."""

    @pytest.mark.asyncio
    async def test_reconcile_dry_run(self, aiohttp_client, mock_user_nodes_file,
                                      mock_user_hosts_file, mock_user_firewalls_file):
        """Test reconcile GET returns dry run results."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

        with patch('config.USER_NODES_PATH', mock_user_nodes_file):
            with patch('config.USER_HOSTS_PATH', mock_user_hosts_file):
                with patch('config.USER_FIREWALLS_PATH', mock_user_firewalls_file):
                    with patch('subprocess.run') as mock_run:
                        mock_run.return_value = Mock(returncode=0, stdout='', stderr='')

                        from nodebuilder_service import create_app
                        client = await aiohttp_client(create_app())
                        resp = await client.get('/reconcile')

                        assert resp.status == 200
                        data = await resp.json()
                        assert data['dry_run'] is True
                        assert 'orphan_entries' in data
                        assert 'zombie_vms' in data


class TestDeleteEndpoints:
    """Tests for delete endpoints."""

    @pytest.mark.asyncio
    async def test_delete_node_missing_name(self, aiohttp_client):
        """Test delete-node rejects missing name."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/delete-node', json={})
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data

    @pytest.mark.asyncio
    async def test_delete_host_missing_name(self, aiohttp_client):
        """Test delete-host rejects missing name."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/delete-host', json={})
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data

    @pytest.mark.asyncio
    async def test_delete_firewall_missing_name(self, aiohttp_client):
        """Test delete-firewall rejects missing name."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
        from nodebuilder_service import create_app

        client = await aiohttp_client(create_app())
        resp = await client.post('/delete-firewall', json={})
        assert resp.status == 400
        data = await resp.json()
        assert 'error' in data


class TestStatusEndpoints:
    """Tests for status endpoints."""

    @pytest.mark.asyncio
    async def test_bridge_status(self, aiohttp_client):
        """Test bridge-status returns bridge info."""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

        with patch('subprocess.run') as mock_run:
            # Mock ovs-vsctl list-br
            mock_run.return_value = Mock(returncode=0, stdout='br0\noob_mgmt\n', stderr='')

            from nodebuilder_service import create_app
            client = await aiohttp_client(create_app())
            resp = await client.get('/bridge-status')

            assert resp.status == 200
            data = await resp.json()
            assert 'bridges' in data
            assert 'total_bridges' in data

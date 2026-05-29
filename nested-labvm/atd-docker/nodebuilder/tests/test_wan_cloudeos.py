"""Tests for WAN CloudEOS auto-deploy endpoints.

Covers orchestration logic: PE detection, D1/D2 existence checks, rollback.
These tests mock the module-level dependencies rather than making HTTP calls,
so they do not require GCP credentials or a running aiohttp server.
"""
import os
import sys
import pytest
from unittest.mock import patch, Mock, MagicMock, AsyncMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


# ---------------------------------------------------------------------------
# Helpers for building mock aiohttp request objects
# ---------------------------------------------------------------------------

def _mock_request(json_body=None, query=None, match_info=None):
    """Create a minimal mock aiohttp Request object."""
    req = MagicMock()
    req.json = AsyncMock(return_value=json_body or {})
    req.query = query or {}
    req.match_info = match_info or {}
    return req


# ---------------------------------------------------------------------------
# Tests for /wan-cloudeos-preview (GET)
# ---------------------------------------------------------------------------

class TestWanCloudeosPreview:
    """Unit tests for handle_wan_cloudeos_preview logic."""

    @pytest.mark.asyncio
    async def test_preview_fails_without_pe1(self):
        """Preview returns 400 when PE1 is missing from topology."""
        import nodebuilder_service as svc

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.wan_cloudeos_preview(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'error' in data

    @pytest.mark.asyncio
    async def test_preview_fails_without_pe2(self):
        """Preview returns 400 when PE2 is missing from topology."""
        import nodebuilder_service as svc

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.wan_cloudeos_preview(req)
                            assert resp.status == 400

    @pytest.mark.asyncio
    async def test_preview_fails_if_d1_exists(self):
        """Preview returns 400 when D1 is already deployed."""
        import nodebuilder_service as svc

        def mock_get_device(name, *args, **kwargs):
            return {'name': 'D1'} if name == 'D1' else None

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', side_effect=mock_get_device):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.wan_cloudeos_preview(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'D1' in data.get('error', '')

    @pytest.mark.asyncio
    async def test_preview_success(self):
        """Preview returns 200 with d1/d2 details when topology is ready."""
        import nodebuilder_service as svc

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.wan_cloudeos_preview(req)
                            assert resp.status == 200
                            import json
                            data = json.loads(resp.body)
                            assert data['status'] == 'ready'
                            assert data['d1']['name'] == 'D1'
                            assert data['d2']['name'] == 'D2'
                            assert data['d1']['ip'] == '192.168.0.50'
                            assert data['d2']['ip'] == '192.168.0.51'


# ---------------------------------------------------------------------------
# Tests for /add-wan-cloudeos (POST)
# ---------------------------------------------------------------------------

class TestWanCloudeosDeploy:
    """Unit tests for handle_add_wan_cloudeos orchestration logic."""

    @pytest.mark.asyncio
    async def test_deploy_fails_without_pe_nodes(self):
        """Deploy returns 400 when PE1/PE2 are not in topology."""
        import nodebuilder_service as svc

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'spine1', 'ip_addr': '192.168.0.10', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.add_wan_cloudeos(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'error' in data

    @pytest.mark.asyncio
    async def test_deploy_rejects_if_d1_exists(self):
        """Deploy returns 400 if D1 already exists."""
        import nodebuilder_service as svc

        def mock_get_device(name, *args, **kwargs):
            return {'name': 'D1'} if name == 'D1' else None

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', side_effect=mock_get_device):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.add_wan_cloudeos(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'D1' in data.get('error', '')

    @pytest.mark.asyncio
    async def test_deploy_rejects_if_d2_exists(self):
        """Deploy returns 400 if D2 already exists."""
        import nodebuilder_service as svc

        def mock_get_device(name, *args, **kwargs):
            return {'name': 'D2'} if name == 'D2' else None

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', side_effect=mock_get_device):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.add_wan_cloudeos(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'D2' in data.get('error', '')

    @pytest.mark.asyncio
    async def test_deploy_rejects_insufficient_ips(self):
        """Deploy returns 400 when fewer than 2 IPs are available."""
        import nodebuilder_service as svc

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}  # Only 1 IP
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            req = _mock_request()
                            resp = await svc.add_wan_cloudeos(req)
                            assert resp.status == 400
                            import json
                            data = json.loads(resp.body)
                            assert 'error' in data

    @pytest.mark.asyncio
    async def test_deploy_rollback_on_d2_failure(self):
        """If D2 creation fails, D1 is rolled back via delete_cloudeos."""
        import nodebuilder_service as svc

        d1_success = {
            'status': 'success', 'name': 'D1',
            'targets_need_reboot': ['PE1'], 'targets_reused_slots': []
        }
        d2_failure = {'status': 'error', 'message': 'Image not found'}

        call_order = []

        def mock_create(name, ip, device_type, connections):
            call_order.append(('create', name))
            return d1_success if name == 'D1' else d2_failure

        def mock_delete(name):
            call_order.append(('delete', name))
            return {'status': 'success'}

        mock_logger = MagicMock()

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            with patch('nodebuilder_service.create_cloudeos', side_effect=mock_create):
                                with patch('nodebuilder_service.delete_cloudeos', side_effect=mock_delete):
                                    with patch('nodebuilder_service.logger', mock_logger):
                                        req = _mock_request()
                                        resp = await svc.add_wan_cloudeos(req)
                                        assert resp.status == 500
                                        import json
                                        data = json.loads(resp.body)
                                        assert 'error' in data
                                        # Verify D1 was created then rolled back
                                        assert ('create', 'D1') in call_order
                                        assert ('delete', 'D1') in call_order

    @pytest.mark.asyncio
    async def test_deploy_success_merges_reboot_lists(self):
        """Successful deploy returns merged reboot/reused lists from D1 and D2."""
        import nodebuilder_service as svc

        d1_result = {
            'status': 'success', 'name': 'D1',
            'targets_need_reboot': ['PE1'], 'targets_reused_slots': []
        }
        d2_result = {
            'status': 'success', 'name': 'D2',
            'targets_need_reboot': ['PE2'], 'targets_reused_slots': ['PE2']
        }

        def mock_create(name, ip, device_type, connections):
            return d1_result if name == 'D1' else d2_result

        with patch('nodebuilder_service.get_available_ips_internal', return_value=[
            {'ip': '192.168.0.50'}, {'ip': '192.168.0.51'}
        ]):
            with patch('validation.get_topo_nodes', return_value=[
                {'name': 'PE1', 'ip_addr': '192.168.0.41', 'neighbors': []},
                {'name': 'PE2', 'ip_addr': '192.168.0.42', 'neighbors': []}
            ]):
                with patch('persistence.get_user_cloudeos_device', return_value=None):
                    with patch('interface_manager.find_next_available_port', return_value='Ethernet7'):
                        with patch('config.get_topo_build_path', return_value='/fake/topo.yml'):
                            with patch('nodebuilder_service.create_cloudeos', side_effect=mock_create):
                                req = _mock_request()
                                resp = await svc.add_wan_cloudeos(req)
                                assert resp.status == 200
                                import json
                                data = json.loads(resp.body)
                                assert data['status'] == 'success'
                                assert set(data['targets_need_reboot']) == {'PE1', 'PE2'}

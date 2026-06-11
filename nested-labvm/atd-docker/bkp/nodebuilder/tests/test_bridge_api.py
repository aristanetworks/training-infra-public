"""
Unit tests for bridge parsing API endpoints.

Tests cover:
- GET /bridge/parse/{bridge_name} - Single bridge parsing
- POST /bridge/parse - Batch bridge parsing
- GET /bridge/abbreviations - Device abbreviation mapping
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

# Import the routes from nodebuilder_service
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


class TestBridgeParseEndpoint(AioHTTPTestCase):
    """Test GET /bridge/parse/{bridge_name} endpoint."""

    async def get_application(self):
        """Create a minimal app with just the bridge routes for testing."""
        from bridge_utils import parse_bridge_name, get_abbreviation_mapping, is_legacy_abbreviation

        app = web.Application()

        async def parse_bridge_name_endpoint(request):
            bridge_name = request.match_info.get('bridge_name', '')
            if not bridge_name:
                return web.json_response({'error': 'Bridge name is required'}, status=400)
            result = parse_bridge_name(bridge_name)
            result['bridge_name'] = bridge_name
            return web.json_response(result)

        async def parse_bridge_names_batch(request):
            try:
                data = await request.json()
            except Exception:
                return web.json_response({'error': 'Invalid JSON'}, status=400)

            bridge_names = data.get('bridge_names', [])
            if not bridge_names:
                return web.json_response({'error': 'bridge_names array is required'}, status=400)
            if not isinstance(bridge_names, list):
                return web.json_response({'error': 'bridge_names must be an array'}, status=400)

            MAX_BATCH_SIZE = 100
            if len(bridge_names) > MAX_BATCH_SIZE:
                return web.json_response(
                    {'error': f'Maximum batch size is {MAX_BATCH_SIZE}'},
                    status=400
                )

            results = {}
            for bridge_name in bridge_names:
                if isinstance(bridge_name, str) and bridge_name:
                    results[bridge_name] = parse_bridge_name(bridge_name)

            return web.json_response({'results': results})

        async def get_device_abbreviations(request):
            abbreviations = get_abbreviation_mapping()
            legacy = [abbrev for abbrev in abbreviations if is_legacy_abbreviation(abbrev)]
            return web.json_response({
                'abbreviations': abbreviations,
                'legacy': legacy,
                'note': 'Legacy abbreviations are supported for parsing but not generated'
            })

        app.router.add_get('/bridge/parse/{bridge_name}', parse_bridge_name_endpoint)
        app.router.add_post('/bridge/parse', parse_bridge_names_batch)
        app.router.add_get('/bridge/abbreviations', get_device_abbreviations)

        return app

    @unittest_run_loop
    async def test_parse_basic_bridge(self):
        """Test parsing a basic bridge name."""
        resp = await self.client.request("GET", "/bridge/parse/le1x1-sp1x2")
        assert resp.status == 200

        data = await resp.json()
        assert data['bridge_name'] == 'le1x1-sp1x2'
        assert data['source_device_name'] == 'leaf1'
        assert data['source_port_name'] == 'Ethernet1'
        assert data['target_device_name'] == 'spine1'
        assert data['target_port_name'] == 'Ethernet2'

    @unittest_run_loop
    async def test_parse_firewall_bridge(self):
        """Test parsing a firewall bridge name."""
        resp = await self.client.request("GET", "/bridge/parse/fi1x1-bo1x5")
        assert resp.status == 200

        data = await resp.json()
        assert data['source_device_name'] == 'firewall1'
        assert data['target_device_name'] == 'borderleaf1'

    @unittest_run_loop
    async def test_parse_velocloud_wan_bridge(self):
        """Test parsing a VeloCloud WAN bridge name."""
        resp = await self.client.request("GET", "/bridge/parse/ve1xwa1-ro1x1")
        assert resp.status == 200

        data = await resp.json()
        assert data['source_port_name'] == 'wan1'
        assert data['target_device_name'] == 'router1'

    @unittest_run_loop
    async def test_parse_legacy_format(self):
        """Test parsing legacy kvmbuilder format."""
        resp = await self.client.request("GET", "/bridge/parse/le11-sp12")
        assert resp.status == 200

        data = await resp.json()
        assert data['source_device_name'] == 'leaf1'
        assert data['source_port_name'] == 'Ethernet1'

    @unittest_run_loop
    async def test_parse_invalid_bridge(self):
        """Test parsing a bridge name without separator."""
        resp = await self.client.request("GET", "/bridge/parse/nobridgename")
        assert resp.status == 200

        data = await resp.json()
        # Should return empty names for unparseable bridge
        assert data['source_device_name'] == ''
        assert data['target_device_name'] == ''


class TestBridgeParseBatchEndpoint(AioHTTPTestCase):
    """Test POST /bridge/parse endpoint."""

    async def get_application(self):
        """Create a minimal app with just the bridge routes for testing."""
        from bridge_utils import parse_bridge_name

        app = web.Application()

        async def parse_bridge_names_batch(request):
            try:
                data = await request.json()
            except Exception:
                return web.json_response({'error': 'Invalid JSON'}, status=400)

            bridge_names = data.get('bridge_names', [])
            if not bridge_names:
                return web.json_response({'error': 'bridge_names array is required'}, status=400)
            if not isinstance(bridge_names, list):
                return web.json_response({'error': 'bridge_names must be an array'}, status=400)

            MAX_BATCH_SIZE = 100
            if len(bridge_names) > MAX_BATCH_SIZE:
                return web.json_response(
                    {'error': f'Maximum batch size is {MAX_BATCH_SIZE}'},
                    status=400
                )

            results = {}
            for bridge_name in bridge_names:
                if isinstance(bridge_name, str) and bridge_name:
                    results[bridge_name] = parse_bridge_name(bridge_name)

            return web.json_response({'results': results})

        app.router.add_post('/bridge/parse', parse_bridge_names_batch)

        return app

    @unittest_run_loop
    async def test_batch_parse_single(self):
        """Test batch parsing with single bridge."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"bridge_names": ["le1x1-sp1x2"]}
        )
        assert resp.status == 200

        data = await resp.json()
        assert 'le1x1-sp1x2' in data['results']
        assert data['results']['le1x1-sp1x2']['source_device_name'] == 'leaf1'

    @unittest_run_loop
    async def test_batch_parse_multiple(self):
        """Test batch parsing with multiple bridges."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"bridge_names": ["le1x1-sp1x2", "fi1x1-bo1x5", "ho1xet0-le1x10"]}
        )
        assert resp.status == 200

        data = await resp.json()
        assert len(data['results']) == 3
        assert data['results']['le1x1-sp1x2']['source_device_name'] == 'leaf1'
        assert data['results']['fi1x1-bo1x5']['source_device_name'] == 'firewall1'
        assert data['results']['ho1xet0-le1x10']['source_device_name'] == 'host1'

    @unittest_run_loop
    async def test_batch_parse_empty_array(self):
        """Test batch parsing with empty array."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"bridge_names": []}
        )
        assert resp.status == 400

        data = await resp.json()
        assert 'error' in data

    @unittest_run_loop
    async def test_batch_parse_missing_field(self):
        """Test batch parsing with missing bridge_names field."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"other_field": "value"}
        )
        assert resp.status == 400

    @unittest_run_loop
    async def test_batch_parse_invalid_type(self):
        """Test batch parsing with non-array bridge_names."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"bridge_names": "not-an-array"}
        )
        assert resp.status == 400

    @unittest_run_loop
    async def test_batch_parse_skips_invalid_entries(self):
        """Test that batch parsing skips non-string entries."""
        resp = await self.client.request(
            "POST",
            "/bridge/parse",
            json={"bridge_names": ["le1x1-sp1x2", 123, None, "", "fi1x1-bo1x5"]}
        )
        assert resp.status == 200

        data = await resp.json()
        # Should only have 2 valid results (strings that are non-empty)
        assert len(data['results']) == 2
        assert 'le1x1-sp1x2' in data['results']
        assert 'fi1x1-bo1x5' in data['results']


class TestBridgeAbbreviationsEndpoint(AioHTTPTestCase):
    """Test GET /bridge/abbreviations endpoint."""

    async def get_application(self):
        """Create a minimal app with just the bridge routes for testing."""
        from bridge_utils import get_abbreviation_mapping, is_legacy_abbreviation

        app = web.Application()

        async def get_device_abbreviations(request):
            abbreviations = get_abbreviation_mapping()
            legacy = [abbrev for abbrev in abbreviations if is_legacy_abbreviation(abbrev)]
            return web.json_response({
                'abbreviations': abbreviations,
                'legacy': legacy,
                'note': 'Legacy abbreviations are supported for parsing but not generated'
            })

        app.router.add_get('/bridge/abbreviations', get_device_abbreviations)

        return app

    @unittest_run_loop
    async def test_get_abbreviations(self):
        """Test getting device abbreviations."""
        resp = await self.client.request("GET", "/bridge/abbreviations")
        assert resp.status == 200

        data = await resp.json()
        assert 'abbreviations' in data
        assert 'legacy' in data
        assert 'note' in data

    @unittest_run_loop
    async def test_abbreviations_contains_standard_devices(self):
        """Test that abbreviations contain standard device types."""
        resp = await self.client.request("GET", "/bridge/abbreviations")
        data = await resp.json()

        abbreviations = data['abbreviations']
        assert abbreviations.get('sp') == 'spine'
        assert abbreviations.get('le') == 'leaf'
        assert abbreviations.get('bo') == 'borderleaf'
        assert abbreviations.get('fi') == 'firewall'
        assert abbreviations.get('ga') == 'gateway'

    @unittest_run_loop
    async def test_legacy_abbreviations_identified(self):
        """Test that legacy abbreviations are correctly identified."""
        resp = await self.client.request("GET", "/bridge/abbreviations")
        data = await resp.json()

        legacy = data['legacy']
        assert 'bl' in legacy  # borderleaf legacy
        assert 'gw' in legacy  # gateway legacy
        assert 'fw' in legacy  # firewall legacy

    @unittest_run_loop
    async def test_standard_abbreviations_not_in_legacy(self):
        """Test that standard abbreviations are not in legacy list."""
        resp = await self.client.request("GET", "/bridge/abbreviations")
        data = await resp.json()

        legacy = data['legacy']
        assert 'bo' not in legacy  # standard borderleaf
        assert 'ga' not in legacy  # standard gateway
        assert 'fi' not in legacy  # standard firewall

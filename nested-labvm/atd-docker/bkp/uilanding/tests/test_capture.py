"""Tests for capture proxy handlers (CaptureBridgesAPIHandler et al.)."""

import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.capture import (
    CaptureBridgesAPIHandler,
    CaptureStatusAPIHandler,
    CaptureStartAPIHandler,
    CaptureStopAPIHandler,
)


def _make_app(cookie_secret='test-secret'):
    """Build a minimal Tornado app with capture handlers."""
    return tornado.web.Application(
        [
            (r'/td-api/capture/bridges', CaptureBridgesAPIHandler),
            (r'/td-api/capture/status', CaptureStatusAPIHandler),
            (r'/td-api/capture/start', CaptureStartAPIHandler),
            (r'/td-api/capture/stop', CaptureStopAPIHandler),
        ],
        cookie_secret=cookie_secret,
    )


def _mock_http_response(code=200, body=b'{"bridges": []}'):
    """Return a mock tornado HTTP response."""
    resp = MagicMock()
    resp.code = code
    resp.body = body
    return resp


def _fetch_with_auth(test_case, path, method='GET', body=None):
    """Issue a request with a valid 'user' secure cookie."""
    signed = tornado.web.create_signed_value(
        test_case._app.settings['cookie_secret'], 'user', 'arista'
    )
    cookie_str = f'user={signed.decode()}'
    headers = {'Cookie': cookie_str}
    kwargs = {'method': method, 'headers': headers}
    if body is not None:
        kwargs['body'] = body
    elif method == 'POST':
        kwargs['body'] = b''
    return test_case.fetch(path, **kwargs)


# ---------------------------------------------------------------------------
# test_capture_bridges_returns_json
# ---------------------------------------------------------------------------

class TestCaptureBridgesReturnsJson(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.capture.AsyncHTTPClient')
    def test_capture_bridges_returns_json(self, mock_client_cls):
        """Authenticated GET /td-api/capture/bridges returns JSON with bridges list."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bridges_data = [{'name': 'veth-sp1-le1', 'source_device': 'sp1', 'target_device': 'le1'}]
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'bridges': bridges_data}).encode())
        )

        response = _fetch_with_auth(self, '/td-api/capture/bridges')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'bridges' in body
        assert 'count' in body
        assert body['count'] == 1


# ---------------------------------------------------------------------------
# test_capture_bridges_fallback_on_primary_failure
# ---------------------------------------------------------------------------

class TestCaptureBridgesFallback(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.capture.AsyncHTTPClient')
    def test_capture_bridges_fallback_on_primary_failure(self, mock_client_cls):
        """When primary URL fails, handler tries fallback and returns its data."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        fallback_bridges = [{'name': 'veth-sp1-le2'}]
        fallback_response = _mock_http_response(200, json.dumps({'bridges': fallback_bridges}).encode())

        async def _side_effect(url, request_timeout=5):
            if 'host.docker.internal' in url:
                raise OSError('connection refused')
            return fallback_response

        mock_client.fetch = _side_effect

        response = _fetch_with_auth(self, '/td-api/capture/bridges')
        assert response.code == 200
        body = json.loads(response.body)
        assert body['count'] == 1
        assert body['bridges'][0]['name'] == 'veth-sp1-le2'


# ---------------------------------------------------------------------------
# test_capture_both_fail_503
# ---------------------------------------------------------------------------

class TestCaptureBothFail503(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.capture.AsyncHTTPClient')
    def test_capture_both_fail_503(self, mock_client_cls):
        """When both primary and fallback fail, returns 503 with error body."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        async def _always_fail(url, request_timeout=5):
            raise OSError('connection refused')

        mock_client.fetch = _always_fail

        response = _fetch_with_auth(self, '/td-api/capture/bridges')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'error' in body
        assert 'bridges' in body


# ---------------------------------------------------------------------------
# test_capture_auth_required
# ---------------------------------------------------------------------------

class TestCaptureAuthRequired(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    def test_capture_bridges_auth_required(self):
        """GET /td-api/capture/bridges without cookie returns 401."""
        response = self.fetch('/td-api/capture/bridges')
        assert response.code == 401
        body = json.loads(response.body)
        assert 'error' in body
        assert 'Authentication required' in body['error']

    def test_capture_status_auth_required(self):
        """GET /td-api/capture/status without cookie returns 401."""
        response = self.fetch('/td-api/capture/status')
        assert response.code == 401

    def test_capture_start_auth_required(self):
        """POST /td-api/capture/start without cookie returns 401."""
        response = self.fetch('/td-api/capture/start', method='POST', body=b'')
        assert response.code == 401

    def test_capture_stop_auth_required(self):
        """POST /td-api/capture/stop without cookie returns 401."""
        response = self.fetch('/td-api/capture/stop', method='POST', body=b'')
        assert response.code == 401


# ---------------------------------------------------------------------------
# test_capture_bridge_enrichment
# ---------------------------------------------------------------------------

class TestCaptureBridgeEnrichment(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.capture.AsyncHTTPClient')
    @patch('handlers.capture._get_topo_build_data')
    def test_capture_bridge_enrichment(self, mock_topo_data, mock_client_cls):
        """enrich_with_topology maps device short codes to full names from topo data."""
        mock_topo_data.return_value = {
            'nodes': [
                {'spine1': {}},
                {'leaf1': {}},
            ]
        }

        bridges_raw = [
            {
                'name': 'veth-sp1-le1',
                'source_device': 'sp1',
                'target_device': 'le1',
            }
        ]

        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'bridges': bridges_raw}).encode())
        )

        response = _fetch_with_auth(self, '/td-api/capture/bridges')
        assert response.code == 200
        body = json.loads(response.body)
        bridges = body['bridges']
        assert len(bridges) == 1
        # Short code 'sp1' -> 'spine1', 'le1' -> 'leaf1'
        assert bridges[0].get('source_device_name') == 'spine1'
        assert bridges[0].get('target_device_name') == 'leaf1'

"""Tests for latency proxy handlers."""

import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.latency import (
    LatencyBridgesAPIHandler,
    LatencyEnableAPIHandler,
    LatencyDisableAPIHandler,
    LatencyDisableAllAPIHandler,
)


def _make_app(cookie_secret='test-secret'):
    """Build a minimal Tornado app with latency handlers."""
    return tornado.web.Application(
        [
            (r'/td-api/latency/bridges', LatencyBridgesAPIHandler),
            (r'/td-api/latency/enable', LatencyEnableAPIHandler),
            (r'/td-api/latency/disable', LatencyDisableAPIHandler),
            (r'/td-api/latency/disable-all', LatencyDisableAllAPIHandler),
        ],
        cookie_secret=cookie_secret,
    )


def _mock_http_response(code=200, body=b'{"ok": true}'):
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
# test_latency_bridges_proxies
# ---------------------------------------------------------------------------

class TestLatencyBridgesProxies(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.latency.AsyncHTTPClient')
    def test_latency_bridges_proxies(self, mock_client_cls):
        """Authenticated GET /td-api/latency/bridges returns proxied JSON."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        bridges_data = [{'name': 'veth-sp1-le1', 'latency_ms': 0}]
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'bridges': bridges_data}).encode())
        )

        response = _fetch_with_auth(self, '/td-api/latency/bridges')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'bridges' in body
        assert body['count'] == 1
        # Verify we hit the primary URL
        call_args = mock_client.fetch.call_args
        url = call_args[0][0]
        assert 'host.docker.internal' in url
        assert '/latency/bridges' in url


# ---------------------------------------------------------------------------
# test_latency_enable_proxies_post
# ---------------------------------------------------------------------------

class TestLatencyEnableProxiesPost(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.latency.AsyncHTTPClient')
    def test_latency_enable_proxies_post(self, mock_client_cls):
        """Authenticated POST /td-api/latency/enable forwards body to capture service."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'status': 'ok'}).encode())
        )

        payload = json.dumps({'bridge': 'veth-sp1-le1', 'delay_ms': 100}).encode()
        response = _fetch_with_auth(self, '/td-api/latency/enable', method='POST', body=payload)
        assert response.code == 200
        body = json.loads(response.body)
        assert body.get('status') == 'ok'

        # Verify the fetch was called with an HTTPRequest whose body matches
        call_args = mock_client.fetch.call_args[0][0]
        assert call_args.method == 'POST'
        request_body = json.loads(call_args.body)
        assert request_body['bridge'] == 'veth-sp1-le1'
        assert request_body['delay_ms'] == 100


# ---------------------------------------------------------------------------
# test_latency_disable_all_proxies
# ---------------------------------------------------------------------------

class TestLatencyDisableAllProxies(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.latency.AsyncHTTPClient')
    def test_latency_disable_all_proxies(self, mock_client_cls):
        """POST /td-api/latency/disable-all proxies to captureservice disable-all endpoint."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'cleared': 3}).encode())
        )

        response = _fetch_with_auth(self, '/td-api/latency/disable-all', method='POST', body=b'')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'cleared' in body

        call_args = mock_client.fetch.call_args[0][0]
        assert '/latency/disable-all' in call_args.url


# ---------------------------------------------------------------------------
# test_latency_service_unavailable_503
# ---------------------------------------------------------------------------

class TestLatencyServiceUnavailable(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.latency.AsyncHTTPClient')
    def test_latency_service_unavailable_503(self, mock_client_cls):
        """When both primary and fallback fail, returns 503."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        async def _always_fail(request, raise_error=False):
            raise OSError('connection refused')

        mock_client.fetch = _always_fail

        response = _fetch_with_auth(self, '/td-api/latency/bridges')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'error' in body
        assert 'unavailable' in body['error'].lower()

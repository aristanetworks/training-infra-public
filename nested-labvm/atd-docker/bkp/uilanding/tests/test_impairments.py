"""Tests for network impairment proxy handlers."""

import json
import os
import sys
from unittest.mock import MagicMock, AsyncMock, patch

import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.impairments import (
    ImpairmentsBridgesAPIHandler,
    ImpairmentsConfigureAPIHandler,
    ImpairmentsClearAPIHandler,
    ImpairmentsClearAllAPIHandler,
)


def _make_app(cookie_secret='test-secret'):
    """Build a minimal Tornado app with impairment handlers."""
    return tornado.web.Application(
        [
            (r'/td-api/impairments/bridges', ImpairmentsBridgesAPIHandler),
            (r'/td-api/impairments/configure', ImpairmentsConfigureAPIHandler),
            (r'/td-api/impairments/clear', ImpairmentsClearAPIHandler),
            (r'/td-api/impairments/clear-all', ImpairmentsClearAllAPIHandler),
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
# test_impairments_bridges_proxies
# ---------------------------------------------------------------------------

class TestImpairmentsBridgesProxies(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.impairments.AsyncHTTPClient')
    def test_impairments_bridges_proxies(self, mock_client_cls):
        """Authenticated GET /td-api/impairments/bridges returns proxied JSON."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        impairments_data = {'bridges': [{'name': 'veth-sp1-le1', 'latency_ms': 50, 'loss_pct': 0}]}
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps(impairments_data).encode())
        )

        response = _fetch_with_auth(self, '/td-api/impairments/bridges')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'bridges' in body
        assert len(body['bridges']) == 1

        # Verify it proxied to the primary capture service URL
        call_args = mock_client.fetch.call_args[0][0]
        assert 'host.docker.internal' in call_args.url
        assert '/impairments/bridges' in call_args.url


# ---------------------------------------------------------------------------
# test_impairments_configure_proxies
# ---------------------------------------------------------------------------

class TestImpairmentsConfigureProxies(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.impairments.AsyncHTTPClient')
    def test_impairments_configure_proxies(self, mock_client_cls):
        """POST /td-api/impairments/configure forwards config body to captureservice."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'status': 'configured'}).encode())
        )

        payload = json.dumps({
            'bridge': 'veth-sp1-le1',
            'latency': 50,
            'loss': 1.5,
        }).encode()
        response = _fetch_with_auth(self, '/td-api/impairments/configure', method='POST', body=payload)
        assert response.code == 200
        body = json.loads(response.body)
        assert body.get('status') == 'configured'

        # Check the proxied request body is correct
        call_args = mock_client.fetch.call_args[0][0]
        assert call_args.method == 'POST'
        forwarded = json.loads(call_args.body)
        assert forwarded['bridge'] == 'veth-sp1-le1'
        assert forwarded['latency'] == 50
        assert forwarded['loss'] == 1.5


# ---------------------------------------------------------------------------
# test_impairments_clear_all_proxies
# ---------------------------------------------------------------------------

class TestImpairmentsClearAllProxies(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.impairments.AsyncHTTPClient')
    def test_impairments_clear_all_proxies(self, mock_client_cls):
        """POST /td-api/impairments/clear-all proxies to captureservice clear-all endpoint."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(
            return_value=_mock_http_response(200, json.dumps({'cleared': 5}).encode())
        )

        response = _fetch_with_auth(self, '/td-api/impairments/clear-all', method='POST', body=b'')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'cleared' in body
        assert body['cleared'] == 5

        call_args = mock_client.fetch.call_args[0][0]
        assert '/impairments/clear-all' in call_args.url


# ---------------------------------------------------------------------------
# test_impairments_service_unavailable_503
# ---------------------------------------------------------------------------

class TestImpairmentsServiceUnavailable(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.impairments.AsyncHTTPClient')
    def test_impairments_service_unavailable_503(self, mock_client_cls):
        """When both primary and fallback fail, returns 503 with error message."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        async def _always_fail(request, raise_error=False):
            raise OSError('connection refused')

        mock_client.fetch = _always_fail

        response = _fetch_with_auth(self, '/td-api/impairments/bridges')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'error' in body
        assert 'unavailable' in body['error'].lower()

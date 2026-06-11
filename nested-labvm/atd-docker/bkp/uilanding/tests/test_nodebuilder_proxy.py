"""Tests for NodeBuilderProxyHandler."""

import os
import sys
import json
from unittest.mock import MagicMock, AsyncMock, patch

import tornado.web
import tornado.testing
import tornado.httpclient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.nodebuilder_proxy import NodeBuilderProxyHandler


def _make_app(cookie_secret='test-secret'):
    """Build a minimal Tornado app with NodeBuilderProxyHandler."""
    return tornado.web.Application(
        [(r'/td-api/nodes/(.*)', NodeBuilderProxyHandler)],
        cookie_secret=cookie_secret,
    )


def _mock_http_response(code=200, body=b'{"ok": true}'):
    """Return a mock tornado HTTP response."""
    resp = MagicMock()
    resp.code = code
    resp.body = body
    return resp


def _set_auth_cookie(test_case):
    """Return Authorization header dict with a valid secure cookie."""
    cookie_val = test_case._app.settings['cookie_secret']
    cookie = test_case.get_cookie('user')
    return {}


# ---------------------------------------------------------------------------
# Helper: fetch with a forged auth cookie so current_user is set
# ---------------------------------------------------------------------------

def _fetch_with_auth(test_case, path, method='GET', body=None):
    """
    Issue a request with a valid 'user' secure cookie so BaseHandler.current_user
    is truthy.
    """
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
# Tests
# ---------------------------------------------------------------------------

class TestNodeBuilderProxyGet(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.nodebuilder_proxy.AsyncHTTPClient')
    def test_get_proxies_to_nodebuilder(self, mock_client_cls):
        """Authenticated GET is forwarded to the primary nodebuilder URL."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(return_value=_mock_http_response(200, b'{"nodes": []}'))

        response = _fetch_with_auth(self, '/td-api/nodes/list')
        assert response.code == 200
        mock_client.fetch.assert_called_once()
        request_arg = mock_client.fetch.call_args[0][0]
        assert 'host.docker.internal:8090' in request_arg.url or '172.17.0.1:8090' in request_arg.url

    def test_auth_required_no_cookie_returns_401(self):
        """GET without auth cookie returns 401."""
        response = self.fetch('/td-api/nodes/list')
        assert response.code == 401
        body = json.loads(response.body)
        assert 'Authentication required' in body['error']


class TestNodeBuilderProxyPost(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.nodebuilder_proxy.AsyncHTTPClient')
    def test_post_proxies_with_body(self, mock_client_cls):
        """Authenticated POST forwards body to nodebuilder."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.fetch = AsyncMock(return_value=_mock_http_response(200, b'{"result": "ok"}'))

        payload = json.dumps({'action': 'add', 'name': 'leaf3'}).encode()
        response = _fetch_with_auth(self, '/td-api/nodes/create', method='POST', body=payload)
        assert response.code == 200
        mock_client.fetch.assert_called_once()
        request_arg = mock_client.fetch.call_args[0][0]
        assert request_arg.method == 'POST'
        assert request_arg.body == payload

    def test_post_auth_required_returns_401(self):
        """POST without auth cookie returns 401."""
        response = self.fetch('/td-api/nodes/create', method='POST', body=b'{}')
        assert response.code == 401


class TestNodeBuilderProxyFallback(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return _make_app()

    @patch('handlers.nodebuilder_proxy.AsyncHTTPClient')
    def test_primary_fails_tries_fallback(self, mock_client_cls):
        """When primary raises, fallback URL is tried."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        fallback_resp = _mock_http_response(200, b'{"fallback": true}')

        async def _side_effect(request, raise_error=False):
            if 'host.docker.internal' in request.url:
                raise OSError('connection refused')
            return fallback_resp

        mock_client.fetch = _side_effect

        response = _fetch_with_auth(self, '/td-api/nodes/list')
        assert response.code == 200
        body = json.loads(response.body)
        assert body.get('fallback') is True

    @patch('handlers.nodebuilder_proxy.AsyncHTTPClient')
    def test_both_fail_returns_503(self, mock_client_cls):
        """When both primary and fallback fail, 503 is returned."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        async def _always_fail(request, raise_error=False):
            raise OSError('connection refused')

        mock_client.fetch = _always_fail

        response = _fetch_with_auth(self, '/td-api/nodes/list')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'error' in body

"""Tests for page and utility handlers extracted into handlers/pages.py."""

import json
import os
import sys
import shutil
import tempfile
from unittest.mock import patch

import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.pages import (
    topoRequestHandler,
    BaseUrlHandler,
    UptimeWithRuntimeHandler,
    TerminalPageHandler,
    ConsolePageHandler,
    ClientLogHandler,
    ConnectivityStatusHandler,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
ACCESS_INFO = os.path.join(FIXTURES_DIR, 'access_info.yaml')


def _make_page_config(html_dir, access_info_path=ACCESS_INFO):
    return {
        'base_path': html_dir + '/',
        'atd_access_path': access_info_path,
        'title': 'Test Lab',
    }


# ---------------------------------------------------------------------------
# ConnectivityStatusHandler
# ---------------------------------------------------------------------------

def _make_session_state(active=None, session_data=None, recent=None, grpc=None):
    return {
        'active_sessions': active if active is not None else set(),
        'active_session_data': session_data if session_data is not None else {},
        'recent_sessions': recent if recent is not None else {},
        'grpc_state': grpc if grpc is not None else {'status': None, 'last_check': None},
    }


class TestConnectivityRequiresAuth(tornado.testing.AsyncHTTPTestCase):
    """Test unauthenticated requests are rejected with 401."""

    def get_app(self):
        session_state = _make_session_state()
        return tornado.web.Application(
            [(r'/td-api/connectivity-status', ConnectivityStatusHandler, {'session_state': session_state})],
            cookie_secret='test-secret',
        )

    def test_connectivity_requires_auth(self):
        """GET without auth cookie returns 401."""
        response = self.fetch('/td-api/connectivity-status')
        assert response.code == 401
        body = json.loads(response.body)
        assert 'error' in body


# Subclass with auth always returning a user, so we can test the happy path.
class _AuthedConnectivityHandler(ConnectivityStatusHandler):
    def get_current_user(self):
        return b'arista'


class TestConnectivityReturnsJson(tornado.testing.AsyncHTTPTestCase):
    """Test authenticated connectivity status response shape."""

    def get_app(self):
        self._session_state = _make_session_state(
            active={'sess-1', 'sess-2'},
            session_data={'sess-1': {'ip': '1.2.3.4'}, 'sess-2': {'ip': '5.6.7.8'}},
            recent={'10.0.0.1': {'reconnect_count': 1}},
            grpc={'status': 'ok', 'last_check': 1234567890.0},
        )
        return tornado.web.Application(
            [(r'/td-api/connectivity-status', _AuthedConnectivityHandler, {'session_state': self._session_state})],
            cookie_secret='test-secret',
        )

    def test_connectivity_returns_json(self):
        """Authenticated GET returns active_sessions, session_data, and gRPC state."""
        response = self.fetch('/td-api/connectivity-status')
        assert response.code == 200
        body = json.loads(response.body)
        assert body['active_sessions'] == 2
        assert body['recent_disconnects'] == 1
        assert body['internal_grpc']['last_status'] == 'ok'
        assert body['internal_grpc']['last_check'] == 1234567890.0
        assert len(body['active_session_data']) == 2


# ---------------------------------------------------------------------------
# ClientLogHandler
# ---------------------------------------------------------------------------

class TestClientLogHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application(
            [(r'/td-api/client-log', ClientLogHandler)],
            cookie_secret='test-secret',
        )

    def test_client_log_accepts_post_returns_204(self):
        """POST a valid log event returns 204 No Content."""
        payload = json.dumps({
            'level': 'info',
            'message': 'Test message',
            'source': 'browser',
            'action': 'test_action',
        }).encode()
        response = self.fetch('/td-api/client-log', method='POST', body=payload)
        assert response.code == 204

    def test_client_log_invalid_json_still_returns_204(self):
        """POST invalid JSON still returns 204 (never crashes)."""
        response = self.fetch('/td-api/client-log', method='POST', body=b'not json{{{')
        assert response.code == 204

    def test_client_log_invalid_level_coerced_to_info(self):
        """POST with invalid log level is silently coerced to 'info'."""
        payload = json.dumps({'level': 'CRITICAL', 'message': 'hi'}).encode()
        response = self.fetch('/td-api/client-log', method='POST', body=payload)
        assert response.code == 204


# ---------------------------------------------------------------------------
# BaseUrlHandler
# ---------------------------------------------------------------------------

class TestBaseUrlHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        config = {'atd_access_path': ACCESS_INFO}
        return tornado.web.Application(
            [(r'/baseUrl', BaseUrlHandler, {'config': config})],
            cookie_secret='test-secret',
        )

    def test_base_url_returns_encoded_credentials(self):
        """GET /baseUrl returns a base64-encoded credential blob."""
        import base64
        response = self.fetch('/baseUrl')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'response' in body
        # Decode and verify structure
        decoded = json.loads(base64.b64decode(body['response']).decode())
        assert 'user' in decoded
        assert 'pwd' in decoded
        assert decoded['user'] == 'arista'
        assert decoded['pwd'] == 'arista123'


# ---------------------------------------------------------------------------
# UptimeWithRuntimeHandler
# ---------------------------------------------------------------------------

class TestUptimeWithRuntimeHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self._exam_state = {'start_time': 100, 'end_time': 200}
        return tornado.web.Application(
            [(r'/uptimeWithRuntime', UptimeWithRuntimeHandler, {
                'exam_state': self._exam_state,
                'topo_data': None,
            })],
            cookie_secret='test-secret',
        )

    @patch('requests.get')
    def test_uptime_returns_defaults_when_service_unavailable(self, mock_get):
        """When atd-uptime is unreachable, returns sensible defaults."""
        mock_get.side_effect = Exception("Connection refused")
        response = self.fetch('/uptimeWithRuntime')
        assert response.code == 200
        body = json.loads(response.body)
        # Should contain exam timing from exam_state
        assert body['exam_end_time'] == 200
        assert body['exam_start_time'] == 100
        # Defaults when uptime service is down
        assert 'runtime' in body
        assert 'status' in body


# ---------------------------------------------------------------------------
# TerminalPageHandler
# ---------------------------------------------------------------------------

class TestTerminalPageHandler(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        self._html_dir = tempfile.mkdtemp()
        # Write a minimal terminal template
        with open(os.path.join(self._html_dir, 'terminal.html'), 'w') as f:
            f.write('<html><body>Terminal</body></html>')
        super().setUp()

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self._html_dir, ignore_errors=True)

    def get_app(self):
        config = _make_page_config(self._html_dir)
        return tornado.web.Application(
            [(r'/terminal', TerminalPageHandler, {'config': config})],
            cookie_secret='test-secret',
            login_url='/login',
        )

    def test_terminal_unauthenticated_redirects_to_login(self):
        """GET /terminal without cookie redirects to /login."""
        response = self.fetch('/terminal', follow_redirects=False)
        assert response.code == 302
        assert '/login' in response.headers.get('Location', '')

    def test_terminal_unauthenticated_with_auth_param_redirects_to_login(self):
        """GET /terminal?auth=X redirects to /login?auth=X."""
        response = self.fetch('/terminal?auth=abc123', follow_redirects=False)
        assert response.code == 302
        loc = response.headers.get('Location', '')
        assert '/login' in loc
        assert 'auth=abc123' in loc


# ---------------------------------------------------------------------------
# ConsolePageHandler
# ---------------------------------------------------------------------------

class TestConsolePageHandler(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        self._html_dir = tempfile.mkdtemp()
        # Write a minimal console template
        with open(os.path.join(self._html_dir, 'console.html'), 'w') as f:
            f.write('<html><body>Console</body></html>')
        super().setUp()

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self._html_dir, ignore_errors=True)

    def get_app(self):
        config = _make_page_config(self._html_dir)
        return tornado.web.Application(
            [(r'/console/?', ConsolePageHandler, {'config': config})],
            cookie_secret='test-secret',
            login_url='/login',
        )

    def test_console_unauthenticated_redirects_to_login(self):
        """GET /console without cookie redirects to /login."""
        response = self.fetch('/console', follow_redirects=False)
        assert response.code == 302
        assert '/login' in response.headers.get('Location', '')

    def test_console_trailing_slash_also_redirects(self):
        """GET /console/ (trailing slash) also redirects unauthenticated users."""
        response = self.fetch('/console/', follow_redirects=False)
        assert response.code == 302
        assert '/login' in response.headers.get('Location', '')

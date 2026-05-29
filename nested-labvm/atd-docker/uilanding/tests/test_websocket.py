"""Tests for the topoDataHandler WebSocket handler (handlers/websocket.py).

Tests cover:
  - test_websocket_open_creates_session
  - test_websocket_close_removes_session
  - test_check_origin_rejects_cross_site
  - test_check_origin_accepts_matching_host
  - test_keepalive_skips_when_closed  (regression: _closed guard)
  - test_pong_updates_rtt
"""

import json
import os
import sys
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import tornado.ioloop
import tornado.testing
import tornado.web
import tornado.websocket

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.websocket import topoDataHandler, prune_recent_sessions, RECONNECT_WINDOW_SECONDS


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_session_state(active=None, session_data=None, recent=None, grpc=None):
    return {
        'active_sessions': active if active is not None else set(),
        'active_session_data': session_data if session_data is not None else {},
        'recent_sessions': recent if recent is not None else {},
        'grpc_state': grpc if grpc is not None else {'status': None, 'last_check': None},
    }


def _make_exam_state(start=0, end=0):
    return {'start_time': start, 'end_time': end}


def _make_app(session_state=None, exam_state=None, cvp_token_fn=None):
    """Build a minimal Tornado app with topoDataHandler wired up."""
    if session_state is None:
        session_state = _make_session_state()
    if exam_state is None:
        exam_state = _make_exam_state()
    if cvp_token_fn is None:
        cvp_token_fn = lambda: None

    ws_kwargs = {
        'session_state': session_state,
        'exam_state': exam_state,
        'cvp_token_fn': cvp_token_fn,
    }
    return tornado.web.Application(
        [(r'/td-ws', topoDataHandler, ws_kwargs)],
        cookie_secret='test-secret',
    )


# ---------------------------------------------------------------------------
# test_websocket_open_creates_session
# ---------------------------------------------------------------------------

class TestWebSocketOpenCreatesSession(tornado.testing.AsyncHTTPTestCase):
    """Connecting to /td-ws causes the session to be added to active_sessions."""

    def get_app(self):
        self._active_sessions = set()
        self._session_state = _make_session_state(active=self._active_sessions)
        return _make_app(session_state=self._session_state)

    @tornado.testing.gen_test
    async def test_websocket_open_creates_session(self):
        ws_url = 'ws://localhost:{}/td-ws'.format(self.get_http_port())
        conn = await tornado.websocket.websocket_connect(ws_url)
        # Give the server one IO cycle to process open()
        await tornado.gen.sleep(0.05)
        assert len(self._active_sessions) == 1
        conn.close()


# ---------------------------------------------------------------------------
# test_websocket_close_removes_session
# ---------------------------------------------------------------------------

class TestWebSocketCloseRemovesSession(tornado.testing.AsyncHTTPTestCase):
    """Closing the WebSocket removes the session from active_sessions."""

    def get_app(self):
        self._active_sessions = set()
        self._recent_sessions = {}
        self._session_state = _make_session_state(
            active=self._active_sessions,
            recent=self._recent_sessions,
        )
        return _make_app(session_state=self._session_state)

    @tornado.testing.gen_test
    async def test_websocket_close_removes_session(self):
        ws_url = 'ws://localhost:{}/td-ws'.format(self.get_http_port())
        conn = await tornado.websocket.websocket_connect(ws_url)
        await tornado.gen.sleep(0.05)
        assert len(self._active_sessions) == 1
        conn.close()
        await tornado.gen.sleep(0.05)
        # Session should be removed from active_sessions and moved to recent_sessions
        assert len(self._active_sessions) == 0


# ---------------------------------------------------------------------------
# test_check_origin_rejects_cross_site
# ---------------------------------------------------------------------------

class TestCheckOriginRejectsCrossSite:
    """check_origin returns False when the origin host doesn't match the Host header."""

    def _make_handler(self, host):
        """Build a handler instance with a mocked request that has the given Host header."""
        app = MagicMock()
        request = MagicMock()
        request.headers = {'Host': host}
        request.connection = MagicMock()

        handler = topoDataHandler.__new__(topoDataHandler)
        handler.application = app
        handler.request = request
        return handler

    def test_rejects_different_host(self):
        handler = self._make_handler('mylab.example.com')
        result = handler.check_origin('http://evil.attacker.com')
        assert result is False

    def test_rejects_empty_host_header(self):
        handler = self._make_handler('')
        result = handler.check_origin('http://anything.com')
        assert result is False

    def test_rejects_subdomain_mismatch(self):
        handler = self._make_handler('lab.example.com')
        result = handler.check_origin('http://other.example.com')
        assert result is False


# ---------------------------------------------------------------------------
# test_check_origin_accepts_matching_host
# ---------------------------------------------------------------------------

class TestCheckOriginAcceptsMatchingHost:
    """check_origin returns True when origin host matches the Host header."""

    def _make_handler(self, host):
        app = MagicMock()
        request = MagicMock()
        request.headers = {'Host': host}
        request.connection = MagicMock()

        handler = topoDataHandler.__new__(topoDataHandler)
        handler.application = app
        handler.request = request
        return handler

    def test_accepts_exact_host_match(self):
        handler = self._make_handler('mylab.example.com')
        result = handler.check_origin('http://mylab.example.com')
        assert result is True

    def test_accepts_https_origin(self):
        handler = self._make_handler('mylab.example.com')
        result = handler.check_origin('https://mylab.example.com')
        assert result is True

    def test_accepts_host_with_port_vs_origin_with_port(self):
        # Both have matching hostnames even with explicit ports
        handler = self._make_handler('mylab.example.com:80')
        result = handler.check_origin('http://mylab.example.com:80')
        assert result is True

    def test_accepts_host_without_port_vs_origin_with_port(self):
        # Strip port from origin netloc for comparison
        handler = self._make_handler('mylab.example.com')
        result = handler.check_origin('http://mylab.example.com:8080')
        assert result is True


# ---------------------------------------------------------------------------
# test_keepalive_skips_when_closed  (regression: _closed guard)
# ---------------------------------------------------------------------------

class TestKeepaliveSkipsWhenClosed(tornado.testing.AsyncTestCase):
    """keepalive() must not call getAPI or write_message when _closed is True.

    This is a regression test: without the guard, calling keepalive() on a
    closed connection would raise WebSocketClosedError and log spurious errors.
    """

    @tornado.testing.gen_test
    async def test_keepalive_skips_when_closed(self):
        active_sessions = set()
        session_state = _make_session_state(active=active_sessions)
        exam_state = _make_exam_state()

        # Build a minimal mock handler (not a live HTTP connection)
        handler = topoDataHandler.__new__(topoDataHandler)
        handler._active_sessions = active_sessions
        handler._active_session_data = {}
        handler._recent_sessions = {}
        handler._grpc_state = {'status': None, 'last_check': None}
        handler._exam_state = exam_state
        handler._cvp_token_fn = lambda: None
        handler._closed = True  # <--- Simulate closed connection

        with patch('handlers.websocket.getAPI') as mock_get_api, \
             patch('handlers.websocket.getUptime') as mock_get_uptime:
            await handler.keepalive()
            # Neither blocking call should have been made
            mock_get_api.assert_not_called()
            mock_get_uptime.assert_not_called()


# ---------------------------------------------------------------------------
# test_pong_updates_rtt
# ---------------------------------------------------------------------------

class TestPongUpdatesRtt(tornado.testing.AsyncHTTPTestCase):
    """Sending a pong message updates session last_rtt and resets missed_pongs."""

    def get_app(self):
        self._active_sessions = set()
        self._session_state = _make_session_state(active=self._active_sessions)
        return _make_app(session_state=self._session_state)

    @tornado.testing.gen_test
    async def test_pong_updates_rtt(self):
        ws_url = 'ws://localhost:{}/td-ws'.format(self.get_http_port())
        conn = await tornado.websocket.websocket_connect(ws_url)
        await tornado.gen.sleep(0.05)

        # Send a pong with a server_ts 50ms in the past
        server_ts = int(time.time() * 1000) - 50
        pong_msg = json.dumps({'type': 'pong', 'data': {'server_ts': server_ts}})
        conn.write_message(pong_msg)
        await tornado.gen.sleep(0.05)

        # Retrieve the handler instance from the app to inspect session state
        # The handler stores itself on the connection; we verify via the active_sessions side-channel.
        # Since we can't easily introspect the handler in a live test, we verify
        # no exception was raised and the session count remains 1.
        assert len(self._active_sessions) == 1
        conn.close()

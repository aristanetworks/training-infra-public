"""Tests for lab handlers — LabHandler, LabStausHandler, ResetLabHandler."""

import os
import sys
import json
from unittest.mock import MagicMock, patch

import docker.errors
import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.lab import LabHandler, LabStausHandler, ResetLabHandler


def _make_docker_client(exec_output=b''):
    """Return a mock Docker client with a container that returns exec_output."""
    mock_exec_result = MagicMock()
    mock_exec_result.exit_code = 0
    mock_exec_result.output = exec_output

    mock_container = MagicMock()
    mock_container.exec_run.return_value = mock_exec_result

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    return mock_client, mock_container


def _make_app(docker_client, default_menu_file_value='TOPO'):
    """Build a minimal Tornado application with the three lab handlers."""
    kwargs = {
        'docker_client': docker_client,
        'default_menu_file_value': default_menu_file_value,
    }
    return tornado.web.Application([
        (r'/lab', LabHandler, kwargs),
        (r'/labStaus', LabStausHandler, kwargs),
        (r'/resetLab', ResetLabHandler, kwargs),
    ], cookie_secret='test-secret')


# ---------------------------------------------------------------------------
# LabHandler
# ---------------------------------------------------------------------------

class TestLabHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.mock_client, self.mock_container = _make_docker_client()
        return _make_app(self.mock_client)

    def test_lab_dispatches_docker_exec(self):
        """GET /lab?lab_value=X calls exec_run on the login container."""
        response = self.fetch('/lab?lab_value=1')
        assert response.code == 200
        body = json.loads(response.body)
        assert 'response' in body
        self.mock_container.exec_run.assert_called_once()
        call_args = self.mock_container.exec_run.call_args[0][0]
        assert 'callConfigTopo.py' in call_args
        assert '1' in call_args

    def test_lab_docker_unavailable_returns_503(self):
        """GET /lab?lab_value=X with docker_client=None returns 503."""
        app = _make_app(docker_client=None)

        class _SubApp(tornado.testing.AsyncHTTPTestCase):
            def get_app(inner_self):
                return app

        tc = _SubApp()
        tc.setUp()
        response = tc.fetch('/lab?lab_value=1')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'error' in body
        tc.tearDown()

    def test_lab_container_not_found_returns_503(self):
        """GET /lab?lab_value=X when container missing returns 503."""
        self.mock_client.containers.get.side_effect = docker.errors.NotFound('not found')
        response = self.fetch('/lab?lab_value=1')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'Login container not found' in body['error']

    def test_lab_generic_exception_returns_500(self):
        """GET /lab?lab_value=X on unexpected docker error returns 500."""
        self.mock_client.containers.get.side_effect = RuntimeError('boom')
        response = self.fetch('/lab?lab_value=1')
        assert response.code == 500
        body = json.loads(response.body)
        assert 'Docker error' in body['error']


# ---------------------------------------------------------------------------
# LabStausHandler
# ---------------------------------------------------------------------------

class TestLabStausHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        raw_output = (
            b"2024-01-01 INFO Checking switches\n"
            b"leaf1,connected\n"
            b"leaf2,connected\n"
            b"WARNING - completed check\n"
            b"spine1,connected\n"
        )
        self.mock_client, self.mock_container = _make_docker_client(exec_output=raw_output)
        return _make_app(self.mock_client)

    def test_lab_status_filters_output(self):
        """GET /labStaus returns only name,status lines, stripping log cruft."""
        response = self.fetch('/labStaus')
        assert response.code == 200
        body = json.loads(response.body)
        items = body['response']
        assert 'leaf1,connected' in items
        assert 'leaf2,connected' in items
        assert 'spine1,connected' in items
        for item in items:
            assert 'INFO' not in item
            assert 'WARNING' not in item
            assert 'Checking' not in item

    def test_lab_status_docker_unavailable_returns_503(self):
        """GET /labStaus with docker_client=None returns 503."""
        app = _make_app(docker_client=None)

        class _SubApp(tornado.testing.AsyncHTTPTestCase):
            def get_app(inner_self):
                return app

        tc = _SubApp()
        tc.setUp()
        response = tc.fetch('/labStaus')
        assert response.code == 503
        tc.tearDown()

    def test_lab_status_container_not_found_returns_503(self):
        """GET /labStaus when container missing returns 503."""
        self.mock_client.containers.get.side_effect = docker.errors.NotFound('not found')
        response = self.fetch('/labStaus')
        assert response.code == 503
        body = json.loads(response.body)
        assert 'Login container not found' in body['error']


# ---------------------------------------------------------------------------
# ResetLabHandler
# ---------------------------------------------------------------------------

class TestResetLabHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.mock_client, self.mock_container = _make_docker_client()
        return _make_app(self.mock_client)

    def test_reset_lab_executes(self):
        """GET /resetLab?lab_names=X calls exec_run on the login container."""
        response = self.fetch('/resetLab?lab_names=leaf1,leaf2')
        assert response.code == 200
        body = json.loads(response.body)
        assert body['response'] == 'leaf1,leaf2'
        self.mock_container.exec_run.assert_called_once()
        call_args = self.mock_container.exec_run.call_args[0][0]
        assert 'resetVMs.py' in call_args

    def test_reset_lab_docker_none_still_returns_200(self):
        """GET /resetLab with docker_client=None returns 200 (response echoed, exec skipped)."""
        app = _make_app(docker_client=None)

        class _SubApp(tornado.testing.AsyncHTTPTestCase):
            def get_app(inner_self):
                return app

        tc = _SubApp()
        tc.setUp()
        response = tc.fetch('/resetLab?lab_names=leaf1')
        assert response.code == 200
        body = json.loads(response.body)
        assert body['response'] == 'leaf1'
        tc.tearDown()

    def test_reset_lab_exec_exception_does_not_crash(self):
        """GET /resetLab when exec_run raises still returns 200."""
        self.mock_container.exec_run.side_effect = RuntimeError('docker gone')
        response = self.fetch('/resetLab?lab_names=leaf1')
        assert response.code == 200

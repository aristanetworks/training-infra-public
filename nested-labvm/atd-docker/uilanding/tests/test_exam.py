"""Tests for exam handlers — all 12 handlers in handlers/exam.py."""

import json
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, patch, call

import tornado.testing
import tornado.web

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from handlers.exam import (
    BeginExamHandler,
    EndExamHandler,
    ExamAlreadyRunningHandler,
    ExamAuthenticationHandler,
    ExamRedoRedirectHandler,
    ExamStatusHandler,
    ExamSubmitHandler,
    ExamSubmittedRedirectHandler,
    GetAccessInfoHandler,
    GetClientIdHandler,
    GetExamInstructionsHandler,
    GetUserSessionIdHandler,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')
HTML_DIR = os.path.join(FIXTURES_DIR, 'html')


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_config(base_path=None, atd_access_path=None):
    return {
        'base_path': base_path or HTML_DIR + '/',
        'atd_access_path': atd_access_path or os.path.join(FIXTURES_DIR, 'access_info.yaml'),
        'project': 'test-project',
        'honorlock_client_id': 'test-client-id',
        'honorlock_secret': 'test-secret',
    }


def _make_docker_client():
    mock_container = MagicMock()
    mock_container.exec_run.return_value = MagicMock(exit_code=0, output=b'')
    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container
    return mock_client, mock_container


_UNSET = object()  # sentinel so callers can pass docker_client=None explicitly


def _make_exam_kwargs(base_path=None, atd_access_path=None, docker_client=_UNSET, exam_state=None):
    """Build exam handler kwargs.  Pass docker_client=None explicitly for no-docker tests."""
    if docker_client is _UNSET:
        docker_client, _ = _make_docker_client()
    return {
        'config': _make_config(base_path=base_path, atd_access_path=atd_access_path),
        'docker_client': docker_client,
        'exam_state': exam_state if exam_state is not None else {'start_time': 0, 'end_time': 0},
    }


# ---------------------------------------------------------------------------
# ExamSubmittedRedirectHandler
# ---------------------------------------------------------------------------

class TestExamSubmittedRedirectHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/exam-submitted', ExamSubmittedRedirectHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_exam_submitted_renders_page(self):
        response = self.fetch('/exam-submitted')
        assert response.code == 200
        assert b'Exam Submitted' in response.body

    def test_exam_submitted_not_found_returns_404(self):
        kwargs = _make_exam_kwargs(base_path='/nonexistent/path/')
        app = tornado.web.Application([
            (r'/exam-submitted', ExamSubmittedRedirectHandler, kwargs),
        ], cookie_secret='test-secret')

        class _Sub(tornado.testing.AsyncHTTPTestCase):
            def get_app(s): return app

        tc = _Sub()
        tc.setUp()
        response = tc.fetch('/exam-submitted')
        assert response.code == 404
        tc.tearDown()


# ---------------------------------------------------------------------------
# ExamAlreadyRunningHandler
# ---------------------------------------------------------------------------

class TestExamAlreadyRunningHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/exam-already-running', ExamAlreadyRunningHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_returns_200_with_html(self):
        response = self.fetch('/exam-already-running')
        assert response.code == 200
        assert b'Exam Already Running' in response.body


# ---------------------------------------------------------------------------
# ExamAuthenticationHandler
# ---------------------------------------------------------------------------

class TestExamAuthenticationHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/exam-authentication', ExamAuthenticationHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_exam_auth_renders_page(self):
        response = self.fetch('/exam-authentication')
        assert response.code == 200
        assert b'Honorlock Auth' in response.body


# ---------------------------------------------------------------------------
# GetClientIdHandler
# ---------------------------------------------------------------------------

class TestGetClientIdHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/getClientId', GetClientIdHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_get_client_id_calls_honorlock(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'access_token': 'abc123'}
        with patch('handlers.exam.requests.post', return_value=mock_resp) as mock_post:
            response = self.fetch('/getClientId')
        assert response.code == 200
        body = json.loads(response.body)
        assert body['access_token'] == 'abc123'
        mock_post.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert 'honorlock.com' in call_url

    def test_get_client_id_passes_credentials(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}
        with patch('handlers.exam.requests.post', return_value=mock_resp) as mock_post:
            self.fetch('/getClientId')
        call_kwargs = mock_post.call_args[1]
        # data is passed as a JSON string; check it contains the credentials
        assert 'test-client-id' in call_kwargs['data']
        assert 'test-secret' in call_kwargs['data']

    def test_get_client_id_honorlock_error_propagates(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.json.return_value = {'error': 'forbidden'}
        with patch('handlers.exam.requests.post', return_value=mock_resp):
            response = self.fetch('/getClientId')
        assert response.code == 403


# ---------------------------------------------------------------------------
# GetExamInstructionsHandler
# ---------------------------------------------------------------------------

class TestGetExamInstructionsHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/getExamInstructions', GetExamInstructionsHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_missing_auth_header_returns_401(self):
        body = json.dumps({'external_exam_id': 'exam-001'})
        response = self.fetch('/getExamInstructions', method='POST', body=body)
        assert response.code == 401
        data = json.loads(response.body)
        assert 'Authorization' in data['error'] or 'token' in data['error'].lower()

    def test_fetches_instructions_from_honorlock(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'instructions': 'Read carefully'}
        with patch('handlers.exam.requests.get', return_value=mock_resp):
            body = json.dumps({'external_exam_id': 'exam-001'})
            response = self.fetch(
                '/getExamInstructions',
                method='POST',
                body=body,
                headers={'Authorization': 'Bearer tok123'},
            )
        assert response.code == 200
        data = json.loads(response.body)
        assert data['instructions'] == 'Read carefully'


# ---------------------------------------------------------------------------
# GetUserSessionIdHandler
# ---------------------------------------------------------------------------

class TestGetUserSessionIdHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/getUserSessionId', GetUserSessionIdHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_missing_auth_header_returns_401(self):
        body = json.dumps({'session_id': 'abc'})
        response = self.fetch('/getUserSessionId', method='POST', body=body)
        assert response.code == 401

    def test_creates_session_with_201(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {'session_id': 'xyz'}
        with patch('handlers.exam.requests.post', return_value=mock_resp):
            body = json.dumps({'exam_id': 'exam-001'})
            response = self.fetch(
                '/getUserSessionId',
                method='POST',
                body=body,
                headers={'Authorization': 'Bearer tok123'},
            )
        assert response.code == 201
        data = json.loads(response.body)
        assert data['session_id'] == 'xyz'


# ---------------------------------------------------------------------------
# ExamStatusHandler
# ---------------------------------------------------------------------------

class TestExamStatusHandler(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        src = os.path.join(FIXTURES_DIR, 'access_info.yaml')
        self.access_info_path = os.path.join(self.temp_dir, 'access_info.yaml')
        shutil.copy(src, self.access_info_path)
        self.exam_state = {'start_time': 0, 'end_time': 0}
        super().setUp()

    def tearDown(self):
        super().tearDown()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def get_app(self):
        kwargs = _make_exam_kwargs(
            atd_access_path=self.access_info_path,
            exam_state=self.exam_state,
        )
        return tornado.web.Application([
            (r'/examStatus', ExamStatusHandler, kwargs),
        ], cookie_secret='test-secret')

    def test_exam_status_get_returns_button_state(self):
        """GET /examStatus reads examButtonNeeded from YAML."""
        response = self.fetch('/examStatus')
        assert response.code == 200
        data = json.loads(response.body)
        assert data['response'] == 'startExamButtonNeeded'  # fixture has examButtonNeeded: true

    def test_exam_status_post_sets_times(self):
        """POST /examStatus writes startExamTime + endExamTime to YAML and updates exam_state."""
        with patch('handlers.exam.update_hubspot_handler', return_value={}):
            response = self.fetch(
                '/examStatus',
                method='POST',
                body=json.dumps({'start': True}),
            )
        assert response.code == 200
        data = json.loads(response.body)
        assert 'ExamButtonNotNeeded' in data['response']
        # exam_state dict should be updated
        assert self.exam_state['start_time'] > 0
        assert self.exam_state['end_time'] > self.exam_state['start_time']

    def test_exam_status_post_updates_yaml(self):
        """POST /examStatus persists examButtonNeeded=False to the YAML file."""
        from ruamel.yaml import YAML
        with patch('handlers.exam.update_hubspot_handler', return_value={}):
            self.fetch('/examStatus', method='POST', body=json.dumps({}))
        with open(self.access_info_path, 'r') as fh:
            saved = YAML().load(fh)
        assert saved['examButtonNeeded'] is False
        assert saved['startExamTime'] > 0


# ---------------------------------------------------------------------------
# ExamSubmitHandler
# ---------------------------------------------------------------------------

class TestExamSubmitHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        self.mock_client, self.mock_container = _make_docker_client()
        kwargs = _make_exam_kwargs(docker_client=self.mock_client)
        return tornado.web.Application([
            (r'/examSubmit', ExamSubmitHandler, kwargs),
        ], cookie_secret='test-secret')

    def test_exam_submit_triggers_docker(self):
        response = self.fetch('/examSubmit')
        assert response.code == 200
        data = json.loads(response.body)
        assert 'submitted' in data['response'].lower()
        self.mock_container.exec_run.assert_called_once()
        cmd = self.mock_container.exec_run.call_args[0][0]
        assert 'exam_upload_v2' in cmd

    # test_exam_submit_docker_unavailable_503 is in TestExamSubmitHandlerNullDocker below


# ---------------------------------------------------------------------------
# BeginExamHandler
# ---------------------------------------------------------------------------

class TestBeginExamHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/beginExam', BeginExamHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_missing_auth_header_returns_401(self):
        response = self.fetch('/beginExam', method='POST', body=json.dumps({}))
        assert response.code == 401

    def test_begin_exam_calls_honorlock(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'started': True}
        with patch('handlers.exam.requests.post', return_value=mock_resp) as mock_post:
            response = self.fetch(
                '/beginExam',
                method='POST',
                body=json.dumps({'session_id': 's1'}),
                headers={'Authorization': 'Bearer tok123'},
            )
        assert response.code == 200
        data = json.loads(response.body)
        assert data['started'] is True
        call_url = mock_post.call_args[0][0]
        assert 'session/start' in call_url

    def test_begin_exam_409_conflict(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 409
        mock_resp.json.return_value = {'error': 'already running'}
        with patch('handlers.exam.requests.post', return_value=mock_resp):
            response = self.fetch(
                '/beginExam',
                method='POST',
                body=json.dumps({'session_id': 's1'}),
                headers={'Authorization': 'Bearer tok123'},
            )
        assert response.code == 409


# ---------------------------------------------------------------------------
# EndExamHandler
# ---------------------------------------------------------------------------

class TestEndExamHandler(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        self.mock_client, self.mock_container = _make_docker_client()
        super().setUp()

    def get_app(self):
        kwargs = _make_exam_kwargs(docker_client=self.mock_client)
        return tornado.web.Application([
            (r'/endExam', EndExamHandler, kwargs),
        ], cookie_secret='test-secret')

    def _post(self, body=None, token='tok123'):
        return self.fetch(
            '/endExam',
            method='POST',
            body=json.dumps(body or {}),
            headers={'Authorization': f'Bearer {token}'},
        )

    def test_missing_auth_header_returns_401(self):
        response = self.fetch('/endExam', method='POST', body=json.dumps({}))
        assert response.code == 401

    def test_end_exam_calls_honorlock_and_docker(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'completed': True}
        with patch('handlers.exam.requests.post', return_value=mock_resp) as mock_post:
            response = self._post()
        assert response.code == 200
        data = json.loads(response.body)
        assert data['exam_submit'] == 'Exam has been submitted'
        assert data['honorlock_response']['completed'] is True
        # Verify docker exec was triggered
        self.mock_container.exec_run.assert_called_once()
        call_url = mock_post.call_args[0][0]
        assert 'session/complete' in call_url

    def test_end_exam_docker_failure_returns_and_stops(self):
        """Regression: docker failure writes response once and returns — no double-write."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'completed': True}
        self.mock_client.containers.get.side_effect = RuntimeError('docker gone')
        with patch('handlers.exam.requests.post', return_value=mock_resp):
            response = self._post()
        assert response.code == 200
        data = json.loads(response.body)
        # Should contain error annotation, not the success message
        assert 'error' in data['exam_submit'].lower() or 'error' in str(data).lower()

    # test_end_exam_docker_unavailable_returns_error_annotation is in TestEndExamHandlerNullDocker below


# ---------------------------------------------------------------------------
# GetAccessInfoHandler
# ---------------------------------------------------------------------------

class TestGetAccessInfoHandler(tornado.testing.AsyncHTTPTestCase):
    def get_app(self):
        return tornado.web.Application([
            (r'/getAccessInfo', GetAccessInfoHandler, _make_exam_kwargs()),
        ], cookie_secret='test-secret')

    def test_missing_auth_header_returns_401(self):
        response = self.fetch('/getAccessInfo')
        assert response.code == 401

    def test_get_access_info_returns_customer_details(self):
        response = self.fetch(
            '/getAccessInfo',
            headers={'Authorization': 'Bearer tok123'},
        )
        assert response.code == 200
        data = json.loads(response.body)
        assert 'customer_details' in data
        # Fixture has customer_details with lab_type: training
        details = data['customer_details']
        assert 'lab_type' in details

    def test_missing_fields_get_defaults(self):
        """Fields absent in the YAML should fall back to defaults."""
        response = self.fetch(
            '/getAccessInfo',
            headers={'Authorization': 'Bearer tok123'},
        )
        data = json.loads(response.body)
        details = data['customer_details']
        # These fields are not in the fixture's customer_details — check defaults
        assert details['exam_taker_id'] == 'Arista-test-taker-ID'
        assert details['exam_taker_email'] == 'arista-test-taker@arista.com'

    def test_bearer_prefix_required(self):
        """Non-Bearer auth scheme is rejected."""
        response = self.fetch(
            '/getAccessInfo',
            headers={'Authorization': 'Basic dXNlcjpwYXNz'},
        )
        assert response.code == 401


# ---------------------------------------------------------------------------
# Null-docker edge-case tests (standalone classes to avoid port conflicts)
# ---------------------------------------------------------------------------

class TestExamSubmitHandlerNullDocker(tornado.testing.AsyncHTTPTestCase):
    """ExamSubmitHandler with docker_client=None should return 503."""

    def get_app(self):
        kwargs = _make_exam_kwargs(docker_client=None)
        return tornado.web.Application([
            (r'/examSubmit', ExamSubmitHandler, kwargs),
        ], cookie_secret='test-secret')

    def test_exam_submit_docker_unavailable_503(self):
        response = self.fetch('/examSubmit')
        assert response.code == 503
        data = json.loads(response.body)
        assert 'unavailable' in data['error'].lower()


class TestEndExamHandlerNullDocker(tornado.testing.AsyncHTTPTestCase):
    """EndExamHandler with docker_client=None should annotate response and return early."""

    def get_app(self):
        kwargs = _make_exam_kwargs(docker_client=None)
        return tornado.web.Application([
            (r'/endExam', EndExamHandler, kwargs),
        ], cookie_secret='test-secret')

    def test_end_exam_docker_unavailable_returns_error_annotation(self):
        """When docker_client is None, writes error annotation and returns (no double-write)."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'completed': True}
        with patch('handlers.exam.requests.post', return_value=mock_resp):
            response = self.fetch(
                '/endExam',
                method='POST',
                body=json.dumps({}),
                headers={'Authorization': 'Bearer tok'},
            )
        assert response.code == 200
        data = json.loads(response.body)
        assert 'exam_submit' in data
        assert 'error' in data['exam_submit'].lower()

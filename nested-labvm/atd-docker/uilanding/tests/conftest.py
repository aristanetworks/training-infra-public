"""
Pytest fixtures and shared test setup for uilanding tests.

These tests use mocking to avoid requiring actual Docker/network infrastructure.
"""

import os
import sys
import shutil
import tempfile
import pytest
from unittest.mock import MagicMock, patch

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def fixture_path():
    """Return the path to the fixtures directory."""
    return FIXTURES_DIR


@pytest.fixture
def access_info_path():
    """Return the path to the access_info.yaml fixture file."""
    return os.path.join(FIXTURES_DIR, 'access_info.yaml')


@pytest.fixture
def topo_build_path():
    """Return the path to the topo_build.yml fixture file."""
    return os.path.join(FIXTURES_DIR, 'topo_build.yml')


@pytest.fixture
def mock_access_info(temp_dir):
    """Copy access_info.yaml fixture to temp dir and return the path."""
    src = os.path.join(FIXTURES_DIR, 'access_info.yaml')
    dst = os.path.join(temp_dir, 'access_info.yaml')
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def mock_topo_build(temp_dir):
    """Copy topo_build.yml fixture to temp dir and return the path."""
    src = os.path.join(FIXTURES_DIR, 'topo_build.yml')
    dst = os.path.join(temp_dir, 'topo_build.yml')
    shutil.copy(src, dst)
    return dst


@pytest.fixture
def mock_docker_client():
    """Mock Docker client with a container that supports exec_run."""
    mock_exec_result = MagicMock()
    mock_exec_result.exit_code = 0
    mock_exec_result.output = b''

    mock_container = MagicMock()
    mock_container.exec_run.return_value = mock_exec_result

    mock_client = MagicMock()
    mock_client.containers.get.return_value = mock_container

    return mock_client


@pytest.fixture
def mock_requests_responses():
    """
    Provide a mutable dict for URL-keyed mock responses.

    Tests populate this dict before using mock_requests:
        mock_requests_responses['http://example.com/api'] = {'key': 'value'}
    """
    return {}


@pytest.fixture
def mock_requests(mock_requests_responses):
    """
    Patch requests.get and requests.post with URL-based routing.

    Looks up the URL in mock_requests_responses and returns a mock response
    with that data as JSON. Returns an empty 200 response for unknown URLs.
    """
    def _make_response(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        data = mock_requests_responses.get(url, {})
        mock_resp.json.return_value = data
        mock_resp.text = str(data)
        mock_resp.ok = True
        return mock_resp

    with patch('requests.get', side_effect=_make_response) as mock_get, \
         patch('requests.post', side_effect=_make_response) as mock_post:
        yield {'get': mock_get, 'post': mock_post}

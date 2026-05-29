"""
Tests for utility functions in src/utils.py.

Covers: encodeID/decodeID, normalize_device_name, getAPI, getUptime,
getEventStatus, genCookieSecret, safe_log, update_hubspot_handler.
"""

import os
import sys
import json
import logging
import pytest
from unittest.mock import patch, MagicMock
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import (
    encodeID,
    decodeID,
    normalize_device_name,
    getAPI,
    getUptime,
    getEventStatus,
    genCookieSecret,
    safe_log,
    update_hubspot_handler,
)


# ---------------------------------------------------------------------------
# Module-level fixture: replace the Cloud Logging logger with a plain one.
# setup_cloud_logging may configure a SyncTransport handler that makes real
# GCP network calls on every logger.error()/logger.warning() invocation,
# which blocks tests indefinitely when no GCP credentials are available.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_utils_logger():
    """Replace the utils module logger with a no-op standard logger."""
    null_logger = logging.getLogger('test_utils_null')
    null_logger.handlers = [logging.NullHandler()]
    null_logger.propagate = False
    with patch('utils.logger', null_logger):
        yield null_logger


# ---------------------------------------------------------------------------
# encodeID / decodeID
# ---------------------------------------------------------------------------

class TestEncodeDecodeID:
    """Roundtrip and error tests for base64 JSON encode/decode helpers."""

    def test_roundtrip_string(self):
        data = "hello-world"
        assert decodeID(encodeID(data)) == data

    def test_roundtrip_dict(self):
        data = {"action": "login", "user": "arista", "count": 42}
        assert decodeID(encodeID(data)) == data

    def test_roundtrip_list(self):
        data = ["leaf1", "spine1", "host1"]
        assert decodeID(encodeID(data)) == data

    def test_encode_returns_string(self):
        result = encodeID({"key": "value"})
        assert isinstance(result, str)

    def test_encode_is_not_original(self):
        original = "test_data"
        encoded = encodeID(original)
        assert encoded != original

    def test_decode_invalid_base64_raises(self):
        with pytest.raises(Exception):
            decodeID("not-valid-base64!!!")

    def test_decode_invalid_json_raises(self):
        import base64
        # Valid base64 but not valid JSON
        bad_payload = base64.b64encode(b"not json {{").decode()
        with pytest.raises(Exception):
            decodeID(bad_payload)

    def test_roundtrip_empty_dict(self):
        assert decodeID(encodeID({})) == {}

    def test_roundtrip_nested(self):
        data = {"outer": {"inner": [1, 2, 3]}}
        assert decodeID(encodeID(data)) == data


# ---------------------------------------------------------------------------
# normalize_device_name
# ---------------------------------------------------------------------------

class TestNormalizeDeviceName:
    """Tests for device name capitalization and suffix handling."""

    # Simple capitalization
    def test_leaf1(self):
        assert normalize_device_name("leaf1") == "Leaf1"

    def test_spine1(self):
        assert normalize_device_name("spine1") == "Spine1"

    def test_host1(self):
        assert normalize_device_name("host1") == "Host1"

    def test_memleaf1(self):
        assert normalize_device_name("memleaf1") == "Memleaf1"

    def test_borderleaf1(self):
        assert normalize_device_name("borderleaf1") == "Borderleaf1"

    # DC suffix handling
    def test_spine1_dc1(self):
        assert normalize_device_name("spine1-dc1") == "Spine1-DC1"

    def test_leaf2_dc2(self):
        assert normalize_device_name("leaf2-dc2") == "Leaf2-DC2"

    def test_spine1_DC1_uppercase(self):
        # Already uppercase DC suffix should stay uppercase
        assert normalize_device_name("spine1-DC1") == "Spine1-DC1"

    # Uppercase abbreviations preserved
    def test_pe1_preserved(self):
        assert normalize_device_name("PE1") == "PE1"

    def test_p3_preserved(self):
        assert normalize_device_name("P3") == "P3"

    def test_ce1_preserved(self):
        assert normalize_device_name("CE1") == "CE1"

    # Edge cases
    def test_empty_string(self):
        assert normalize_device_name("") == ""

    def test_none_returns_none(self):
        assert normalize_device_name(None) is None

    def test_already_capitalized(self):
        assert normalize_device_name("Leaf1") == "Leaf1"


# ---------------------------------------------------------------------------
# getAPI
# ---------------------------------------------------------------------------

class TestGetAPI:
    """Tests for getAPI — calls the configtopo HTTP endpoint."""

    def _make_mock_response(self, data):
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(data)
        mock_resp.status_code = 200
        return mock_resp

    @patch('utils.requests.get')
    def test_success_returns_parsed_json(self, mock_get):
        expected = {"status": "OK", "nodes": ["leaf1", "spine1"]}
        mock_get.return_value = self._make_mock_response(expected)
        result = getAPI("getTopology")
        assert result == expected

    @patch('utils.requests.get')
    def test_includes_timeout_5(self, mock_get):
        mock_get.return_value = self._make_mock_response({})
        getAPI("getTopology")
        _, kwargs = mock_get.call_args
        assert kwargs.get('timeout') == 5

    @patch('utils.requests.get')
    def test_timeout_returns_down_status(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        result = getAPI("getTopology")
        assert result['status'] == 'DOWN'
        assert 'error' in result

    @patch('utils.requests.get')
    def test_connection_error_returns_down_status(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        result = getAPI("getTopology")
        assert result['status'] == 'DOWN'

    @patch('utils.requests.get')
    def test_never_returns_none(self, mock_get):
        mock_get.side_effect = Exception("unexpected")
        result = getAPI("getTopology")
        assert result is not None

    @patch('utils.requests.get')
    def test_custom_topo_api_host(self, mock_get):
        mock_get.return_value = self._make_mock_response({"status": "OK"})
        getAPI("getTopology", topo_api="my-custom-host")
        call_url = mock_get.call_args[0][0]
        assert "my-custom-host" in call_url


# ---------------------------------------------------------------------------
# getUptime
# ---------------------------------------------------------------------------

class TestGetUptime:
    """Tests for getUptime — fetches runtime/boot status from an instance."""

    def _make_mock_response(self, data):
        mock_resp = MagicMock()
        mock_resp.text = json.dumps(data)
        mock_resp.status_code = 200
        return mock_resp

    @patch('utils.requests.get')
    def test_success_returns_parsed_data(self, mock_get):
        payload = {"boottime": 100, "uptime": 200, "runtime": 30, "status": "running"}
        mock_get.return_value = self._make_mock_response(payload)
        result = getUptime("10.0.0.1")
        assert result['status'] == 'running'
        assert result['boottime'] == 100

    @patch('utils.requests.get')
    def test_init_status_uses_topo_data_runtime(self, mock_get):
        payload = {"boottime": 0, "uptime": 0, "status": "init"}
        mock_get.return_value = self._make_mock_response(payload)
        topo_data = {"labels": {"runtime": "45"}}
        result = getUptime("10.0.0.1", topo_data=topo_data)
        assert result['runtime'] == 45

    @patch('utils.requests.get')
    def test_init_status_without_topo_data_uses_default_runtime(self, mock_get):
        payload = {"boottime": 0, "uptime": 0, "status": "init"}
        mock_get.return_value = self._make_mock_response(payload)
        result = getUptime("10.0.0.1")
        assert result['runtime'] == 12

    @patch('utils.requests.get')
    def test_timeout_returns_default_dict(self, mock_get):
        mock_get.side_effect = requests.exceptions.Timeout("timed out")
        result = getUptime("10.0.0.1")
        assert result['status'] == 'init'
        assert 'boottime' in result
        assert 'uptime' in result
        assert 'runtime' in result

    @patch('utils.requests.get')
    def test_connection_error_returns_default_dict(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        result = getUptime("10.0.0.1")
        assert result['status'] == 'init'

    @patch('utils.requests.get')
    def test_exception_does_not_propagate(self, mock_get):
        mock_get.side_effect = Exception("unknown error")
        result = getUptime("10.0.0.1")
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# getEventStatus
# ---------------------------------------------------------------------------

class TestGetEventStatus:
    """Tests for getEventStatus — queries instance state from a Cloud Function."""

    def _make_mock_response(self, data):
        mock_resp = MagicMock()
        mock_resp.json.return_value = data
        mock_resp.status_code = 200
        return mock_resp

    @patch('utils.requests.get')
    def test_schema2_appends_eos_to_url(self, mock_get):
        mock_get.return_value = self._make_mock_response({"state": "RUNNING"})
        getEventStatus("myinstance", "us-central1-a", func_state="http://func-url", schema=2)
        call_url = mock_get.call_args[0][0]
        assert "myinstance-eos" in call_url

    @patch('utils.requests.get')
    def test_schema1_does_not_append_eos(self, mock_get):
        mock_get.return_value = self._make_mock_response({"state": "RUNNING"})
        getEventStatus("myinstance", "us-central1-a", func_state="http://func-url", schema=1)
        call_url = mock_get.call_args[0][0]
        assert "myinstance-eos" not in call_url
        assert "myinstance" in call_url

    @patch('utils.requests.get')
    def test_includes_timeout_parameter(self, mock_get):
        mock_get.return_value = self._make_mock_response({})
        getEventStatus("inst", "zone", func_state="http://func-url")
        _, kwargs = mock_get.call_args
        assert 'timeout' in kwargs

    @patch('utils.requests.get')
    def test_connection_error_returns_false(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("refused")
        result = getEventStatus("inst", "zone", func_state="http://func-url")
        assert result is False

    @patch('utils.requests.get')
    def test_success_returns_json(self, mock_get):
        expected = {"state": "RUNNING", "instance": "myinstance"}
        mock_get.return_value = self._make_mock_response(expected)
        result = getEventStatus("myinstance", "us-central1-a", func_state="http://func-url")
        assert result == expected

    @patch('utils.requests.get')
    def test_value_error_returns_false(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.side_effect = ValueError("bad JSON")
        mock_get.return_value = mock_resp
        result = getEventStatus("inst", "zone", func_state="http://func-url")
        assert result is False

    @patch('utils.requests.get')
    def test_generic_exception_returns_false(self, mock_get):
        mock_get.side_effect = Exception("unexpected failure")
        result = getEventStatus("inst", "zone", func_state="http://func-url")
        assert result is False


# ---------------------------------------------------------------------------
# genCookieSecret
# ---------------------------------------------------------------------------

class TestGenCookieSecret:
    """Tests for genCookieSecret — random hex token generator."""

    def test_returns_string(self):
        assert isinstance(genCookieSecret(), str)

    def test_returns_hex_string(self):
        result = genCookieSecret()
        # Should not raise — valid hex
        int(result, 16)

    def test_unique_each_call(self):
        results = {genCookieSecret() for _ in range(20)}
        # All 20 should be unique (collision probability is astronomically low)
        assert len(results) == 20

    def test_expected_length(self):
        # secrets.token_hex(16) returns 32-char hex string
        result = genCookieSecret()
        assert len(result) == 32


# ---------------------------------------------------------------------------
# safe_log
# ---------------------------------------------------------------------------

class TestSafeLog:
    """Tests for safe_log — logging wrapper that never raises."""

    def test_never_raises_on_valid_input(self):
        safe_log('info', 'Test message', key='value')

    def test_never_raises_on_invalid_level(self):
        safe_log('not_a_real_level', 'Test message')

    def test_never_raises_with_no_kwargs(self):
        safe_log('info', 'No extra kwargs')

    def test_never_raises_with_none_values(self):
        safe_log('warning', 'Message with None', device=None, count=None)

    def test_never_raises_on_empty_message(self):
        safe_log('info', '')

    def test_connectivity_event_handled(self):
        # Connectivity events go to JSONL file — should not raise
        safe_log('info', 'Connectivity check', event='connectivity', host='leaf1')


# ---------------------------------------------------------------------------
# update_hubspot_handler
# ---------------------------------------------------------------------------

class TestUpdateHubspotHandler:
    """Tests for update_hubspot_handler — calls a GCP Cloud Function."""

    def _make_mock_response(self, status_code, data):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.json.return_value = data
        return mock_resp

    @patch('utils.requests.post')
    def test_success_returns_json(self, mock_post):
        expected = {"result": "success"}
        mock_post.return_value = self._make_mock_response(200, expected)
        result = update_hubspot_handler("user@example.com", "update_exam_start", "my-gcp-project")
        assert result == expected

    @patch('utils.requests.post')
    def test_timeout_returns_error_dict(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("timed out")
        result = update_hubspot_handler("user@example.com", "update_exam_start", "my-gcp-project")
        assert 'error' in result

    @patch('utils.requests.post')
    def test_includes_timeout_parameter(self, mock_post):
        mock_post.return_value = self._make_mock_response(200, {})
        update_hubspot_handler("user@example.com", "update_exam_start", "my-gcp-project")
        _, kwargs = mock_post.call_args
        assert 'timeout' in kwargs

    @patch('utils.requests.post')
    def test_non_200_returns_error_info(self, mock_post):
        mock_post.return_value = self._make_mock_response(500, {"error": "server error"})
        result = update_hubspot_handler("user@example.com", "update_exam_start", "my-gcp-project")
        # Returns the error detail from the response
        assert result is not None

    @patch('utils.requests.post')
    def test_connection_error_returns_error_dict(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("refused")
        result = update_hubspot_handler("user@example.com", "update_exam_start", "my-gcp-project")
        assert 'error' in result

    @patch('utils.requests.post')
    def test_project_used_in_url(self, mock_post):
        mock_post.return_value = self._make_mock_response(200, {})
        update_hubspot_handler("user@example.com", "update_exam_start", "my-specific-project")
        # url is passed as keyword arg in the implementation
        call_kwargs = mock_post.call_args[1]
        assert "my-specific-project" in call_kwargs.get('url', '')

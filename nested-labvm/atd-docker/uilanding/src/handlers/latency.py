"""Latency injection proxy handlers for UILanding.

Proxies latency control requests to the captureservice container,
which runs with host network mode and controls tc-netem on OVS bridges.
All handlers try the primary Docker Desktop URL first, then fall back
to the Linux Docker bridge IP.
"""

import json
import traceback

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from handlers.auth import BaseHandler
from utils import safe_log


# Latency API Handlers (proxy to captureservice)

class LatencyBridgesAPIHandler(BaseHandler):
    """API endpoint to list bridges with latency status."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            http_client = AsyncHTTPClient()

            bridges = []
            try:
                response = await http_client.fetch(
                    f"{self.CAPTURE_SERVICE_URL}/latency/bridges",
                    request_timeout=5
                )
                data = json.loads(response.body.decode('utf-8'))
                bridges = data.get('bridges', [])
            except Exception as e:
                safe_log('warning', f'LatencyBridges primary failed: {e}', event='proxy', handler='latency_bridges', action='primary_failed')
                try:
                    response = await http_client.fetch(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/bridges",
                        request_timeout=5
                    )
                    data = json.loads(response.body.decode('utf-8'))
                    bridges = data.get('bridges', [])
                except Exception as e2:
                    safe_log('error', f'LatencyBridges fallback also failed: {e2}', event='proxy', handler='latency_bridges', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({
                        'error': 'Latency service unavailable',
                        'bridges': []
                    }))
                    return

            self.write(json.dumps({
                'bridges': bridges,
                'count': len(bridges)
            }))

        except Exception as e:
            safe_log('error', f'Error in LatencyBridgesAPIHandler: {e}', event='error', handler='LatencyBridgesAPIHandler')
            safe_log('error', f'LatencyBridges error: {e}', event='proxy', handler='latency_bridges')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyEnableAPIHandler(BaseHandler):
    """API endpoint to enable latency on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON'}))
            return

        try:
            http_client = AsyncHTTPClient()

            request_body = json.dumps(body)

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/enable",
                    method="POST",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                    request_timeout=10
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'LatencyEnable primary failed: {e}', event='proxy', handler='latency_enable', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/enable",
                        method="POST",
                        body=request_body,
                        headers={"Content-Type": "application/json"},
                        request_timeout=10
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'LatencyEnable fallback also failed: {e2}', event='proxy', handler='latency_enable', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyEnableAPIHandler: {e}', event='error', handler='LatencyEnableAPIHandler')
            safe_log('error', f'LatencyEnable error: {e}', event='proxy', handler='latency_enable')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAPIHandler(BaseHandler):
    """API endpoint to disable latency on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            body = json.loads(self.request.body.decode('utf-8'))
        except json.JSONDecodeError:
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON'}))
            return

        try:
            http_client = AsyncHTTPClient()

            request_body = json.dumps(body)

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/disable",
                    method="POST",
                    body=request_body,
                    headers={"Content-Type": "application/json"},
                    request_timeout=10
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'LatencyDisable primary failed: {e}', event='proxy', handler='latency_disable', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/disable",
                        method="POST",
                        body=request_body,
                        headers={"Content-Type": "application/json"},
                        request_timeout=10
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'LatencyDisable fallback also failed: {e2}', event='proxy', handler='latency_disable', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyDisableAPIHandler: {e}', event='error', handler='LatencyDisableAPIHandler')
            safe_log('error', f'LatencyDisable error: {e}', event='proxy', handler='latency_disable')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAllAPIHandler(BaseHandler):
    """API endpoint to disable latency on all bridges."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/latency/disable-all",
                    method="POST",
                    body="{}",
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'LatencyDisableAll primary failed: {e}', event='proxy', handler='latency_disable_all', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/latency/disable-all",
                        method="POST",
                        body="{}",
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'LatencyDisableAll fallback also failed: {e2}', event='proxy', handler='latency_disable_all', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Latency service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in LatencyDisableAllAPIHandler: {e}', event='error', handler='LatencyDisableAllAPIHandler')
            safe_log('error', f'LatencyDisableAll error: {e}', event='proxy', handler='latency_disable_all')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

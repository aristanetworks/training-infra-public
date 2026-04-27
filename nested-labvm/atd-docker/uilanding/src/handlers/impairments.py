"""Network impairment proxy handlers for UILanding.

Proxies unified impairment control requests (latency, loss, duplication,
corruption) to the captureservice container, which runs with host network
mode and controls tc-netem on OVS bridges.
All handlers try the primary Docker Desktop URL first, then fall back
to the Linux Docker bridge IP.
"""

import json
import traceback

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from handlers.auth import BaseHandler
from utils import safe_log


# Impairment API Handlers (unified control for latency, loss, duplication, corruption)

class ImpairmentsBridgesAPIHandler(BaseHandler):
    """API endpoint to get bridges with all impairment status."""

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

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/bridges",
                    method="GET",
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'ImpairmentsBridges primary failed: {e}', event='proxy', handler='impairments_bridges', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/bridges",
                        method="GET",
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'ImpairmentsBridges fallback also failed: {e2}', event='proxy', handler='impairments_bridges', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in ImpairmentsBridgesAPIHandler: {e}', event='error', handler='ImpairmentsBridgesAPIHandler')
            safe_log('error', f'ImpairmentsBridges error: {e}', event='proxy', handler='impairments_bridges')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsConfigureAPIHandler(BaseHandler):
    """API endpoint to configure impairments on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment configured', event='impairment', action='configure',
                     bridge=str(_body.get('bridge', '')), latency=str(_body.get('latency', '')),
                     loss=str(_body.get('loss', '')))
        except Exception:
            safe_log('info', 'Network impairment configured', event='impairment', action='configure')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/configure",
                    method="POST",
                    body=json.dumps(request_data),
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'ImpairmentsConfigure primary failed: {e}', event='proxy', handler='impairments_configure', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/configure",
                        method="POST",
                        body=json.dumps(request_data),
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'ImpairmentsConfigure fallback also failed: {e2}', event='proxy', handler='impairments_configure', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except json.JSONDecodeError:
            safe_log('error', 'Error in ImpairmentsConfigureAPIHandler: Invalid JSON', event='error', handler='ImpairmentsConfigureAPIHandler')
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON in request body'}))
        except Exception as e:
            safe_log('error', f'Error in ImpairmentsConfigureAPIHandler: {e}', event='error', handler='ImpairmentsConfigureAPIHandler')
            safe_log('error', f'ImpairmentsConfigure error: {e}', event='proxy', handler='impairments_configure')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsClearAPIHandler(BaseHandler):
    """API endpoint to clear all impairments on a bridge."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear',
                     bridge=str(_body.get('bridge', '')))
        except Exception:
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear')
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{self.CAPTURE_SERVICE_URL}/impairments/clear",
                    method="POST",
                    body=json.dumps(request_data),
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'ImpairmentsClear primary failed: {e}', event='proxy', handler='impairments_clear', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear",
                        method="POST",
                        body=json.dumps(request_data),
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'ImpairmentsClear fallback also failed: {e2}', event='proxy', handler='impairments_clear', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except json.JSONDecodeError:
            safe_log('error', 'Error in ImpairmentsClearAPIHandler: Invalid JSON', event='error', handler='ImpairmentsClearAPIHandler')
            self.set_status(400)
            self.write(json.dumps({'error': 'Invalid JSON in request body'}))
        except Exception as e:
            safe_log('error', f'Error in ImpairmentsClearAPIHandler: {e}', event='error', handler='ImpairmentsClearAPIHandler')
            safe_log('error', f'ImpairmentsClear error: {e}', event='proxy', handler='impairments_clear')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class ImpairmentsClearAllAPIHandler(BaseHandler):
    """API endpoint to clear all impairments on all bridges."""

    CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
    CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"

    async def post(self):
        safe_log('info', 'All network impairments cleared', event='impairment', action='clear_all')
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
                    f"{self.CAPTURE_SERVICE_URL}/impairments/clear-all",
                    method="POST",
                    body="{}",
                    headers={"Content-Type": "application/json"},
                    request_timeout=30
                )
                response = await http_client.fetch(request)
                data = json.loads(response.body.decode('utf-8'))
                self.write(json.dumps(data))
            except Exception as e:
                safe_log('warning', f'ImpairmentsClearAll primary failed: {e}', event='proxy', handler='impairments_clear_all', action='primary_failed')
                try:
                    request = HTTPRequest(
                        f"{self.CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear-all",
                        method="POST",
                        body="{}",
                        headers={"Content-Type": "application/json"},
                        request_timeout=30
                    )
                    response = await http_client.fetch(request)
                    data = json.loads(response.body.decode('utf-8'))
                    self.write(json.dumps(data))
                except Exception as e2:
                    safe_log('error', f'ImpairmentsClearAll fallback also failed: {e2}', event='proxy', handler='impairments_clear_all', action='all_failed')
                    self.set_status(503)
                    self.write(json.dumps({'error': 'Impairments service unavailable'}))

        except Exception as e:
            safe_log('error', f'Error in ImpairmentsClearAllAPIHandler: {e}', event='error', handler='ImpairmentsClearAllAPIHandler')
            safe_log('error', f'ImpairmentsClearAll error: {e}', event='proxy', handler='impairments_clear_all')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

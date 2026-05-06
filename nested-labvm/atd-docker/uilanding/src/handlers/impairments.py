"""Network impairment proxy handlers for UILanding.

Proxies unified impairment control requests (latency, loss, duplication,
corruption) to the captureservice container, which runs with host network
mode and controls tc-netem on OVS bridges.
All handlers try the primary Docker Desktop URL first, then fall back
to the Linux Docker bridge IP.

Includes legacy latency-only handlers (kept for backwards compatibility)
and the unified impairments handlers that supersede them.
"""

import json
import traceback

from tornado.httpclient import AsyncHTTPClient, HTTPRequest

from handlers.auth import BaseHandler
from utils import safe_log, CAPTURE_SERVICE_URL, CAPTURE_SERVICE_URL_FALLBACK


# Impairment API Handlers (unified control for latency, loss, duplication, corruption)

class ImpairmentsBridgesAPIHandler(BaseHandler):
    """API endpoint to get bridges with all impairment status."""

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
                    f"{CAPTURE_SERVICE_URL}/impairments/bridges",
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/impairments/bridges",
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

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment configured', event='impairment', action='configure',
                     bridge=str(_body.get('bridge', '')), latency=str(_body.get('latency', '')),
                     loss=str(_body.get('loss', '')))
        except Exception:
            safe_log('info', 'Network impairment configured', event='impairment', action='configure')

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{CAPTURE_SERVICE_URL}/impairments/configure",
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/impairments/configure",
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

    async def post(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        try:
            _body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear',
                     bridge=str(_body.get('bridge', '')))
        except Exception:
            safe_log('info', 'Network impairment cleared', event='impairment', action='clear')

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get request body
            body = self.request.body.decode('utf-8')
            request_data = json.loads(body) if body else {}

            http_client = AsyncHTTPClient()

            try:
                request = HTTPRequest(
                    f"{CAPTURE_SERVICE_URL}/impairments/clear",
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear",
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
                    f"{CAPTURE_SERVICE_URL}/impairments/clear-all",
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/impairments/clear-all",
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


# ===============================
# Legacy Latency Handlers
# (kept for backwards compatibility — use impairments handlers for new code)
# ===============================

class LatencyBridgesAPIHandler(BaseHandler):
    """API endpoint to list bridges with latency status (legacy)."""

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
                    f"{CAPTURE_SERVICE_URL}/latency/bridges",
                    request_timeout=5
                )
                data = json.loads(response.body.decode('utf-8'))
                bridges = data.get('bridges', [])
            except Exception as e:
                safe_log('warning', f'LatencyBridges primary failed: {e}', event='proxy', handler='latency_bridges', action='primary_failed')
                try:
                    response = await http_client.fetch(
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/latency/bridges",
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
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyEnableAPIHandler(BaseHandler):
    """API endpoint to enable latency on a bridge (legacy)."""

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
                    f"{CAPTURE_SERVICE_URL}/latency/enable",
                    method="POST", body=request_body,
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/latency/enable",
                        method="POST", body=request_body,
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
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAPIHandler(BaseHandler):
    """API endpoint to disable latency on a bridge (legacy)."""

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
                    f"{CAPTURE_SERVICE_URL}/latency/disable",
                    method="POST", body=request_body,
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/latency/disable",
                        method="POST", body=request_body,
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
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class LatencyDisableAllAPIHandler(BaseHandler):
    """API endpoint to disable latency on all bridges (legacy)."""

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
                    f"{CAPTURE_SERVICE_URL}/latency/disable-all",
                    method="POST", body="{}",
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
                        f"{CAPTURE_SERVICE_URL_FALLBACK}/latency/disable-all",
                        method="POST", body="{}",
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
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

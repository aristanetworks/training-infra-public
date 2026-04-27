"""NodeBuilder proxy handler for UILanding."""

import json
import traceback

from tornado.httpclient import AsyncHTTPClient, HTTPRequest, HTTPClientError

from handlers.auth import BaseHandler
from utils import safe_log


class NodeBuilderProxyHandler(BaseHandler):
    """
    Proxy handler for nodebuilder service API calls.

    The nodebuilder service runs on port 8090 with host network mode
    for libvirt/virsh access. This handler proxies requests from the
    UI to the nodebuilder service.
    """

    NODEBUILDER_URL = "http://host.docker.internal:8090"
    NODEBUILDER_URL_FALLBACK = "http://172.17.0.1:8090"

    async def get(self, path):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        _nb_path = self.request.uri.replace('/nodebuilder/', '')
        safe_log('info', f'Node builder GET: {_nb_path}', event='nodebuilder', method='GET', path=_nb_path)
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        await self._proxy_request('GET', path)

    async def post(self, path):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        _nb_path = self.request.uri.replace('/nodebuilder/', '')
        try:
            _nb_body = json.loads(self.request.body.decode('utf-8')) if self.request.body else {}
            _nb_action = _nb_body.get('action', _nb_body.get('type', 'unknown'))
            _nb_name = _nb_body.get('name', _nb_body.get('hostname', ''))
            safe_log('info', f'Node builder POST: {_nb_path}', event='nodebuilder', method='POST', path=_nb_path,
                     action=str(_nb_action), node_name=str(_nb_name))
        except Exception:
            safe_log('info', f'Node builder POST: {_nb_path}', event='nodebuilder', method='POST', path=_nb_path)
        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        await self._proxy_request('POST', path, self.request.body)

    async def options(self, path):
        """Handle CORS preflight requests."""
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.set_header("Access-Control-Max-Age", "86400")
        self.set_status(204)
        self.finish()

    async def _proxy_request(self, method, path, body=None):
        """Proxy request to nodebuilder service."""
        http_client = AsyncHTTPClient()
        url = f"/{path}" if path else ""

        # Longer timeout for operations that may take a while
        timeout = 180 if 'reset-all' in path or 'cleanup' in path else 60

        async def try_fetch(base_url):
            """Attempt to fetch from a URL, returning (response, error)."""
            request = HTTPRequest(
                f"{base_url}{url}",
                method=method,
                body=body if method == 'POST' else None,
                headers={"Content-Type": "application/json"} if body else {},
                request_timeout=timeout
            )
            try:
                response = await http_client.fetch(request, raise_error=False)
                return response, None
            except Exception as e:
                return None, e

        try:
            # Try primary URL first (Docker Desktop)
            response, error = await try_fetch(self.NODEBUILDER_URL)

            if error:
                # Connection error - try fallback
                safe_log('warning', f'NodeBuilderProxy primary failed: {error}', event='proxy',
                         handler='nodebuilder_proxy', action='primary_failed')
                response, error = await try_fetch(self.NODEBUILDER_URL_FALLBACK)

            if error:
                # Both failed to connect
                safe_log('error', f'NodeBuilderProxy fallback also failed: {error}', event='proxy',
                         handler='nodebuilder_proxy', action='all_failed')
                self.set_status(503)
                self.write(json.dumps({
                    'error': 'Nodebuilder service unavailable',
                    'detail': str(error)
                }))
                return

            # Forward the response (including non-2xx status codes like 400)
            self.set_status(response.code)
            if response.body:
                self.write(response.body)

        except Exception as e:
            safe_log('error', f'Error in NodeBuilderProxyHandler: {e}', event='error',
                     handler='NodeBuilderProxyHandler')
            safe_log('error', f'NodeBuilderProxy error: {e}', event='proxy', handler='nodebuilder_proxy')
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

"""WebSocket handler for UILanding topology data streaming.

Extracted from uilanding.py.

Handler:
  - topoDataHandler  — WebSocket endpoint at /td-ws

Helper functions (only used by topoDataHandler):
  - prune_recent_sessions()  — remove stale reconnect tracking entries

Constants:
  - RECONNECT_WINDOW_SECONDS  — how long to track recently-closed sessions

Dependencies injected at route registration time via initialize():
  - session_state  dict: active_sessions, active_session_data, recent_sessions, grpc_state
  - exam_state     dict: start_time, end_time (shared mutable with exam handlers)
  - cvp_token_fn   callable: _get_cvp_token from uilanding.py (avoids circular import)
"""

import json
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

import tornado.ioloop
import tornado.websocket

from utils import getAPI, getUptime, pS, safe_log

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RECONNECT_WINDOW_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def prune_recent_sessions(recent_sessions):
    """Remove entries from recent_sessions that are older than RECONNECT_WINDOW_SECONDS."""
    now = datetime.utcnow()
    expired = [
        ip for ip, data in recent_sessions.items()
        if (now - data['closed_at']).total_seconds() > RECONNECT_WINDOW_SECONDS
    ]
    for ip in expired:
        del recent_sessions[ip]


# ---------------------------------------------------------------------------
# topoDataHandler
# ---------------------------------------------------------------------------

class topoDataHandler(tornado.websocket.WebSocketHandler):
    """WebSocket handler for topology data and connectivity monitoring.

    Injected via initialize():
        session_state  — dict with keys: active_sessions, active_session_data,
                         recent_sessions, grpc_state
        exam_state     — dict with keys: start_time, end_time
        cvp_token_fn   — callable returning a CVP session token string or None
    """

    def initialize(self, session_state=None, exam_state=None, cvp_token_fn=None):
        self._active_sessions = session_state['active_sessions'] if session_state else set()
        self._active_session_data = session_state['active_session_data'] if session_state else {}
        self._recent_sessions = session_state['recent_sessions'] if session_state else {}
        self._grpc_state = session_state['grpc_state'] if session_state else {'status': None, 'last_check': None}
        self._exam_state = exam_state if exam_state is not None else {'start_time': 0, 'end_time': 0}
        self._cvp_token_fn = cvp_token_fn if cvp_token_fn is not None else (lambda: None)

    def open(self):
        # Prefer X-Real-IP from nginx, fall back to remote_ip
        client_ip = self.request.headers.get(
            'X-Real-IP',
            self.request.headers.get('X-Forwarded-For', self.request.remote_ip)
        )
        # X-Forwarded-For can be comma-separated — take the first (original client)
        if ',' in client_ip:
            client_ip = client_ip.split(',')[0].strip()

        session_id = str(uuid.uuid4())
        user_agent = self.request.headers.get('User-Agent', '')[:200]

        # Check for reconnect
        prune_recent_sessions(self._recent_sessions)
        reconnect_count = 0
        reconnect_gap = None
        if client_ip in self._recent_sessions:
            prev = self._recent_sessions[client_ip]
            reconnect_count = prev['reconnect_count'] + 1
            reconnect_gap = (datetime.utcnow() - prev['closed_at']).total_seconds()

        self._closed = False
        self.session = {
            'id': session_id,
            'connected_at': datetime.utcnow(),
            'reconnect_count': reconnect_count,
            'missed_pongs': 0,
            'last_pong': None,
            'last_rtt': None,
            'client_ip': client_ip,
            'debug_mode': False,
            'user_agent': user_agent,
            'last_token_send': 0,
            'client_id': ''
        }
        self._active_sessions.add(session_id)

        safe_log('info', 'WebSocket session started',
            event='connectivity', action='session_start',
            session_id=session_id,
            client_ip=client_ip,
            reconnect_count=str(reconnect_count),
            user_agent=user_agent)

        if reconnect_gap is not None:
            safe_log('info', 'Client reconnected',
                event='connectivity', action='reconnect',
                session_id=session_id,
                client_ip=client_ip,
                reconnect_gap_seconds=str(round(reconnect_gap, 1)),
                reconnect_count=str(reconnect_count))

        self.cvp_status = ''
        self.cvp_tasks = ''
        self.uptime = {}
        self.schedule_summary()
        pS("New backend websocket connection")

    async def on_message(self, message):
        try:
            recv = json.loads(message)
            cdata = recv['data']
            msg_type = recv['type']
            session_id = self.session['id'][:8] if hasattr(self, 'session') else '?'

            if msg_type == 'hello':
                # Store persistent client_id from frontend (survives page refreshes)
                client_id = cdata.get('client_id', '')
                if client_id and hasattr(self, 'session'):
                    self.session['client_id'] = client_id
                pS("[{}] WS hello - client_id={} sending status + session info".format(
                    session_id, client_id[:12] if client_id else '?'))
                # Grab current uptime of topology (run in executor to avoid blocking)
                loop = tornado.ioloop.IOLoop.current()
                self.uptime = await loop.run_in_executor(None, getUptime, '192.168.0.1')
                # Get initial topology status
                self.cvp_status = await loop.run_in_executor(None, getAPI, "cvp_status")
                self.endexamtime = self._exam_state.get('end_time', 0)
                self.startExamTime = self._exam_state.get('start_time', 0)
                if self.cvp_status['status'] == 'UP':
                    self.cvp_tasks = await loop.run_in_executor(None, getAPI, "cvp_tasks")
                else:
                    self.cvp_tasks = ''
                self.sendData('status')
                self.send_session_info()
                self.schedule_update()

            elif msg_type == 'pong':
                if hasattr(self, 'session'):
                    server_ts = cdata.get('server_ts', 0)
                    now_ms = int(time.time() * 1000)
                    rtt = now_ms - server_ts if server_ts else None
                    self.session['last_pong'] = datetime.utcnow()
                    self.session['last_rtt'] = rtt
                    self.session['missed_pongs'] = 0
                    pS("[{}] WS pong rtt={}ms".format(session_id, rtt))

                    if self.session.get('debug_mode'):
                        safe_log('debug', 'Pong received',
                            event='connectivity', action='pong',
                            session_id=self.session['id'],
                            rtt_ms=str(rtt) if rtt else 'unknown')

            elif msg_type == 'connectivity':
                event_name = cdata.get('event', '?')
                pS("[{}] WS connectivity: {}".format(session_id, event_name))
                self.handle_connectivity_event(cdata)

            elif msg_type == 'debug_toggle':
                if hasattr(self, 'session'):
                    self.session['debug_mode'] = not self.session['debug_mode']
                    pS("[{}] WS debug_toggle -> {}".format(session_id, self.session['debug_mode']))
                    safe_log('info', 'Debug mode toggled',
                        event='connectivity', action='debug_toggle',
                        session_id=self.session['id'],
                        debug_mode=str(self.session['debug_mode']))
                    try:
                        self.write_message(json.dumps({
                            'type': 'debug_ack',
                            'data': {'debug_mode': self.session['debug_mode']}
                        }))
                    except Exception:
                        pass

            elif msg_type == 'update':
                pass  # ACK from frontend status receipt — no action needed

            else:
                pS("[{}] WS unknown type: {}".format(session_id, msg_type))

        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.on_message: {e}',
                     event='error', handler='topoDataHandler')
            pS("WS ERROR")

    def schedule_update(self):
        try:
            self.timeout = tornado.ioloop.IOLoop.current().call_later(30, self._run_keepalive)
        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.schedule_update: {e}',
                     event='error', handler='topoDataHandler')

    def _run_keepalive(self):
        """Bridge between call_later (sync callback) and async keepalive."""
        tornado.ioloop.IOLoop.current().spawn_callback(self.keepalive)

    async def keepalive(self):
        if getattr(self, '_closed', True):
            return
        try:
            loop = tornado.ioloop.IOLoop.current()
            # Run blocking HTTP calls in executor to avoid freezing the event loop
            self.uptime = await loop.run_in_executor(None, getUptime, '192.168.0.1')
            self.endexamtime = self._exam_state.get('end_time', 0)
            self.startExamTime = self._exam_state.get('start_time', 0)
            self.cvp_status = await loop.run_in_executor(None, getAPI, "cvp_status")
            if self.cvp_status['status'] == 'UP':
                self.cvp_tasks = await loop.run_in_executor(None, getAPI, "cvp_tasks")
            else:
                self.cvp_tasks = ''
            self.sendData('status')

            # Send timestamped ping for latency measurement
            # Include internal gRPC status when available for synchronized checks
            ping_data = {'ts': int(time.time() * 1000)}
            if self._grpc_state['status'] is not None:
                ping_data['internal_grpc'] = self._grpc_state['status']
            if hasattr(self, 'session') and self.session['last_rtt'] is not None:
                ping_data['server_rtt'] = self.session['last_rtt']
            self.write_message(json.dumps({
                'type': 'ping',
                'data': ping_data
            }))

            # Check for missed pongs
            if hasattr(self, 'session'):
                if self.session['last_pong'] is not None:
                    pong_age = (datetime.utcnow() - self.session['last_pong']).total_seconds()
                    if pong_age > 60:
                        self.session['missed_pongs'] += 1
                        if self.session['missed_pongs'] in (3, 10, 30, 100) or \
                                self.session['missed_pongs'] % 100 == 0:
                            safe_log('warning', 'Client missing pong responses',
                                event='connectivity', action='missed_pongs',
                                session_id=self.session['id'],
                                missed_pongs=str(self.session['missed_pongs']),
                                last_pong_age_seconds=str(round(pong_age, 1)))
                elif self.session['connected_at']:
                    conn_age = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                    if conn_age > 90:
                        self.session['missed_pongs'] += 1

            # Refresh CVP token to frontend every 20 minutes
            if hasattr(self, 'session') and \
                    time.time() - self.session.get('last_token_send', 0) > 1200:
                self.session['last_token_send'] = time.time()
                try:
                    token = await loop.run_in_executor(None, self._cvp_token_fn)
                    if token:
                        self.write_message(json.dumps({
                            'type': 'token_refresh',
                            'data': {'cvp_token': token}
                        }))
                except Exception:
                    pass

            # Update active session data snapshot
            if hasattr(self, 'session'):
                self._active_session_data[self.session['id']] = {
                    'session_id': self.session['id'],
                    'client_ip': self.session['client_ip'],
                    'connected_at': str(self.session['connected_at']),
                    'missed_pongs': self.session['missed_pongs'],
                    'last_rtt': self.session['last_rtt'],
                    'reconnect_count': self.session['reconnect_count']
                }
        except Exception as e:
            safe_log('error', f'Error in topoDataHandler.keepalive: {e}',
                     event='error', handler='topoDataHandler')
            pS("ERROR sending update")
        finally:
            if not getattr(self, '_closed', True):
                self.schedule_update()

    def on_close(self):
        self._closed = True
        duration = 0
        session_id = 'unknown'
        try:
            duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
            session_id = self.session['id']
            self._active_sessions.discard(session_id)
            self._active_session_data.pop(session_id, None)

            # Store in recent_sessions for reconnect detection
            self._recent_sessions[self.session['client_ip']] = {
                'session_id': session_id,
                'closed_at': datetime.utcnow(),
                'reconnect_count': self.session['reconnect_count']
            }

            safe_log('info', 'WebSocket session ended',
                event='connectivity', action='session_end',
                session_id=session_id,
                client_id=str(self.session.get('client_id', '')),
                client_ip=str(self.session['client_ip']),
                duration_seconds=str(round(duration, 1)),
                missed_pongs=str(self.session['missed_pongs']),
                reconnect_count=str(self.session['reconnect_count']))
        except AttributeError:
            safe_log('info', 'WebSocket connection closed (no session)',
                event='websocket', action='disconnect')
        try:
            tornado.ioloop.IOLoop.current().remove_timeout(self.timeout)
            if hasattr(self, 'summary_timeout'):
                tornado.ioloop.IOLoop.current().remove_timeout(self.summary_timeout)
            pS('connection closed')
        except Exception:
            safe_log('warning', 'Timeout already removed on close',
                     event='websocket', action='timeout_cleanup')

    def check_origin(self, origin):
        """Validate origin matches the request host to prevent cross-site WebSocket hijacking."""
        host = self.request.headers.get('Host', '')
        if not host:
            return False
        try:
            parsed = urlparse(origin)
            return parsed.netloc == host or parsed.netloc.split(':')[0] == host.split(':')[0]
        except Exception:
            return False

    def sendData(self, mtype):
        instance_data = {
            'cvp': self.cvp_status,
            'tasks': self.cvp_tasks,
            'uptime': self.uptime,
            'endexamtime': self._exam_state.get('end_time', 0),
            'startExamTime': self._exam_state.get('start_time', 0),
        }
        self.write_message(json.dumps({
            'type': mtype,
            'data': instance_data
        }))

    def send_session_info(self):
        """Send session metadata to the frontend for diagnostics panel."""
        try:
            cvp_token = self._cvp_token_fn()
            self.write_message(json.dumps({
                'type': 'session_info',
                'data': {
                    'session_id': self.session['id'],
                    'client_id': self.session.get('client_id', ''),
                    'reconnect_count': self.session['reconnect_count'],
                    'debug_mode': self.session['debug_mode'],
                    'cvp_token': cvp_token or ''
                }
            }))
        except Exception:
            safe_log('error', 'Error sending session info',
                event='error', handler='topoDataHandler')

    def handle_connectivity_event(self, data):
        """Process connectivity events from the frontend."""
        if not hasattr(self, 'session'):
            return

        event = data.get('event', '')
        session_id = self.session['id']

        if event == 'periodic_summary':
            safe_log('info', 'Client connectivity summary',
                event='connectivity', action='periodic_summary',
                source='client',
                session_id=session_id,
                client_id=str(self.session.get('client_id', '')),
                client_ip=str(self.session['client_ip']),
                ws_latency_ms=str(data.get('wsRoundTrip', '')),
                grpc_status=str(data.get('grpcStatus', '')),
                grpc_failures=str(data.get('grpcFailures', '')),
                event_count=str(data.get('eventCount', '')),
                session_uptime_s=str(data.get('sessionUptime', '')),
                external_check=str(data.get('externalCheck', '')),
                external_rtt_ms=str(data.get('externalRttMs', '')),
                network_type=str(data.get('networkType', '')),
                effective_type=str(data.get('effectiveType', '')),
                downlink_mbps=str(data.get('downlinkMbps', '')),
                browser_rtt_ms=str(data.get('browserRttMs', '')),
                uptime_percent=str(data.get('uptimePercent', '')))

        elif event == 'reconnect_report':
            safe_log('warning', 'Client reconnected after outage',
                event='connectivity', action='reconnect_report',
                source='client',
                session_id=session_id,
                client_ip=str(self.session['client_ip']),
                offline_duration_ms=str(data.get('offlineDuration', '')),
                offline_from=str(data.get('offlineFrom', '')),
                offline_to=str(data.get('offlineTo', '')),
                buffered_event_count=str(len(data.get('bufferedEvents', []))))

            if self.session.get('debug_mode'):
                for evt in data.get('bufferedEvents', [])[:100]:
                    safe_log('debug', 'Buffered client event',
                        event='connectivity', action='buffered_event',
                        source='client',
                        session_id=session_id,
                        event_type=str(evt.get('type', '')),
                        event_ts=str(evt.get('ts', '')),
                        event_data=str(evt.get('data', '')))

        elif event == 'grpc_check':
            grpc_status = data.get('status', 'unknown')
            # Only log on state transitions (ok→error or error→ok), not every check
            prev_grpc = getattr(self, '_last_grpc_status', None)
            if grpc_status != prev_grpc:
                self._last_grpc_status = grpc_status
                log_level = 'info' if grpc_status == 'ok' else 'warning'
                safe_log(log_level, 'Client gRPC check: ' + grpc_status,
                    event='connectivity', action='grpc_check',
                    source='client',
                    session_id=session_id,
                    client_id=str(self.session.get('client_id', '')),
                    client_ip=str(self.session['client_ip']),
                    status=str(grpc_status),
                    detail=str(data.get('detail', ''))[:200])

        elif event == 'state_change':
            safe_log('info', 'Client connectivity state change',
                event='connectivity', action='state_change',
                source='client',
                session_id=session_id,
                client_ip=str(self.session['client_ip']),
                change_type=str(data.get('changeType', '')),
                detail=str(data.get('detail', '')))

    def schedule_summary(self):
        """Schedule periodic session summary logging (every 5 minutes)."""
        try:
            self.summary_timeout = tornado.ioloop.IOLoop.current().add_timeout(
                timedelta(seconds=300), self.log_session_summary)
        except Exception:
            pass

    def log_session_summary(self):
        """Log a summary of the current session state."""
        if getattr(self, '_closed', True):
            return
        try:
            if hasattr(self, 'session'):
                duration = (datetime.utcnow() - self.session['connected_at']).total_seconds()
                safe_log('info', 'Active session summary',
                    event='connectivity', action='session_summary',
                    session_id=self.session['id'],
                    client_id=str(self.session.get('client_id', '')),
                    client_ip=str(self.session['client_ip']),
                    duration_seconds=str(round(duration, 1)),
                    missed_pongs=str(self.session['missed_pongs']),
                    last_rtt_ms=str(self.session['last_rtt'] if self.session['last_rtt'] else ''),
                    reconnect_count=str(self.session['reconnect_count']),
                    debug_mode=str(self.session['debug_mode']))
        except Exception:
            pass
        finally:
            if not getattr(self, '_closed', True):
                self.schedule_summary()

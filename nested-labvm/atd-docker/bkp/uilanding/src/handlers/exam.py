"""Exam-related handlers for UILanding.

All 12 handlers are extracted from uilanding.py. Dependencies that were
module-level globals are injected via Tornado's ``initialize()`` mechanism so
the classes are testable without importing the main module.

Dependency groups passed as kwargs to the route table:

    config = {
        'base_path':       BASE_PATH,
        'atd_access_path': ATD_ACCESS_PATH,
        'project':         PROJECT,
        'honorlock_client_id':     HonorLockClientID,
        'honorlock_secret':        HonorLockSecret,
    }

    docker_client  = DOCKER_CLIENT   (may be None)

    exam_state = {   # shared mutable dict — mutated by ExamStatusHandler
        'start_time': 0,
        'end_time':   0,
    }
"""

import json
import time
from datetime import datetime

import requests
import tornado.web
from ruamel.yaml import YAML

from handlers.auth import BaseHandler
from utils import safe_log, update_hubspot_handler


# ---------------------------------------------------------------------------
# Simple HTML page handlers
# ---------------------------------------------------------------------------

class ExamSubmittedRedirectHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.base_path = config['base_path']

    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(self.base_path + 'exam-submitted.html', 'r') as fh:
                self.write(fh.read())
        except FileNotFoundError:
            safe_log('error', 'Error in ExamSubmittedRedirectHandler: exam-submitted.html not found',
                     event='error', handler='ExamSubmittedRedirectHandler')
            self.set_status(404)
            self.write("Error: exam-submitted.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamSubmittedRedirectHandler: {e}',
                     event='error', handler='ExamSubmittedRedirectHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")


class ExamAlreadyRunningHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.base_path = config['base_path']

    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(self.base_path + 'exam-already-running.html', 'r') as fh:
                self.write(fh.read())
        except FileNotFoundError:
            safe_log('error', 'Error in ExamAlreadyRunningHandler: exam-already-running.html not found',
                     event='error', handler='ExamAlreadyRunningHandler')
            self.set_status(404)
            self.write("Error: exam-already-running.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamAlreadyRunningHandler: {e}',
                     event='error', handler='ExamAlreadyRunningHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")


class ExamAuthenticationHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.base_path = config['base_path']

    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(self.base_path + 'honorlock-index.html', 'r') as fh:
                self.write(fh.read())
        except FileNotFoundError:
            safe_log('error', 'Error in ExamAuthenticationHandler: honorlock-index.html not found',
                     event='error', handler='ExamAuthenticationHandler')
            self.set_status(404)
            self.write("Error: honorlock-index.html not found")
        except Exception as e:
            safe_log('error', f'Error in ExamAuthenticationHandler: {e}',
                     event='error', handler='ExamAuthenticationHandler')
            self.set_status(500)
            self.write(f"Error: {str(e)}")


# ---------------------------------------------------------------------------
# Honorlock API proxy handlers
# ---------------------------------------------------------------------------

class GetClientIdHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.honorlock_client_id = config['honorlock_client_id']
        self.honorlock_secret = config['honorlock_secret']

    def get(self):
        """Fetch client token from Honorlock API."""
        url = "https://app.honorlock.com/api/en/v1/token"
        payload = json.dumps({
            "client_id": self.honorlock_client_id,
            "client_secret": self.honorlock_secret,
        })
        headers = {'Content-Type': 'application/json'}
        try:
            response = requests.post(url, headers=headers, data=payload, timeout=10)
            if response.status_code in [200, 201]:
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetClientIdHandler: {e}',
                     event='error', handler='GetClientIdHandler')
            self.set_status(500)
            self.write({"error": str(e)})


class GetExamInstructionsHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        pass  # no per-instance config needed beyond the auth header

    def post(self):
        """Fetch exam instructions from Honorlock API."""
        safe_log('info', 'Exam instructions requested', event='exam', action='get_instructions')
        try:
            payload = json.loads(self.request.body)
            url = f"https://app.honorlock.com/api/en/v1/exams/{payload['external_exam_id']}/instructions"
            auth_header = self.request.headers.get('Authorization')

            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }

            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetExamInstructionsHandler: {e}',
                     event='error', handler='GetExamInstructionsHandler')
            self.set_status(500)
            self.write({"error": str(e)})


class GetUserSessionIdHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        pass

    def post(self):
        """Create a user session in Honorlock API."""
        safe_log('info', 'User session ID requested', event='exam', action='create_session')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/exams/sessions/create"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 201:
                self.set_status(201)
                self.write(response.json())
            elif response.status_code == 200:
                self.set_status(200)
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in GetUserSessionIdHandler: {e}',
                     event='error', handler='GetUserSessionIdHandler')
            self.set_status(500)
            self.write({"error": str(e)})


class BeginExamHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        pass

    def post(self):
        """Start an Honorlock exam session."""
        safe_log('info', 'Exam begin requested', event='exam', action='begin')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/session/start"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            if response.status_code == 200:
                self.write(response.json())
            elif response.status_code == 409:
                self.set_status(409)
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write(response.json())
        except Exception as e:
            safe_log('error', f'Error in BeginExamHandler: {e}',
                     event='error', handler='BeginExamHandler')
            self.set_status(500)
            self.write({"error": str(e)})


class EndExamHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.docker_client = docker_client

    def post(self):
        """Complete an Honorlock session and trigger exam upload via Docker."""
        safe_log('info', 'Exam end requested', event='exam', action='end')
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            access_token = auth_header.split(' ')[1]
            url = "https://app.honorlock.com/api/en/v1/session/complete"
            payload = json.loads(self.request.body)
            headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {access_token}',
            }

            response = requests.post(url, headers=headers, json=payload, timeout=10)
            try:
                print("Calling exam_upload_v2 module to upload exam")
                if self.docker_client:
                    login_container = self.docker_client.containers.get('atd-login')
                    login_container.exec_run('sudo python3 -m exam_upload_v2.main', detach=True)
                else:
                    raise Exception("Docker service unavailable")
            except Exception as e:
                safe_log('error', f'Error in EndExamHandler upload_exam: {e}',
                         event='error', handler='EndExamHandler')
                print(f"Error running exam_upload_v2: {e}")
                self.write({
                    'honorlock_response': response.json(),
                    'exam_submit': 'Exam has been submitted but error running exam_upload_v2',
                })
                return

            if response.status_code in [200, 201]:
                try:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit': 'Exam has been submitted',
                    })
                except Exception as e:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit_error': str(e),
                    })
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            safe_log('error', f'Error in EndExamHandler: {e}',
                     event='error', handler='EndExamHandler')
            self.set_status(500)
            self.write({"error": str(e)})


# ---------------------------------------------------------------------------
# Exam state / YAML handlers
# ---------------------------------------------------------------------------

class ExamStatusHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.atd_access_path = config['atd_access_path']
        self.project = config['project']
        # exam_state is the shared mutable dict so callers (e.g. UptimeWithRuntime)
        # see updated values without re-reading the YAML.
        self.exam_state = exam_state if exam_state is not None else {}

    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            with open(self.atd_access_path, 'r') as fh:
                host_yaml = YAML().load(fh)
            self.write({
                'response': (
                    "startExamButtonNeeded"
                    if host_yaml['examButtonNeeded']
                    else "startExamButtonNotNeeded"
                ),
                'examStartTime': host_yaml.get('startExamTime', 0),
            })
        except Exception as e:
            safe_log('error', f'Error in ExamStatusHandler.get: {e}',
                     event='error', handler='ExamStatusHandler')
            self.set_status(500)
            self.write({"error": str(e)})

    def post(self):
        try:
            json.loads(self.request.body.decode('utf-8'))  # validate JSON
            with open(self.atd_access_path, 'r') as fh:
                host_yaml = YAML().load(fh)
            exam_duration = host_yaml.get("exam_duration", 0)
            safe_log('info', 'Exam started', event='exam', action='start',
                     duration_minutes=str(exam_duration))
            current_time = int(time.time())
            start_time = current_time
            end_time = current_time + (exam_duration * 60)

            # Update shared mutable state
            self.exam_state['start_time'] = start_time
            self.exam_state['end_time'] = end_time

            host_yaml['startExamTime'] = start_time
            host_yaml['endExamTime'] = end_time
            host_yaml['examButtonNeeded'] = False
            yaml = YAML()
            with open(self.atd_access_path, "w") as fh:
                yaml.dump(host_yaml, fh)

            # Call HubSpot to update exam start time
            try:
                customer_email = host_yaml.get('customer_details', {}).get('exam_taker_email', '')
                if customer_email and customer_email != 'arista-test-taker@arista.com':
                    print(f"Calling HubSpot to update exam start time for {customer_email}")
                    hubspot_response = update_hubspot_handler(customer_email, 'update_exam_start', self.project)
                    print(f"HubSpot response: {hubspot_response}")
                else:
                    print("Skipping HubSpot update - no valid customer email found")
            except Exception as hubspot_error:
                safe_log('error', f'Error in ExamStatusHandler HubSpot update: {hubspot_error}',
                         event='error', handler='ExamStatusHandler')
                print(f"Warning: HubSpot update failed but exam started successfully: {hubspot_error}")

            self.write({'response': 'Status updated to ExamButtonNotNeeded'})
        except Exception as e:
            safe_log('error', f'Error in ExamStatusHandler.post: {e}',
                     event='error', handler='ExamStatusHandler')
            self.set_status(500)
            self.write({"error": str(e)})


class ExamSubmitHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.docker_client = docker_client

    def get(self):
        safe_log('info', 'Exam submitted', event='exam', action='submit')
        self.set_header("Access-Control-Allow-Origin", "*")
        try:
            if not self.docker_client:
                self.set_status(503)
                self.write({"error": "Docker service unavailable"})
                return
            login_container = self.docker_client.containers.get('atd-login')
            login_container.exec_run('sudo python3 -m exam_upload_v2.main', detach=True)
            self.write({'response': 'Exam has been submitted'})
        except Exception as e:
            safe_log('error', f'Error in ExamSubmitHandler: {e}',
                     event='error', handler='ExamSubmitHandler')
            self.set_status(500)
            self.write({"error": str(e)})


# ---------------------------------------------------------------------------
# Page rendering handlers (require BaseHandler for cookie auth)
# ---------------------------------------------------------------------------

class ExamRedoRedirectHandler(BaseHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.base_path = config['base_path']
        self.atd_access_path = config['atd_access_path']

    def get(self):
        try:
            with open(self.atd_access_path, 'r') as fh:
                host_yaml = YAML().load(fh)

            exam_taker_name = host_yaml.get('customer_details', {}).get('exam_taker_full_name', 'Student')
            start_exam_time = host_yaml.get('startExamTime', 0)
            if start_exam_time:
                session_start_time = datetime.fromtimestamp(start_exam_time).strftime('%Y-%m-%d %H:%M:%S UTC')
            else:
                session_start_time = 'Unknown time'

            self.render(
                self.base_path + 'exam-redo.html',
                exam_taker_name=exam_taker_name,
                session_start_time=session_start_time,
            )
        except Exception as e:
            safe_log('error', f'Error in ExamRedoRedirectHandler: {e}',
                     event='error', handler='ExamRedoRedirectHandler')
            print(f"Error in ExamRedoRedirectHandler: {e}")
            self.render(
                self.base_path + 'exam-redo.html',
                exam_taker_name='Student',
                session_start_time='Unknown time',
            )


class GetAccessInfoHandler(tornado.web.RequestHandler):
    def initialize(self, config, docker_client=None, exam_state=None):
        self.atd_access_path = config['atd_access_path']

    def validate_field(self, customer_details, field_name, default_value, validated_details, defaulted_fields):
        """Validate a single field and add to validated_details with default if needed."""
        field_value = customer_details.get(field_name)
        if field_value is None or str(field_value).strip() == '':
            validated_details[field_name] = default_value
            defaulted_fields.append(field_name)
            print(f"Field '{field_name}' is empty or missing, using default: {default_value}")
        else:
            validated_details[field_name] = str(field_value)

    def get(self):
        """Return customer details from ACCESS_INFO.yaml."""
        self.set_header("Access-Control-Allow-Origin", "*")
        default_values = {
            "exam_taker_id": "Arista-test-taker-ID",
            "exam_taker_email": "arista-test-taker@arista.com",
            "exam_taker_full_name": "Arista Test Taker",
            "external_exam_id": "default-training-exam",
            "exam_taker_attempt_id": "1",
            "exam_hours": "240",
            "lab_type": "Lab",
            "exam_code": "001",
        }
        try:
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            with open(self.atd_access_path, 'r') as fh:
                host_yaml = YAML().load(fh)

            customer_details = host_yaml.get('customer_details', {})
            validated_details = {}
            defaulted_fields = []
            for field_name, default_value in default_values.items():
                self.validate_field(customer_details, field_name, default_value,
                                    validated_details, defaulted_fields)
            self.write({"customer_details": validated_details})

        except Exception as e:
            safe_log('error', f'Error in GetAccessInfoHandler: {e}',
                     event='error', handler='GetAccessInfoHandler')
            print(f"Error in GetAccessInfoHandler: {str(e)}")
            self.set_status(500)
            self.write({"error": str(e), "customer_details": default_values})

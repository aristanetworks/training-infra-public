"""Lab configuration handlers for UILanding."""

import re

import docker
from handlers.auth import BaseHandler
from utils import safe_log

# Only allow alphanumeric characters, underscores, hyphens, dots, and spaces
_SAFE_LAB_VALUE = re.compile(r'^[a-zA-Z0-9_. -]+$')


class LabHandler(BaseHandler):
    def initialize(self, docker_client, default_menu_file_value):
        self.docker_client = docker_client
        self.default_menu_file_value = default_menu_file_value

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write({"error": "Authentication required"})
            return

        safe_log('info', 'Lab configuration started', event='lab', action='start',
                 lab_value=str(self.get_argument('lab_value', 'unknown')))
        self.set_header("Access-Control-Allow-Origin", "*")
        selected_lab_option = self.get_argument('lab_value')

        if not _SAFE_LAB_VALUE.match(selected_lab_option):
            self.set_status(400)
            self.write({"error": "Invalid lab value"})
            return

        if not self.docker_client:
            self.set_status(503)
            self.write({"error": "Docker service unavailable"})
            return
        try:
            login_container = self.docker_client.containers.get('atd-login')
            login_container.exec_run(
                f'python3 /usr/local/bin/callConfigTopo.py  {self.default_menu_file_value} {selected_lab_option}',
                detach=True
            )
            print(f'python3 /usr/local/bin/callConfigTopo.py  {self.default_menu_file_value} {selected_lab_option}')
            self.write({
                'response': 'Configuration is being applied. Check in CVP that all tasks have been applied'
            })
        except docker.errors.NotFound:
            self.set_status(503)
            self.write({"error": "Login container not found"})
        except Exception as e:
            safe_log('error', f'Error in LabHandler: {e}', event='error', handler='LabHandler')
            self.set_status(500)
            self.write({"error": f"Docker error: {str(e)}"})


class LabStausHandler(BaseHandler):
    def initialize(self, docker_client, default_menu_file_value):
        self.docker_client = docker_client
        self.default_menu_file_value = default_menu_file_value

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write({"error": "Authentication required"})
            return

        self.set_header("Access-Control-Allow-Origin", "*")
        if not self.docker_client:
            self.set_status(503)
            self.write({"error": "Docker service unavailable"})
            return
        try:
            login_container = self.docker_client.containers.get('atd-login')
            container_output = login_container.exec_run(f'sudo lab_status.py')

            # Filter output to only include lines with format "name,status"
            # Skip log lines that contain timestamps or log levels (INFO, WARNING, ERROR, DEBUG)
            response = []
            output_text = container_output.output.decode("utf-8")

            for line in output_text.splitlines():
                # Only include lines that match the switch status format (contain comma)
                # and don't contain log-related keywords
                if ',' in line and not any(keyword in line for keyword in
                                           ['INFO', 'WARNING', 'ERROR', 'DEBUG', ' - ', 'Checking', 'completed']):
                    response.append(line.strip())

            print(f"Filtered lab status response: {response}")
            self.write({
                'response': response
            })
        except docker.errors.NotFound:
            self.set_status(503)
            self.write({"error": "Login container not found"})
        except Exception as e:
            safe_log('error', f'Error in LabStausHandler: {e}', event='error', handler='LabStausHandler')
            self.set_status(500)
            self.write({"error": f"Docker error: {str(e)}"})


class ResetLabHandler(BaseHandler):
    def initialize(self, docker_client, default_menu_file_value):
        self.docker_client = docker_client
        self.default_menu_file_value = default_menu_file_value

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write({"error": "Authentication required"})
            return

        safe_log('info', 'Lab reset initiated', event='lab', action='reset')
        self.set_header("Access-Control-Allow-Origin", "*")
        lab_names = self.get_argument('lab_names')
        if not self.docker_client:
            self.set_status(503)
            self.write({"error": "Docker service unavailable"})
            return
        try:
            login_container = self.docker_client.containers.get('atd-login')
            login_container.exec_run('sudo python3 /usr/local/bin/resetVMs.py')
            self.write({'response': lab_names})
        except docker.errors.NotFound:
            self.set_status(503)
            self.write({"error": "Login container not found"})
        except Exception as e:
            safe_log('error', f'Error in ResetLabHandler: {e}', event='error', handler='ResetLabHandler')
            self.set_status(500)
            self.write({"error": f"Docker error: {str(e)}"})

#!/usr/bin/env python3

from datetime import datetime, timedelta
from ruamel.yaml import YAML
from time import sleep
from base64 import b64decode, b64encode
import tornado.ioloop
import tornado.web
import tornado.websocket
import requests
import secrets
import hashlib, uuid
import json
import docker
import urllib3
import traceback
import os
import subprocess
import time
import threading
import pyeapi

# Disable any TLS Warnings when getting instance Uptime
urllib3.disable_warnings()


PORT = 80
TOPO_API = 'atd-conftopo'
BASE_PATH = '/opt/topo/html/'
ATD_ACCESS_PATH = '/etc/atd/ACCESS_INFO.yaml'

ArBASE_PATH = '/opt/modules/'
MODULE_FILE = ArBASE_PATH + 'modules.yaml'
MENU_BASE_PATH = '/opt/menus/'
EXAM_END_TIME = 0
EXAM_START_TIME = 0
# Open yaml for the default yaml and read what file to lookup for default menu
default_menu_file_generated_flag = (os.path.join(MENU_BASE_PATH, 'labguides-done.txt'))
print ("Waiting for labguides-done.txt file existance to start the server")
while True:
    if os.path.exists(default_menu_file_generated_flag):
        print("Deleting labguides-done.txt file to start the server")
        os.remove(default_menu_file_generated_flag)
        break
    else:
        print("labguides-done.txt file does not exist yet, waiting for 1 sec")
        sleep(1)
default_menu_file = open(MENU_BASE_PATH+'default.yaml')
default_menu_info = YAML().load(default_menu_file)
default_menu_file.close()
if str(default_menu_info['default_menu']).lower() == 'ssh':
    NOMENUOPTIONFILE =True
else:
    # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
    NOMENUOPTIONFILE = False
    menu_file = open('/opt/menus/{0}'.format(default_menu_info['default_menu']))
    MENU_ITEMS = YAML().load(menu_file)  
    menu_file.close()
    DEFAULT_MENU_FILE_VALUE = default_menu_info['default_menu'].replace('.yaml', '')
    

with open(MODULE_FILE, 'r') as mf:
    MOD_YAML = YAML().load(mf)

# Add in check to make sure arista password has been updated
while True:
    host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
    if host_yaml['login_info']['jump_host']['pw'] == 'REPLACE_PWD':
        sleep(2)
    else:
        break

salt = uuid.uuid4().hex

accounts = {
    hashlib.sha512((host_yaml['login_info']['jump_host']['user'] + salt).encode('utf-8')).hexdigest(): hashlib.sha512((host_yaml['login_info']['jump_host']['pw'] + salt).encode('utf-8')).hexdigest()
}

# Get the topo project and update function
PROJECT = host_yaml['project']
FUNC_STATE = 'https://us-central1-{0}.cloudfunctions.net/atd-state'.format(PROJECT)
NAME = host_yaml['name']
ZONE = host_yaml['zone']
TOPO = host_yaml['topology']
if 'schema' in host_yaml:
    SCHEMA = host_yaml['schema']
else:
    SCHEMA = 1
# Add a check for the title parameter for legacy deployment catches
if 'title' in host_yaml:
    TITLE = host_yaml['title']
else:
    TITLE = 'Test Drive Lab'

def get_metadata_extract(attribute):
    try:
        metadata_url = "http://169.254.169.254/computeMetadata/v1/project/attributes/{}".format(attribute)
        headers = {"Metadata-Flavor": "Google"}
        response = requests.get(metadata_url, headers=headers)
        if response.status_code == 200:
            return response.text
        else:
            return None
    except requests.exceptions.RequestException as e:
        print(f"Error fetching metadata: {e}")
        return None

HonorLockClientID = get_metadata_extract('honorlockClientID')
HonorLockSecret = get_metadata_extract('honorlockClientSecret')


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return(self.get_secure_cookie("user"))
class ExamSubmittedRedirectHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")  # Set the correct content type for HTML
        try:
            with open(BASE_PATH + 'exam-submitted.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)  # Write the HTML content to the response
        except FileNotFoundError:
            self.set_status(404)
            self.write("Error: exam-submitted.html not found")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error: {str(e)}")

class ExamAlreadyRunningHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(BASE_PATH + 'exam-already-running.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)
        except FileNotFoundError:
            self.set_status(404)
            self.write("Error: exam-already-running.html not found")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error: {str(e)}")
class ExamAuthenticationHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")  # Set the correct content type for HTML
        try:
            with open(BASE_PATH + 'honorlock-index.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)  # Write the HTML content to the response
        except FileNotFoundError:
            self.set_status(404)
            self.write("Error: honorlock-index.html not found")
        except Exception as e:      
            self.set_status(500)
            self.write(f"Error: {str(e)}")
class LoginHandler(BaseHandler):
    def get(self):
        AUTH = False
        if 'auth' in self.request.arguments:
            try:
                decoded_cred = decodeID(self.get_argument('auth'))
                tmp_username_hash = hashlib.sha512((decoded_cred['user'] + salt).encode('utf-8')).hexdigest()
                if tmp_username_hash in accounts:
                    tmp_pwd_hash = hashlib.sha512((decoded_cred['pwd'] + salt).encode('utf-8')).hexdigest()
                    if tmp_pwd_hash == accounts[tmp_username_hash]:
                        AUTH = True
            except:
                pass
        if AUTH:
            self.set_secure_cookie("user", decoded_cred['user'])
            self.redirect('/')
        else:
            self.render(
                BASE_PATH + 'login.html',
                LOGIN_MESSAGE=""
            )

    def post(self):
        tmp_username_hash = hashlib.sha512((self.get_argument("name") + salt).encode('utf-8')).hexdigest()
        if tmp_username_hash in accounts:
            tmp_pwd_hash = hashlib.sha512((self.get_argument("pwd") + salt).encode('utf-8')).hexdigest()
            if tmp_pwd_hash == accounts[tmp_username_hash]:
                self.set_secure_cookie("user", self.get_argument("name"))
                self.redirect("/")
            else:
                self.render(
                    BASE_PATH + 'login.html',
                    LOGIN_MESSAGE="Wrong username and/or password."
                )
        else:
            self.render(
                BASE_PATH + 'login.html',
                LOGIN_MESSAGE="Wrong username and/or password."
            )

class topoRequestHandler(BaseHandler):
    def get(self):
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        lab_type = host_yaml.get('customer_details', {}).get('lab_type', 'Lab')
        if lab_type == "Exam" and 'auth' in self.request.arguments and 'honorlock' not in self.request.arguments:
            # Clear authentication and force re-authentication through Honorlock
            self.clear_cookie("user")
            self.redirect('/exam-authentication')
            return()        
        if not self.current_user:
            if lab_type == "Exam":

                if 'auth' in self.request.arguments and 'honorlock' in self.request.arguments:
                    self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
                elif 'auth' in self.request.arguments:
                    self.redirect('/exam-authentication')
                else:
                    self.redirect('/login')
            else:
                if 'auth' in self.request.arguments:
                    self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
                else:
                    self.redirect('/login')

            return()
        else:
            _topo_cvp = False
            if 'disabled_links' in host_yaml:
                disable_links = host_yaml['disabled_links']
            else:
                disable_links = []
            menu={} 
            if NOMENUOPTIONFILE:
                disable_links.append('lab_menu')
            else:
                for lab in MENU_ITEMS['lab_list']:
                    menu[lab] = MENU_ITEMS['lab_list'][lab]['description']
            if 'labguides' in host_yaml:
                if host_yaml['labguides'] == 'self':
                    labguides = '/labguides/index.html'
                else:
                    labguides = host_yaml['labguides']
            else:
                labguides = '/labguides/index.html'
            if 'cvp' in host_yaml:
                if host_yaml['cvp'] != "none":
                    _topo_cvp = True       
            gui_urls,servers =[],[]
            if host_yaml['eos_type'] == 'container-labs':
                try:
                    servers =  MOD_YAML['topology']['servers']
                    if servers is None:
                        servers = [] 
                    external_ip_url = "http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip"
                    headers = {"Metadata-Flavor": "Google"}
                    response = requests.get(external_ip_url, headers=headers)
                    for server in servers:
                        gui_urls.append(f'http://{response.text}:{servers[server]["port"]}')
                except Exception as e:
                    pS(f"Error while looking for servers in GUI {e}")
            self.render(
                BASE_PATH + 'index.html',
                NODES = MOD_YAML['topology']['nodes'],
                SERVERS = servers,
                GUI_URLS= gui_urls,
                ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
                topo_title = TITLE,
                disable_links = disable_links,
                labguides = labguides,
                topo_cvp = _topo_cvp,
                menu_options = menu,
                lab_type = lab_type
            )
    
class topoDataHandler(tornado.websocket.WebSocketHandler):
    def open(self):
        self.cvp_status = ''
        self.cvp_tasks = ''
        self.uptime = {}
        pS("New backend websocket connection")
    
    def on_message(self,message):
        pS("Message Received")
        try:
            recv = json.loads(message)
            cdata = recv['data']
            if recv['type'] == 'hello':
                # Grab current uptime of topology
                self.uptime = getUptime('192.168.0.1')
                # Get initial topology status
                self.cvp_status = getAPI("cvp_status")
                self.endexamtime = EXAM_END_TIME    
                self.startExamTime = EXAM_START_TIME            
                if self.cvp_status['status'] == 'UP':
                    self.cvp_tasks = getAPI("cvp_tasks")
                else:
                    self.cvp_tasks = ''
                self.sendData('status')
                self.schedule_update()
        except:
            pS("WS ERROR")

    def schedule_update(self):
        try:
            self.timeout = tornado.ioloop.IOLoop.instance().add_timeout(timedelta(seconds=30),self.keepalive)
        except:
            pS("Error with timeout call")
        
    def keepalive(self):
        try:
            self.uptime = getUptime('192.168.0.1')
            self.endexamtime = EXAM_END_TIME
            self.startExamTime = EXAM_START_TIME
            self.cvp_status = getAPI("cvp_status")
            if self.cvp_status['status'] == 'UP':
                self.cvp_tasks = getAPI("cvp_tasks")
            else:
                self.cvp_tasks = ''
            self.sendData('status')
        except:
            pS("ERROR sending update")
        finally:
            self.schedule_update()

    def on_close(self):
        try:
            tornado.ioloop.IOLoop.instance().remove_timeout(self.timeout)
            pS('connection closed')
        except:
            pS('connection already closed')
 
    def check_origin(self, origin):
        return(True)
    
    def sendData(self, mtype):
        instance_data = {
            'cvp': self.cvp_status,
            'tasks': self.cvp_tasks,
            'uptime': self.uptime,
            'endexamtime' : EXAM_END_TIME, 
            'startExamTime' : EXAM_START_TIME         
        }
        self.write_message(json.dumps({
            'type': mtype,
            'data': instance_data
        }))


# ===============================
# Utility Functions
# ===============================

def getAPI(action):
    try:
        _action = encodeID(action)
        response = requests.get(f"http://{TOPO_API}:50010/td-api/conftopo?action={_action}")
        return(json.loads(response.text))
    except Exception as e:
        pS("Error calling backend API.")
        traceback.print_exc()
        print("Message: {err}".format(
            err = str(e),
        ))


def encodeID(tmp_data):
    tmp_str = json.dumps(tmp_data).encode()
    enc_str = b64encode(tmp_str).decode()
    return(enc_str)

def decodeID(tmp_data):
    decrypt_str = b64decode(tmp_data.encode()).decode()
    tmp_json = json.loads(decrypt_str)
    return(tmp_json)

def genCookieSecret():
    """
    Function to generate a cookie_secret
    """
    return(secrets.token_hex(16))

def getUptime(instanceIP):
    """
    Function to get response from instances /uptime.
    instanceIP = IP/URL for instance (str)
    """
    try:
        response = requests.get(f"https://{instanceIP}/uptime", verify=False, timeout=0.5)
        instance_data = json.loads(response.text)
        if instance_data['status'] == 'init':
            instance_data['runtime'] = int(TOPO_DATA['labels']['runtime'])
        else:
            instance_data['runtime'] = 12
        return(instance_data)
    except:
        return({
            'boottime': 0,
            'uptime': 0,
            'runtime': 12,
            'status': 'init'
        })

def getEventStatus(instanceName, instanceZone):
    """
    Function to get the currnet status of an instance.
    """
    try:
        if SCHEMA == 2:
            response = requests.get(FUNC_STATE + "?function=state&instance={0}-eos&zone={1}".format(instanceName, instanceZone))
        else:
            response = requests.get(FUNC_STATE + "?function=state&instance={0}&zone={1}".format(instanceName, instanceZone))
        return(response.json())
    except ValueError:
        pS("Value Error retrieving status for {0}".format(instanceName))
        return(False)
    except requests.exceptions.ConnectionError:
        pS("Connection Error retrieving status for {0}".format(instanceName))
        return(False)
    except:
        pS("Error retrieving status for {0}".format(instanceName))
        return(False)


def pS(mtype):
    """
    Function to send output from service file to Syslog
    """
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


# Cache for topo_build.yml data (loaded once on first use)
_TOPO_BUILD_CACHE = None
# Cache for merged device list from both sources
_ALL_DEVICES_CACHE = None


def _get_topo_build_data():
    """
    Load and cache topo_build.yml data.
    Returns cached data on subsequent calls.
    """
    global _TOPO_BUILD_CACHE

    if _TOPO_BUILD_CACHE is not None:
        return _TOPO_BUILD_CACHE

    topo_path = f"/opt/atd/topologies/{TOPO}/topo_build.yml"
    try:
        with open(topo_path, 'r') as f:
            _TOPO_BUILD_CACHE = YAML().load(f)
        pS(f"Cached topo_build.yml from {topo_path}")
    except Exception as e:
        pS(f"Error reading topo_build.yml: {e}")
        _TOPO_BUILD_CACHE = {}  # Empty dict to avoid repeated failures

    return _TOPO_BUILD_CACHE


def get_device_ip_from_sources(device_name):
    """
    Look up device IP from multiple sources with case-insensitive matching.
    Checks modules.yaml first, then falls back to cached topo_build.yml.

    Args:
        device_name: Name of the device to look up

    Returns:
        str: IP address if found, None otherwise
    """
    if not device_name:
        return None

    device_name_lower = device_name.lower()

    # First, try modules.yaml (MOD_YAML)
    nodes = MOD_YAML.get('topology', {}).get('nodes', {})
    for node_name, node_data in nodes.items():
        if node_name.lower() == device_name_lower:
            ip = node_data.get('ip')
            if ip:
                return ip

    # Second, try cached topo_build.yml
    topo_data = _get_topo_build_data()
    if topo_data and 'nodes' in topo_data:
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for name, info in node_entry.items():
                    if name.lower() == device_name_lower:
                        ip = info.get('ip_addr')
                        if ip and ip != 'N/A':
                            return ip

    return None


def get_all_devices():
    """
    Get all devices from both modules.yaml and topo_build.yml.
    Returns a dict of {device_name: {'ip': ip_address}}.
    Uses caching to avoid repeated lookups.
    """
    global _ALL_DEVICES_CACHE

    if _ALL_DEVICES_CACHE is not None:
        return _ALL_DEVICES_CACHE

    devices = {}

    # First, add devices from modules.yaml
    mod_nodes = MOD_YAML.get('topology', {}).get('nodes', {})
    for device_name, device_info in mod_nodes.items():
        devices[device_name] = {'ip': device_info.get('ip', '')}

    # Second, add devices from topo_build.yml (won't overwrite existing)
    topo_data = _get_topo_build_data()
    if topo_data and 'nodes' in topo_data:
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for name, info in node_entry.items():
                    if name not in devices:
                        ip = info.get('ip_addr', '')
                        if ip == 'N/A':
                            ip = ''
                        devices[name] = {'ip': ip}

    _ALL_DEVICES_CACHE = devices
    pS(f"Cached {len(devices)} devices from modules.yaml and topo_build.yml")
    return devices


def update_hubspot_handler(email, action, project):
    """
    Call the HubSpot Cloud Function to update exam properties

    Args:
        email: Email address of the user
        action: Action to perform ('update_exam_start' or 'update_exam_submit')
        project: GCP project name

    Returns:
        dict: Response from the Cloud Function or error dict
    """
    print(f"Updating HubSpot: {action} for {email} in project {project}")
    hubspot_url = f"https://us-central1-{project}.cloudfunctions.net/api-hl-hubspot-handler"
    headers = {'Content-Type': 'application/json'}

    payload = {
        "action": action,
        "email": email
    }

    try:
        response = requests.post(url=hubspot_url, headers=headers, json=payload, timeout=60)

        if response.status_code == 200:
            print(f"Successfully updated HubSpot: {action} for {email}")
            return response.json()
        else:
            error_msg = f"HubSpot update failed with status {response.status_code}"
            print(error_msg)
            try:
                error_detail = response.json()
                print(f"Error details: {error_detail}")
                return error_detail
            except:
                return {"error": error_msg, "status_code": response.status_code}

    except requests.exceptions.Timeout:
        error_msg = "HubSpot request timed out"
        print(error_msg)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"HubSpot update error: {str(e)}"
        print(error_msg)
        return {"error": error_msg}

class GetClientIdHandler(tornado.web.RequestHandler):
    def get(self):
        """
        Handler to fetch client ID from Honorlock API.
        """
        url = "https://app.honorlock.com/api/en/v1/token"
        payload = json.dumps({
            "client_id": HonorLockClientID,
            "client_secret": HonorLockSecret
        })
        headers = {'Content-Type': 'application/json'}

        try:
            response = requests.post(url, headers=headers, data=payload)
            if response.status_code in [200, 201]:
                 self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class GetExamInstructionsHandler(tornado.web.RequestHandler):
    def post(self):
        """
        Handler to fetch exam instructions from Honorlock API.
        """
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
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                self.write(response.json())
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})



class GetUserSessionIdHandler(tornado.web.RequestHandler):
    def post(self):
        """
        Handler to create a user session in Honorlock API.
        """
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
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 201:
                self.set_status(201)
                self.write(response.json())
            elif response.status_code == 200:
                self.set_status(200)
                self.write(response.json())
                return
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class LabHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        selected_lab_option = self.get_argument('lab_value')
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        login_container.exec_run(f'python3 /usr/local/bin/callConfigTopo.py  {DEFAULT_MENU_FILE_VALUE} {selected_lab_option}', detach=True)
        print(f'python3 /usr/local/bin/callConfigTopo.py  {DEFAULT_MENU_FILE_VALUE} {selected_lab_option}')
        # print(container_output)
        # log_file = open('log.txt','w')
        # log_file.write(str(container_output.output.decode("utf-8")))
        # log_file.close()
        # with open("log.txt", "r") as txt_file:
        #     response =  txt_file.readlines()
        self.write({
            'response':'Configuration is being applied. Check in CVP that all tasks have been applied'
        })

class LabStausHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        container_output=login_container.exec_run(f'sudo lab_status.py')
        log_file = open('log.txt','w')
        log_file.write(str(container_output.output.decode("utf-8")))
        log_file.close()
        with open("log.txt", "r") as txt_file:
            response =  txt_file.readlines()
        print(response)
        self.write({
            'response':response
        })        


class ResetLabHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        lab_names = self.get_argument('lab_names')
        self.write({
            'response':lab_names
        })
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        login_container.exec_run(f'sudo python3 /usr/local/bin/resetVMs.py')

class ExamStatusHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            self.write({
                'response':"startExamButtonNeeded" if host_yaml['examButtonNeeded'] else "startExamButtonNotNeeded",
                'examStartTime': host_yaml.get('startExamTime', 0),
            })   
        except Exception as e:    
            self.set_status(500)
            self.write({"error": str(e)})

    def post(self):
        try:
            data = json.loads(self.request.body.decode('utf-8'))
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            exam_duration = host_yaml.get("exam_duration", 0)
            current_time = int(time.time())
            global EXAM_END_TIME
            global EXAM_START_TIME
            EXAM_START_TIME = current_time
            EXAM_END_TIME = current_time + (exam_duration * 60)
            host_yaml['startExamTime'] = EXAM_START_TIME
            host_yaml['endExamTime'] = EXAM_END_TIME
            host_yaml['examButtonNeeded'] = False
            yaml = YAML()
            with open(ATD_ACCESS_PATH, "w") as file:
                yaml.dump(host_yaml, file)

            # Call HubSpot to update exam start time
            try:
                customer_email = host_yaml.get('customer_details', {}).get('exam_taker_email', '')
                if customer_email and customer_email != 'arista-test-taker@arista.com':
                    print(f"Calling HubSpot to update exam start time for {customer_email}")
                    hubspot_response = update_hubspot_handler(customer_email, 'update_exam_start', PROJECT)
                    print(f"HubSpot response: {hubspot_response}")
                else:
                    print(f"Skipping HubSpot update - no valid customer email found")
            except Exception as hubspot_error:
                # Don't fail the exam start if HubSpot update fails
                print(f"Warning: HubSpot update failed but exam started successfully: {hubspot_error}")

            self.write({
                'response':f'Status updated to ExamButtonNotNeeded'
                    })
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)}) 

class ExamSubmitHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")  
        try:
            docker_conn= docker.from_env()
            login_container = docker_conn.containers.get('atd-login') 
            login_container.exec_run(f'sudo python3 /usr/local/bin/upload_exam_unattended.py', detach=True)    
            self.write({
                'response':f'Exam has been submitted'
                    }) 
        except Exception as e:    
            self.set_status(500)
            self.write({"error": str(e)}) 

class ToolsHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)            
            # Extract the three parameters
            changeLatency = data.get('changeLatency', False)
            devices = data.get('devices', [])
            score = data.get('score', 0)
            result = subprocess.run(f'please update code {"ENABLE" if changeLatency else "DISABLE"} -d {score} -i {",".join(devices)}"',
                shell=True,
                capture_output=True,
                text=True
            )
            
            # Prepare the response
            response = {
                "changeLatency": changeLatency,
                "devices": devices,
                "score": score,
                "result" : result.stdout,
                "message": "Parameters received successfully"
            }
            
            # Send the response
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(response))
        
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON in request body"})
        except ValueError as e:
            self.set_status(400)
            self.write({"error": str(e)})
        except Exception as e:
            self.set_status(500)
            self.write({"error": "Internal server error"})

class ViewConfigHandler(tornado.web.RequestHandler):
    def post(self):
        try:
            # Parse the JSON body of the request
            data = json.loads(self.request.body)
            
            # Extract the three parameters
            devices = data.get('devices', False)
            result = subprocess.run(f'please update code "sudo -S python3 /home/atdadmin/change-latency.py "SHOW" -i {",".join(devices)}"',
                shell=True,
                capture_output=True,
                text=True
            )
            # Prepare the response
            response = {
                "devices": devices,
                "result" : result.stdout,
                "message": "Parameters received successfully"
            }
            
            # Send the response
            self.set_header("Content-Type", "application/json")
            self.write(json.dumps(response))
        
        except json.JSONDecodeError:
            self.set_status(400)
            self.write({"error": "Invalid JSON in request body"})
        except ValueError as e:
            self.set_status(400)
            self.write({"error": str(e)})
        except Exception as e:
            self.set_status(500)
            self.write({"error": "Internal server error"})
class ExamRedoRedirectHandler(BaseHandler):
    def get(self):
        try:
            # Load access info to get customer details
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            
            # Get customer name
            exam_taker_name = host_yaml.get('customer_details', {}).get('exam_taker_full_name', 'Student')
            
            # Get start exam time and convert to readable format
            start_exam_time = host_yaml.get('startExamTime', 0)
            if start_exam_time:
                session_start_time = datetime.fromtimestamp(start_exam_time).strftime('%Y-%m-%d %H:%M:%S UTC')
            else:
                session_start_time = 'Unknown time'
            
            self.render(
                BASE_PATH + 'exam-redo.html',
                exam_taker_name=exam_taker_name,
                session_start_time=session_start_time
            )
        except Exception as e:
            print(f"Error in ExamRedoRedirectHandler: {e}")
            # Fallback rendering with default values
            self.render(
                BASE_PATH + 'exam-redo.html',
                exam_taker_name='Student',
                session_start_time='Unknown time'
            )
class BeginExamHandler(tornado.web.RequestHandler):
    def post(self):

        """
        Handler to create a user Begin Exam in Honorlock API.
        """
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
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            if response.status_code == 200:
                self.write(response.json())
            elif response.status_code == 409:
                self.set_status(409)
                self.write(response.json())
                return
            else:
                self.set_status(response.status_code)
                self.write(response.json())
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})
class BaseUrlHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            self.set_header("Access-Control-Allow-Origin", "*")
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            login_info = host_yaml.get('login_info', {}).get('jump_host', {})
            response = {
                "pwd": login_info.get('pw', ''),
                "user": login_info.get('user', '')
            }
            encoded_response = b64encode(json.dumps(response).encode()).decode()
            self.write({"response": encoded_response})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})

class GetAccessInfoHandler(tornado.web.RequestHandler):
    def validate_field(self, customer_details, field_name, default_value, validated_details, defaulted_fields):
        """
        Validate a single field and add to validated_details with default if needed
        """
        field_value = customer_details.get(field_name)
        if field_value is None or str(field_value).strip() == '':
            validated_details[field_name] = default_value
            defaulted_fields.append(field_name)
            print(f"Field '{field_name}' is empty or missing, using default: {default_value}")
        else:
            validated_details[field_name] = str(field_value)

    def get(self):
        """
        Handler to fetch user details from ACCESS_INFO.yaml file.
        """
        self.set_header("Access-Control-Allow-Origin", "*")
        try:
            # Check authorization
            auth_header = self.request.headers.get('Authorization')
            if not auth_header or not auth_header.startswith('Bearer '):
                self.set_status(401)
                self.write({"error": "Authorization token is missing or invalid"})
                return

            # Read the YAML file
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            
            # Extract customer details
            customer_details = host_yaml.get('customer_details', {})

            default_values = {
                "exam_taker_id": "Arista-test-taker-ID",
                "exam_taker_email": "arista-test-taker@arista.com", 
                "exam_taker_full_name": "Arista Test Taker",
                "external_exam_id": "default-training-exam",
                "exam_taker_attempt_id": "1",
                "exam_hours": "240",
                "lab_type": "Lab",
                "exam_code": "001"
            }
            validated_details = {}
            defaulted_fields = []
            # Validate each field using the helper method
            for field_name, default_value in default_values.items():
                self.validate_field(customer_details, field_name, default_value, validated_details, defaulted_fields)
            self.write({
                    "customer_details": validated_details
                })

        except Exception as e:
            print(f"Error in GetAccessInfoHandler: {str(e)}")
            self.set_status(500)
            self.write({
                "error": str(e),
                    "customer_details": default_values
            })

class TerminalPageHandler(BaseHandler):
    """Handler for the tabbed terminal page."""

    def get(self):
        if not self.current_user:
            if 'auth' in self.request.arguments:
                self.redirect('/login?auth={0}'.format(self.get_argument('auth')))
            else:
                self.redirect('/login')
            return

        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        self.render(
            BASE_PATH + 'terminal.html',
            topo_title=TITLE,
            ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
        )


class TopologyAPIHandler(BaseHandler):
    """API endpoint to return topology data for interactive Cytoscape.js diagram."""

    # Thread-safe cache for parsed topology data (30 second TTL)
    _cache = {}
    _cache_time = 0
    _cache_lock = threading.Lock()
    CACHE_TTL = 30

    # Device type to tier mapping (lower tier = higher in diagram)
    # Tier 0: Internet (top, cloud/WAN edge)
    # Tier 1: ISP cores (grouped by provider: ISP1, ISP2, etc.), Route Reflectors
    # Tier 2: DCI, core, P routers (inter-DC connectivity)
    # Tier 3: Borderleaf, PE, CE, GW (DC edge / WAN gateways)
    # Tier 4: Routers (campus routers, above spines)
    # Tier 5: Spines
    # Tier 6: Leafs
    # Tier 7: Memleaf (member leafs, between leaf and host in campus)
    # Tier 8: Hosts, customers, OOB
    # Tier 9: Other
    DEVICE_TIERS = {
        'internet': 0,
        'isp': 1,
        'rr': 1,
        'core': 2,
        'dci': 2,
        'p': 2,
        'borderleaf': 3,
        'pe': 3,
        'ce': 3,
        'gw': 3,
        'router': 4,
        'spine': 5,
        'leaf': 6,
        'memleaf': 7,
        'host': 8,
        'customer': 8,
        'oob': 8,
        'other': 9
    }

    @staticmethod
    def classify_device_type(device_name):
        """Classify device type based on naming pattern."""
        name_lower = device_name.lower()

        # Order matters: more specific patterns first
        if 'borderleaf' in name_lower or device_name.startswith('BL'):
            return 'borderleaf'
        elif 'memleaf' in name_lower:
            return 'memleaf'
        elif 'spine' in name_lower:
            return 'spine'
        elif 'leaf' in name_lower:
            return 'leaf'
        elif 'router' in name_lower:
            return 'router'
        elif 'host' in name_lower:
            return 'host'
        elif 'dci' in name_lower or device_name == 'DCI':
            return 'dci'
        elif 'internet' in name_lower:
            return 'internet'
        elif 'isp' in name_lower:
            return 'isp'
        elif 'core' in name_lower:
            return 'core'
        elif 'oob' in name_lower:
            return 'oob'
        # Route Reflector: RR or RR1, RR2, etc.
        elif device_name == 'RR' or (device_name.startswith('RR') and len(device_name) > 2 and device_name[2].isdigit()):
            return 'rr'
        # WAN Gateways: GW11, GW12, GW21, GW22, GW31, etc. (GW + DC number + device number)
        elif device_name.startswith('GW') and len(device_name) > 2 and device_name[2].isdigit():
            return 'gw'
        elif device_name.startswith('PE'):
            return 'pe'
        elif device_name.startswith('CE'):
            return 'ce'
        elif device_name.startswith('P') and len(device_name) > 1 and device_name[1].isdigit():
            return 'p'
        # Customer devices: A1, A2, B1, B2, C1, C2, D1, D2, etc.
        elif device_name[0] in ('A', 'B', 'C', 'D') and len(device_name) > 1 and device_name[1].isdigit():
            return 'customer'
        else:
            return 'other'

    @staticmethod
    def extract_datacenter(device_name):
        """
        Extract datacenter identifier from device name.
        E.g., 'spine1-DC1' -> 'DC1', 'leaf2-DC2' -> 'DC2', 'host1' -> ''
        Also handles WAN Gateway naming: 'GW11' -> 'DC1', 'GW21' -> 'DC2', 'GW31' -> 'DC3'
        """
        import re
        # Match -DC followed by number or letter at end of name
        match = re.search(r'-?(DC\d+|dc\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()

        # Handle GW device naming: GW11, GW12 -> DC1, GW21, GW22 -> DC2, GW31 -> DC3
        # First digit after GW is the DC number
        if device_name.startswith('GW') and len(device_name) >= 3 and device_name[2].isdigit():
            dc_num = device_name[2]
            return f'DC{dc_num}'

        return ''  # No datacenter suffix

    @staticmethod
    def extract_isp_provider(device_name):
        """
        Extract ISP provider identifier from device name.
        E.g., 'core1-ISP1' -> 'ISP1', 'core2-ISP2' -> 'ISP2', 'internet' -> ''
        Used for grouping ISP devices by provider in the topology layout.
        """
        import re
        # Match -ISP followed by number
        match = re.search(r'-?(ISP\d+)$', device_name, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        return ''  # No ISP suffix

    @staticmethod
    def get_sort_key(device_name):
        """
        Generate a sort key for natural ordering of device names.
        E.g., spine1, spine2, spine10 sorts correctly (not spine1, spine10, spine2)
        """
        import re
        # Split name into text and number parts
        parts = re.split(r'(\d+)', device_name)
        result = []
        for part in parts:
            if part.isdigit():
                result.append(int(part))
            else:
                result.append(part.lower())
        return result

    @staticmethod
    def detect_topology_type(nodes_data):
        """
        Detect the type of topology based on device types present.
        Returns: 'wan' for P-router mesh topologies, 'datacenter' for spine-leaf
        """
        device_types = set(node['data']['device_type'] for node in nodes_data)

        has_p_routers = 'p' in device_types
        has_pe_routers = 'pe' in device_types
        has_spines = 'spine' in device_types
        has_leaves = 'leaf' in device_types

        # WAN topology: has P and PE routers, no spines
        if has_p_routers and has_pe_routers and not has_spines:
            return 'wan'

        # Datacenter topology: has spines or leaves
        if has_spines or has_leaves:
            return 'datacenter'

        # Default to datacenter layout
        return 'datacenter'

    @staticmethod
    def extract_site_number(device_name):
        """
        Extract site number from device name for WAN topology positioning.
        E.g., 'PE1' -> 1, 'A2' -> 2, 'PE-1' -> 1, 'SiteA-1' -> 1
        Returns 1 for "left" side, 2 for "right" side, 0 for center (P routers)
        """
        import re

        # Look for trailing number
        match = re.search(r'[-_]?(\d+)$', device_name)
        if match:
            num = int(match.group(1))
            # Odd numbers = site 1 (left), Even numbers = site 2 (right)
            return 1 if num % 2 == 1 else 2

        return 0  # No number found - treat as center

    @staticmethod
    def calculate_wan_positions(nodes_data, edges_data):
        """
        Calculate positions for WAN topology with P-router mesh in center.
        Layout: Left customers -> Left PEs -> P mesh -> Right PEs -> Right customers
        """
        import re

        NODE_SPACING_X = 220   # Horizontal spacing
        NODE_SPACING_Y = 160   # Vertical spacing
        COLUMN_SPACING = 260   # Extra spacing between columns
        PADDING = 100

        # Build adjacency for graph analysis
        adjacency = {}
        for node in nodes_data:
            adjacency[node['data']['id']] = set()
        for edge in edges_data:
            src = edge['data']['source']
            tgt = edge['data']['target']
            if src in adjacency and tgt in adjacency:
                adjacency[src].add(tgt)
                adjacency[tgt].add(src)

        # Categorize nodes
        p_routers = []
        pe_routers = []
        customer_devices = []  # CE, hosts, or other edge devices
        other_devices = []

        for node in nodes_data:
            dtype = node['data']['device_type']
            if dtype == 'p':
                p_routers.append(node)
            elif dtype == 'pe':
                pe_routers.append(node)
            elif dtype in ('ce', 'host', 'leaf', 'customer', 'other'):
                customer_devices.append(node)
            else:
                other_devices.append(node)

        # Determine which side each PE is on using graph analysis
        # PEs are on "left" if their connected customers have odd site numbers
        pe_sides = {}
        for pe in pe_routers:
            pe_id = pe['data']['id']
            pe_neighbors = adjacency.get(pe_id, set())

            # Check connected devices (excluding P routers)
            side_votes = []
            for neighbor in pe_neighbors:
                neighbor_node = next((n for n in nodes_data if n['data']['id'] == neighbor), None)
                if neighbor_node and neighbor_node['data']['device_type'] != 'p':
                    site = TopologyAPIHandler.extract_site_number(neighbor)
                    if site > 0:
                        side_votes.append(site)

            # Also consider the PE's own name
            pe_site = TopologyAPIHandler.extract_site_number(pe_id)
            if pe_site > 0:
                side_votes.append(pe_site)

            # Majority vote, default to name-based
            if side_votes:
                pe_sides[pe_id] = 1 if side_votes.count(1) >= side_votes.count(2) else 2
            else:
                pe_sides[pe_id] = pe_site if pe_site > 0 else 1

        # Determine customer sides based on their PE connections
        customer_sides = {}
        for cust in customer_devices:
            cust_id = cust['data']['id']
            cust_neighbors = adjacency.get(cust_id, set())

            # Find connected PEs
            for neighbor in cust_neighbors:
                if neighbor in pe_sides:
                    customer_sides[cust_id] = pe_sides[neighbor]
                    break

            # Fallback to name-based
            if cust_id not in customer_sides:
                site = TopologyAPIHandler.extract_site_number(cust_id)
                customer_sides[cust_id] = site if site > 0 else 1

        # Split into columns (left to right)
        left_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 1]
        left_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 1) == 1]
        right_pes = [n for n in pe_routers if pe_sides.get(n['data']['id'], 2) == 2]
        right_customers = [n for n in customer_devices if customer_sides.get(n['data']['id'], 1) == 2]

        # Sort each group by name
        for group in [left_customers, left_pes, p_routers, right_pes, right_customers]:
            group.sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        # Calculate column X positions
        columns = [left_customers, left_pes, p_routers, right_pes, right_customers]
        column_names = ['left_cust', 'left_pe', 'p_mesh', 'right_pe', 'right_cust']

        # Find max height (most nodes in any column)
        max_height = max(len(col) for col in columns) if columns else 1

        # Position each column
        current_x = PADDING
        for col_idx, column in enumerate(columns):
            if not column:
                continue

            # Center column vertically
            col_height = len(column) * NODE_SPACING_Y
            start_y = PADDING + (max_height * NODE_SPACING_Y - col_height) / 2

            for row_idx, node in enumerate(column):
                node['position'] = {
                    'x': current_x,
                    'y': start_y + row_idx * NODE_SPACING_Y
                }
                node['data']['wan_column'] = column_names[col_idx]

            current_x += COLUMN_SPACING

        # Position any other devices at the top
        if other_devices:
            other_devices.sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))
            for idx, node in enumerate(other_devices):
                node['position'] = {
                    'x': PADDING + idx * NODE_SPACING_X,
                    'y': PADDING / 2
                }
                node['data']['wan_column'] = 'other'

        return nodes_data

    @staticmethod
    def calculate_positions(nodes_data, edges_data=None):
        """
        Calculate x,y positions for nodes based on topology type.
        Automatically detects WAN vs datacenter topologies.
        """
        import re

        # Detect topology type
        topo_type = TopologyAPIHandler.detect_topology_type(nodes_data)

        # Use WAN layout for P-router mesh topologies
        if topo_type == 'wan' and edges_data:
            return TopologyAPIHandler.calculate_wan_positions(nodes_data, edges_data)

        # Standard datacenter layout (tier-based)
        # Group nodes by tier, then by grouping key (datacenter or ISP provider)
        tiers = {}
        ISP_TIER = TopologyAPIHandler.DEVICE_TIERS.get('isp', 1)

        for node in nodes_data:
            device_type = node['data']['device_type']
            tier = TopologyAPIHandler.DEVICE_TIERS.get(device_type, 7)

            # For ISP tier, group by ISP provider (ISP1, ISP2) instead of datacenter
            if tier == ISP_TIER:
                group_key = TopologyAPIHandler.extract_isp_provider(node['data']['id'])
            else:
                group_key = TopologyAPIHandler.extract_datacenter(node['data']['id'])

            if tier not in tiers:
                tiers[tier] = {}
            if group_key not in tiers[tier]:
                tiers[tier][group_key] = []
            tiers[tier][group_key].append(node)

        # Sort nodes within each tier/group by name (natural sort)
        for tier in tiers:
            for group_key in tiers[tier]:
                tiers[tier][group_key].sort(key=lambda n: TopologyAPIHandler.get_sort_key(n['data']['id']))

        # Calculate positions
        NODE_SPACING_X = 200   # Horizontal spacing between nodes
        NODE_SPACING_Y = 220   # Vertical spacing between tiers
        DC_SPACING = 120       # Extra spacing between datacenter groups
        PADDING = 100          # Left padding

        # Calculate max width considering groups and spacing
        # Groups can be DCs (DC1, DC2) or ISP providers (ISP1, ISP2) depending on tier
        max_width = 0
        for tier in tiers:
            tier_width = 0
            group_keys = sorted(tiers[tier].keys())  # Sort: '', 'DC1', 'DC2' or 'ISP1', 'ISP2'
            for i, gk in enumerate(group_keys):
                tier_width += len(tiers[tier][gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:  # Add spacing between groups
                    tier_width += DC_SPACING
            max_width = max(max_width, tier_width)

        if max_width == 0:
            max_width = NODE_SPACING_X

        # Position nodes by tier (use row_index to skip empty tiers)
        row_index = 0
        for tier_num in sorted(tiers.keys()):
            tier_groups = tiers[tier_num]
            group_keys = sorted(tier_groups.keys())  # Sort: '', 'DC1', 'DC2' or 'ISP1', 'ISP2'

            # Calculate this tier's total width
            tier_width = 0
            for i, gk in enumerate(group_keys):
                tier_width += len(tier_groups[gk]) * NODE_SPACING_X
                if i < len(group_keys) - 1:
                    tier_width += DC_SPACING

            # Center this tier
            start_x = PADDING + (max_width - tier_width) / 2
            current_x = start_x

            for i, group_key in enumerate(group_keys):
                group_nodes = tier_groups[group_key]

                for node in group_nodes:
                    node['position'] = {
                        'x': current_x,
                        'y': PADDING + row_index * NODE_SPACING_Y
                    }
                    # Store grouping info for potential UI use
                    if tier_num == ISP_TIER:
                        node['data']['isp_provider'] = group_key if group_key else 'default'
                        node['data']['datacenter'] = 'shared'  # ISP devices are shared across DCs
                    else:
                        node['data']['datacenter'] = group_key if group_key else 'default'
                    current_x += NODE_SPACING_X

                # Add spacing after each group (except last)
                if i < len(group_keys) - 1:
                    current_x += DC_SPACING

            # Increment row index for next tier that has nodes
            row_index += 1

        return nodes_data

    def parse_topology(self, topo_path):
        """
        Parse topo_build.yml and return Cytoscape.js formatted data.

        Returns:
            dict: Success with 'data' key containing topology
            dict: Error with 'error' and 'error_type' keys
        """
        # Try to open and parse the file
        try:
            with open(topo_path, 'r') as f:
                topo_data = YAML().load(f)
        except FileNotFoundError:
            return {'error': f'Topology file not found: {topo_path}', 'error_type': 'not_found'}
        except PermissionError:
            return {'error': f'Permission denied accessing: {topo_path}', 'error_type': 'permission'}
        except Exception as e:
            pS(f"Error parsing topology file: {e}")
            return {'error': f'Failed to parse topology file: {str(e)}', 'error_type': 'parse_error'}

        # Validate topo_data structure
        if topo_data is None:
            return {'error': 'Topology file is empty', 'error_type': 'empty_file'}

        if 'nodes' not in topo_data:
            return {'error': 'Topology file missing "nodes" key', 'error_type': 'invalid_format'}

        if not topo_data['nodes']:
            # Empty nodes list - return empty topology (valid case)
            pS("Warning: Topology file has no nodes")
            return {
                'data': {
                    'metadata': {
                        'topology_name': TOPO,
                        'node_count': 0,
                        'edge_count': 0,
                        'generated_at': datetime.now().isoformat()
                    },
                    'nodes': [],
                    'edges': []
                }
            }

        nodes = []
        edges = []
        edge_set = set()  # Track edges to avoid duplicates

        # First pass: collect all valid node names
        valid_node_names = set()
        for node_entry in topo_data['nodes']:
            if isinstance(node_entry, dict):
                for device_name in node_entry.keys():
                    valid_node_names.add(device_name)

        # Second pass: build nodes and edges
        for node_entry in topo_data['nodes']:
            # Validate node entry is a dict
            if not isinstance(node_entry, dict):
                pS(f"Warning: Invalid node entry format (not a dict): {node_entry}")
                continue

            # Each entry is a dict with device name as key
            for device_name, device_info in node_entry.items():
                # Validate device_info is a dict
                if not isinstance(device_info, dict):
                    pS(f"Warning: Invalid device info for {device_name} (not a dict)")
                    continue

                device_type = self.classify_device_type(device_name)
                ip_addr = device_info.get('ip_addr', 'N/A')
                sys_mac = device_info.get('sys_mac', 'N/A')
                neighbors = device_info.get('neighbors', [])

                # Validate neighbors is a list
                if not isinstance(neighbors, list):
                    pS(f"Warning: Invalid neighbors format for {device_name}")
                    neighbors = []

                # Build port info for tooltip
                ports = []
                for neighbor in neighbors:
                    if not isinstance(neighbor, dict):
                        continue

                    neighbor_device = neighbor.get('neighborDevice', '')

                    ports.append({
                        'port': neighbor.get('port', ''),
                        'neighbor': neighbor_device,
                        'neighbor_port': neighbor.get('neighborPort', '')
                    })

                    # Create edge only if both nodes exist (prevents Cytoscape.js errors)
                    if neighbor_device and neighbor_device in valid_node_names:
                        # Get port values with None-safety
                        device_port = neighbor.get('port') or ''
                        neighbor_port = neighbor.get('neighborPort') or ''

                        # Create edge key that includes ports to support multiple links
                        # between the same device pair (e.g., MLAG, port-channel, redundancy)
                        port_pair = tuple(sorted([device_port, neighbor_port]))
                        edge_key = (tuple(sorted([device_name, neighbor_device])), port_pair)

                        if edge_key not in edge_set:
                            edge_set.add(edge_key)

                            # Use alphabetically sorted order for source/target to ensure
                            # consistent port assignment regardless of processing order.
                            # edge_key[0][0] is the alphabetically first device name.
                            sorted_devices = edge_key[0]
                            if device_name == sorted_devices[0]:
                                # Current device is alphabetically first, so it's the source.
                                # Ports stay as-is: device_port -> source, neighbor_port -> target
                                source_node = device_name
                                target_node = neighbor_device
                                source_port = device_port
                                target_port = neighbor_port
                            else:
                                # Neighbor device is alphabetically first, so it becomes source.
                                # Since we're processing from device_name's perspective, swap ports:
                                # neighbor_port belongs to the alphabetically-first (source) node
                                # device_port belongs to the alphabetically-second (target) node
                                source_node = neighbor_device
                                target_node = device_name
                                source_port = neighbor_port
                                target_port = device_port

                            # Use unique edge ID that includes ports to support parallel links
                            edge_id = f"{source_node}-{target_node}-{source_port}-{target_port}"
                            edges.append({
                                'data': {
                                    'id': edge_id,
                                    'source': source_node,
                                    'target': target_node,
                                    'source_port': source_port,
                                    'target_port': target_port
                                }
                            })
                    elif neighbor_device:
                        pS(f"Warning: Skipping edge {device_name}->{neighbor_device}: target node not in topology")

                # Create node
                nodes.append({
                    'data': {
                        'id': device_name,
                        'label': device_name,
                        'ip': ip_addr,
                        'sys_mac': sys_mac,
                        'device_type': device_type,
                        'status': 'unknown',
                        'ports': ports
                    },
                    'classes': f"device-type-{device_type} status-unknown"
                })

        # Calculate positions based on device type tiers and natural name sorting
        # Pass edges for WAN topology detection which uses graph adjacency
        nodes = self.calculate_positions(nodes, edges)

        return {
            'data': {
                'metadata': {
                    'topology_name': TOPO,
                    'node_count': len(nodes),
                    'edge_count': len(edges),
                    'generated_at': datetime.now().isoformat()
                },
                'nodes': nodes,
                'edges': edges
            }
        }

    def options(self):
        """Handle CORS preflight requests."""
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.set_header("Access-Control-Max-Age", "86400")  # Cache preflight for 24 hours
        self.set_status(204)
        self.finish()

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            current_time = time.time()

            # Thread-safe cache check
            with TopologyAPIHandler._cache_lock:
                if (TopologyAPIHandler._cache and
                    current_time - TopologyAPIHandler._cache_time < TopologyAPIHandler.CACHE_TTL):
                    self.write(json.dumps(TopologyAPIHandler._cache))
                    return

            # Parse topology file (outside lock to avoid blocking)
            topo_path = f"/opt/atd/topologies/{TOPO}/topo_build.yml"
            result = self.parse_topology(topo_path)

            # Check for errors
            if 'error' in result:
                error_type = result.get('error_type', 'unknown')
                if error_type == 'not_found':
                    self.set_status(404)
                elif error_type == 'permission':
                    self.set_status(403)
                else:
                    self.set_status(500)
                self.write(json.dumps({'error': result['error']}))
                return

            topology_data = result['data']

            # Thread-safe cache update
            with TopologyAPIHandler._cache_lock:
                TopologyAPIHandler._cache = topology_data
                TopologyAPIHandler._cache_time = current_time

            self.write(json.dumps(topology_data))

        except Exception as e:
            pS(f"Error in TopologyAPIHandler: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': f'Internal server error: {str(e)}'}))


class DevicesAPIHandler(BaseHandler):
    """API endpoint to return device list grouped by type for terminal page."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        try:
            # Get devices from both modules.yaml and topo_build.yml
            nodes = get_all_devices()

            # Define grouping patterns (order matters - more specific first)
            group_patterns = [
                ('Spine', ['spine']),
                ('Borderleaf', ['borderleaf', 'BL']),
                ('Memleaf', ['memleaf']),
                ('Leaf', ['leaf']),
                ('Router', ['router']),
                ('Host', ['host']),
                ('Core', ['core', 'DCI']),
                ('ISP', ['ISP', 'Internet']),
                ('Route Reflectors', ['RR']),
                ('WAN Gateways', ['GW']),
                ('PE Routers', ['PE']),
                ('P Routers', ['P']),
                ('Customer', []),  # Special handling for A, B, C, D prefixed devices
            ]

            # Group devices
            groups = {name: [] for name, _ in group_patterns}
            groups['Other'] = []

            for device_name, device_info in nodes.items():
                matched = False

                # Special handling for Customer devices (A1, B1, C1, D1, etc.)
                if len(device_name) > 1 and device_name[0] in ('A', 'B', 'C', 'D') and device_name[1].isdigit():
                    groups['Customer'].append({
                        'name': device_name,
                        'ip': device_info.get('ip', ''),
                    })
                    matched = True

                # Special handling for P routers (P1, P2, etc. but not PE)
                elif device_name.startswith('P') and len(device_name) > 1 and device_name[1].isdigit():
                    groups['P Routers'].append({
                        'name': device_name,
                        'ip': device_info.get('ip', ''),
                    })
                    matched = True

                # Check other patterns (case-insensitive)
                if not matched:
                    device_name_lower = device_name.lower()
                    for group_name, prefixes in group_patterns:
                        for prefix in prefixes:
                            prefix_lower = prefix.lower()
                            if prefix_lower and (device_name_lower.startswith(prefix_lower) or prefix_lower in device_name_lower):
                                groups[group_name].append({
                                    'name': device_name,
                                    'ip': device_info.get('ip', ''),
                                })
                                matched = True
                                break
                        if matched:
                            break

                if not matched:
                    groups['Other'].append({
                        'name': device_name,
                        'ip': device_info.get('ip', ''),
                    })

            # Sort devices within each group and remove empty groups
            result = []
            for group_name, _ in group_patterns + [('Other', [])]:
                devices = groups[group_name]
                if devices:
                    # Sort by name
                    devices.sort(key=lambda x: x['name'])
                    result.append({
                        'group': group_name,
                        'devices': devices
                    })

            self.write(json.dumps({
                'topology': TITLE,
                'groups': result
            }))

        except Exception as e:
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))


class InterfaceStatsAPIHandler(BaseHandler):
    """API endpoint for interface statistics via eAPI."""

    # Cache: {device_interface: (timestamp, data)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 10  # seconds

    # Rate calculation: store previous readings for rate computation
    _previous_counters = {}
    _previous_lock = threading.Lock()

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)
        interface = self.get_argument('interface', None)

        if not device or not interface:
            self.set_status(400)
            self.write(json.dumps({'error': 'device and interface parameters required'}))
            return

        try:
            stats = self.get_interface_stats(device, interface)
            self.write(json.dumps(stats))
        except Exception as e:
            pS(f"InterfaceStatsAPIHandler error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def get_interface_stats(self, device_name, interface_name):
        """Query EOS device for interface counters via eAPI."""
        cache_key = f"{device_name}:{interface_name}"
        current_time = time.time()

        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        # Get device IP from topology
        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        # Get credentials from ACCESS_INFO
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        # Connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=10
            )

            # Execute show interfaces command
            result = connection.execute([f"show interfaces {interface_name}"])

            # Parse interface data
            interfaces = result.get('result', [{}])[0].get('interfaces', {})
            intf_data = interfaces.get(interface_name, {})

            if not intf_data:
                raise ValueError(f"Interface {interface_name} not found on {device_name}")

            counters = intf_data.get('interfaceCounters', {})
            bandwidth = intf_data.get('bandwidth', 0)

            # Calculate rates from counter deltas
            rates = self.calculate_rates(cache_key, counters, current_time)

            # Calculate utilization percentage
            if bandwidth > 0:
                utilization_in = (rates['in_rate_bps'] / bandwidth) * 100
                utilization_out = (rates['out_rate_bps'] / bandwidth) * 100
            else:
                utilization_in = 0
                utilization_out = 0

            stats = {
                'device': device_name,
                'interface': interface_name,
                'stats': {
                    'in_octets': counters.get('inOctets', 0),
                    'out_octets': counters.get('outOctets', 0),
                    'in_rate_bps': rates['in_rate_bps'],
                    'out_rate_bps': rates['out_rate_bps'],
                    'in_packets': counters.get('inUcastPkts', 0) + counters.get('inMulticastPkts', 0) + counters.get('inBroadcastPkts', 0),
                    'out_packets': counters.get('outUcastPkts', 0) + counters.get('outMulticastPkts', 0) + counters.get('outBroadcastPkts', 0),
                    'in_errors': counters.get('inErrors', 0) + counters.get('inputErrorsDetail', {}).get('crcErrors', 0),
                    'out_errors': counters.get('outErrors', 0),
                    'in_discards': counters.get('inDiscards', 0),
                    'out_discards': counters.get('outDiscards', 0),
                    'speed_bps': bandwidth,
                    'utilization_in': round(utilization_in, 2),
                    'utilization_out': round(utilization_out, 2),
                    'operational_status': intf_data.get('interfaceStatus', 'unknown'),
                    'line_protocol': intf_data.get('lineProtocolStatus', 'unknown'),
                    'description': intf_data.get('description', ''),
                    'last_updated': datetime.now().isoformat()
                }
            }

            # Update cache
            with self._cache_lock:
                self._cache[cache_key] = (current_time, stats)

            return stats

        except pyeapi.eapilib.ConnectionError as e:
            raise ValueError(f"Cannot connect to {device_name} ({device_ip}): {e}")
        except pyeapi.eapilib.CommandError as e:
            raise ValueError(f"Command error on {device_name}: {e}")

    def calculate_rates(self, cache_key, current_counters, current_time):
        """Calculate bit rates from counter deltas."""
        with self._previous_lock:
            if cache_key in self._previous_counters:
                prev_time, prev_counters = self._previous_counters[cache_key]
                time_delta = current_time - prev_time

                if time_delta > 0:
                    in_octet_delta = current_counters.get('inOctets', 0) - prev_counters.get('inOctets', 0)
                    out_octet_delta = current_counters.get('outOctets', 0) - prev_counters.get('outOctets', 0)

                    # Handle counter wrap (unlikely but possible)
                    if in_octet_delta < 0:
                        in_octet_delta = current_counters.get('inOctets', 0)
                    if out_octet_delta < 0:
                        out_octet_delta = current_counters.get('outOctets', 0)

                    in_rate = (in_octet_delta * 8) / time_delta
                    out_rate = (out_octet_delta * 8) / time_delta
                else:
                    in_rate = out_rate = 0
            else:
                # First reading, no rate available yet
                in_rate = out_rate = 0

            # Store current reading for next calculation
            self._previous_counters[cache_key] = (current_time, {
                'inOctets': current_counters.get('inOctets', 0),
                'outOctets': current_counters.get('outOctets', 0)
            })

            return {'in_rate_bps': round(in_rate, 2), 'out_rate_bps': round(out_rate, 2)}


class DeviceStatusAPIHandler(BaseHandler):
    """API endpoint to check device reachability via eAPI."""

    # Cache: {device: (timestamp, status)}
    _cache = {}
    _cache_lock = threading.Lock()
    CACHE_TTL = 30  # seconds - longer cache for status checks

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)

        # If no device specified, check all devices
        if device:
            try:
                status = self.check_device_status(device)
                self.write(json.dumps(status))
            except Exception as e:
                self.write(json.dumps({
                    'device': device,
                    'status': 'error',
                    'error': str(e)
                }))
        else:
            # Check all devices in topology
            statuses = self.check_all_devices()
            self.write(json.dumps({'devices': statuses}))

    def check_device_status(self, device_name):
        """Check if a single device is reachable via eAPI."""
        cache_key = device_name
        current_time = time.time()

        # Check cache
        with self._cache_lock:
            if cache_key in self._cache:
                timestamp, data = self._cache[cache_key]
                if current_time - timestamp < self.CACHE_TTL:
                    return data

        # Get device IP from topology
        device_ip = get_device_ip_from_sources(device_name)
        pS(f"[DeviceStatus] Checking {device_name} -> IP: {device_ip}")
        if not device_ip:
            result = {
                'device': device_name,
                'status': 'unknown',
                'error': 'Device not found in topology'
            }
            return result

        # Get credentials from ACCESS_INFO
        try:
            host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
            username = host_yaml['login_info']['jump_host']['user']
            password = host_yaml['login_info']['jump_host']['pw']
        except Exception as e:
            result = {
                'device': device_name,
                'status': 'error',
                'error': f'Cannot read credentials: {e}'
            }
            return result

        # Try to connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=5  # Short timeout for status check
            )

            # Simple command to verify connectivity
            result_cmd = connection.execute(['show version'])
            version = result_cmd.get('result', [{}])[0].get('version', 'unknown')

            result = {
                'device': device_name,
                'ip': device_ip,
                'status': 'up',
                'version': version,
                'last_check': datetime.now().isoformat()
            }

        except pyeapi.eapilib.ConnectionError:
            result = {
                'device': device_name,
                'ip': device_ip,
                'status': 'down',
                'error': 'Connection failed',
                'last_check': datetime.now().isoformat()
            }
        except Exception as e:
            result = {
                'device': device_name,
                'ip': device_ip,
                'status': 'error',
                'error': str(e),
                'last_check': datetime.now().isoformat()
            }

        # Update cache
        with self._cache_lock:
            self._cache[cache_key] = (current_time, result)

        return result

    def check_all_devices(self):
        """Check status of all devices from both modules.yaml and topo_build.yml."""
        # Get devices from both sources
        nodes = get_all_devices()
        statuses = {}

        # Debug logging
        pS(f"[DeviceStatus] Found {len(nodes)} devices from all sources")

        # Use thread pool for parallel checks
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {
                executor.submit(self.check_device_status, device_name): device_name
                for device_name in nodes.keys()
            }

            for future in as_completed(futures, timeout=30):
                device_name = futures[future]
                try:
                    result = future.result()
                    statuses[device_name] = result
                except Exception as e:
                    statuses[device_name] = {
                        'device': device_name,
                        'status': 'error',
                        'error': str(e)
                    }

        return statuses


class RunningConfigAPIHandler(BaseHandler):
    """API endpoint to fetch running config from a device via eAPI."""

    def get(self):
        if not self.current_user:
            self.set_status(401)
            self.write(json.dumps({'error': 'Authentication required'}))
            return

        self.set_header("Content-Type", "application/json")
        self.set_header("Access-Control-Allow-Origin", "*")

        device = self.get_argument('device', None)

        if not device:
            self.set_status(400)
            self.write(json.dumps({'error': 'device parameter required'}))
            return

        try:
            config = self.get_running_config(device)
            self.write(json.dumps(config))
        except Exception as e:
            pS(f"RunningConfigAPIHandler error: {e}")
            traceback.print_exc()
            self.set_status(500)
            self.write(json.dumps({'error': str(e)}))

    def get_running_config(self, device_name):
        """Query EOS device for running config via eAPI."""
        # Get device IP from topology
        device_ip = get_device_ip_from_sources(device_name)
        if not device_ip:
            raise ValueError(f"Device {device_name} not found in topology")

        # Get credentials from ACCESS_INFO
        host_yaml = YAML().load(open(ATD_ACCESS_PATH, 'r'))
        username = host_yaml['login_info']['jump_host']['user']
        password = host_yaml['login_info']['jump_host']['pw']

        # Connect via eAPI
        try:
            connection = pyeapi.connect(
                host=device_ip,
                username=username,
                password=password,
                transport='https',
                timeout=15
            )

            # Execute show running-config command with text encoding
            result = connection.execute(['show running-config'], encoding='text')

            # Get the config output from the text response
            config_output = result.get('result', [{}])[0].get('output', '')

            return {
                'device': device_name,
                'config': config_output,
                'timestamp': datetime.now().isoformat()
            }

        except pyeapi.eapilib.ConnectionError as e:
            raise ValueError(f"Cannot connect to {device_name} ({device_ip}): {e}")
        except pyeapi.eapilib.CommandError as e:
            raise ValueError(f"Command error on {device_name}: {e}")


class EndExamHandler(tornado.web.RequestHandler):
    def post(self):

        """
        Handler to create a user Begin Exam in Honorlock API.
        """
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
                'Authorization': f'Bearer {access_token}'
            }

            response = requests.post(url, headers=headers, json=payload)
            try:
                print("Calling upload_exam_unattended.py script to upload exam")
                docker_conn = docker.from_env()
                login_container = docker_conn.containers.get('atd-login')
                login_container.exec_run(f'sudo python3 /usr/local/bin/upload_exam_unattended.py', detach=True)
            except Exception as e:
                print(f"Error running upload_exam_unattended.py: {e}")
                self.write({
                    'honorlock_response': response.json(),
                    'exam_submit': 'Exam has been submitted but error running upload_exam_unattended.py',
                })
            if response.status_code in [200, 201]:
                try:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit': 'Exam has been submitted'
                    })
                except Exception as e:
                    self.write({
                        'honorlock_response': response.json(),
                        'exam_submit_error': str(e)
                    })
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
        except Exception as e:
            self.set_status(500)
            self.write({"error": str(e)})
if __name__ == "__main__":
    settings = {
        'cookie_secret': genCookieSecret(),
        'login_url': "/login"
    }

    app = tornado.web.Application([
        (r'/exam-submitted', ExamSubmittedRedirectHandler),
        (r'/exam-already-running', ExamAlreadyRunningHandler),
        (r'/exam-redo', ExamRedoRedirectHandler),
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "js/"}),
        (r'/css/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "css/"}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "images/"}),
        (r'/topo/(.*)', tornado.web.StaticFileHandler, {'path': ArBASE_PATH}),
        (r'/', topoRequestHandler),
        (r'/td-ws', topoDataHandler),
        (r'/login', LoginHandler),
        (r'/lab', LabHandler),
        (r'/labStaus', LabStausHandler),
        #(r'/tools', ToolsHandler),
        (r'/viewConfig', ViewConfigHandler),
        (r'/resetLab', ResetLabHandler),
        (r'/examStatus', ExamStatusHandler),
        (r'/examSubmit', ExamSubmitHandler),
        (r'/exam-authentication', ExamAuthenticationHandler),     
        (r'/getAccessInfo', GetAccessInfoHandler),
        (r'/getClientId', GetClientIdHandler),
        (r'/getExamInstructions', GetExamInstructionsHandler),
        (r'/getUserSessionId', GetUserSessionIdHandler),
        (r'/beginExam', BeginExamHandler),  
        (r'/endExam', EndExamHandler),
        (r'/baseUrl', BaseUrlHandler),
        (r'/terminal', TerminalPageHandler),
        (r'/td-api/devices', DevicesAPIHandler),
        (r'/td-api/topology', TopologyAPIHandler),
        (r'/td-api/interface-stats', InterfaceStatsAPIHandler),
        (r'/td-api/device-status', DeviceStatusAPIHandler),
        (r'/td-api/running-config', RunningConfigAPIHandler),
    ], **settings)
    app.listen(PORT)
    print('*** Websocket Server Started on {} ***'.format(PORT))
    try:
        TOPO_DATA = getEventStatus(NAME, ZONE)
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        print("*** Websocked Server Stopped ***")
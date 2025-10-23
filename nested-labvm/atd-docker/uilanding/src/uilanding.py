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
            'endexamtime' : EXAM_END_TIME            
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
                self.write({"error": "Conflict detected, redirecting to /exam-redo", "status_code": response.status_code})
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
                'response':"startExamButtonNeeded" if host_yaml['examButtonNeeded'] else "startExamButtonNotNeeded"
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
            EXAM_END_TIME = current_time + (exam_duration * 60)
            host_yaml['endExamTime'] = EXAM_END_TIME
            host_yaml['examButtonNeeded'] = False
            yaml = YAML()
            with open(ATD_ACCESS_PATH, "w") as file:
                yaml.dump(host_yaml, file)         
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
class ExamRedoRedirectHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Content-Type", "text/html")
        try:
            with open(BASE_PATH + 'exam-redo.html', 'r') as file:
                html_content = file.read()
            self.write(html_content)
        except FileNotFoundError:
            self.set_status(404)
            self.write("Error: exam-redo.html not found")
        except Exception as e:
            self.set_status(500)
            self.write(f"Error: {str(e)}")
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
                self.write({"error": "Conflict detected, redirecting to /exam-redo", "status_code": response.status_code})
                return
            else:
                self.set_status(response.status_code)
                self.write({"error": "Failed to fetch data", "status_code": response.status_code})
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
            
            if not customer_details:
                # Return default values if no customer details found
                self.write({
                    "customer_details": {
                        "exam_taker_id": "Arista-test-taker-ID",
                        "exam_taker_email": "arista-test-taker@arista.com",
                        "exam_taker_full_name": "Arista Test Taker",
                        "external_exam_id": "test-course-april_external_id",
                        "exam_taker_attempt_id": "1"
                    }
                })
            else:
                # Return the actual customer details from YAML
                self.write({
                    "customer_details": {
                        "exam_taker_id": str(customer_details.get('exam_taker_id', '')),
                        "exam_taker_email": customer_details.get('exam_taker_email', '').strip(),
                        "exam_taker_full_name": customer_details.get('exam_taker_full_name', ''),
                        "external_exam_id": str(customer_details.get('external_exam_id', '')),
                        "exam_taker_attempt_id": str(customer_details.get('exam_taker_attempt_id', ''))
                    }
                })

        except Exception as e:
            print(f"Error in GetAccessInfoHandler: {str(e)}")
            self.set_status(500)
            self.write({
                "error": str(e),
                    "customer_details": {
                        "exam_taker_id": "Arista-test-taker-ID",
                        "exam_taker_email": "arista-test-taker@arista.com",
                        "exam_taker_full_name": "Arista Test Taker",
                        "external_exam_id": "test-course-april_external_id",
                        "exam_taker_attempt_id": "1"
                    }
            })

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
    ], **settings)
    app.listen(PORT)
    print('*** Websocket Server Started on {} ***'.format(PORT))
    try:
        TOPO_DATA = getEventStatus(NAME, ZONE)
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        print("*** Websocked Server Stopped ***")
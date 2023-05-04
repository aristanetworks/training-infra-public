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
import urllib3
import traceback
import os
import glob
import docker
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
# Open yaml for the default yaml and read what file to lookup for default menu
with open(MENU_BASE_PATH+'default.yaml') as default_menu_file:
    default_menu_info = YAML().load(default_menu_file)
if str(default_menu_info['default_menu']).lower() == 'ssh':
    NOMENUOPTIONFILE =True
else:
    # Open yaml for the lab option (minus 'LAB_' from menu mode) and load the variables
    NOMENUOPTIONFILE = False
    with open('/opt/menus/{0}'.format(default_menu_info['default_menu'])) as  menu_file:
        MENU_ITEMS = YAML().load(menu_file)  
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

# Add a check for the title parameter for legacy deployment catches
if 'title' in host_yaml:
    TITLE = host_yaml['title']
else:
    TITLE = 'Test Drive Lab'

class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return(self.get_secure_cookie("user"))

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
        if not self.current_user:
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
            self.render(
                BASE_PATH + 'index.html',
                NODES = MOD_YAML['topology']['nodes'],
                ARISTA_PWD=host_yaml['login_info']['jump_host']['pw'],
                topo_title = TITLE,
                disable_links = disable_links,
                labguides = labguides,
                topo_cvp = _topo_cvp,
                menu_options = menu
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
            'uptime': self.uptime
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
            instance_data['runtime'] = 8
        return(instance_data)
    except:
        return({
            'boottime': 0,
            'uptime': 0,
            'runtime': 8,
            'status': 'init'
        })

def getEventStatus(instanceName, instanceZone):
    """
    Function to get the currnet status of an instance.
    """
    try:
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

class LabHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        selected_lab_option = self.get_argument('lab_value')
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        container_output=login_container.exec_run(f'python3 /usr/local/bin/callConfigTopo.py  {DEFAULT_MENU_FILE_VALUE} {selected_lab_option}')
        print(container_output)
        with open('log.txt','w') as log_file:
            log_file.write(str(container_output.output.decode("utf-8")))
        with open("log.txt", "r") as txt_file:
            response =  txt_file.readlines()
        self.write({
            'response':response
        })

class LabStausHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        docker_conn= docker.from_env()
        login_container = docker_conn.containers.get('atd-login')
        container_output=login_container.exec_run(f'sudo lab_status.py')
        with open('log.txt','w') as log_file:
            log_file.write(str(container_output.output.decode("utf-8")))
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
class LabGradingHandler(BaseHandler):
    _FILE_PATH = "/etc/opt/graderlogs/"
    _FILE_PATTERN = "*-output.json"
    _TITLE_MAPPINGS = {
        "Training Level 3 Lab":"training-3-1",
    }
    def get(self):
        """
        Gets the latest output json file
        """
        data = self.get_data()
        self.write(json.dumps(data))

    def post(self):
        """
        Calls the docker image to grade
        """
        client = docker.from_env()
        # Call for the running configs
        login_container = client.containers.get('atd-login')
        login_container.exec_run(f'sudo localGrading.py')
        #Call for grading
        lab = self.get_labname()
        if lab:
            grader_container = client.containers.get('atd-grader')
            grader_container.exec_run(f'python selfgrader.py {lab}')
            # container = client.containers.run(image_name, detach=True, tty=True, volumes={
            #     "/etc/opt": {
            #         "bind": "/etc/opt",
            #         "mode": "rw"
            #     }
            # },
            # command=lab
            # )
            # container.wait()
            # output = container.logs().decode("utf-8")
            # container.remove()
        data = self.get_data()
        self.write(json.dumps(data))
    def get_labname(self):
        """
        Gets the lab name and returns the relative lab code
        """
        access_file = "/etc/atd/ACCESS_INFO.yaml"
        host_yaml = YAML().load(open(access_file, 'r'))
        title = host_yaml["title"]
        #if title in title_maps
        lab_code = self._TITLE_MAPPINGS.get(title, None)
        return lab_code


    def get_data(self):
        """
        Gets the data from the output file 
        parses it to the frontend
        """
        data = 'No data available'
        ret_data = {}
        p_data = None
        date_time = None
        if os.path.exists(self._FILE_PATH):
            # get a list of all files in the folder that match the pattern
            files = glob.glob(os.path.join(self._FILE_PATH, self._FILE_PATTERN))
            # sort the files by modification time
            if files:
                files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                latest_updated_file = files[0]
                with open (latest_updated_file, 'r') as f:
                    data = json.load(f)
                p_data = self.parse_data(data)
                file_string = latest_updated_file.split("/")[-1]
                date_string = file_string.split("-")[0] + " " + file_string.split("-")[1]
                date_time = datetime.strptime(date_string, '%Y_%m_%d %H:%M:%S')

        if p_data and date_time:
            ret_data['timestamp'] = str(date_time)
            ret_data['grading'] = p_data
        else:
            ret_data['timestamp'] = str(datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
            ret_data['grading'] =  'No data available'

        return ret_data
    def parse_data(self,data):
        """
        Parses the grading data as per the FE
        """
        out_data = {}
        for lab in data:
            out_data[data[lab]['name']] = {}
            for device in data[lab]['devices']:
                if data[lab]['devices'][device]["status"] != 'pass':
                    out_data[data[lab]['name']][device] = data[lab]['devices'][device]["errors"]
        return out_data

if __name__ == "__main__":
    settings = {
        'cookie_secret': genCookieSecret(),
        'login_url': "/login"
    }
    app = tornado.web.Application([
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "js/"}),
        (r'/css/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "css/"}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': BASE_PATH +  "images/"}),
        (r'/topo/(.*)', tornado.web.StaticFileHandler, {'path': ArBASE_PATH}),
        (r'/', topoRequestHandler),
        (r'/td-ws', topoDataHandler),
        (r'/login', LoginHandler),
        (r'/lab', LabHandler),
        (r'/labStaus', LabStausHandler),
        (r'/resetLab', ResetLabHandler),
        (r'/grade', LabGradingHandler)
    ], **settings)
    app.listen(PORT)
    print('*** Websocket Server Started on {} ***'.format(PORT))
    try:
        TOPO_DATA = getEventStatus(NAME, ZONE)
        tornado.ioloop.IOLoop.instance().start()
    except KeyboardInterrupt:
        tornado.ioloop.IOLoop.instance().stop()
        print("*** Websocked Server Stopped ***")
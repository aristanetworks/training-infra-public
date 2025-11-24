#!/usr/bin/env python

from rcvpapi.rcvpapi import *
import syslog, time
from ruamel.yaml import YAML
import paramiko
from scp import SCPClient
import os
import urllib3
import requests
import grpc
import json
import ssl
import uuid
from google.protobuf.json_format import Parse, MessageToDict
from arista.workspace.v1 import services as ws_services
from arista.workspace.v1 import models as ws_models
from cvprac.cvp_client import CvpClient
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


DEBUG = False

# Cmds to copy bare startup to running
cp_run_start = """enable
copy running-config startup-config
"""
cp_start_run = """enable
copy startup-config running-config
"""
# Cmds to grab ZTP status
ztp_cmds = """enable
show zerotouch | grep ZeroTouch
"""
# Cancel ZTP
ztp_cancel = """enable
zerotouch cancel
"""

# Create class to handle configuring the topology
class ConfigureTopology():
    # Configuration constants
    RECENT_TASK_LIMIT = 50
    TASK_CHECK_INTERVAL = 10
    MAX_TASK_WAIT = 600  # 10 minutes
    TOPOLOGY_SETTLE_TIME = 15
    RESET_SETTLE_TIME = 5
    WORKSPACE_TIMEOUT = 30
    GRPC_LIST_TIMEOUT = 10

    def __init__(self,selected_menu,selected_lab,public_module_flag=False):
        self.selected_menu = selected_menu
        self.selected_lab = selected_lab
        self.public_module_flag = public_module_flag
        self.deploy_lab()

    def connect_to_cvp(self,access_info):
        # Adding new connection to CVP via rcvpapi
        cvp_clnt = ''
        cvpUsername = access_info['login_info']['jump_host']['user']
        cvpPassword = access_info['login_info']['jump_host']['pw']
        while not cvp_clnt:
            try:
                cvp_clnt = CVPCON(access_info['nodes']['cvp'][0]['ip'], cvpUsername, cvpPassword)
                self.send_to_syslog("OK","Connected to CVP at {0}".format(access_info['nodes']['cvp'][0]['ip']))
                return cvp_clnt
            except Exception as e:
                self.send_to_syslog("ERROR", "CVP is currently unavailable: {0}. Retrying in 30 seconds.".format(str(e)))
                time.sleep(30)

    def remove_configlets(self,device,lab_configlets):
        """
        Removes all configlets except the ones defined as 'base'
        Define base configlets that are to be untouched
        """
        base_configlets = ['ATD-INFRA']
        
        configlets_to_remove = []
        configlets_to_remain = base_configlets

        configlets = self.client.getConfigletsByNetElementId(device)
        for configlet in configlets['configletList']:
            if configlet['name'] in base_configlets:
                configlets_to_remain.append(configlet['name'])
                self.send_to_syslog("INFO", "Configlet {0} is part of the base on {1} - Configlet will remain.".format(configlet['name'], device.hostname))
            elif configlet['name'] not in lab_configlets:
                configlets_to_remove.append(configlet['name'])
                self.send_to_syslog("INFO", "Configlet {0} not part of lab configlets on {1} - Removing from device".format(configlet['name'], device.hostname))
            else:
                pass
        if len(configlets_to_remain) > 0:
            device.removeConfiglets(self.client,configlets_to_remove)
            self.client.addDeviceConfiglets(device, configlets_to_remain)
            self.client.applyConfiglets(device)
        else:
            pass

    def get_device_info(self):
        eos_devices = []
        for dev in self.client.inventory:
            tmp_eos = self.client.inventory[dev]
            tmp_eos_sw = CVPSWITCH(dev, tmp_eos['ipAddress'])
            tmp_eos_sw.updateDevice(self.client)
            eos_devices.append(tmp_eos_sw)
        return(eos_devices)


    def update_topology(self,configlets):
        # Get all the devices in CVP
        devices = self.get_device_info()
        # Loop through all devices
        
        for device in devices:
            # Get the actual name of the device
            device_name = device.hostname
            
            # Define a list of configlets built off of the lab yaml file
            lab_configlets = []
            for configlet_name in configlets[self.selected_lab][device_name]:
                lab_configlets.append(configlet_name)

            # Remove unnecessary configlets
            self.remove_configlets(device, lab_configlets)

            # Apply the configlets to the device
            self.client.addDeviceConfiglets(device, lab_configlets)
            self.client.applyConfiglets(device)

        # Perform a single Save Topology by default
        self.client.saveTopology()

    def send_to_syslog(self,mstat,mtype):
        """
        Function to send output from service file to Syslog
        Parameters:
        mstat = Message Status, ie "OK", "INFO" (required)
        mtype = Message to be sent/displayed (required)
        """
        mmes = "\t" + mtype
        syslog.syslog("[{0}] {1}".format(mstat,mmes.expandtabs(7 - len(mstat))))
        if DEBUG:
            print("[{0}] {1}".format(mstat,mmes.expandtabs(7 - len(mstat))))


    def push_bare_config(self,veos_host, veos_ip, veos_config):
        """
        Pushes a bare config to the EOS device.
        """
        # Write config to tmp file
        device_config = "/tmp/" + veos_host + ".cfg"
        with open(device_config,"a") as tmp_config:
            tmp_config.write(veos_config)

        DEVREBOOT = False
        veos_ssh = paramiko.SSHClient()
        veos_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        veos_ssh.connect(hostname=veos_ip, username="root", password="", port="50001")
        scp = SCPClient(veos_ssh.get_transport())
        scp.put(device_config,remote_path="/mnt/flash/startup-config")
        scp.close()
        veos_ssh.exec_command('FastCli -c "{0}"'.format(cp_start_run))
        veos_ssh.exec_command('FastCli -c "{0}"'.format(cp_run_start))
        stdin, stdout, stderr = veos_ssh.exec_command('FastCli -c "{0}"'.format(ztp_cmds))
        ztp_out = stdout.readlines()
        if 'Active' in ztp_out[0]:
            DEVREBOOT = True
            self.send_to_syslog("INFO", "Rebooting {0}...This will take a couple minutes to come back up".format(veos_host))
            #veos_ssh.exec_command("/sbin/reboot -f > /dev/null 2>&1 &")
            veos_ssh.exec_command('FastCli -c "{0}"'.format(ztp_cancel))
        veos_ssh.close()
        return(DEVREBOOT)

    def check_for_tasks(self, max_wait=None, interval=None):
        """
        Check for tasks in progress and wait for them to complete.

        Args:
            max_wait: Maximum time to wait in seconds (default: class constant MAX_TASK_WAIT)
            interval: Check interval in seconds (default: class constant TASK_CHECK_INTERVAL)

        Returns:
            bool: True if all tasks completed, False if timeout
        """
        if max_wait is None:
            max_wait = self.MAX_TASK_WAIT
        if interval is None:
            interval = self.TASK_CHECK_INTERVAL

        elapsed = 0
        while elapsed < max_wait:
            self.client.getRecentTasks(self.RECENT_TASK_LIMIT)
            tasks_in_progress = False

            for task in self.client.tasks['recent']:
                if 'in progress' in task['workOrderUserDefinedStatus'].lower():
                    self.send_to_syslog('INFO', 'Task Check: Task {0} status: {1}'.format(
                        task['workOrderId'], task['workOrderUserDefinedStatus']))
                    tasks_in_progress = True

            if not tasks_in_progress:
                self.send_to_syslog('OK', 'All tasks completed')
                return True

            self.send_to_syslog('INFO', 'Tasks in progress. Waiting for {0} seconds...'.format(interval))
            print('Tasks are currently executing. Waiting {0} seconds...'.format(interval))
            time.sleep(interval)
            elapsed += interval

        self.send_to_syslog('ERROR', 'Timeout waiting for tasks to complete after {0} seconds'.format(max_wait))
        return False



    def get_cvprac_client(self, access_info):
        cvpUsername = access_info['login_info']['jump_host']['user']
        cvpPassword = access_info['login_info']['jump_host']['pw']
        cvp_ip = access_info['nodes']['cvp'][0]['ip']
        clnt = CvpClient()
        clnt.connect([cvp_ip], cvpUsername, cvpPassword)
        return clnt

    def get_grpc_channel(self, access_info):
        cvpUsername = access_info['login_info']['jump_host']['user']
        cvpPassword = access_info['login_info']['jump_host']['pw']
        cvp_ip = access_info['nodes']['cvp'][0]['ip']
        
        # Get token
        try:
            response = requests.post(
                'https://{0}/cvpservice/login/authenticate.do'.format(cvp_ip),
                auth=(cvpUsername, cvpPassword),
                verify=False
            )
            token = response.json()['sessionId']
        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to get CVP token: {0}".format(str(e)))
            raise e

        # Get cert
        try:
            cert = ssl.get_server_certificate((cvp_ip, 443))
        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to get CVP cert: {0}".format(str(e)))
            raise e

        # Create channel
        call_creds = grpc.access_token_call_credentials(token)
        channel_creds = grpc.ssl_channel_credentials(root_certificates=cert.encode())
        conn_creds = grpc.composite_channel_credentials(channel_creds, call_creds)
        
        return grpc.secure_channel('{0}:443'.format(cvp_ip), conn_creds)

    def reset_studios(self, access_info):
        """
        Reset all studios to blank state
        """
        self.send_to_syslog("INFO", "Resetting CloudVision Studios (Master Reset)...")
        print("Resetting CloudVision Studios (Master Reset)...")

        # Extract credentials
        self.cvp_ip = access_info['nodes']['cvp'][0]['ip']
        self.username = access_info['login_info']['jump_host']['user']
        self.password = access_info['login_info']['jump_host']['pw']

        try:
            # Initialize gRPC channel and stubs
            channel = self.get_grpc_channel(access_info)
            self.workspace_stub = ws_services.WorkspaceServiceStub(channel)
            self.workspace_config_stub = ws_services.WorkspaceConfigServiceStub(channel)
            
            # Import Studio services locally
            try:
                import arista.studio.v1.services as studio_services
                self.inputs_stub = studio_services.InputsServiceStub(channel)
                self.inputs_config_stub = studio_services.InputsConfigServiceStub(channel)
            except ImportError:
                self.send_to_syslog("ERROR", "Could not import arista.studio.v1")
                print("ERROR: Could not import arista.studio.v1")
                return False

            self.channel = channel # Save for CC stubs later

            # 1. Create a new workspace for the reset
            self.send_to_syslog("INFO", "Creating reset workspace...")
            print("Creating reset workspace...")
            req_id = str(uuid.uuid4())
            reset_ws_id = "Reset-Studios-" + req_id
            
            # Create workspace
            json_ws_req = json.dumps({
                "value": {
                    "key": {
                        "workspace_id": reset_ws_id
                    },
                    "display_name": "Reset Studios " + req_id,
                    "description": "Automated reset of all studios"
                }
            })
            ws_req = Parse(json_ws_req, ws_services.WorkspaceConfigSetRequest(), False)
            self.workspace_config_stub.Set(ws_req, timeout=self.WORKSPACE_TIMEOUT)
            self.send_to_syslog("OK", "Created workspace: {0}".format(reset_ws_id))
            print("  ✓ Created workspace: {0}".format(reset_ws_id))
            
            # 2. Identify and Clear Inputs for Mainline Studios
            self.send_to_syslog("INFO", "Clearing studio inputs...")
            print("Clearing studio inputs...")
            studios_to_reset = ["Campus Fabric", "Data Center", "Enterprise Routing"]
            studios_reset_count = 0
            
            # Get all inputs to find the ones we need to clear
            # We need to list inputs and filter by studio ID
            # Since we can't easily list all inputs by studio name directly without mapping,
            # we will iterate through known studio IDs if possible, or list all inputs and filter.
            # For this environment, we'll try to list all inputs and clear those belonging to the target studios.
            
            # Actually, a better approach for "Reset" is to set the inputs to empty for the specific studios.
            # But we need the Studio IDs.
            # Let's assume we need to clear everything.
            
            # Simplified approach: Iterate through all studios, if name matches, clear inputs.
            # We need a Studio Service for this, but we only have InputsService.
            # Let's try to list all inputs and see if we can identify them.
            
            # Alternative: Just use the known Studio IDs if they were constant, but they aren't.
            # Let's use the InputsService to get all inputs.
            
            json_inputs_req = json.dumps({})
            inputs_req = Parse(json_inputs_req, studio_services.InputsStreamRequest(), False)
            
            # We will collect keys to clear
            keys_to_clear = []
            
            for response in self.inputs_stub.GetAll(inputs_req, timeout=self.GRPC_LIST_TIMEOUT):
                if hasattr(response, 'value') and response.value:
                    inp = response.value
                    # We need to check if this input belongs to a studio we want to reset.
                    # The input key has studio_id.
                    # We don't have a map of Studio Name -> Studio ID here easily without StudioService.
                    # However, the previous script logic implied we might just want to clear *all* inputs or specific ones.
                    # Let's look at what the user likely wants: A full reset.
                    # We will clear ALL inputs found.
                    
                    inp_dict = MessageToDict(inp, preserving_proto_field_name=True)
                    key = inp_dict.get('key', {})
                    if key:
                        keys_to_clear.append(key)
            
            if keys_to_clear:
                self.send_to_syslog("INFO", "Found {0} input configurations to clear.".format(len(keys_to_clear)))
                print("  Found {0} input configurations to clear.".format(len(keys_to_clear)))
                for key in keys_to_clear:
                    # Set inputs to empty string/null for this key in the reset workspace
                    # The key needs to include the workspace_id
                    key['workspace_id'] = reset_ws_id
                    
                    # Create a delete/clear request. 
                    # Setting the value to empty string or null might work depending on the model.
                    # Or using InputsConfigDeleteRequest if available? No, usually we Set with empty value.
                    # Let's try setting the path value to null/empty.
                    
                    # Actually, to "reset", we usually remove the inputs.
                    # Let's try to set the inputs to empty JSON "{}"
                    
                    json_set_req = json.dumps({
                        "value": {
                            "key": key,
                            "inputs": "{}" # Empty JSON object
                        }
                    })
                    set_req = Parse(json_set_req, studio_services.InputsConfigSetRequest(), False)
                    self.inputs_config_stub.Set(set_req, timeout=self.WORKSPACE_TIMEOUT)
                self.send_to_syslog("OK", "Cleared inputs for {0} items".format(len(keys_to_clear)))
                print("  ✓ Cleared inputs for {0} items".format(len(keys_to_clear)))
                studios_reset_count = len(keys_to_clear)
            else:
                self.send_to_syslog("INFO", "No inputs found to clear.")
                print("  No inputs found to clear.")

            if studios_reset_count > 0:
                # 3. Start Build
                self.send_to_syslog("INFO", "Starting build...")
                print("Starting build...")
                json_build_req = json.dumps({
                    "value": {
                        "key": {
                            "workspace_id": reset_ws_id
                        },
                        "request": 1, # REQUEST_START_BUILD
                        "request_params": {
                            "request_id": req_id
                        }
                    }
                })
                build_req = Parse(json_build_req, ws_models.WorkspaceConfigSetRequest(), False)
                self.workspace_config_stub.Set(build_req, timeout=self.WORKSPACE_TIMEOUT)
                self.send_to_syslog("OK", "Build requested")
                print("  ✓ Build requested")
                
                # 4. Poll for Build Completion
                self.send_to_syslog("INFO", "Waiting for build to complete...")
                print("Waiting for build to complete...")
                for i in range(10):
                    try:
                        # Get workspace status
                        json_get_req = json.dumps({})
                        get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                        
                        found = False
                        for response in self.workspace_stub.GetAll(get_req, timeout=self.GRPC_LIST_TIMEOUT):
                            if hasattr(response, 'value') and response.value:
                                ws = response.value
                                ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                key = ws_dict.get('key', {})
                                if key.get('workspace_id') == reset_ws_id:
                                    state = ws_dict.get('state', 'UNKNOWN')
                                    self.send_to_syslog("INFO", "Status check {0}/10: {1}".format(i+1, state))
                                    print("  Status check {0}/10: {1}".format(i+1, state))
                                    
                                    if str(state) == 'WORKSPACE_STATE_BUILT' or state == 5:
                                        self.send_to_syslog("OK", "Workspace is BUILT")
                                        print("  ✓ Workspace is BUILT")
                                        found = True
                                        break
                                    elif str(state) == 'WORKSPACE_STATE_CONFLICTS' or state == 4:
                                        self.send_to_syslog("ERROR", "Workspace has CONFLICTS")
                                        print("  ✗ Workspace has CONFLICTS")
                                        return False
                        
                        if found:
                            break
                        
                        time.sleep(2)
                    except Exception as e:
                        self.send_to_syslog("ERROR", "Error checking status: {0}".format(e))
                        print("  Error checking status: {0}".format(e))
                else:
                    self.send_to_syslog("ERROR", "Timeout waiting for workspace to build")
                    print("  ✗ Timeout waiting for workspace to build")
                    return False

                # 5. Submit Workspace
                self.send_to_syslog("INFO", "Submitting workspace...")
                print("Submitting workspace...")
                json_submit_req = json.dumps({
                    "value": {
                        "key": {
                            "workspace_id": reset_ws_id
                        },
                        "request": 3, # REQUEST_SUBMIT
                        "request_params": {
                            "request_id": req_id
                        }
                    }
                })
                submit_req = Parse(json_submit_req, ws_models.WorkspaceConfigSetRequest(), False)
                self.workspace_config_stub.Set(submit_req, timeout=self.WORKSPACE_TIMEOUT)
                self.send_to_syslog("OK", "Submit requested")
                print("  ✓ Submit requested")
                
                # Poll for submission
                self.send_to_syslog("INFO", "Waiting for submission...")
                print("Waiting for submission...")
                max_retries = 30
                for i in range(max_retries):
                    # Get workspace status
                    json_get_req = json.dumps({})
                    get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                    
                    found = False
                    for response in self.workspace_stub.GetAll(get_req, timeout=self.GRPC_LIST_TIMEOUT):
                        if hasattr(response, 'value') and response.value:
                            ws = response.value
                            ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                            key = ws_dict.get('key', {})
                            if key.get('workspace_id') == reset_ws_id:
                                state = ws_dict.get('state', 'UNKNOWN')
                                self.send_to_syslog("INFO", "Status check {0}/{1}: {2}".format(i+1, max_retries, state))
                                print("  Status check {0}/{1}: {2}".format(i+1, max_retries, state))
                                
                                if str(state) == 'WORKSPACE_STATE_SUBMITTED' or state == 2:
                                    self.send_to_syslog("OK", "Workspace is SUBMITTED")
                                    print("  ✓ Workspace is SUBMITTED")
                                    found = True
                                    break
                                elif str(state) == 'WORKSPACE_STATE_CONFLICTS' or state == 4:
                                    self.send_to_syslog("ERROR", "Workspace has CONFLICTS")
                                    print("  ✗ Workspace has CONFLICTS")
                                    return False
                    
                    if found:
                        break
                    
                    time.sleep(2)
                else:
                    self.send_to_syslog("ERROR", "Timeout waiting for workspace to submit")
                    print("  ✗ Timeout waiting for workspace to submit")
                    return False

                # 6. Execute Change Controls
                self.send_to_syslog("INFO", "Checking for generated Change Controls...")
                print("Checking for generated Change Controls...")
                
                # Import CC services locally
                try:
                    import arista.changecontrol.v1.services as cc_services
                    import arista.changecontrol.v1.models as cc_models
                    cc_stub = cc_services.ChangeControlServiceStub(self.channel)
                    approve_stub = cc_services.ApproveConfigServiceStub(self.channel)
                    cc_config_stub = cc_services.ChangeControlConfigServiceStub(self.channel)
                except ImportError:
                    self.send_to_syslog("ERROR", "Could not import arista.changecontrol.v1")
                    print("ERROR: Could not import arista.changecontrol.v1")
                    return False

                # Get workspace again to find cc_ids
                try:
                    json_get_req = json.dumps({})
                    get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                    
                    cc_ids = []
                    for response in self.workspace_stub.GetAll(get_req, timeout=self.GRPC_LIST_TIMEOUT):
                        if hasattr(response, 'value') and response.value:
                            ws = response.value
                            ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                            key = ws_dict.get('key', {})
                            if key.get('workspace_id') == reset_ws_id:
                                if 'cc_ids' in ws_dict and 'values' in ws_dict['cc_ids']:
                                    cc_ids = ws_dict['cc_ids']['values']
                                break
                    
                    if cc_ids:
                        self.send_to_syslog("INFO", "Found {0} Change Controls: {1}".format(len(cc_ids), cc_ids))
                        print("Found {0} Change Controls: {1}".format(len(cc_ids), cc_ids))
                        for cc_id in cc_ids:
                            self.send_to_syslog("INFO", "Processing Change Control: {0}".format(cc_id))
                            print("Processing Change Control: {0}".format(cc_id))
                            
                            # 6.0 Get Change Control Version
                            try:
                                json_cc_req = json.dumps({"key": {"id": cc_id}})
                                cc_req = Parse(json_cc_req, cc_services.ChangeControlRequest(), False)
                                cc_resp = cc_stub.GetOne(cc_req, timeout=self.GRPC_LIST_TIMEOUT)
                                cc_version = None
                                if hasattr(cc_resp, 'value') and hasattr(cc_resp.value, 'change') and hasattr(cc_resp.value.change, 'time'):
                                     cc_version = cc_resp.value.change.time
                                     self.send_to_syslog("INFO", "Fetched CC version: {0}".format(cc_version))
                                     print("  Fetched CC version: {0}".format(cc_version))
                                else:
                                     self.send_to_syslog("WARNING", "Could not fetch CC version, trying without...")
                                     print("  Warning: Could not fetch CC version, trying without...")
                            except Exception as e:
                                self.send_to_syslog("WARNING", "Failed to fetch CC details: {0}".format(e))
                                print("  Warning: Failed to fetch CC details: {0}".format(e))
                                cc_version = None

                            # 6.1 Approve Change Control
                            try:
                                self.send_to_syslog("INFO", "Approving Change Control...")
                                print("  Approving...")
                                approve_dict = {
                                    "value": {
                                        "key": {
                                            "id": cc_id
                                        },
                                        "approve": {
                                            "value": True,
                                            "notes": "Approved by reset script"
                                        }
                                    }
                                }
                                
                                if cc_version:
                                    approve_dict["value"]["version"] = MessageToDict(cc_version)

                                json_approve_req = json.dumps(approve_dict)
                                approve_req = Parse(json_approve_req, cc_services.ApproveConfigSetRequest(), False)
                                approve_stub.Set(approve_req, timeout=self.WORKSPACE_TIMEOUT)
                                self.send_to_syslog("OK", "Approved Change Control")
                                print("  ✓ Approved")
                            except Exception as e:
                                self.send_to_syslog("ERROR", "Failed to approve Change Control: {0}".format(e))
                                print("  ✗ Failed to approve: {0}".format(e))
                                continue

                            # 6.2 Start Change Control
                            try:
                                self.send_to_syslog("INFO", "Starting Change Control...")
                                print("  Starting...")
                                start_dict = {
                                    "value": {
                                        "key": {
                                            "id": cc_id
                                        },
                                        "start": {
                                            "value": True,
                                            "notes": "Started by reset script"
                                        }
                                    }
                                }
                                json_start_req = json.dumps(start_dict)
                                start_req = Parse(json_start_req, cc_services.ChangeControlConfigSetRequest(), False)
                                cc_config_stub.Set(start_req, timeout=self.WORKSPACE_TIMEOUT)
                                self.send_to_syslog("OK", "Started Change Control")
                                print("  ✓ Started")
                            except Exception as e:
                                self.send_to_syslog("ERROR", "Failed to start Change Control: {0}".format(e))
                                print("  ✗ Failed to start: {0}".format(e))
                                continue
                            
                            # 6.3 Wait for Completion
                            self.send_to_syslog("INFO", "Waiting for Change Control completion...")
                            print("  Waiting for completion...")
                            for i in range(60): # Increased wait time
                                try:
                                    # Get CC status
                                    json_cc_req = json.dumps({"key": {"id": cc_id}})
                                    cc_req = Parse(json_cc_req, cc_services.ChangeControlRequest(), False)
                                    cc_resp = cc_stub.GetOne(cc_req, timeout=self.GRPC_LIST_TIMEOUT)
                                    
                                    if hasattr(cc_resp, 'value'):
                                        cc_val = cc_resp.value
                                        status = cc_val.status
                                        # 0: UNSPECIFIED, 1: RUNNING, 2: COMPLETED, 3: SCHEDULED, 4: NOT_STARTED
                                        self.send_to_syslog("INFO", "Status check {0}/60: {1}".format(i+1, status))
                                        print("    Status check {0}/60: {1}".format(i+1, status))
                                        
                                        if status == 2: # COMPLETED
                                            self.send_to_syslog("OK", "Change Control Completed")
                                            print("  ✓ Change Control Completed")
                                            break
                                        
                                        # Check for errors
                                        if hasattr(cc_val, 'error') and cc_val.error and hasattr(cc_val.error, 'message') and cc_val.error.message:
                                             self.send_to_syslog("ERROR", "Change Control Error: {0}".format(cc_val.error.message))
                                             print("  ✗ Change Control Error: {0}".format(cc_val.error.message))
                                             break
                                        
                                        # If RUNNING, SCHEDULED, or NOT_STARTED, keep waiting
                                        if status in [1, 3, 4]:
                                            pass 
                                        else:
                                            # Unknown status
                                            self.send_to_syslog("WARNING", "Unknown Change Control status: {0}".format(status))
                                            pass

                                except Exception as e:
                                    self.send_to_syslog("ERROR", "Error checking Change Control status: {0}".format(e))
                                    print("    Error checking status: {0}".format(e))
                                
                                time.sleep(2)
                            else:
                                self.send_to_syslog("WARNING", "Timeout waiting for Change Control completion")
                                print("  Warning: Timeout waiting for Change Control completion")

                    else:
                        self.send_to_syslog("INFO", "No Change Controls found in workspace.")
                        print("No Change Controls found in workspace.")

                except Exception as e:
                    self.send_to_syslog("ERROR", "Failed to process Change Controls: {0}".format(e))
                    print("  ✗ Failed to process Change Controls: {0}".format(e))
                return True

            else:
                self.send_to_syslog("INFO", "No studios to reset.")
                print("No studios to reset.")
                # Delete the empty workspace
                try:
                    json_del_req = json.dumps({
                        "key": {
                            "workspace_id": reset_ws_id
                        }
                    })
                    del_req = Parse(json_del_req, ws_services.WorkspaceConfigDeleteRequest(), False)
                    self.workspace_config_stub.Delete(del_req, timeout=self.WORKSPACE_TIMEOUT)
                    self.send_to_syslog("OK", "Deleted empty reset workspace")
                    print("  ✓ Deleted empty reset workspace")
                except Exception as e:
                    self.send_to_syslog("WARNING", "Failed to delete empty reset workspace: {0}".format(e))
                    pass
                return True

        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to reset Studios: {0}".format(str(e)))
            print("Failed to reset Studios: {0}".format(str(e)))
            return False

    def cancel_pending_tasks(self, access_info):
        self.send_to_syslog("INFO", "Checking for pending tasks to cancel...")
        try:
            clnt = self.get_cvprac_client(access_info)
            # Check if 'task' attribute exists or use direct method (fix for CvpApi error)
            if hasattr(clnt.api, 'task'):
                tasks = clnt.api.task.get_tasks_by_status('Pending')
                cancel_func = clnt.api.task.cancel_task
            else:
                tasks = clnt.api.get_tasks_by_status('Pending')
                cancel_func = clnt.api.cancel_task
                
            if tasks:
                for task in tasks:
                    self.send_to_syslog("INFO", "Cancelling task: {0}".format(task['workOrderId']))
                    cancel_func(task['workOrderId'])
                self.send_to_syslog("OK", "All pending tasks cancelled.")
            else:
                self.send_to_syslog("INFO", "No pending tasks found.")
        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to cancel tasks: {0}".format(str(e)))
            print("Failed to cancel tasks: {0}".format(str(e)))

    def check_pending_tasks_warning(self, access_info):
        try:
            clnt = self.get_cvprac_client(access_info)
            tasks = clnt.api.task.get_tasks_by_status('Pending')
            if tasks:
                msg = "WARNING: Found {0} pending tasks. This might block the lab deployment.".format(len(tasks))
                self.send_to_syslog("WARNING", msg)
                print("\n" + "!"*50)
                print(msg)
                print("!"*50 + "\n")
        except:
            pass

    def deploy_lab(self):


        # Check for additional commands in lab yaml file
        lab_file = open('/home/arista/menus/{0}'.format(self.selected_menu + '.yaml'))
        lab_info = YAML().load(lab_file)
        lab_file.close()

        additional_commands = []
        if 'additional_commands' in lab_info['lab_list'][self.selected_lab]:
            additional_commands = lab_info['lab_list'][self.selected_lab]['additional_commands']

        # Get access info for the topology
        f = open('/etc/atd/ACCESS_INFO.yaml')
        access_info = YAML().load(f)
        f.close()

        # List of configlets
        lab_configlets = lab_info['labconfiglets']

        # Send message that deployment is beginning
        self.send_to_syslog('INFO', 'Starting deployment for {0} - {1} lab...'.format(self.selected_menu,self.selected_lab))
        print("Starting deployment for {0} - {1} lab...".format(self.selected_menu,self.selected_lab))

        # Check if the topo has CVP, and if it does, create CVP connection
        if 'cvp' in access_info['nodes']:
            self.client = self.connect_to_cvp(access_info)

            # Handle Reset vs Standard Lab
            if self.selected_lab == 'reset':
                # Step 1: Cancel any pending CVP tasks
                self.send_to_syslog("INFO", "Step 1/4: Cancelling pending tasks...")
                print("\n=== CVP Reset Process ===")
                print("Step 1/4: Cancelling pending tasks...")
                self.cancel_pending_tasks(access_info)

                # Step 2: Wait for task cancellations to complete
                self.send_to_syslog("INFO", "Step 2/4: Waiting for task cancellations to settle...")
                print("Step 2/4: Waiting for task cancellations to settle...")
                time.sleep(self.RESET_SETTLE_TIME)
                self.check_for_tasks()

                # Step 3: Delete all user studios/workspaces
                self.send_to_syslog("INFO", "Step 3/4: Deleting user workspaces...")
                print("Step 3/4: Deleting user workspaces...")
                studio_reset_success = self.reset_studios(access_info)

                # Step 4: Wait for studio deletions to settle before applying baseline configs
                self.send_to_syslog("INFO", "Step 4/4: Resetting devices to baseline configuration...")
                print("Step 4/4: Resetting devices to baseline configuration...")
                time.sleep(self.RESET_SETTLE_TIME)

                if not studio_reset_success:
                    self.send_to_syslog("WARNING", "Studio reset had failures, but continuing with configuration reset...")
                    print("WARNING: Studio reset had some failures, but continuing with configuration reset...")
            else:
                self.check_pending_tasks_warning(access_info)

            self.check_for_tasks()

            # Config the topology
            self.update_topology(lab_configlets)
            # Wait time for CVP to generate tasks
            time.sleep(self.TOPOLOGY_SETTLE_TIME)
            
            # Execute all tasks generated from reset_devices()
            print('Gathering task information...')
            self.send_to_syslog("INFO", 'Gathering task information')
            self.client.getAllTasks("pending")
            tasks_to_check = self.client.tasks['pending']
            self.send_to_syslog('INFO', 'Relevant tasks: {0}'.format([task['workOrderId'] for task in tasks_to_check]))
            self.client.execAllTasks("pending")
            self.send_to_syslog("OK", 'Completed setting devices to topology: {}'.format(self.selected_lab))

            print('Waiting on change control to finish executing...')
            all_tasks_completed = False
            while not all_tasks_completed:
                tasks_running = []
                for task in tasks_to_check:
                    if self.client.getTaskStatus(task['workOrderId'])['taskStatus'] != 'Completed':
                        tasks_running.append(task)
                    elif self.client.getTaskStatus(task['workOrderId'])['taskStatus'] == 'Failed':
                        print('Task {0} failed.'.format(task['workOrderId']))
                    else:
                        pass
                
                if len(tasks_running) == 0:

                    # Execute additional commands in linux if needed
                    if len(additional_commands) > 0:
                        print('Running additional setup commands...')
                        self.send_to_syslog('INFO', 'Running additional setup commands.')

                        for command in additional_commands:
                            os.system(command)

                    if not self.public_module_flag:
                        input('Lab Setup Completed. Please press Enter to continue...')
                        self.send_to_syslog("OK", 'Lab Setup Completed.')
                    else:
                        print('Lab Setup Completed. ')
                        self.send_to_syslog("OK", 'Lab Setup Completed.')
                    all_tasks_completed = True
                else:
                    pass
        else:
            # Open up defaults
            f = open('/home/arista/cvp/cvp_info.yaml')
            cvp_info = YAML().load(f)
            f.close()

            cvp_configs = cvp_info["cvp_info"]["configlets"]
            infra_configs = cvp_configs["containers"]["Tenant"]

            self.send_to_syslog("INFO","Setting up {0} lab".format(self.selected_lab))
            for node in access_info["nodes"]["veos"]:
                device_config = ""
                hostname = node["hostname"]
                base_configs = cvp_configs["netelements"]
                configs = base_configs[hostname] + infra_configs + lab_configlets[self.selected_lab][hostname]
                configs = list(dict.fromkeys(configs))
                for config in configs:
                    with open('/opt/atd/topologies/{0}/configlets/{1}'.format(access_info['topology'], config), 'r') as configlet:
                        device_config += configlet.read()
                self.send_to_syslog("INFO","Pushing {0} config for {1} on IP {2} with configlets: {3}".format(self.selected_lab,hostname,node["ip"],configs))
                self.push_bare_config(hostname, node["ip"], device_config)

                # Execute additional commands in linux if needed
                if len(additional_commands) > 0:
                    print('Running additional setup commands...')

                    for command in additional_commands:
                        os.system(command)
            if not self.public_module_flag:
                input('Lab Setup Completed. Please press Enter to continue...')
                self.send_to_syslog("OK", 'Lab Setup Completed.')
            else:
                print('Lab Setup Completed. ')
                self.send_to_syslog("OK", 'Lab Setup Completed.')
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
from cvprac.cvp_client import CvpClient

# Optional imports for enhanced reset functionality
try:
    from arista.tag.v2 import services as tag_services
except ImportError:
    tag_services = None

try:
    from arista.configlet.v1 import services as configlet_services
except ImportError:
    configlet_services = None

# Try to import cloud_logging_utils from site-packages (Docker) or parent directory (local)
try:
    from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error
except ImportError:
    # If not in site-packages, try relative import from parent directory
    import sys
    import os
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error

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

    def __init__(self,selected_menu,selected_lab,public_module_flag=False):
        self.logger = setup_cloud_logging('ConfigureTopology')
        self.logger.info(f"ConfigureTopology initialized", extra={'labels': {
            'menu': selected_menu,
            'lab': selected_lab,
            'operation': 'configure-topology'
        }})
        self.selected_menu = selected_menu
        self.selected_lab = selected_lab
        self.public_module_flag = public_module_flag
        log_operation_start(self.logger, 'deploy-lab', menu=selected_menu, lab=selected_lab)
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
            except:
                self.send_to_syslog("ERROR", "CVP is currently unavailable....Retrying in 30 seconds.")
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

        # Also log to cloud logging
        if mstat == 'ERROR':
            self.logger.error(mtype, extra={'labels': {'status': mstat, 'menu': self.selected_menu, 'lab': self.selected_lab}})
        elif mstat == 'INFO':
            self.logger.info(mtype, extra={'labels': {'status': mstat, 'menu': self.selected_menu, 'lab': self.selected_lab}})
        elif mstat == 'OK':
            self.logger.info(mtype, extra={'labels': {'status': mstat, 'menu': self.selected_menu, 'lab': self.selected_lab}})
        else:
            self.logger.info(f"[{mstat}] {mtype}", extra={'labels': {'status': mstat, 'menu': self.selected_menu, 'lab': self.selected_lab}})


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

    def check_for_tasks(self):
        self.client.getRecentTasks(50)
        tasks_in_progress = False
        for task in self.client.tasks['recent']:
            if 'in progress' in task['workOrderUserDefinedStatus'].lower():
                self.send_to_syslog('INFO', 'Task Check: Task {0} status: {1}'.format(task['workOrderId'],task['workOrderUserDefinedStatus']))
                tasks_in_progress = True
            else:
                pass
        
        if tasks_in_progress:
            self.send_to_syslog('INFO', 'Tasks in progress. Waiting for 10 seconds.')
            print('Tasks are currently executing. Waiting 10 seconds...')
            time.sleep(10)
            self.check_for_tasks()

        else:
            return

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
        Reset all studios to blank state (Master Reset) - logic from standalone_reset.py
        """
        self.send_to_syslog("INFO", "Resetting CloudVision Studios (Master Reset)...")
        print("Resetting CloudVision Studios (Master Reset)...")

        try:
            channel = self.get_grpc_channel(access_info)
            workspace_stub = ws_services.WorkspaceServiceStub(channel)
            workspace_config_stub = ws_services.WorkspaceConfigServiceStub(channel)

            # Import Studio services
            try:
                from arista.studio.v1 import services as studio_services
                from arista.studio.v1 import models as studio_models
                inputs_stub = studio_services.InputsServiceStub(channel)
                inputs_config_stub = studio_services.InputsConfigServiceStub(channel)
            except ImportError:
                self.send_to_syslog("ERROR", "Could not import arista.studio.v1")
                print("ERROR: Could not import arista.studio.v1")
                return False
            except AttributeError:
                self.send_to_syslog("ERROR", "Could not find InputsConfigServiceStub")
                print("ERROR: Could not find InputsConfigServiceStub")
                return False

            # 1. Create a new workspace for the reset
            reset_ws_id = str(uuid.uuid4())
            reset_ws_name = "Reset_Studios_" + reset_ws_id[:8]
            self.send_to_syslog("INFO", "Creating reset workspace: {0} ({1})".format(reset_ws_name, reset_ws_id))
            print("Creating reset workspace: {0} ({1})".format(reset_ws_name, reset_ws_id))

            try:
                # Create workspace
                json_ws_req = json.dumps({
                    "value": {
                        "key": {
                            "workspace_id": reset_ws_id
                        },
                        "display_name": reset_ws_name
                    }
                })
                ws_req = Parse(json_ws_req, ws_services.WorkspaceConfigSetRequest(), False)
                workspace_config_stub.Set(ws_req, timeout=30)
                self.send_to_syslog("OK", "Workspace created")
                print("  ✓ Workspace created")
            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to create workspace: {0}".format(e))
                print("  ✗ Failed to create workspace: {0}".format(e))
                return False

            # 2. Identify studios to reset (Mainline configs)
            self.send_to_syslog("INFO", "Identifying studios with mainline configuration...")
            print("Identifying studios with mainline configuration...")
            studios_to_reset = set()

            try:
                def get_value(field):
                    if hasattr(field, 'value'):
                        return field.value
                    return field

                # Get all inputs for mainline (workspace_id="")
                json_inputs_req = json.dumps({})
                inputs_req = Parse(json_inputs_req, studio_services.InputsStreamRequest(), False)

                for response in inputs_stub.GetAll(inputs_req, timeout=10):
                    if hasattr(response, 'value') and response.value:
                        val = response.value
                        key = val.key

                        s_id = get_value(key.studio_id)
                        w_id = get_value(key.workspace_id)

                        # Check if it's mainline (empty workspace_id)
                        if w_id == "":
                            studios_to_reset.add(s_id)

                self.send_to_syslog("OK", "Found {0} studios with mainline configuration: {1}".format(len(studios_to_reset), list(studios_to_reset)))
                print("Found {0} studios with mainline configuration: {1}".format(len(studios_to_reset), list(studios_to_reset)))

            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to list inputs: {0}".format(e))
                print("  ✗ Failed to list inputs: {0}".format(e))

            # 3. Clear inputs for these studios in the new workspace
            if studios_to_reset:
                self.send_to_syslog("INFO", "Clearing inputs for {0} studios...".format(len(studios_to_reset)))
                print("Clearing inputs for {0} studios...".format(len(studios_to_reset)))
                for studio_id in studios_to_reset:
                    try:
                        # Set inputs with remove=True
                        json_set_req = json.dumps({
                            "value": {
                                "key": {
                                    "studio_id": studio_id,
                                    "workspace_id": reset_ws_id,
                                    "path": {}  # Root path
                                },
                                "remove": True
                            }
                        })
                        set_req = Parse(json_set_req, studio_services.InputsConfigSetRequest(), False)
                        inputs_config_stub.Set(set_req, timeout=30)
                        self.send_to_syslog("OK", "Cleared inputs for {0}".format(studio_id))
                        print("  ✓ Cleared inputs for {0}".format(studio_id))
                    except Exception as e:
                        self.send_to_syslog("ERROR", "Failed to clear inputs for {0}: {1}".format(studio_id, e))
                        print("  ✗ Failed to clear inputs for {0}: {1}".format(studio_id, e))

                # 3.1. Reset tags (removes user tags and tag assignments)
                self.reset_tags(access_info, reset_ws_id)

                # 3.2. Reset static configlets (removes configlets created in Static Configuration Studio)
                self.reset_configlets(access_info, reset_ws_id)

                # 3.5. Start Build
                print("\nStarting build for reset workspace...")
                try:
                    req_id = str(uuid.uuid4())
                    json_build_req = json.dumps({
                        "value": {
                            "key": {
                                "workspace_id": reset_ws_id
                            },
                            "request": 1,  # REQUEST_START_BUILD
                            "request_params": {
                                "request_id": req_id
                            }
                        }
                    })
                    build_req = Parse(json_build_req, ws_services.WorkspaceConfigSetRequest(), False)
                    response = workspace_config_stub.Set(build_req, timeout=30)
                    print("  ✓ Build request sent")
                    print("Waiting for build to complete...")
                    # 4. Poll for Build Completion
                    print("Waiting for build to complete...")
                    for i in range(10):
                        try:
                            # Get workspace status
                            json_get_req = json.dumps({})
                            get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)

                            found = False
                            for response in workspace_stub.GetAll(get_req, timeout=10):
                                if hasattr(response, 'value') and response.value:
                                    ws = response.value
                                    ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                    key = ws_dict.get('key', {})
                                    if key.get('workspace_id') == reset_ws_id:
                                        state = ws_dict.get('state', 'UNKNOWN')
                                        print("  Status check {0}/10: {1}".format(i+1, state))

                                        if str(state) == 'WORKSPACE_STATE_BUILT' or str(state) == 'BUILT' or state == 6:
                                            print("  ✓ Workspace is BUILT")
                                            found = True
                                            break
                                        elif str(state) == 'WORKSPACE_STATE_CONFLICTS' or state == 4:
                                            print("  ✗ Workspace has CONFLICTS")
                                            return False

                            if found:
                                break

                            time.sleep(2)
                        except Exception as e:
                            print("  Error checking status: {0}".format(e))
                    else:
                        print("  Warning: Timeout waiting for build. Attempting submit anyway...")

                except Exception as e:
                    print("  ✗ Failed to start/wait for build: {0}".format(e))

                # 4. Submit the workspace
                print("Submitting reset workspace...")
                try:
                    req_id = str(uuid.uuid4())
                    json_submit_req = json.dumps({
                        "value": {
                            "key": {
                                "workspace_id": reset_ws_id
                            },
                            "request": 3,  # REQUEST_SUBMIT
                            "request_params": {
                                "request_id": req_id
                            }
                        }
                    })
                    submit_req = Parse(json_submit_req, ws_services.WorkspaceConfigSetRequest(), False)
                    workspace_config_stub.Set(submit_req, timeout=30)
                    print("  ✓ Workspace submit request sent")

                    # 5. Wait for Workspace to be SUBMITTED
                    print("Waiting for workspace to be SUBMITTED...")
                    max_retries = 20
                    for i in range(max_retries):
                        # Get workspace status
                        json_get_req = json.dumps({})
                        get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)

                        found = False
                        for response in workspace_stub.GetAll(get_req, timeout=10):
                            if hasattr(response, 'value') and response.value:
                                ws = response.value
                                ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                key = ws_dict.get('key', {})
                                if key.get('workspace_id') == reset_ws_id:
                                    state = ws_dict.get('state', 'UNKNOWN')
                                    print("  Status check {0}/{1}: {2}".format(i+1, max_retries, state))

                                    if str(state) == 'WORKSPACE_STATE_SUBMITTED' or state == 2:
                                        print("  ✓ Workspace is SUBMITTED")
                                        found = True
                                        break
                                    elif str(state) == 'WORKSPACE_STATE_CONFLICTS' or state == 4:
                                        print("  ✗ Workspace has CONFLICTS")
                                        return False

                        if found:
                            break

                        time.sleep(2)
                    else:
                        print("  ✗ Timeout waiting for workspace to submit")
                        return False

                    # 6. Execute Change Controls
                    print("Checking for generated Change Controls...")

                    # Import CC services
                    try:
                        from arista.changecontrol.v1 import services as cc_services
                        from arista.changecontrol.v1 import models as cc_models
                        cc_stub = cc_services.ChangeControlServiceStub(channel)
                        approve_stub = cc_services.ApproveConfigServiceStub(channel)
                        cc_config_stub = cc_services.ChangeControlConfigServiceStub(channel)
                    except ImportError:
                        print("  ✗ Could not import CC services")
                        return False

                    # Get workspace again to find cc_ids
                    try:
                        json_get_req = json.dumps({})
                        get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)

                        cc_ids = []
                        for response in workspace_stub.GetAll(get_req, timeout=10):
                            if hasattr(response, 'value') and response.value:
                                ws = response.value
                                ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                key = ws_dict.get('key', {})
                                if key.get('workspace_id') == reset_ws_id:
                                    if 'cc_ids' in ws_dict and 'values' in ws_dict['cc_ids']:
                                        cc_ids = ws_dict['cc_ids']['values']
                                    break

                        if cc_ids:
                            print("Found {0} Change Controls: {1}".format(len(cc_ids), cc_ids))
                            for cc_id in cc_ids:
                                print("Processing Change Control: {0}".format(cc_id))

                                # 6.0 Get Change Control Version
                                try:
                                    json_cc_req = json.dumps({"key": {"id": cc_id}})
                                    cc_req = Parse(json_cc_req, cc_services.ChangeControlRequest(), False)
                                    cc_resp = cc_stub.GetOne(cc_req, timeout=10)
                                    cc_version = None
                                    if hasattr(cc_resp, 'value') and hasattr(cc_resp.value, 'change') and hasattr(cc_resp.value.change, 'time'):
                                        cc_version = cc_resp.value.change.time
                                        print("  Fetched CC version: {0}".format(cc_version))
                                    else:
                                        print("  Warning: Could not fetch CC version, trying without...")
                                except Exception as e:
                                    print("  Warning: Failed to fetch CC details: {0}".format(e))
                                    cc_version = None

                                # 6.1 Approve Change Control
                                try:
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
                                    approve_stub.Set(approve_req, timeout=30)
                                    print("  ✓ Approved")
                                except Exception as e:
                                    print("  ✗ Failed to approve: {0}".format(e))
                                    continue

                                # 6.2 Start Change Control
                                try:
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
                                    cc_config_stub.Set(start_req, timeout=30)
                                    print("  ✓ Started")
                                except Exception as e:
                                    print("  ✗ Failed to start: {0}".format(e))
                                    continue

                                # 6.3 Wait for Completion
                                print("  Waiting for completion...")
                                for i in range(60):
                                    try:
                                        # Get CC status
                                        json_cc_req = json.dumps({"key": {"id": cc_id}})
                                        cc_req = Parse(json_cc_req, cc_services.ChangeControlRequest(), False)
                                        cc_resp = cc_stub.GetOne(cc_req, timeout=10)

                                        if hasattr(cc_resp, 'value'):
                                            cc_val = cc_resp.value
                                            status = cc_val.status
                                            print("    Status check {0}/60: {1}".format(i+1, status))

                                            if status == 2:  # COMPLETED
                                                print("  ✓ Change Control Completed")
                                                break

                                            # Check for errors
                                            if hasattr(cc_val, 'error') and cc_val.error and hasattr(cc_val.error, 'message') and cc_val.error.message:
                                                print("  ✗ Change Control Error: {0}".format(cc_val.error.message))
                                                break

                                            if status in [1, 3, 4]:
                                                pass

                                    except Exception as e:
                                        print("    Error checking status: {0}".format(e))

                                    time.sleep(2)
                                else:
                                    print("  Warning: Timeout waiting for Change Control completion")

                        else:
                            print("No Change Controls found in workspace.")

                    except Exception as e:
                        print("  ✗ Failed to process Change Controls: {0}".format(e))

                except Exception as e:
                    print("  ✗ Failed to submit/execute workspace: {0}".format(e))
            else:
                print("No studios to reset.")
                # Still reset tags and configlets even if no studios
                tags_changed = self.reset_tags(access_info, reset_ws_id)
                configlets_changed = self.reset_configlets(access_info, reset_ws_id)

                # Use return values to determine if we should submit the workspace
                if tags_changed or configlets_changed:
                    try:
                        # Start build
                        req_id = str(uuid.uuid4())
                        json_build_req = json.dumps({
                            "value": {
                                "key": {
                                    "workspace_id": reset_ws_id
                                },
                                "request": 1,  # REQUEST_START_BUILD
                                "request_params": {
                                    "request_id": req_id
                                }
                            }
                        })
                        build_req = Parse(json_build_req, ws_services.WorkspaceConfigSetRequest(), False)
                        workspace_config_stub.Set(build_req, timeout=30)
                        print("Building workspace...")

                        # Wait for build to complete with polling
                        for i in range(10):
                            time.sleep(2)
                            json_get_req = json.dumps({})
                            get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)

                            for response in workspace_stub.GetAll(get_req, timeout=10):
                                if hasattr(response, 'value') and response.value:
                                    ws = response.value
                                    ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                    key = ws_dict.get('key', {})
                                    if key.get('workspace_id') == reset_ws_id:
                                        state = ws_dict.get('state', 'UNKNOWN')
                                        print("  Build status check {0}/10: {1}".format(i+1, state))
                                        if str(state) == 'WORKSPACE_STATE_BUILT' or str(state) == 'BUILT' or state == 6:
                                            break
                            else:
                                continue
                            break

                        # Submit workspace
                        print("Submitting workspace with tag/configlet changes...")
                        req_id = str(uuid.uuid4())
                        json_submit_req = json.dumps({
                            "value": {
                                "key": {
                                    "workspace_id": reset_ws_id
                                },
                                "request": 3,  # REQUEST_SUBMIT
                                "request_params": {
                                    "request_id": req_id
                                }
                            }
                        })
                        submit_req = Parse(json_submit_req, ws_services.WorkspaceConfigSetRequest(), False)
                        workspace_config_stub.Set(submit_req, timeout=30)
                        print("  ✓ Workspace submitted")
                    except Exception as e:
                        self.send_to_syslog("ERROR", "Could not finalize workspace: {0}".format(e))
                        print("  ✗ Could not finalize workspace: {0}".format(e))
                else:
                    # No changes made, delete the empty workspace
                    try:
                        json_del_req = json.dumps({
                            "key": {
                                "workspace_id": reset_ws_id
                            }
                        })
                        del_req = Parse(json_del_req, ws_services.WorkspaceConfigDeleteRequest(), False)
                        workspace_config_stub.Delete(del_req, timeout=30)
                        print("  ✓ Deleted empty reset workspace")
                    except Exception as e:
                        self.send_to_syslog("WARNING", "Could not delete empty workspace: {0}".format(e))

        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to reset Studios: {0}".format(str(e)))
            print("Failed to reset Studios: {0}".format(str(e)))
            return False

    def reset_tags(self, access_info, workspace_id):
        """
        Reset all user tags and tag assignments in CloudVision.
        This addresses the issue where tags generated by studios persist after reset.

        Parameters:
        access_info: Lab access information dict
        workspace_id: The workspace ID to use for the reset operation

        Returns:
        bool: True if reset successful or made changes, False otherwise
        """
        # Check if tag_services is available
        if tag_services is None:
            self.send_to_syslog("WARNING", "arista.tag.v2 not available - skipping tag reset")
            print("  ⚠ arista.tag.v2 not available - skipping tag reset")
            return False

        self.send_to_syslog("INFO", "Resetting CloudVision Tags...")
        print("\n=== Resetting CloudVision Tags ===")

        changes_made = False
        try:
            channel = self.get_grpc_channel(access_info)

            # Initialize tag service stubs
            tag_stub = tag_services.TagServiceStub(channel)
            tag_config_stub = tag_services.TagConfigServiceStub(channel)
            tag_assignment_stub = tag_services.TagAssignmentServiceStub(channel)
            tag_assignment_config_stub = tag_services.TagAssignmentConfigServiceStub(channel)

            def get_value(field):
                if hasattr(field, 'value'):
                    return field.value
                return field

            # 1. Get all tag assignments from mainline and remove them
            print("Identifying tag assignments to remove...")
            tag_assignments_to_remove = []

            try:
                json_req = json.dumps({})
                req = Parse(json_req, tag_services.TagAssignmentStreamRequest(), False)

                for response in tag_assignment_stub.GetAll(req, timeout=30):
                    if hasattr(response, 'value') and response.value:
                        val = response.value
                        key = val.key

                        ws_id = get_value(key.workspace_id) if hasattr(key, 'workspace_id') else ""

                        # Only process mainline tag assignments
                        if ws_id == "":
                            tag_assignments_to_remove.append({
                                'element_type': get_value(key.element_type) if hasattr(key, 'element_type') else None,
                                'label': get_value(key.label) if hasattr(key, 'label') else None,
                                'value': get_value(key.value) if hasattr(key, 'value') else None,
                                'device_id': get_value(key.device_id) if hasattr(key, 'device_id') else None,
                                'interface_id': get_value(key.interface_id) if hasattr(key, 'interface_id') else None,
                            })

                self.send_to_syslog("OK", "Found {0} tag assignments to remove".format(len(tag_assignments_to_remove)))
                print("Found {0} tag assignments to remove".format(len(tag_assignments_to_remove)))

            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to list tag assignments: {0}".format(e))
                print("  ✗ Failed to list tag assignments: {0}".format(e))

            # 2. Remove tag assignments in the workspace
            if tag_assignments_to_remove:
                print("Removing tag assignments...")
                removed_count = 0
                for ta in tag_assignments_to_remove:
                    try:
                        # Build the key for deletion
                        key_dict = {
                            "workspace_id": workspace_id,
                        }
                        if ta['element_type'] is not None:
                            key_dict['element_type'] = ta['element_type']
                        if ta['label']:
                            key_dict['label'] = ta['label']
                        if ta['value']:
                            key_dict['value'] = ta['value']
                        if ta['device_id']:
                            key_dict['device_id'] = ta['device_id']
                        if ta['interface_id']:
                            key_dict['interface_id'] = ta['interface_id']

                        # Use Set with remove=True pattern (same as studio inputs)
                        json_set_req = json.dumps({
                            "value": {
                                "key": key_dict,
                                "remove": True
                            }
                        })
                        set_req = Parse(json_set_req, tag_services.TagAssignmentConfigSetRequest(), False)
                        tag_assignment_config_stub.Set(set_req, timeout=30)
                        removed_count += 1
                    except Exception as e:
                        # Some assignments may not be deletable (system tags)
                        if 'permission' not in str(e).lower() and 'not found' not in str(e).lower():
                            self.send_to_syslog("WARNING", "Could not delete tag assignment {0}: {1}".format(ta.get('label', 'unknown'), e))

                self.send_to_syslog("OK", "Removed {0} tag assignments".format(removed_count))
                print("  ✓ Removed {0} tag assignments".format(removed_count))
                if removed_count > 0:
                    changes_made = True

            # 3. Get all user-defined tags from mainline and remove them
            print("Identifying user tags to remove...")
            tags_to_remove = []

            # System tag labels that should not be deleted
            system_labels = ['topology_hint_pod', 'topology_hint_datacenter', 'topology_hint_rack',
                           'topology_hint_type', 'device_type', 'eos_version', 'terminattr_version',
                           'model', 'serial_number', 'hostname']

            try:
                json_req = json.dumps({})
                req = Parse(json_req, tag_services.TagStreamRequest(), False)

                for response in tag_stub.GetAll(req, timeout=30):
                    if hasattr(response, 'value') and response.value:
                        val = response.value
                        key = val.key

                        ws_id = get_value(key.workspace_id) if hasattr(key, 'workspace_id') else ""
                        label = get_value(key.label) if hasattr(key, 'label') else ""

                        # Only process mainline tags that are not system tags
                        if ws_id == "" and label not in system_labels:
                            tags_to_remove.append({
                                'element_type': get_value(key.element_type) if hasattr(key, 'element_type') else None,
                                'label': label,
                                'value': get_value(key.value) if hasattr(key, 'value') else None,
                            })

                self.send_to_syslog("OK", "Found {0} user tags to remove".format(len(tags_to_remove)))
                print("Found {0} user tags to remove".format(len(tags_to_remove)))

            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to list tags: {0}".format(e))
                print("  ✗ Failed to list tags: {0}".format(e))

            # 4. Remove user tags in the workspace
            if tags_to_remove:
                print("Removing user tags...")
                removed_count = 0
                for tag in tags_to_remove:
                    try:
                        key_dict = {
                            "workspace_id": workspace_id,
                        }
                        if tag['element_type'] is not None:
                            key_dict['element_type'] = tag['element_type']
                        if tag['label']:
                            key_dict['label'] = tag['label']
                        if tag['value']:
                            key_dict['value'] = tag['value']

                        # Use Set with remove=True pattern (same as studio inputs)
                        json_set_req = json.dumps({
                            "value": {
                                "key": key_dict,
                                "remove": True
                            }
                        })
                        set_req = Parse(json_set_req, tag_services.TagConfigSetRequest(), False)
                        tag_config_stub.Set(set_req, timeout=30)
                        removed_count += 1
                    except Exception as e:
                        # Some tags may not be deletable
                        if 'permission' not in str(e).lower() and 'not found' not in str(e).lower():
                            self.send_to_syslog("WARNING", "Could not delete tag {0}: {1}".format(tag.get('label', 'unknown'), e))

                self.send_to_syslog("OK", "Removed {0} user tags".format(removed_count))
                print("  ✓ Removed {0} user tags".format(removed_count))
                if removed_count > 0:
                    changes_made = True

            return changes_made

        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to reset tags: {0}".format(str(e)))
            print("Failed to reset tags: {0}".format(str(e)))
            return False

    def reset_configlets(self, access_info, workspace_id):
        """
        Reset all static configlets created in the Static Configuration Studio.
        This removes the configlets themselves, not just unassigns them.

        Parameters:
        access_info: Lab access information dict
        workspace_id: The workspace ID to use for the reset operation

        Returns:
        bool: True if reset successful and made changes, False otherwise
        """
        # Check if configlet_services is available
        if configlet_services is None:
            self.send_to_syslog("WARNING", "arista.configlet.v1 not available - skipping configlet reset")
            print("  ⚠ arista.configlet.v1 not available - skipping configlet reset")
            return False

        self.send_to_syslog("INFO", "Resetting Static Configuration Studio configlets...")
        print("\n=== Resetting Static Configlets ===")

        changes_made = False
        try:
            channel = self.get_grpc_channel(access_info)

            # Initialize configlet service stubs
            configlet_stub = configlet_services.ConfigletServiceStub(channel)
            configlet_config_stub = configlet_services.ConfigletConfigServiceStub(channel)
            configlet_assignment_stub = configlet_services.ConfigletAssignmentServiceStub(channel)
            configlet_assignment_config_stub = configlet_services.ConfigletAssignmentConfigServiceStub(channel)

            def get_value(field):
                if hasattr(field, 'value'):
                    return field.value
                return field

            # System configlets that should not be deleted
            system_configlets = ['ATD-INFRA']

            # 1. Get all configlet assignments from mainline and remove them
            print("Identifying configlet assignments to remove...")
            assignments_to_remove = []

            try:
                json_req = json.dumps({})
                req = Parse(json_req, configlet_services.ConfigletAssignmentStreamRequest(), False)

                for response in configlet_assignment_stub.GetAll(req, timeout=30):
                    if hasattr(response, 'value') and response.value:
                        val = response.value
                        key = val.key

                        ws_id = get_value(key.workspace_id) if hasattr(key, 'workspace_id') else ""
                        assignment_id = get_value(key.configlet_assignment_id) if hasattr(key, 'configlet_assignment_id') else ""

                        # Only process mainline assignments
                        if ws_id == "" and assignment_id:
                            assignments_to_remove.append(assignment_id)

                self.send_to_syslog("OK", "Found {0} configlet assignments to remove".format(len(assignments_to_remove)))
                print("Found {0} configlet assignments to remove".format(len(assignments_to_remove)))

            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to list configlet assignments: {0}".format(e))
                print("  ✗ Failed to list configlet assignments: {0}".format(e))

            # 2. Remove configlet assignments in the workspace
            if assignments_to_remove:
                print("Removing configlet assignments...")
                removed_count = 0
                for assignment_id in assignments_to_remove:
                    try:
                        # Use Set with remove=True pattern (same as studio inputs)
                        json_set_req = json.dumps({
                            "value": {
                                "key": {
                                    "workspace_id": workspace_id,
                                    "configlet_assignment_id": assignment_id
                                },
                                "remove": True
                            }
                        })
                        set_req = Parse(json_set_req, configlet_services.ConfigletAssignmentConfigSetRequest(), False)
                        configlet_assignment_config_stub.Set(set_req, timeout=30)
                        removed_count += 1
                    except Exception as e:
                        if 'permission' not in str(e).lower() and 'not found' not in str(e).lower():
                            self.send_to_syslog("WARNING", "Could not delete configlet assignment {0}: {1}".format(assignment_id, e))

                self.send_to_syslog("OK", "Removed {0} configlet assignments".format(removed_count))
                print("  ✓ Removed {0} configlet assignments".format(removed_count))
                if removed_count > 0:
                    changes_made = True

            # 3. Get all static configlets from mainline and remove them
            print("Identifying static configlets to remove...")
            configlets_to_remove = []

            try:
                json_req = json.dumps({})
                req = Parse(json_req, configlet_services.ConfigletStreamRequest(), False)

                for response in configlet_stub.GetAll(req, timeout=30):
                    if hasattr(response, 'value') and response.value:
                        val = response.value
                        key = val.key

                        ws_id = get_value(key.workspace_id) if hasattr(key, 'workspace_id') else ""
                        configlet_id = get_value(key.configlet_id) if hasattr(key, 'configlet_id') else ""

                        # Only process mainline configlets that are not system configlets
                        if ws_id == "" and configlet_id and configlet_id not in system_configlets:
                            configlets_to_remove.append(configlet_id)

                self.send_to_syslog("OK", "Found {0} static configlets to remove".format(len(configlets_to_remove)))
                print("Found {0} static configlets to remove".format(len(configlets_to_remove)))

            except Exception as e:
                self.send_to_syslog("ERROR", "Failed to list configlets: {0}".format(e))
                print("  ✗ Failed to list configlets: {0}".format(e))

            # 4. Remove static configlets in the workspace
            if configlets_to_remove:
                print("Removing static configlets...")
                removed_count = 0
                for configlet_id in configlets_to_remove:
                    try:
                        # Use Set with remove=True pattern (same as studio inputs)
                        json_set_req = json.dumps({
                            "value": {
                                "key": {
                                    "workspace_id": workspace_id,
                                    "configlet_id": configlet_id
                                },
                                "remove": True
                            }
                        })
                        set_req = Parse(json_set_req, configlet_services.ConfigletConfigSetRequest(), False)
                        configlet_config_stub.Set(set_req, timeout=30)
                        removed_count += 1
                    except Exception as e:
                        if 'permission' not in str(e).lower() and 'not found' not in str(e).lower():
                            self.send_to_syslog("WARNING", "Could not delete configlet {0}: {1}".format(configlet_id, e))

                self.send_to_syslog("OK", "Removed {0} static configlets".format(removed_count))
                print("  ✓ Removed {0} static configlets".format(removed_count))
                if removed_count > 0:
                    changes_made = True

            return changes_made

        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to reset configlets: {0}".format(str(e)))
            print("Failed to reset configlets: {0}".format(str(e)))
            return False

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
                self.send_to_syslog("INFO", "Performing CVP Studio reset...")
                print("\n=== CVP Studio Reset ===")
                self.reset_studios(access_info)
                self.send_to_syslog("OK", "CVP Studio reset completed")

            self.check_for_tasks()

            # Config the topology
            self.update_topology(lab_configlets)
            # Wait time for CVP to generate tasks
            time.sleep(15)
            
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
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
from google.protobuf.json_format import Parse, MessageToDict
from arista.workspace.v1 import services as ws_services
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
        Reset CloudVision Studios by deleting all user-created workspaces.

        This method removes ALL user workspaces regardless of state (PENDING, SUBMITTED,
        BUILT, CONFLICT, etc.) while preserving built-in Arista studios. For workspaces
        in certain states, it attempts to abandon them before deletion.

        Args:
            access_info: Dictionary containing CVP connection details

        Returns:
            bool: True if reset successful (with possible warnings), False if critical failure
        """
        self.send_to_syslog("INFO", "Resetting CloudVision Studios...")
        print("Resetting CloudVision Studios...")

        deleted_count = 0
        failed_workspaces = []

        try:
            channel = self.get_grpc_channel(access_info)
            workspace_stub = ws_services.WorkspaceServiceStub(channel)
            workspace_config_stub = ws_services.WorkspaceConfigServiceStub(channel)

            # Get all workspaces
            json_request = json.dumps({})
            req = Parse(json_request, ws_services.WorkspaceStreamRequest(), False)

            workspaces_to_delete = []
            for response in workspace_stub.GetAll(req, timeout=self.GRPC_LIST_TIMEOUT):
                if hasattr(response, 'value') and response.value:
                    ws = response.value
                    ws_dict = MessageToDict(ws, preserving_proto_field_name=True)

                    # Get display name safely
                    display_name = ""
                    if hasattr(ws, 'display_name') and hasattr(ws.display_name, 'value'):
                        display_name = str(ws.display_name.value) if ws.display_name.value else ""
                    elif 'display_name' in ws_dict:
                        dn = ws_dict['display_name']
                        if isinstance(dn, dict) and 'value' in dn:
                            display_name = str(dn['value']) if dn['value'] else ""
                        elif isinstance(dn, str):
                            display_name = dn

                    display_name = display_name.strip() if display_name else ""

                    # CRITICAL: Filter out built-in Arista studios - DO NOT DELETE THESE
                    if 'Add built-in studio' in display_name:
                        self.send_to_syslog("INFO", "Skipping built-in studio: {0}".format(display_name))
                        continue

                    # Get workspace ID
                    ws_id = ws_dict.get('key', {}).get('workspace_id', None)
                    if not ws_id:
                        self.send_to_syslog("WARNING", "Skipping workspace with no ID: {0}".format(display_name))
                        continue

                    # Get workspace state for logging
                    ws_state = ws_dict.get('state', 'UNKNOWN')

                    # Delete ALL user workspaces regardless of state
                    workspaces_to_delete.append((ws_id, display_name, ws_state))
                    self.send_to_syslog("INFO", "Found user workspace to delete: {0} (ID: {1}, State: {2})".format(
                        display_name, ws_id, ws_state))

            # Delete workspaces
            if workspaces_to_delete:
                self.send_to_syslog("INFO", "Deleting {0} user workspace(s)...".format(len(workspaces_to_delete)))
                print("Deleting {0} user workspace(s)...".format(len(workspaces_to_delete)))

                for ws_id, display_name, ws_state in workspaces_to_delete:
                    try:
                        # Try to abandon the workspace first (required for some states like BUILT/SUBMITTED)
                        # This may fail if the workspace is not in a state that can be abandoned, which is OK
                        try:
                            json_abandon_req = json.dumps({
                                "key": {
                                    "workspace_id": ws_id
                                }
                            })
                            abandon_request = Parse(json_abandon_req, ws_services.WorkspaceConfigSetRequest(), False)
                            workspace_config_stub.Abandon(abandon_request, timeout=self.WORKSPACE_TIMEOUT)
                            self.send_to_syslog("INFO", "Abandoned workspace: {0}".format(display_name))
                        except grpc.RpcError as abandon_error:
                            # Abandon may fail if workspace is already abandoned or in a state that doesn't need it
                            # This is not a critical error - continue to delete
                            if 'not found' not in str(abandon_error).lower():
                                self.send_to_syslog("INFO", "Abandon not needed for {0}: {1}".format(
                                    display_name, abandon_error.details() if hasattr(abandon_error, 'details') else str(abandon_error)))

                        # Now delete the workspace
                        json_req = json.dumps({
                            "key": {
                                "workspace_id": ws_id
                            }
                        })
                        request = Parse(json_req, ws_services.WorkspaceConfigDeleteRequest(), False)
                        workspace_config_stub.Delete(request, timeout=self.WORKSPACE_TIMEOUT)

                        deleted_count += 1
                        self.send_to_syslog("OK", "Deleted workspace: {0} (State: {1})".format(display_name, ws_state))
                        print("  ✓ Deleted: {0}".format(display_name))

                    except grpc.RpcError as grpc_error:
                        error_msg = grpc_error.details() if hasattr(grpc_error, 'details') else str(grpc_error)
                        self.send_to_syslog("ERROR", "Failed to delete workspace {0}: {1}".format(display_name, error_msg))
                        print("  ✗ Failed to delete {0}: {1}".format(display_name, error_msg))
                        failed_workspaces.append((ws_id, display_name, error_msg))
                    except Exception as e:
                        self.send_to_syslog("ERROR", "Unexpected error deleting workspace {0}: {1}".format(display_name, str(e)))
                        print("  ✗ Failed to delete {0}: {1}".format(display_name, str(e)))
                        failed_workspaces.append((ws_id, display_name, str(e)))

                # Report results
                if failed_workspaces:
                    self.send_to_syslog("WARNING", "Studio reset completed with {0} failure(s) out of {1} workspace(s)".format(
                        len(failed_workspaces), len(workspaces_to_delete)))
                    print("\nStudio Reset Summary:")
                    print("  Successfully deleted: {0}".format(deleted_count))
                    print("  Failed to delete: {0}".format(len(failed_workspaces)))
                    for ws_id, ws_name, error in failed_workspaces:
                        print("    - {0}: {1}".format(ws_name, error))
                    return deleted_count > 0  # Partial success
                else:
                    self.send_to_syslog("OK", "CloudVision Studios Reset Complete - {0} workspace(s) deleted".format(deleted_count))
                    print("\n✓ Successfully deleted all {0} user workspace(s)".format(deleted_count))
                    return True
            else:
                self.send_to_syslog("INFO", "No user workspaces found to delete")
                print("No user workspaces found to delete")
                return True

        except grpc.RpcError as grpc_error:
            error_msg = grpc_error.details() if hasattr(grpc_error, 'details') else str(grpc_error)
            self.send_to_syslog("ERROR", "gRPC error during studio reset: {0}".format(error_msg))
            print("Failed to reset Studios (gRPC error): {0}".format(error_msg))
            return False
        except Exception as e:
            self.send_to_syslog("ERROR", "Failed to reset Studios: {0}".format(str(e)))
            print("Failed to reset Studios: {0}".format(str(e)))
            return False

    def cancel_pending_tasks(self, access_info):
        self.send_to_syslog("INFO", "Checking for pending tasks to cancel...")
        try:
            clnt = self.get_cvprac_client(access_info)
            tasks = clnt.api.task.get_tasks_by_status('Pending')
            if tasks:
                for task in tasks:
                    self.send_to_syslog("INFO", "Cancelling task: {0}".format(task['workOrderId']))
                    clnt.api.task.cancel_task(task['workOrderId'])
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
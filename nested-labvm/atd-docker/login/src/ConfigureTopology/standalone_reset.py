#!/usr/bin/env python3

import sys
import json
import grpc
import ssl
import requests
import time
import os
from cvprac.cvp_client import CvpClient
try:
    from arista.workspace.v1 import services as ws_services
    from google.protobuf.json_format import Parse, MessageToDict
except ImportError:
    print("Could not import arista.workspace.v1. Please ensure cloudvision package is installed.")
    sys.exit(1)

# Disable insecure request warnings
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Constants
WORKSPACE_TIMEOUT = 30
GRPC_LIST_TIMEOUT = 10
RESET_SETTLE_TIME = 5

def get_cvprac_client(cvp_ip, username, password):
    clnt = CvpClient()
    clnt.connect([cvp_ip], username, password)
    return clnt

def get_grpc_channel(cvp_ip, username, password):
    # Get token
    try:
        response = requests.post(
            'https://{0}/cvpservice/login/authenticate.do'.format(cvp_ip),
            auth=(username, password),
            verify=False
        )
        token = response.json()['sessionId']
    except Exception as e:
        print("ERROR: Failed to get CVP token: {0}".format(str(e)))
        raise e

    # Get cert
    try:
        cert = ssl.get_server_certificate((cvp_ip, 443))
    except Exception as e:
        print("ERROR: Failed to get CVP cert: {0}".format(str(e)))
        raise e

    # Create channel
    call_creds = grpc.access_token_call_credentials(token)
    channel_creds = grpc.ssl_channel_credentials(root_certificates=cert.encode())
    conn_creds = grpc.composite_channel_credentials(channel_creds, call_creds)
    
    return grpc.secure_channel('{0}:443'.format(cvp_ip), conn_creds)

def inspect_protobufs():
    print("\n=== Inspecting Protobufs ===")
    try:
        print("WorkspaceConfigSetRequest fields:", ws_services.WorkspaceConfigSetRequest.DESCRIPTOR.fields_by_name.keys())
        print("WorkspaceConfigDeleteRequest fields:", ws_services.WorkspaceConfigDeleteRequest.DESCRIPTOR.fields_by_name.keys())
        
        # Inspect Service Stubs
        print("\nWorkspaceConfigServiceStub methods:")
        for method in dir(ws_services.WorkspaceConfigServiceStub):
            if not method.startswith('_'):
                print("  - " + method)
                
    except Exception as e:
        print("Error inspecting protobufs:", e)
    print("============================\n")

def cancel_pending_tasks(cvp_ip, username, password):
    print("\n=== Cancelling Pending Tasks ===")
    try:
        clnt = get_cvprac_client(cvp_ip, username, password)
        
        # FIX: Use clnt.api.get_tasks_by_status instead of clnt.api.task.get_tasks_by_status
        # Checking if 'task' attribute exists or if we should use direct method
        if hasattr(clnt.api, 'task'):
            print("Using clnt.api.task...")
            tasks = clnt.api.task.get_tasks_by_status('Pending')
            cancel_func = clnt.api.task.cancel_task
        else:
            print("Using clnt.api directly...")
            tasks = clnt.api.get_tasks_by_status('Pending')
            cancel_func = clnt.api.cancel_task

        if tasks:
            for task in tasks:
                print("Cancelling task: {0}".format(task['workOrderId']))
                cancel_func(task['workOrderId'])
            print("All pending tasks cancelled.")
        else:
            print("No pending tasks found.")
    except Exception as e:
        print("ERROR: Failed to cancel tasks: {0}".format(str(e)))

def reset_studios(cvp_ip, username, password):
    print("\n=== Resetting Studios (Master Reset) ===")
    
    try:
        channel = get_grpc_channel(cvp_ip, username, password)
        workspace_stub = ws_services.WorkspaceServiceStub(channel)
        workspace_config_stub = ws_services.WorkspaceConfigServiceStub(channel)
        
        # Import Studio services and models
        try:
            from arista.studio.v1 import services as studio_services
            from arista.studio.v1 import models as studio_models
            inputs_stub = studio_services.InputsServiceStub(channel)
            inputs_config_stub = studio_services.InputsConfigServiceStub(channel)
        except ImportError:
            print("ERROR: Could not import arista.studio.v1. Please ensure cloudvision package is installed.")
            return
        except AttributeError:
            print("ERROR: Could not find InputsConfigServiceStub. Inspecting available stubs...")
            for attr in dir(studio_services):
                if attr.endswith('Stub'):
                    print("  - " + attr)
            return

        import uuid
        
        # 1. Create a new Workspace for the reset
        reset_ws_id = str(uuid.uuid4())
        reset_ws_name = "Reset_Studios_" + reset_ws_id[:8]
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
            workspace_config_stub.Set(ws_req, timeout=WORKSPACE_TIMEOUT)
            print("  ✓ Workspace created")
        except Exception as e:
            print("  ✗ Failed to create workspace: {0}".format(e))
            return

        # 2. Identify studios to reset (Mainline configs)
        print("Identifying studios with mainline configuration...")
        studios_to_reset = set()
        try:
            # Get all inputs for mainline (workspace_id="")
            json_inputs_req = json.dumps({})
            inputs_req = Parse(json_inputs_req, studio_services.InputsStreamRequest(), False)
            
            def get_value(field):
                if hasattr(field, 'value'):
                    return field.value
                return field

            for response in inputs_stub.GetAll(inputs_req, timeout=GRPC_LIST_TIMEOUT):
                if hasattr(response, 'value') and response.value:
                    val = response.value
                    key = val.key
                    
                    s_id = get_value(key.studio_id)
                    w_id = get_value(key.workspace_id)
                    
                    # Check if it's mainline (empty workspace_id)
                    if w_id == "":
                        studios_to_reset.add(s_id)
            
            print("Found {0} studios with mainline configuration: {1}".format(len(studios_to_reset), list(studios_to_reset)))
            
        except Exception as e:
            print("  ✗ Failed to list inputs: {0}".format(e))

        # 3. Clear inputs for these studios in the new workspace
        if studios_to_reset:
            print("Clearing inputs for {0} studios...".format(len(studios_to_reset)))
            for studio_id in studios_to_reset:
                try:
                    # Set inputs with remove=True
                    json_set_req = json.dumps({
                        "value": {
                            "key": {
                                "studio_id": studio_id,
                                "workspace_id": reset_ws_id,
                                "path": {} # Root path
                            },
                            "remove": True
                        }
                    })
                    set_req = Parse(json_set_req, studio_services.InputsConfigSetRequest(), False)
                    inputs_config_stub.Set(set_req, timeout=WORKSPACE_TIMEOUT)
                    print("  ✓ Cleared inputs for {0}".format(studio_id))
                except Exception as e:
                    print("  ✗ Failed to clear inputs for {0}: {1}".format(studio_id, e))

            # Inspect WorkspaceConfig fields
            # Inspect WorkspaceConfig fields
            try:
                from arista.workspace.v1 import models as ws_models
                print("WorkspaceConfig fields: {0}".format([f.name for f in ws_models.WorkspaceConfig.DESCRIPTOR.fields]))
            except ImportError:
                print("Could not import arista.workspace.v1.models")
            except AttributeError:
                print("Could not find WorkspaceConfig in models")

            # 3.5. Start Build
            print("\nStarting build for reset workspace...")
            try:
                req_id = str(uuid.uuid4())
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
                build_req = Parse(json_build_req, ws_services.WorkspaceConfigSetRequest(), False)
                response = workspace_config_stub.Set(build_req, timeout=WORKSPACE_TIMEOUT)
                print("  ✓ Build request sent")
                
                # Wait for build to complete
                print("Waiting for build to complete...")
            # 4. Poll for Build Completion
            print("Waiting for build to complete...")
            for i in range(10):
                try:
                    # Get workspace status
                    json_get_req = json.dumps({})
                    get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                    
                    found = False
                    for response in workspace_stub.GetAll(get_req, timeout=GRPC_LIST_TIMEOUT):
                        if hasattr(response, 'value') and response.value:
                            ws = response.value
                            ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                            key = ws_dict.get('key', {})
                            if key.get('workspace_id') == reset_ws_id:
                                state = ws_dict.get('state', 'UNKNOWN')
                                print("  Status check {0}/10: {1}".format(i+1, state))
                                
                                if str(state) == 'WORKSPACE_STATE_BUILT' or state == 5:
                                    print("  ✓ Workspace is BUILT")
                                    found = True
                                    break
                                elif str(state) == 'WORKSPACE_STATE_CONFLICTS' or state == 4:
                                    print("  ✗ Workspace has CONFLICTS")
                                    return
                    
                    if found:
                        break
                    
                    time.sleep(2)
                except Exception as e:
                    print("  Error checking status: {0}".format(e))
            else:
                print("  ✗ Timeout waiting for workspace to build")
                return

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
                        "request": 3, # REQUEST_SUBMIT
                        "request_params": {
                            "request_id": req_id
                        }
                    }
                })
                submit_req = Parse(json_submit_req, ws_services.WorkspaceConfigSetRequest(), False)
                workspace_config_stub.Set(submit_req, timeout=WORKSPACE_TIMEOUT)
                print("  ✓ Workspace submit request sent")
                
                # 5. Wait for Workspace to be SUBMITTED
                print("Waiting for workspace to be SUBMITTED...")
                max_retries = 20
                for i in range(max_retries):
                    # Get workspace status
                    # WorkspaceStreamRequest doesn't support 'key' filter directly in this version
                    json_get_req = json.dumps({})
                    get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                    # Use GetAll because GetOne might not be available or behaves differently
                    # But we can try GetOne if we saw it in methods. We saw GetOne.
                    # Let's use GetAll with filter to be safe/consistent with previous code
                    
                    found = False
                    for response in workspace_stub.GetAll(get_req, timeout=GRPC_LIST_TIMEOUT):
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
                                    return
                    
                    if found:
                        break
                    
                    time.sleep(2)
                else:
                    print("  ✗ Timeout waiting for workspace to submit")
                    return

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
                     return

                # Get workspace again to find cc_ids
                # We already have the workspace from the polling loop, but let's get it fresh to be sure
                try:
                    json_get_req = json.dumps({})
                    get_req = Parse(json_get_req, ws_services.WorkspaceStreamRequest(), False)
                    
                    cc_ids = []
                    for response in workspace_stub.GetAll(get_req, timeout=GRPC_LIST_TIMEOUT):
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
                                cc_resp = cc_stub.GetOne(cc_req, timeout=GRPC_LIST_TIMEOUT)
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
                                approve_stub.Set(approve_req, timeout=WORKSPACE_TIMEOUT)
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
                                if cc_version:
                                     # Some APIs might require version for start too, though usually just for approve
                                     # Let's check if we need it. The error was on approve.
                                     pass

                                json_start_req = json.dumps(start_dict)
                                start_req = Parse(json_start_req, cc_services.ChangeControlConfigSetRequest(), False)
                                cc_config_stub.Set(start_req, timeout=WORKSPACE_TIMEOUT)
                                print("  ✓ Started")
                            except Exception as e:
                                print("  ✗ Failed to start: {0}".format(e))
                                continue
                            
                            # 6.3 Wait for Completion
                            print("  Waiting for completion...")
                            for i in range(60): # Increased wait time
                                try:
                                    # Get CC status
                                    json_cc_req = json.dumps({"key": {"id": cc_id}})
                                    cc_req = Parse(json_cc_req, cc_services.ChangeControlRequest(), False)
                                    cc_resp = cc_stub.GetOne(cc_req, timeout=GRPC_LIST_TIMEOUT)
                                    
                                    if hasattr(cc_resp, 'value'):
                                        cc_val = cc_resp.value
                                        status = cc_val.status
                                        # 0: UNSPECIFIED, 1: RUNNING, 2: COMPLETED, 3: SCHEDULED, 4: NOT_STARTED
                                        print("    Status check {0}/60: {1}".format(i+1, status))
                                        
                                        if status == 2: # COMPLETED
                                            print("  ✓ Change Control Completed")
                                            break
                                        
                                        # Check for errors
                                        if hasattr(cc_val, 'error') and cc_val.error and hasattr(cc_val.error, 'message') and cc_val.error.message:
                                             print("  ✗ Change Control Error: {0}".format(cc_val.error.message))
                                             break
                                        
                                        # If RUNNING, SCHEDULED, or NOT_STARTED, keep waiting
                                        if status in [1, 3, 4]:
                                            pass 
                                        else:
                                            # Unknown status
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
            # Delete the empty workspace
            try:
                json_del_req = json.dumps({
                    "key": {
                        "workspace_id": reset_ws_id
                    }
                })
                del_req = Parse(json_del_req, ws_services.WorkspaceConfigDeleteRequest(), False)
                workspace_config_stub.Delete(del_req, timeout=WORKSPACE_TIMEOUT)
                print("  ✓ Deleted empty reset workspace")
            except:
                pass

    except Exception as e:
        print("Failed to reset Studios: {0}".format(str(e)))

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python3 standalone_reset.py <CVP_IP> <USERNAME> <PASSWORD>")
        # Try to read from ACCESS_INFO.yaml if not provided
        access_file = '/etc/atd/ACCESS_INFO.yaml'
        if os.path.exists(access_file):
            print("Reading credentials from {0}...".format(access_file))
            try:
                import yaml
                with open(access_file, 'r') as f:
                    access_info = yaml.safe_load(f)
                cvp_ip = access_info['nodes']['cvp'][0]['ip']
                username = access_info['login_info']['jump_host']['user']
                password = access_info['login_info']['jump_host']['pw']
                print("Using CVP: {0}, User: {1}".format(cvp_ip, username))
            except Exception as e:
                print("Failed to read ACCESS_INFO.yaml: {0}".format(e))
                sys.exit(1)
        else:
            sys.exit(1)
    else:
        cvp_ip = sys.argv[1]
        username = sys.argv[2]
        password = sys.argv[3]

    inspect_protobufs()
    cancel_pending_tasks(cvp_ip, username, password)
    reset_studios(cvp_ip, username, password)

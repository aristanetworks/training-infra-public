#!/usr/bin/env python3
"""
CVP Information Collector
Collects Studios, Configlets, Change Controls, and Dashboards from Arista CloudVision Portal
"""

import argparse
import sys
import os
import json
import warnings
import ssl
import tempfile
import requests
import yaml
import tarfile
from datetime import datetime, timezone
from cvprac.cvp_client import CvpClient
from cvprac.cvp_client_errors import CvpApiError
from cloud_logging_utils import setup_cloud_logging, log_operation_start, log_operation_success, log_operation_error

# Try to import jsonrpclib for switch eAPI access
try:
    import jsonrpclib
    JSONRPCLIB_AVAILABLE = True
except ImportError:
    JSONRPCLIB_AVAILABLE = False

# Configure SSL to disable certificate verification (for lab environments)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Suppress SSL warnings for CVP connections
warnings.filterwarnings('ignore', message='Unverified HTTPS request')
requests.packages.urllib3.disable_warnings()
labACCESS = '/etc/atd/ACCESS_INFO.yaml'
EDIT_INSTANCE = 'https://us-central1-{}.cloudfunctions.net/edit-instance'
def firewall(action, instanceName, instanceRegion,labProject, logger=None):
    """
    Block or unblock firewall access for lab instance
    """
    if logger:
        log_operation_start(logger, 'firewall-action', action=action, instance=instanceName)

    try:
        response = requests.get(EDIT_INSTANCE.format(labProject) + "?function={0}&instance={1}&zone={2}".format(action, instanceName, instanceRegion))
        os.system("pkill -KILL -u arista")
        if response.status_code == 200:
            print("Access to this lab has been blocked")
            if logger:
                logger.info("Lab access blocked successfully", extra={'labels': {'action': action, 'instance': instanceName, 'status': 'success'}})
            try:
                with open(labACCESS, 'r') as f:
                    access_info = yaml.safe_load(f)
                if 'customer_details' in access_info and access_info['customer_details'].get('lab_type') == 'Exam':
                    access_info['customer_details']['lab_type'] = 'Lab'

                    # Write back to the file
                    with open(labACCESS, 'w') as f:
                        yaml.dump(access_info, f, default_flow_style=False)

                    print("Lab type changed from Exam to Lab")
                    if logger:
                        logger.info("Lab type changed from Exam to Lab", extra={'labels': {'instance': instanceName}})

            except Exception as yaml_error:
                print(f"Failed to update lab_type: {str(yaml_error)}")
                if logger:
                    logger.error(f"Failed to update lab_type: {str(yaml_error)}", extra={'labels': {'instance': instanceName, 'error': str(yaml_error)}})

        os.system("pkill -KILL -u arista")
        if logger:
            log_operation_success(logger, 'firewall-action', action=action, instance=instanceName)
        return(response.json())
    except Exception as e:
        error_msg = "An error occurred, please contact the proctor." + str(e)
        print(error_msg)
        if logger:
            log_operation_error(logger, 'firewall-action', str(e), action=action, instance=instanceName)
        return(False)

def gradeExam(project, instance, region, logger=None):
    """Submit exam for grading"""
    if logger:
        log_operation_start(logger, 'exam-grading', instance=instance, region=region)

    grade_url = f"https://us-central1-{project}.cloudfunctions.net/api-grading"
    headers = {'Content-Type': 'application/json'}

    try:
        grade_response = requests.post(url=grade_url,headers=headers,data=json.dumps({"instance":instance,"zone":region,"source":"cli"}))
        if grade_response.status_code == 200:
            marks_dict = grade_response.json()["marks"] # {'V2_L2-Exam': {'score': 10, 'total_points': 195}}
            score, total_points = 0,0
            for lab in marks_dict:
                score = score + marks_dict[lab]['score']
                total_points = total_points + marks_dict[lab]['total_points']
            if score/total_points > 0.75:
                result = "Pass"
            else:
                result = "Fail"

            if logger:
                logger.info(f"Exam graded: {result}", extra={'labels': {
                    'operation': 'exam-grading',
                    'instance': instance,
                    'result': result,
                    'score': score,
                    'total_points': total_points
                }})
                log_operation_success(logger, 'exam-grading', instance=instance, result=result, score=score)
        else:
            raise Exception

    except Exception as e:
        result = "Pending"
        if logger:
            log_operation_error(logger, 'exam-grading', str(e), instance=instance, result='Pending')

    return result

def update_hubspot_handler(email, action, project, logger=None):
    """
    Call the HubSpot Cloud Function to update exam properties

    Args:
        email: Email address of the user
        action: Action to perform ('update_exam_start' or 'update_exam_submit')
        project: GCP project name
        logger: Logger instance for cloud logging

    Returns:
        dict: Response from the Cloud Function or error dict
    """
    print( f"Updating HubSpot: {action} for {email} in project {project}" )
    if logger:
        log_operation_start(logger, 'hubspot-update', action=action, email=email)

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
            if logger:
                log_operation_success(logger, 'hubspot-update', action=action, email=email)
            return response.json()
        else:
            error_msg = f"HubSpot update failed with status {response.status_code}"
            print(error_msg)
            if logger:
                log_operation_error(logger, 'hubspot-update', error_msg, action=action, email=email, status_code=response.status_code)
            try:
                error_detail = response.json()
                print(f"Error details: {error_detail}")
                return error_detail
            except:
                return {"error": error_msg, "status_code": response.status_code}

    except requests.exceptions.Timeout:
        error_msg = "HubSpot request timed out"
        print(error_msg)
        if logger:
            log_operation_error(logger, 'hubspot-update', error_msg, action=action, email=email)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"HubSpot update error: {str(e)}"
        print(error_msg)
        if logger:
            log_operation_error(logger, 'hubspot-update', str(e), action=action, email=email)
        return {"error": error_msg}

def load_access_info(yaml_path= labACCESS):
    """Load CVP access information from ACCESS_INFO.yaml"""
    try:
        with open(yaml_path, 'r') as f:
            access_info = yaml.safe_load(f)

        # Extract jump_host arista credentials
        jump_host = access_info.get('login_info', {}).get('jump_host', {})
        username = jump_host.get('user', 'arista')
        password = jump_host.get('pw', '')

        # Extract CVP IP address (first CVP node)
        cvp_nodes = access_info.get('nodes', {}).get('cvp', [])
        if cvp_nodes:
            host = cvp_nodes[0].get('ip', '')
        else:
            host = ''

        # Extract lab details
        lab_name = access_info.get('name', 'unknown')
        topology = access_info.get('topology', 'unknown')
        zone = access_info.get('zone', 'unknown')
        project = access_info.get('project', 'unknown')
        lab_type = access_info.get('customer_details', {}).get('lab_type', 'Lab')
        customer_email = access_info.get('customer_details', {}).get('exam_taker_email', 'unknown')
        return {
            'host': host,
            'username': username,
            'password': password,
            'lab_name': lab_name,
            'topology': topology,
            'zone': zone,
            'project': project,
            'lab_type': lab_type,
            'customer_email': customer_email
        }
    except FileNotFoundError:
        print(f"Warning: Could not find {yaml_path}")
        return None
    except Exception as e:
        print(f"Warning: Error reading {yaml_path}: {e}")
        return None


def create_metadata_file(output_dir, lab_name, topology):
    """Create a JSON file with lab metadata including the UTC creation timestamp"""
    metadata = {
        "lab_name": lab_name,
        "lab_topology": topology,
        "created_utc_time": datetime.now(timezone.utc).isoformat(),
        "collector_version": "2.0"
    }

    metadata_filename = "lab-metadata.json"
    complete_path = os.path.join(output_dir, metadata_filename)

    with open(complete_path, 'w') as f:
        json.dump(metadata, f, indent=4)

    print(f"✓ Metadata file created: {metadata_filename}")


def create_tarball(output_dir, lab_name):
    """Create a tarball of all collected data"""
    tarball_name = f"{lab_name[:-11]}.gz"

    print(f"\nCreating tarball: {tarball_name}")

    try:
        with tarfile.open(tarball_name, "w:gz") as tar:
            tar.add(output_dir, arcname=os.path.basename(output_dir))

            # Add apps/coder directory if it exists (student's lab work)
            coder_path = "apps/coder/"
            if os.path.exists(coder_path):
                tar.add(coder_path, arcname=os.path.basename(output_dir))
                print(f"✓ Added {coder_path} to tarball")
            else:
                print(f"⚠ Warning: {coder_path} not found, skipping")

        print(f"✓ Tarball created: {tarball_name}")
        print(f"  Location: {os.path.abspath(tarball_name)}")
        return tarball_name
    except Exception as e:
        print(f"✗ Failed to create tarball: {e}")
        return None


class CVPCollector:
    """Collector for CVP information"""

    def __init__(self, host, username, password):
        """Initialize CVP client connection"""
        self.host = host
        self.username = username
        self.password = password
        self.token = None
        self.cert = None
        self.grpc_port = None  # Cache the working gRPC port

        self.client = CvpClient()
        try:
            self.client.connect([host], username, password)
            print(f"✓ Successfully connected to CVP at {host}\n")
        except CvpApiError as e:
            print(f"✗ Failed to connect to CVP: {e}")
            sys.exit(1)

    def get_token_and_cert(self):
        """Get session token and SSL certificate for gRPC calls"""
        try:
            # Get session token
            r = requests.post(
                f'https://{self.host}/cvpservice/login/authenticate.do',
                auth=(self.username, self.password),
                verify=False
            )
            self.token = r.json()['sessionId']

            # Get SSL certificate
            self.cert = ssl.get_server_certificate((self.host, 443))

            return True
        except Exception as e:
            print(f"Note: Could not get token/cert for workspace collection: {e}")
            return False

    def get_device_info(self, device_name):
        """Get device information"""
        try:
            inventory = self.client.api.get_inventory()
            for device in inventory:
                if device['hostname'] == device_name or device['fqdn'] == device_name:
                    return device
            return None
        except CvpApiError as e:
            print(f"Error getting device info: {e}")
            return None

    def get_all_devices(self):
        """Get all devices from CVP inventory"""
        try:
            inventory = self.client.api.get_inventory()
            return inventory if inventory else []
        except CvpApiError as e:
            print(f"Error getting device inventory: {e}")
            return []

    def get_configlets_for_device(self, device_name):
        """Get configlets assigned to a device"""
        try:
            configlets = self.client.api.get_configlets_by_device_id(device_name)
        except CvpApiError as e:
            # Try alternative method
            try:
                device = self.get_device_info(device_name)
                if device:
                    configlets = self.client.api.get_configlets_by_netelement_id(device['systemMacAddress'])
                else:
                    print(f"Error getting configlets: {e}")
                    return []
            except Exception as ex:
                print(f"Error getting configlets: {e}")
                return []

        # Normalize the response - handle different data structures
        if not configlets:
            return []

        # Handle dict with nested data
        if isinstance(configlets, dict):
            if 'data' in configlets:
                configlets = configlets['data']
            elif 'configlets' in configlets:
                configlets = configlets['configlets']
            elif 'configletList' in configlets:
                configlets = configlets['configletList']
            else:
                # Single configlet as dict - check if it has expected fields
                if 'name' in configlets or 'key' in configlets:
                    configlets = [configlets]
                else:
                    # Might be a wrapper, try to find the actual list
                    for key, value in configlets.items():
                        if isinstance(value, list) and len(value) > 0:
                            configlets = value
                            break

        # Ensure we have a list
        if not isinstance(configlets, list):
            return []

        # Filter to only include dict objects (skip strings or other types)
        valid_configlets = []
        for item in configlets:
            if isinstance(item, dict):
                # Always keep dict items - they have the data we need
                valid_configlets.append(item)
            elif isinstance(item, str):
                # If it's a string, try to fetch full configlet data
                try:
                    full_configlet = self.client.api.get_configlet_by_name(item)
                    if full_configlet and isinstance(full_configlet, dict):
                        valid_configlets.append(full_configlet)
                    else:
                        valid_configlets.append({'name': item, 'config': 'N/A - Only name available'})
                except:
                    valid_configlets.append({'name': item, 'config': 'N/A - Only name available'})

        return valid_configlets

    def get_configlet_builders(self):
        """Get all configlet builders from CVP including Python code and form layouts"""
        builders = []
        try:
            # Get all configlets and filter for builders
            all_configlets = self.client.api.get_configlets()

            if not all_configlets:
                return []

            # Extract configlet list from response
            configlet_list = []
            if isinstance(all_configlets, dict):
                if 'data' in all_configlets:
                    configlet_list = all_configlets['data']
                elif 'configlets' in all_configlets:
                    configlet_list = all_configlets['configlets']
                else:
                    configlet_list = [all_configlets]
            elif isinstance(all_configlets, list):
                configlet_list = all_configlets

            for configlet in configlet_list:
                if not isinstance(configlet, dict):
                    continue

                # Check if this is a configlet builder
                is_builder = (
                    configlet.get('isAutoBuilder') == 'true' or
                    configlet.get('isAutoBuilder') is True or
                    configlet.get('type') == 'Builder'
                )

                if is_builder:
                    builder_key = configlet.get('key')
                    builder_name = configlet.get('name', 'Unknown')

                    if builder_key:
                        try:
                            # Get full builder details including Python code
                            builder_details = self.client.api.get_configlet_builder(builder_key)
                            if builder_details:
                                # Handle nested 'data' wrapper from API response
                                if 'data' in builder_details:
                                    builder_data = builder_details['data']
                                else:
                                    builder_data = builder_details

                                # Extract script code - try multiple possible field names
                                main_script = {}
                                script_code = None

                                # Try different possible locations for the script
                                if 'main_script' in builder_data:
                                    if isinstance(builder_data['main_script'], dict):
                                        script_code = builder_data['main_script'].get('data', '')
                                    else:
                                        script_code = builder_data['main_script']
                                elif 'MainScript' in builder_data:
                                    if isinstance(builder_data['MainScript'], dict):
                                        script_code = builder_data['MainScript'].get('Data', '')
                                    else:
                                        script_code = builder_data['MainScript']
                                elif 'config' in builder_data:
                                    script_code = builder_data['config']

                                if script_code:
                                    main_script = {'Data': script_code}

                                # Extract form list - try multiple possible field names
                                form_list = (
                                    builder_data.get('formList') or
                                    builder_data.get('FormList') or
                                    builder_data.get('form_list') or
                                    []
                                )

                                builders.append({
                                    'name': builder_name,
                                    'key': builder_key,
                                    'type': 'ConfigletBuilder',
                                    'main_script': main_script,
                                    'form_list': form_list,
                                    'metadata': configlet,
                                    'raw_builder_response': builder_details  # Keep raw for debugging
                                })
                        except Exception as e:
                            print(f"  Note: Could not get builder details for {builder_name}: {e}")

        except Exception as e:
            print(f"Error getting configlet builders: {e}")

        return builders

    def _extract_protobuf_value(self, obj, default='N/A'):
        """Helper to safely extract value from protobuf objects"""
        if obj is None:
            return default
        if hasattr(obj, 'value'):
            return obj.value if obj.value else default
        return str(obj) if obj else default

    def get_studio_contents(self, channel, debug=False, debug_data=None):
        """Get studio contents for all studios"""
        try:
            import arista.studio.v1.services as studio_services
            from google.protobuf.json_format import Parse, MessageToDict

            studio_stub = studio_services.StudioServiceStub(channel)
            json_request = json.dumps({})
            req = Parse(json_request, studio_services.StudioStreamRequest(), False)

            studios = {}
            response_count = 0
            for response in studio_stub.GetAll(req, timeout=10):
                response_count += 1

                if hasattr(response, 'value') and response.value:
                    studio = response.value

                    # Convert protobuf to dict for JSON serialization
                    studio_dict = MessageToDict(studio, preserving_proto_field_name=True)

                    # Store in debug data if provided
                    if debug_data is not None:
                        debug_data['studios_raw'].append(studio_dict)

                    # Use studio_id as key if available
                    studio_id = studio_dict.get('key', {}).get('studio_id', f'studio_{response_count}')

                    studios[studio_id] = studio_dict

            return studios
        except Exception as e:
            print(f"  Note: Could not retrieve studio contents: {e}")
            return {}

    def get_workspaces_grpc(self, device_mac, grpc_port=None, debug_file='cvp_debug.json'):
        """Get workspaces using gRPC API"""
        workspaces = []
        debug_data = {
            'workspaces_raw': [],
            'studios_raw': [],
            'studio_configs_raw': [],
            'matching_attempts': []
        }

        try:
            # Import gRPC dependencies
            import grpc
            from arista.workspace.v1 import services as ws_services
            from google.protobuf.json_format import Parse

            # Ensure we have token and cert
            if not self.token or not self.cert:
                if not self.get_token_and_cert():
                    return []

            # Create gRPC credentials
            call_creds = grpc.access_token_call_credentials(self.token)
            channel_creds = grpc.ssl_channel_credentials(root_certificates=self.cert.encode())
            conn_creds = grpc.composite_channel_credentials(channel_creds, call_creds)

            # Try multiple common gRPC ports if not specified
            # Use cached port if available, otherwise try common ports (443 first)
            if grpc_port:
                ports_to_try = [grpc_port]
            elif self.grpc_port:
                ports_to_try = [self.grpc_port]
            else:
                ports_to_try = [443, 8443, 9910]  # Try 443 first since it's most common

            last_error = None
            for port in ports_to_try:
                try:
                    server_address = f"{self.host}:{port}"
                    print(f"  Trying gRPC connection to {server_address}...")

                    with grpc.secure_channel(server_address, conn_creds) as channel:
                        workspace_stub = ws_services.WorkspaceServiceStub(channel)

                        # Create GetAll request
                        json_request = json.dumps({})
                        req = Parse(json_request, ws_services.WorkspaceStreamRequest(), False)

                        # Get all workspaces
                        from google.protobuf.json_format import MessageToDict

                        for response in workspace_stub.GetAll(req, timeout=10):
                            if hasattr(response, 'value') and response.value:
                                ws = response.value

                                # Convert to dict to see ALL fields
                                ws_dict = MessageToDict(ws, preserving_proto_field_name=True)
                                debug_data['workspaces_raw'].append(ws_dict)

                                workspace_name = self._extract_protobuf_value(ws.display_name if hasattr(ws, 'display_name') else None, 'Unknown')

                                # Skip built-in studio workspaces
                                if 'Add built-in studio' in workspace_name:
                                    continue

                                # Extract workspace_id from key object
                                ws_id = 'N/A'
                                if 'key' in ws_dict and 'workspace_id' in ws_dict['key']:
                                    ws_id = ws_dict['key']['workspace_id']

                                # Extract studio_id if present in the workspace config
                                studio_id = None
                                if 'studio_id' in ws_dict:
                                    studio_id = ws_dict['studio_id']
                                elif 'config' in ws_dict and 'studio_id' in ws_dict.get('config', {}):
                                    studio_id = ws_dict['config']['studio_id']

                                workspace_info = {
                                    'name': workspace_name,
                                    'workspace_id': ws_id,
                                    'type': 'Workspace',
                                    'source': 'grpc_workspace_api',
                                    'raw_data': ws_dict  # Keep full data for matching
                                }

                                # Add studio_id if found
                                if studio_id:
                                    workspace_info['studio_id'] = studio_id

                                # Add build info if available
                                if 'last_build_id' in ws_dict:
                                    workspace_info['build_id'] = ws_dict['last_build_id']

                                # Add state info
                                if 'state' in ws_dict:
                                    workspace_info['state'] = ws_dict['state']

                                workspaces.append(workspace_info)

                        # Get workspace builds to find studio_id and inputs
                        print(f"  Retrieving workspace builds (contains studio_id and student inputs)...")
                        try:
                            from arista.workspace.v1 import services as ws_build_services

                            ws_build_stub = ws_build_services.WorkspaceBuildServiceStub(channel)

                            for workspace in workspaces:
                                ws_id = workspace.get('workspace_id')
                                ws_name = workspace.get('name')
                                build_id = workspace.get('build_id')

                                if ws_id and build_id:
                                    try:
                                        # Get workspace build
                                        json_req = json.dumps({
                                            "key": {
                                                "workspace_id": ws_id,
                                                "build_id": build_id
                                            }
                                        })
                                        build_req = Parse(json_req, ws_build_services.WorkspaceBuildRequest(), False)
                                        build_response = ws_build_stub.GetOne(build_req, timeout=10)

                                        if hasattr(build_response, 'value') and build_response.value:
                                            build_dict = MessageToDict(build_response.value, preserving_proto_field_name=True)

                                            print(f"  ✓ Retrieved build for '{ws_name}'")

                                            # Try to extract studio_id from key or request
                                            if 'key' in build_dict and 'studio_id' in build_dict['key']:
                                                workspace['studio_id'] = build_dict['key']['studio_id']
                                                print(f"    Studio ID: {workspace['studio_id']}")
                                            elif 'request' in build_dict and 'studio_id' in build_dict['request']:
                                                workspace['studio_id'] = build_dict['request']['studio_id']
                                                print(f"    Studio ID: {workspace['studio_id']}")

                                            # Extract studio_id and inputs from studio_build_details
                                            if 'studio_build_details' in build_dict:
                                                studio_details = build_dict['studio_build_details']

                                                # Extract studio_id
                                                if 'studio_id' in studio_details:
                                                    workspace['studio_id'] = studio_details['studio_id']
                                                    print(f"    Studio ID: {workspace['studio_id']}")

                                                # Extract student inputs
                                                if 'inputs' in studio_details:
                                                    workspace['inputs'] = studio_details['inputs']
                                                    print(f"    ✓ Found student input values in build!")

                                            # Store build data for debug
                                            workspace['build_data'] = build_dict

                                    except Exception as e:
                                        print(f"  Note: Could not get build for '{ws_name}': {e}")
                                        import traceback
                                        traceback.print_exc()

                        except Exception as e:
                            print(f"  Note: WorkspaceBuild API error: {e}")
                            import traceback
                            traceback.print_exc()

                        # Use InputsService GetAll to get student-configured inputs
                        print(f"\n  Retrieving student inputs using InputsService...")
                        try:
                            import arista.studio.v1.services as inputs_services

                            inputs_stub = inputs_services.InputsServiceStub(channel)

                            # Get ALL inputs using GetAll
                            json_req = json.dumps({})
                            inputs_req = Parse(json_req, inputs_services.InputsStreamRequest(), False)

                            all_inputs = []
                            for response in inputs_stub.GetAll(inputs_req, timeout=30):
                                if hasattr(response, 'value') and response.value:
                                    inputs_dict = MessageToDict(response.value, preserving_proto_field_name=True)
                                    all_inputs.append(inputs_dict)

                            print(f"  ✓ Retrieved {len(all_inputs)} input configurations")

                            # Match inputs to workspaces by workspace_id
                            for workspace in workspaces:
                                ws_id = workspace.get('workspace_id')
                                ws_name = workspace.get('name')

                                # Skip if we already have inputs from build
                                if 'inputs' in workspace and workspace['inputs']:
                                    continue

                                # Find matching input by EXACT workspace_id match
                                for input_item in all_inputs:
                                    key = input_item.get('key', {})
                                    item_workspace_id = key.get('workspace_id', '')
                                    item_studio_id = key.get('studio_id', '')

                                    # Match on EXACT workspace_id (student configured inputs have workspace_id set)
                                    if item_workspace_id == ws_id and ws_id:
                                        # Parse the JSON string in the 'inputs' field
                                        inputs_json_str = input_item.get('inputs', '{}')
                                        try:
                                            inputs_parsed = json.loads(inputs_json_str)
                                            workspace['inputs'] = inputs_parsed
                                            workspace['studio_id'] = item_studio_id  # Also set studio_id from inputs
                                            print(f"  ✓ Found student inputs for workspace '{ws_name}'")
                                            print(f"    Matched by workspace_id: {ws_id}")
                                            print(f"    Studio: {item_studio_id}")
                                            break
                                        except json.JSONDecodeError as e:
                                            print(f"  ⚠ Could not parse inputs JSON for '{ws_name}': {e}")

                        except ImportError as e:
                            print(f"  Note: InputsService API not available - install 'cloudvision' package")
                        except Exception as e:
                            print(f"  Note: Could not retrieve inputs: {type(e).__name__}: {e}")

                        # Get studio templates (built-in studios)
                        print(f"  Retrieving studio templates...")
                        studio_templates = self.get_studio_contents(channel, debug_data=debug_data)
                        print(f"    Found {len(studio_templates)} studio templates")

                        # Get studio configs/instances (actual workspace studios with inputs)
                        print(f"  Retrieving studio instances (configs with student inputs)...")
                        studios = {}
                        try:
                            import arista.studio.v1.services as studio_config_services

                            studio_config_stub = studio_config_services.StudioConfigServiceStub(channel)
                            json_req = json.dumps({})
                            config_req = Parse(json_req, studio_config_services.StudioConfigStreamRequest(), False)

                            for response in studio_config_stub.GetAll(config_req, timeout=10):
                                if hasattr(response, 'value') and response.value:
                                    config_dict = MessageToDict(response.value, preserving_proto_field_name=True)

                                    # Use workspace_id as key for matching
                                    if 'key' in config_dict and 'workspace_id' in config_dict['key']:
                                        ws_id = config_dict['key']['workspace_id']
                                        studios[ws_id] = config_dict
                                        print(f"    Found studio config for workspace: {ws_id}")

                            print(f"  ✓ Retrieved {len(studios)} studio instances")

                        except Exception as e:
                            print(f"  Note: Could not retrieve studio configs: {e}")
                            # Fall back to templates if configs unavailable
                            studios = studio_templates

                        # Match studio configs to workspaces
                        print(f"  Matching {len(workspaces)} workspaces to {len(studios)} studio instances...")
                        for workspace in workspaces:
                            ws_id = workspace.get('workspace_id')
                            ws_name = workspace.get('name')

                            # Studios are now keyed by workspace_id, so direct lookup
                            if ws_id in studios:
                                studio_data = studios[ws_id]
                                workspace['studio_details'] = studio_data

                                # Extract the studio template ID
                                if 'key' in studio_data and 'studio_id' in studio_data['key']:
                                    workspace['studio_id'] = studio_data['key']['studio_id']
                                    print(f"  ✓ Matched workspace '{ws_name}' to studio '{workspace['studio_id']}'")

                                # Extract inputs (THE STUDENT'S CONFIGURATION!)
                                if 'inputs' in studio_data:
                                    workspace['inputs'] = studio_data['inputs']
                                    print(f"    ✓ Found student input values!")
                                else:
                                    print(f"    ⚠ No inputs found in studio config")
                                    print(f"    Studio config keys: {list(studio_data.keys())}")
                            else:
                                print(f"  ⚠ No studio config found for workspace '{ws_name}' (ID: {ws_id})")

                        # Get dashboards via gRPC
                        print(f"\n  Retrieving dashboards...")
                        dashboards_grpc = self.get_dashboards_grpc(channel)

                    # Cache the working port for future gRPC calls
                    if not self.grpc_port:
                        self.grpc_port = port

                    print(f"\n✓ Successfully connected to gRPC on port {port}")
                    print(f"✓ Retrieved {len(workspaces)} workspaces via gRPC")
                    if studios:
                        print(f"✓ Retrieved details for {len(studios)} studios")
                    if dashboards_grpc:
                        print(f"✓ Retrieved {len(dashboards_grpc)} dashboards via gRPC")

                    # Write debug data to file
                    try:
                        debug_data['workspaces_processed'] = workspaces
                        debug_data['studios_processed'] = studios
                        debug_data['dashboards_grpc'] = dashboards_grpc if dashboards_grpc else []
                        with open(debug_file, 'w') as f:
                            json.dump(debug_data, f, indent=2)
                        print(f"\n✓ Debug data written to: {debug_file}")
                    except Exception as e:
                        print(f"\n⚠ Could not write debug file: {e}")

                    # Store dashboards in self for later retrieval
                    self.dashboards_grpc = dashboards_grpc if dashboards_grpc else []

                    return workspaces

                except Exception as e:
                    last_error = e
                    print(f"  Port {port} failed: {type(e).__name__}")
                    continue

            # If we get here, all ports failed
            if last_error:
                print(f"\nNote: Could not connect to gRPC workspace API on any port")
                print(f"  Tried ports: {', '.join(str(p) for p in ports_to_try)}")
                print(f"  This CVP may not have workspace/studio feature enabled")
                print(f"  Or gRPC may require additional network configuration")
            return []

        except ImportError as e:
            print(f"Note: gRPC workspace collection not available - install 'cloudvision' package")
            print(f"  pip install cloudvision")
            return []
        except Exception as e:
            print(f"Note: Unexpected error in workspace collection: {e}")
            return []

    def get_studios_for_device(self, device_name, grpc_port=None):
        """Get container hierarchy and workspaces for device"""
        containers_info = []

        try:
            # Get device info first
            device = self.get_device_info(device_name)
            if not device:
                return []

            # Get all containers
            try:
                containers_response = self.client.api.get_containers()

                # Extract container list from response
                all_containers = []
                if isinstance(containers_response, dict):
                    if 'data' in containers_response:
                        all_containers = containers_response['data']
                elif isinstance(containers_response, list):
                    all_containers = containers_response

                # Build a map of containers by key for easy lookup
                container_map = {}
                for cont in all_containers:
                    if isinstance(cont, dict) and 'key' in cont:
                        container_map[cont['key']] = cont

                # Get device's direct container
                parent_id = device.get('parentContainerId')
                if parent_id and parent_id in container_map:
                    current_container = container_map[parent_id]

                    # Build the hierarchy from device up to root
                    hierarchy = []
                    while current_container:
                        hierarchy.append({
                            'name': current_container.get('name', 'Unknown'),
                            'key': current_container.get('key', 'N/A'),
                            'type': 'Container',
                            'parentName': current_container.get('parentName', 'N/A'),
                            'associatedConfiglets': len(current_container.get('associatedConfiglets', [])),
                            'associatedSwitches': len(current_container.get('associatedSwitches', [])),
                            'source': 'container_hierarchy'
                        })

                        # Move to parent container
                        parent_name = current_container.get('parentName')
                        current_container = None
                        if parent_name:
                            # Find parent container by name
                            for cont in all_containers:
                                if isinstance(cont, dict) and cont.get('name') == parent_name:
                                    current_container = cont
                                    break

                    # Add hierarchy info
                    for idx, cont_info in enumerate(hierarchy):
                        cont_info['level'] = idx
                        cont_info['hierarchy_position'] = f"Level {idx}" + (" (Device's Container)" if idx == 0 else "")
                        containers_info.append(cont_info)

            except Exception as e:
                print(f"Note: Error getting containers: {e}")

            # Try to get workspaces via gRPC
            device_mac = device.get('systemMacAddress')
            workspaces = self.get_workspaces_grpc(device_mac, grpc_port)

            # Add workspaces to the output
            for ws in workspaces:
                containers_info.append(ws)

            return containers_info

        except Exception as e:
            print(f"Note: Container/Workspace retrieval encountered error: {e}")
            return containers_info

    def get_snapshot_template_name(self, template_id):
        """Get snapshot template name from template ID via REST API"""
        try:
            # Use the REST API endpoint
            url = f'/snapshot/template?templateId={template_id}'
            response = self.client.get(url)

            if response and isinstance(response, dict):
                return response.get('name', template_id)

            return None

        except Exception as e:
            return None

    def get_network_provisioning_structure(self, target_device_name=None):
        """Get the full network provisioning structure (container hierarchy with devices)"""
        try:
            print(f"Retrieving network provisioning structure...")

            # Get all containers
            containers_response = self.client.api.get_containers()

            # Extract container list
            all_containers = []
            if isinstance(containers_response, dict):
                if 'data' in containers_response:
                    all_containers = containers_response['data']
            elif isinstance(containers_response, list):
                all_containers = containers_response

            # Get all devices
            inventory = self.client.api.get_inventory()

            # Build container map for easy lookup
            container_map = {}
            for container in all_containers:
                if isinstance(container, dict) and 'key' in container:
                    container_map[container['key']] = {
                        'name': container.get('name', 'Unknown'),
                        'parentName': container.get('parentName'),
                        'children': [],
                        'devices': []
                    }

            # Add devices to their containers
            for device in inventory:
                parent_container_id = device.get('parentContainerId')
                device_name = device.get('hostname', device.get('fqdn', 'Unknown'))

                # Mark if this is the target device (if target specified)
                if target_device_name and device_name == target_device_name:
                    device_name = f"{device_name} (This device)"

                if parent_container_id and parent_container_id in container_map:
                    container_map[parent_container_id]['devices'].append(device_name)

            # Build parent-child relationships for containers
            for container_id, container_data in container_map.items():
                parent_name = container_data['parentName']
                if parent_name:
                    # Find parent container
                    for parent_id, parent_data in container_map.items():
                        if parent_data['name'] == parent_name:
                            parent_data['children'].append({
                                'id': container_id,
                                'name': container_data['name'],
                                'data': container_data
                            })
                            break

            # Find root containers (those without parents)
            root_containers = []
            for container_id, container_data in container_map.items():
                parent_name = container_data['parentName']
                # Only add containers with no parent as roots
                if not parent_name:
                    root_containers.append({
                        'id': container_id,
                        'name': container_data['name'],
                        'data': container_data
                    })

            print(f"✓ Retrieved network structure with {len(all_containers)} container(s) and {len(inventory)} device(s)")

            return {
                'containers': container_map,
                'roots': root_containers
            }

        except Exception as e:
            print(f"Error getting network provisioning structure: {e}")
            import traceback
            traceback.print_exc()
            return None

    def get_device_path_in_hierarchy(self, device):
        """Get the path from device to root container"""
        try:
            # Get all containers
            containers_response = self.client.api.get_containers()

            # Extract container list
            all_containers = []
            if isinstance(containers_response, dict):
                if 'data' in containers_response:
                    all_containers = containers_response['data']
            elif isinstance(containers_response, list):
                all_containers = containers_response

            # Build a map of containers by key for easy lookup
            container_map = {}
            for cont in all_containers:
                if isinstance(cont, dict) and 'key' in cont:
                    container_map[cont['key']] = cont

            # Get device's direct container
            parent_id = device.get('parentContainerId')
            path = []

            if parent_id and parent_id in container_map:
                current_container = container_map[parent_id]

                # Build the path from device up to root
                while current_container:
                    path.append({
                        'name': current_container.get('name', 'Unknown'),
                        'key': current_container.get('key', 'N/A')
                    })

                    # Move to parent container
                    parent_name = current_container.get('parentName')
                    current_container = None
                    if parent_name:
                        # Find parent container by name
                        for cont in all_containers:
                            if isinstance(cont, dict) and cont.get('name') == parent_name:
                                current_container = cont
                                break

            return path

        except Exception as e:
            print(f"  Note: Could not get device path: {e}")
            return []

    def get_all_snapshot_templates(self):
        """Get all configured snapshot templates with their commands"""
        try:
            print(f"Retrieving all snapshot templates...")

            # First, get the list of all snapshot templates
            # Try to get a large range to capture all templates
            url = '/snapshot/templates?startIndex=0&endIndex=100'
            response = self.client.get(url)

            if not response:
                print(f"  Note: No snapshot templates found or API unavailable")
                return []

            # Extract templates list from response
            templates_list = []
            if isinstance(response, dict):
                if 'templateKeys' in response:
                    templates_list = response['templateKeys']
                elif 'data' in response:
                    templates_list = response['data']
                elif 'templates' in response:
                    templates_list = response['templates']
                else:
                    templates_list = [response]
            elif isinstance(response, list):
                templates_list = response

            if not templates_list:
                print(f"  Note: No snapshot templates found")
                return []

            # Now get details for each template
            detailed_templates = []
            for template in templates_list:
                # Get template ID
                template_id = template.get('id') or template.get('key') or template.get('templateId')

                if not template_id:
                    continue

                try:
                    # Get full template details including commandList
                    detail_url = f'/snapshot/template?templateId={template_id}'
                    detail_response = self.client.get(detail_url)

                    if detail_response and isinstance(detail_response, dict):
                        template_info = {
                            'id': template_id,
                            'name': detail_response.get('name', 'Unknown'),
                            'commands': detail_response.get('commandList', [])
                        }
                        detailed_templates.append(template_info)
                except Exception as e:
                    print(f"  Note: Could not get details for template {template_id}: {e}")
                    continue

            print(f"✓ Retrieved {len(detailed_templates)} snapshot template(s)")
            return detailed_templates

        except Exception as e:
            print(f"Error getting snapshot templates: {e}")
            return []

    def get_change_control_grpc_details(self, cc_id_v2):
        """Get change control details via gRPC API to retrieve configured actions"""
        try:
            import grpc
            import arista.changecontrol.v1.services as cc_services
            from google.protobuf.json_format import Parse, MessageToDict

            # Ensure we have token and cert
            if not self.token or not self.cert:
                if not self.get_token_and_cert():
                    return None

            # Create gRPC credentials
            call_creds = grpc.access_token_call_credentials(self.token)
            channel_creds = grpc.ssl_channel_credentials(root_certificates=self.cert.encode())
            conn_creds = grpc.composite_channel_credentials(channel_creds, call_creds)

            # Try gRPC ports - use cached port first if available
            if self.grpc_port:
                ports_to_try = [self.grpc_port]
            else:
                ports_to_try = [443, 8443, 9910]  # Try 443 first since it's most common

            for port in ports_to_try:
                try:
                    server_address = f"{self.host}:{port}"
                    with grpc.secure_channel(server_address, conn_creds) as channel:
                        cc_stub = cc_services.ChangeControlServiceStub(channel)

                        # Get specific change control by ID
                        json_req = json.dumps({
                            "key": {
                                "id": cc_id_v2
                            }
                        })
                        req = Parse(json_req, cc_services.ChangeControlRequest(), False)
                        response = cc_stub.GetOne(req, timeout=10)

                        if hasattr(response, 'value') and response.value:
                            cc_dict = MessageToDict(response.value, preserving_proto_field_name=True)

                            # Cache the working port
                            if not self.grpc_port:
                                self.grpc_port = port

                            return cc_dict

                except Exception as e:
                    continue

            return None

        except ImportError:
            return None
        except Exception:
            return None

    def get_change_control_details(self, cc):
        """Get detailed information about a change control including actions taken"""
        details = {}

        try:
            # Get task ID
            task_id = cc.get('workOrderId')
            if not task_id:
                return details

            # Try to get change control details via gRPC for configured actions
            cc_id_v2 = cc.get('ccIdV2')
            if cc_id_v2:
                grpc_cc_details = self.get_change_control_grpc_details(cc_id_v2)
                if grpc_cc_details:
                    # Extract configured actions from change control
                    if 'change' in grpc_cc_details and 'stages' in grpc_cc_details['change']:
                        stages_dict = grpc_cc_details['change']['stages']
                        all_actions = []

                        # Stages are stored as a dict in 'values'
                        if 'values' in stages_dict:
                            for stage_id, stage in stages_dict['values'].items():
                                # Each stage can have an action
                                if 'action' in stage:
                                    action_data = {
                                        'stage_name': stage.get('name', 'Unknown'),
                                        'action_name': stage['action'].get('name', 'Unknown'),
                                        'timeout': stage['action'].get('timeout', 'N/A'),
                                        'status': stage.get('status', 'Unknown'),
                                        'start_time': stage.get('start_time'),
                                        'end_time': stage.get('end_time')
                                    }

                                    # Extract action arguments
                                    if 'args' in stage['action'] and 'values' in stage['action']['args']:
                                        action_data['args'] = stage['action']['args']['values']

                                        # If it's a snapshot action, try to resolve the template name
                                        if action_data['action_name'] == 'snapshot' and 'TemplateID' in action_data['args']:
                                            template_id = action_data['args']['TemplateID']
                                            template_name = self.get_snapshot_template_name(template_id)
                                            if template_name:
                                                action_data['snapshot_template_name'] = template_name

                                    all_actions.append(action_data)

                        if all_actions:
                            # Sort actions by start_time
                            all_actions.sort(key=lambda x: x.get('start_time', ''))
                            details['configured_actions'] = all_actions
                            print(f"  ✓ Found {len(all_actions)} configured action(s) in change control")

            # Try to get task details with actions
            try:
                task_details = self.client.api.get_task_by_id(task_id)
                if task_details and 'data' in task_details:
                    task_data = task_details['data']

                    # Extract config snapshots (from task execution)
                    if 'configSnapshots' in task_data and task_data['configSnapshots']:
                        snapshots = task_data['configSnapshots']
                        details['config_snapshots'] = snapshots
                        print(f"  ✓ Found {len(snapshots) if isinstance(snapshots, list) else 1} config snapshot(s)")

                    # Extract configlet list (what was applied)
                    if 'configletList' in task_data and task_data['configletList']:
                        details['configlet_list'] = task_data['configletList']
                        print(f"  ✓ Found configlet list with {len(task_data['configletList'])} configlet(s)")

                    # Extract ignored configlets
                    if 'ignoreConfigletList' in task_data and task_data['ignoreConfigletList']:
                        details['ignored_configlets'] = task_data['ignoreConfigletList']

                    # Extract designed config (what CVP wanted to apply)
                    if 'designedConfig' in task_data and task_data['designedConfig']:
                        details['designed_config'] = task_data['designedConfig']
                        print(f"  ✓ Found designed config")

                    # Extract running config (what was on the device)
                    if 'runningConfig' in task_data and task_data['runningConfig']:
                        details['running_config'] = task_data['runningConfig']
                        print(f"  ✓ Found running config")

                    # Extract workflow action
                    if 'WORKFLOW_ACTION' in task_data:
                        details['workflow_action'] = task_data['WORKFLOW_ACTION']

                    # Extract rollback information
                    if 'isRollbackTask' in task_data and task_data['isRollbackTask']:
                        details['is_rollback'] = task_data['isRollbackTask']
                        if 'isRollbackFromSnapshotFlow' in task_data:
                            details['rollback_from_snapshot'] = task_data['isRollbackFromSnapshotFlow']

                    # Extract image information if present
                    if 'image' in task_data and task_data['image']:
                        details['image'] = task_data['image']
                    if 'imageToBePushedToDevice' in task_data and task_data['imageToBePushedToDevice']:
                        details['image_to_push'] = task_data['imageToBePushedToDevice']

            except Exception as e:
                print(f"  Note: Could not get task details: {type(e).__name__}")

            # Get template details if template ID exists
            # Note: 'ztp' is a default value and doesn't mean ZTP template was applied
            template_id = cc.get('templateId')
            if template_id and template_id.lower() not in ['ztp', 'ztps']:
                # Only process if it's NOT the default ztp value
                try:
                    template = self.client.api.get_configlet_by_name(template_id)
                    if template:
                        details['template'] = {
                            'name': template.get('name'),
                            'key': template.get('key'),
                            'type': template.get('type', 'Template')
                        }
                        print(f"  ✓ Found template: {template.get('name')}")
                except:
                    # Template might not be a configlet - could be a custom template
                    details['template'] = {
                        'name': template_id,
                        'type': 'Template'
                    }
                    print(f"  ✓ Found template reference: {template_id}")

        except Exception as e:
            print(f"  Note: Could not get details for change control: {e}")

        return details

    def get_all_change_controls(self):
        """Get all change controls (tasks)"""
        try:
            # Get all change controls (tasks)
            tasks = self.client.api.get_tasks()

            # Handle different response types
            if not tasks:
                return []

            # Check if tasks is a dict with 'data' key
            if isinstance(tasks, dict):
                if 'data' in tasks:
                    tasks = tasks['data']
                elif 'tasks' in tasks:
                    tasks = tasks['tasks']
                else:
                    # Try to use the dict as a single task
                    tasks = [tasks]

            # Ensure tasks is a list
            if not isinstance(tasks, list):
                print(f"Unexpected tasks format: {type(tasks)}")
                return []

            # Filter out any non-dict items
            valid_tasks = [t for t in tasks if isinstance(t, dict)]

            if not valid_tasks:
                return []

            # Determine which timestamp field to use for sorting
            timestamp_fields = ['createdOnInLongFormat', 'executedOnInLongFormat', 'createdOn', 'createdTimestamp', 'created', 'timestamp', 'executedOn', 'dateTime']

            # Find which field exists in the tasks
            timestamp_field = None
            for field in timestamp_fields:
                if valid_tasks[0].get(field) is not None:
                    timestamp_field = field
                    break

            if timestamp_field:
                print(f"Sorting change controls by: {timestamp_field}")

                # Sort by the timestamp field
                def get_sort_key(task):
                    value = task.get(timestamp_field, 0)
                    # Handle different timestamp formats
                    if isinstance(value, (int, float)):
                        return value
                    elif isinstance(value, str):
                        try:
                            # Try to parse as ISO date
                            from datetime import datetime
                            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
                        except:
                            return 0
                    else:
                        return 0

                sorted_tasks = sorted(
                    valid_tasks,
                    key=get_sort_key,
                    reverse=True
                )
                return sorted_tasks
            else:
                print("Warning: No timestamp field found, returning all tasks in original order")
                return valid_tasks

        except CvpApiError as e:
            print(f"Error getting change controls: {e}")
            return []
        except Exception as e:
            print(f"Unexpected error getting change controls: {e}")
            return []

    def get_dashboards_grpc(self, channel):
        """Get dashboards using gRPC API"""
        dashboards = []

        try:
            import arista.dashboard.v1.services as dashboard_services
            from google.protobuf.json_format import Parse, MessageToDict
            import grpc

            print(f"\n  Retrieving dashboards via gRPC...")

            # Use DashboardServiceStub (not GlobalDashboardConfigServiceStub)
            dashboard_stub = dashboard_services.DashboardServiceStub(channel)

            # Get all dashboards using DashboardStreamRequest
            json_req = json.dumps({})
            req = Parse(json_req, dashboard_services.DashboardStreamRequest(), False)

            dashboard_count = 0
            for response in dashboard_stub.GetAll(req, timeout=30):
                if hasattr(response, 'value') and response.value:
                    dashboard_dict = MessageToDict(response.value, preserving_proto_field_name=True)
                    dashboards.append(dashboard_dict)
                    dashboard_count += 1

                    # Print dashboard name if available
                    dashboard_name = dashboard_dict.get('name', 'Unknown')
                    print(f"    ✓ Found dashboard: {dashboard_name}")

            print(f"  ✓ Retrieved {len(dashboards)} dashboard configurations")
            return dashboards

        except ImportError as e:
            print(f"  Note: Dashboard gRPC API not available - install 'cloudvision' package")
            return []
        except grpc.RpcError as e:
            # Handle gRPC-specific errors gracefully
            if e.code() == grpc.StatusCode.PERMISSION_DENIED:
                print(f"  Note: No permission to access dashboards (this is normal for some CVP user accounts)")
            elif e.code() == grpc.StatusCode.UNIMPLEMENTED:
                print(f"  Note: Dashboard API not available in this CVP version")
            else:
                print(f"  Note: Could not retrieve dashboards: {e.details()}")
            return []
        except Exception as e:
            print(f"  Note: Could not retrieve dashboards: {type(e).__name__}")
            return []

    def get_dashboards(self):
        """Get dashboards if available (legacy REST API - deprecated)"""
        # This method is kept for backwards compatibility but not used
        return []

    def save_workspace_inputs_to_files(self, workspaces, output_dir):
        """Save each workspace's student inputs to a separate file"""
        workspaces_dir = os.path.join(output_dir, 'workspace_inputs')
        os.makedirs(workspaces_dir, exist_ok=True)

        saved_files = []
        for idx, workspace in enumerate(workspaces, 1):
            if workspace.get('type') != 'Workspace':
                continue

            try:
                name = workspace.get('name', f'workspace_{idx}')
                # Sanitize filename
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
                if not safe_name:
                    safe_name = f'workspace_{idx}'
                safe_name = safe_name.replace(' ', '_')

                # Save the workspace inputs (student configuration)
                if 'inputs' in workspace:
                    inputs_file = os.path.join(workspaces_dir, f'{safe_name}_inputs.json')
                    with open(inputs_file, 'w') as f:
                        json.dump(workspace['inputs'], f, indent=2)
                    saved_files.append(inputs_file)

                # Save full workspace metadata
                metadata = {
                    'name': workspace.get('name'),
                    'workspace_id': workspace.get('workspace_id'),
                    'studio_id': workspace.get('studio_id'),
                    'state': workspace.get('state'),
                    'build_id': workspace.get('build_id'),
                    'inputs': workspace.get('inputs')
                }
                metadata_file = os.path.join(workspaces_dir, f'{safe_name}_metadata.json')
                with open(metadata_file, 'w') as f:
                    json.dump(metadata, f, indent=2)
                saved_files.append(metadata_file)

            except Exception as e:
                print(f"Warning: Error saving workspace inputs for {idx}: {e}")
                continue

        return saved_files

    def save_configlets_to_files(self, configlets, output_dir):
        """Save each configlet to a separate file"""
        configlets_dir = os.path.join(output_dir, 'configlets')
        os.makedirs(configlets_dir, exist_ok=True)

        saved_files = []
        for idx, configlet in enumerate(configlets, 1):
            # Skip non-dict items (should be caught earlier, but just in case)
            if not isinstance(configlet, dict):
                print(f"Warning: Skipping non-dict configlet at index {idx}: {type(configlet)}")
                continue

            try:
                name = configlet.get('name', f'configlet_{idx}')
                # Sanitize filename
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
                if not safe_name:
                    safe_name = f'configlet_{idx}'
                safe_name = safe_name.replace(' ', '_')

                # Save the full configlet as JSON
                json_file = os.path.join(configlets_dir, f'{safe_name}_metadata.json')
                with open(json_file, 'w') as f:
                    json.dump(configlet, f, indent=2)
                saved_files.append(json_file)

                # Save the actual config content
                if 'config' in configlet and configlet['config']:
                    config_file = os.path.join(configlets_dir, f'{safe_name}_config.txt')
                    with open(config_file, 'w') as f:
                        f.write(configlet['config'])
                    saved_files.append(config_file)
            except Exception as e:
                print(f"Warning: Error saving configlet {idx}: {e}")
                continue

        return saved_files

    def save_configlet_builders_to_files(self, builders, output_dir):
        """Save configlet builders to files including Python code and form layouts"""
        builders_dir = os.path.join(output_dir, 'configlet_builders')
        os.makedirs(builders_dir, exist_ok=True)

        saved_files = []
        for idx, builder in enumerate(builders, 1):
            try:
                name = builder.get('name', f'builder_{idx}')
                # Sanitize filename
                safe_name = "".join(c for c in name if c.isalnum() or c in (' ', '-', '_')).strip()
                if not safe_name:
                    safe_name = f'builder_{idx}'
                safe_name = safe_name.replace(' ', '_')

                # Save the Python code as .py file
                main_script = builder.get('main_script', {})
                if main_script and main_script.get('Data'):
                    py_file = os.path.join(builders_dir, f'{safe_name}.py')
                    with open(py_file, 'w') as f:
                        f.write(main_script['Data'])
                    saved_files.append(py_file)

                # Save form layout as .form file (YAML format for consistency)
                form_list = builder.get('form_list', [])
                if form_list:
                    form_file = os.path.join(builders_dir, f'{safe_name}.form')
                    with open(form_file, 'w') as f:
                        yaml.dump({'FormList': form_list}, f, default_flow_style=False)
                    saved_files.append(form_file)

                # Save full metadata as JSON
                metadata_file = os.path.join(builders_dir, f'{safe_name}_metadata.json')
                with open(metadata_file, 'w') as f:
                    json.dump(builder, f, indent=2)
                saved_files.append(metadata_file)

            except Exception as e:
                print(f"Warning: Error saving builder {idx}: {e}")
                continue

        return saved_files

    def collect_switch_eapi_data(self, device, password, output_dir):
        """Collect operational data from a switch via eAPI"""
        if not JSONRPCLIB_AVAILABLE:
            print(f"  ✗ ERROR: jsonrpclib not available - Cannot collect switch running configs!")
            print(f"  Please install jsonrpclib: pip install jsonrpclib-pelix")
            return

        device_name = device.get('hostname', device.get('fqdn', 'Unknown'))
        device_ip = device.get('ipAddress', '')

        if not device_ip:
            print(f"  ✗ Warning: No IP address for {device_name}, skipping eAPI collection")
            return

        print(f"  → Collecting eAPI data from {device_name} ({device_ip})...")

        # Connect to switch with SSL verification disabled (using global SSL context)
        try:
            print(f"    → Attempting to connect to {device_ip} with username '{self.username}'...")
            # Use the global SSL context that's already configured at module level (lines 26-31)
            switch = jsonrpclib.Server(
                f"https://{self.username}:{password}@{device_ip}/command-api"
            )
            print(f"    ✓ Connection object created")
        except Exception as e:
            print(f"  ✗ ERROR: Failed to connect to {device_name} eAPI: {e}")
            print(f"    Troubleshooting:")
            print(f"      - Verify eAPI is enabled on switch: 'management api http-commands'")
            print(f"      - Check IP address is reachable: {device_ip}")
            print(f"      - Verify credentials: username={self.username}")
            return

        # Dictionary to store all outputs
        outputs = {}

        # Collect various command outputs
        self._collect_command_output(switch, device_name, outputs, "running-config",
                                     ["show running-config"])
        self._collect_mlag_info(switch, device_name, outputs)
        self._collect_vxlan_info(switch, device_name, outputs)
        self._collect_ospf_info(switch, device_name, outputs)
        self._collect_command_output(switch, device_name, outputs, "ip-route",
                                     ["show ip route vrf all"])
        self._collect_command_output(switch, device_name, outputs, "vrf",
                                     ["show vrf"])
        self._collect_command_output(switch, device_name, outputs, "bgp-summary",
                                     ["show ip bgp summary"])

        # EVPN commands - only for non-host devices
        if "host" not in device_name.lower():
            self._collect_command_output(switch, device_name, outputs, "evpn-summary",
                                         ["show bgp evpn summary"])
            self._collect_command_output(switch, device_name, outputs, "evpn",
                                         ["show bgp evpn"])

        self._collect_command_output(switch, device_name, outputs, "interface-brief",
                                     ["show ip interface brief"])
        self._collect_command_output(switch, device_name, outputs, "vlan",
                                     ["show vlan"])

        # Save all collected outputs to files
        if outputs:
            self._save_eapi_outputs(device_name, outputs, output_dir)
            print(f"  ✓ Collected eAPI data from {device_name} ({len(outputs)} categories)")
        else:
            print(f"  ✗ WARNING: No eAPI data collected from {device_name} - all commands failed!")
            print(f"     Check if device is reachable and eAPI is enabled")

    def _collect_command_output(self, switch, device_name, outputs_dict, output_key, commands):
        """Run a command and store its output with error handling"""
        try:
            result = switch.runCmds(1, ["enable"] + commands, "text")
            outputs_dict[output_key] = result[1]["output"]
            print(f"    ✓ Collected {output_key}")
        except Exception as e:
            # Command not supported or feature not enabled
            print(f"    ⓘ Skipped {output_key} (not available or error: {str(e)[:50]})")
            pass

    def _collect_mlag_info(self, switch, device_name, outputs_dict):
        """Collect MLAG information with error handling"""
        try:
            commands = ["show mlag", "show mlag config-sanity", "show mlag interfaces detail"]
            result = switch.runCmds(1, ["enable"] + commands, "text")

            mlag_output = "show mlag\n"
            mlag_output += result[1]["output"]
            mlag_output += "\nshow mlag config-sanity\n"
            mlag_output += result[2]["output"]
            mlag_output += "\nshow mlag interfaces detail\n"
            mlag_output += result[3]["output"]

            outputs_dict["mlag"] = mlag_output
            print(f"    ✓ Collected mlag info")
        except Exception as e:
            print(f"    ⓘ Skipped mlag (not available or error: {str(e)[:50]})")
            pass

    def _collect_ospf_info(self, switch, device_name, outputs_dict):
        """Collect OSPF information with error handling"""
        try:
            commands = ["show ip ospf", "show ip ospf neighbor", "show ip ospf interface brief"]
            result = switch.runCmds(1, ["enable"] + commands, "text")

            ospf_output = "show ip ospf\n"
            ospf_output += result[1]["output"]
            ospf_output += "\nshow ip ospf neighbor\n"
            ospf_output += result[2]["output"]
            ospf_output += "\nshow ip ospf interface brief\n"
            ospf_output += result[3]["output"]

            outputs_dict["ospf"] = ospf_output
            print(f"    ✓ Collected ospf info")
        except Exception as e:
            print(f"    ⓘ Skipped ospf (not available or error: {str(e)[:50]})")
            pass

    def _collect_vxlan_info(self, switch, device_name, outputs_dict):
        """Collect VXLAN information with error handling"""
        try:
            commands = [
                "show vxlan address-table",
                "show mac address-table",
                "show vxlan flood vtep",
                "show vxlan vtep detail",
                "show vxlan vni summary",
                "show vxlan vni",
                "show vxlan config-sanity"
            ]
            result = switch.runCmds(1, ["enable"] + commands, "text")

            vxlan_output = "show vxlan address-table\n"
            vxlan_output += result[1]["output"]
            vxlan_output += "\nshow mac address-table\n"
            vxlan_output += result[2]["output"]
            vxlan_output += "\nshow vxlan flood vtep\n"
            vxlan_output += result[3]["output"]
            vxlan_output += "\nshow vxlan vtep detail\n"
            vxlan_output += result[4]["output"]
            vxlan_output += "\nshow vxlan vni summary\n"
            vxlan_output += result[5]["output"]
            vxlan_output += "\nshow vxlan vni\n"
            vxlan_output += result[6]["output"]
            vxlan_output += "\nshow vxlan config-sanity\n"
            vxlan_output += result[7]["output"]

            outputs_dict["vxlan"] = vxlan_output
            print(f"    ✓ Collected vxlan info")
        except Exception as e:
            print(f"    ⓘ Skipped vxlan (not available or error: {str(e)[:50]})")
            pass

    def _save_eapi_outputs(self, device_name, outputs_dict, output_dir):
        """Save all collected eAPI outputs to files"""
        eapi_dir = os.path.join(output_dir, 'eapi_data')
        os.makedirs(eapi_dir, exist_ok=True)

        saved_count = 0
        for output_type, content in outputs_dict.items():
            try:
                filename = f"{device_name}-{output_type}.txt"
                complete_path = os.path.join(eapi_dir, filename)
                with open(complete_path, 'w') as f:
                    f.write(content)
                saved_count += 1
            except Exception as e:
                print(f"    ✗ Failed to save {output_type}: {e}")

        if saved_count > 0:
            print(f"    → Saved {saved_count} file(s) to {eapi_dir}/")

    def format_overview_report(self, all_workspaces, dashboards, snapshot_templates, all_change_controls, network_structure, configlet_builders=None, output_dir=None):
        """Format CVP-wide overview report"""
        output = []
        output.append("=" * 100)
        output.append("CVP OVERVIEW REPORT")
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append("=" * 100)
        output.append("")

        # Network Provisioning Structure Section
        output.append("─" * 100)
        output.append("NETWORK PROVISIONING STRUCTURE")
        output.append("─" * 100)
        if network_structure and network_structure.get('roots'):
            def display_tree(node, prefix="", is_last=True):
                """Recursively display tree structure"""
                lines = []

                # Determine the branch characters
                branch = "└── " if is_last else "├── "

                # Display current node
                lines.append(f"{prefix}{branch}{node['name']}")

                # Prepare prefix for children
                extension = "    " if is_last else "│   "
                new_prefix = prefix + extension

                # Display devices in this container
                devices = node['data'].get('devices', [])
                children = node['data'].get('children', [])

                # Combine devices and children for proper tree display
                all_items = []
                for device in devices:
                    all_items.append(('device', device))
                for child in children:
                    all_items.append(('container', child))

                # Display all items
                for idx, (item_type, item) in enumerate(all_items):
                    is_last_item = (idx == len(all_items) - 1)

                    if item_type == 'device':
                        device_branch = "└── " if is_last_item else "├── "
                        lines.append(f"{new_prefix}{device_branch}{item}")
                    else:  # container
                        lines.extend(display_tree(item, new_prefix, is_last_item))

                return lines

            output.append("Container and Device Hierarchy:\n")

            # Display each root container
            roots = network_structure['roots']
            for idx, root in enumerate(roots):
                is_last_root = (idx == len(roots) - 1)
                tree_lines = display_tree(root, "", is_last_root)
                output.extend(tree_lines)

            output.append("")
        else:
            output.append("Network structure not available")
        output.append("")

        # All Workspaces/Studios Section
        output.append("─" * 100)
        output.append("ALL WORKSPACES/STUDIOS")
        output.append("─" * 100)
        if all_workspaces:
            output.append(f"Total Workspaces: {len(all_workspaces)}\n")

            # Save workspace inputs to files if output directory is specified
            if output_dir:
                saved_files = self.save_workspace_inputs_to_files(all_workspaces, output_dir)
                if saved_files:
                    output.append(f"Workspace inputs saved to: {os.path.join(output_dir, 'workspace_inputs')}/")
                    output.append(f"Total files created: {len(saved_files)}\n")

            for idx, workspace in enumerate(all_workspaces, 1):
                output.append(f"[Workspace {idx}]")
                output.append(f"  Name:         {workspace.get('name', 'Unknown')}")
                output.append(f"  Type:         {workspace.get('type', 'N/A')}")
                output.append(f"  Workspace ID: {workspace.get('workspace_id', 'N/A')}")
                if 'studio_id' in workspace:
                    output.append(f"  Studio ID:    {workspace['studio_id']}")
                if 'build_id' in workspace:
                    output.append(f"  Build ID:     {workspace['build_id']}")
                if 'state' in workspace:
                    output.append(f"  State:        {workspace['state']}")
                output.append(f"  Source:       {workspace.get('source', 'N/A')}")

                # Show studio template details if available
                if 'studio_details' in workspace:
                    output.append(f"\n  Studio Template:")
                    studio = workspace['studio_details']

                    # Show key studio information
                    if 'description' in studio:
                        output.append(f"    Description:  {studio['description']}")

                # Show student's configured input values (THE KEY DATA FOR GRADING)
                if 'inputs' in workspace:
                    output.append(f"\n  Student Configuration (Input Values):")
                    output.append(f"  {'-' * 90}")
                    inputs = workspace['inputs']

                    # Format inputs nicely for grading
                    def format_inputs(data, indent=4):
                        lines = []
                        if isinstance(data, dict):
                            for key, value in sorted(data.items()):
                                prefix = " " * indent
                                if isinstance(value, (dict, list)) and value:
                                    lines.append(f"{prefix}{key}:")
                                    lines.extend(format_inputs(value, indent + 2))
                                else:
                                    lines.append(f"{prefix}{key}: {value}")
                        elif isinstance(data, list):
                            for idx, item in enumerate(data):
                                prefix = " " * indent
                                if isinstance(item, (dict, list)):
                                    lines.append(f"{prefix}[{idx}]:")
                                    lines.extend(format_inputs(item, indent + 2))
                                else:
                                    lines.append(f"{prefix}- {item}")
                        return lines

                    # Format all input lines
                    all_input_lines = format_inputs(inputs)

                    # Show preview (first 10 lines) in main report
                    preview_lines = all_input_lines[:10]
                    for line in preview_lines:
                        output.append(f"  {line}")

                    # Show truncation message if there are more lines
                    if len(all_input_lines) > 10:
                        output.append(f"  ... ({len(all_input_lines) - 10} more lines)")

                    output.append(f"  {'-' * 90}")

                    # Reference to full file if output directory specified
                    if output_dir:
                        safe_name = "".join(c for c in workspace.get('name', f'workspace_{idx}')
                                          if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
                        output.append(f"  Full inputs saved to: workspace_inputs/{safe_name}_inputs.json")
                else:
                    output.append(f"\n  Student Configuration: No input values found")

                # Show workspace state and metadata
                if 'state' in workspace:
                    output.append(f"\n  Workspace State: {workspace['state']}")
                if 'build_id' in workspace:
                    output.append(f"  Build ID:        {workspace['build_id']}")

                output.append("")
        else:
            output.append("No workspaces found")
        output.append("")

        # Snapshot Templates Section
        output.append("─" * 100)
        output.append("CONFIGURED SNAPSHOT TEMPLATES")
        output.append("─" * 100)
        if snapshot_templates:
            output.append(f"Total Snapshot Templates: {len(snapshot_templates)}\n")
            for idx, template in enumerate(snapshot_templates, 1):
                output.append(f"\n[Snapshot Template {idx}]")
                output.append(f"Name:        {template.get('name', 'Unknown')}")
                output.append(f"Template ID: {template.get('id', 'N/A')}")

                # Show commands
                commands = template.get('commands', [])
                if commands:
                    if isinstance(commands, list):
                        output.append(f"Commands:    {len(commands)} command(s)")
                        for cmd_idx, command in enumerate(commands, 1):
                            output.append(f"  [{cmd_idx}] {command}")
                    else:
                        output.append(f"Commands:    {commands}")
                else:
                    output.append(f"Commands:    No commands configured")

                output.append("")
        else:
            output.append("No snapshot templates configured")
        output.append("")

        # Configlet Builders Section
        output.append("─" * 100)
        output.append("CONFIGLET BUILDERS")
        output.append("─" * 100)
        if configlet_builders:
            output.append(f"Total Configlet Builders: {len(configlet_builders)}\n")
            for idx, builder in enumerate(configlet_builders, 1):
                output.append(f"\n[Configlet Builder {idx}]")
                output.append(f"Name:         {builder.get('name', 'Unknown')}")
                output.append(f"Type:         ConfigletBuilder")

                # Show Python code preview
                main_script = builder.get('main_script', {})
                if main_script and main_script.get('Data'):
                    code_lines = main_script['Data'].split('\n')
                    output.append(f"Code Lines:   {len(code_lines)}")
                    output.append(f"\nCode Preview (first 10 lines):")
                    output.append("  " + "-" * 80)
                    for line in code_lines[:10]:
                        output.append(f"  {line}")
                    if len(code_lines) > 10:
                        output.append(f"  ... ({len(code_lines) - 10} more lines)")
                    output.append("  " + "-" * 80)

                # Show form fields
                form_list = builder.get('form_list', [])
                if form_list:
                    output.append(f"\nForm Fields: {len(form_list)} field(s)")
                    for field in form_list:
                        field_label = field.get('fieldLabel', 'Unknown')
                        field_type = field.get('type', 'Unknown')
                        output.append(f"  - {field_label}: {field_type}")

                output.append("")
        else:
            output.append("No configlet builders found")
        output.append("")

        # Dashboards Section
        output.append("─" * 100)
        output.append("DASHBOARDS")
        output.append("─" * 100)
        if dashboards:
            output.append(f"Total Dashboards: {len(dashboards)}\n")
            for idx, dashboard in enumerate(dashboards, 1):
                output.append(f"\n[Dashboard {idx}]")

                # Dashboard name and basic info
                output.append(f"Name:               {dashboard.get('name', 'Unknown')}")

                # Dashboard ID from key
                if 'key' in dashboard and 'dashboard_id' in dashboard['key']:
                    output.append(f"Dashboard ID:       {dashboard['key']['dashboard_id']}")

                # Description
                description = dashboard.get('description', '')
                if description:
                    output.append(f"Description:        {description}")

                # Created/Modified info
                if 'created_by' in dashboard:
                    output.append(f"Created By:         {dashboard['created_by']}")
                if 'created_at' in dashboard:
                    output.append(f"Created At:         {dashboard['created_at']}")

                # Widgets count
                if 'widgets' in dashboard:
                    widgets_data = dashboard['widgets']
                    widgets_list = []
                    if isinstance(widgets_data, dict) and 'values' in widgets_data:
                        widgets_list = widgets_data['values']
                    elif isinstance(widgets_data, list):
                        widgets_list = widgets_data

                    if widgets_list:
                        output.append(f"Widgets:            {len(widgets_list)} widget(s)")

                output.append("")
        else:
            output.append("No dashboards found or dashboard API not available")
        output.append("")

        # All Change Controls Section
        output.append("─" * 100)
        output.append("ALL CHANGE CONTROLS")
        output.append("─" * 100)
        if all_change_controls:
            output.append(f"Total Change Controls: {len(all_change_controls)}\n")
            for idx, cc in enumerate(all_change_controls, 1):
                output.append(f"\n[Change Control {idx}]")
                output.append(f"Description:         {cc.get('description', 'No description')}")
                output.append(f"Work Order ID:       {cc.get('workOrderId', 'N/A')}")
                output.append(f"Work Order State:    {cc.get('workOrderState', 'N/A')}")
                output.append(f"Current Task Type:   {cc.get('currentTaskType', 'N/A')}")

                # Handle both timestamp field formats
                created_time = cc.get('createdOnInLongFormat') or cc.get('createdOn')
                if created_time:
                    timestamp = datetime.fromtimestamp(created_time / 1000.0)
                    output.append(f"Created On:          {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                output.append(f"Created By:          {cc.get('createdBy', 'N/A')}")

                # Device info if available
                if 'netElementId' in cc:
                    output.append(f"Net Element ID:      {cc['netElementId']}")

                output.append("")
        else:
            output.append("No change controls found")
        output.append("")

        output.append("=" * 100)
        output.append("END OF OVERVIEW REPORT")
        output.append("=" * 100)

        return "\n".join(output)

    def format_device_report(self, device, configlets, device_path, change_controls, output_dir=None):
        """Format per-device report"""
        device_name = device.get('hostname', device.get('fqdn', 'Unknown'))

        output = []
        output.append("=" * 100)
        output.append(f"DEVICE REPORT: {device_name}")
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append("=" * 100)
        output.append("")

        # Device Details Section
        output.append("─" * 100)
        output.append("DEVICE DETAILS")
        output.append("─" * 100)
        output.append(f"Hostname:           {device.get('hostname', 'N/A')}")
        output.append(f"FQDN:               {device.get('fqdn', 'N/A')}")
        output.append(f"IP Address:         {device.get('ipAddress', 'N/A')}")
        output.append(f"System MAC:         {device.get('systemMacAddress', 'N/A')}")
        output.append(f"Model:              {device.get('modelName', 'N/A')}")
        output.append(f"Serial Number:      {device.get('serialNumber', 'N/A')}")
        output.append(f"Software Version:   {device.get('version', 'N/A')}")
        output.append(f"Status:             {device.get('status', 'N/A')}")
        output.append(f"Streaming Status:   {device.get('streamingStatus', 'N/A')}")
        output.append(f"Boot Time:          {device.get('bootupTimeStamp', 'N/A')}")
        output.append(f"Container ID:       {device.get('parentContainerId', 'N/A')}")
        output.append("")

        # Device Path in Network Hierarchy
        output.append("─" * 100)
        output.append("LOCATION IN NETWORK HIERARCHY")
        output.append("─" * 100)
        if device_path:
            output.append(f"Path from device to root ({len(device_path)} level(s)):\n")
            # Reverse to show from root down to device
            reversed_path = list(reversed(device_path))
            for idx, container in enumerate(reversed_path):
                indent = "  " * idx
                output.append(f"{indent}[Level {idx}] {container['name']}")
                output.append(f"{indent}  Container Key: {container['key']}")
            output.append(f"{indent}  └── {device_name} (This device)")
        else:
            output.append("Device is not in any container")
        output.append("")

        # Configlets Section
        output.append("─" * 100)
        output.append("CONFIGLETS ASSIGNED TO DEVICE")
        output.append("─" * 100)
        if configlets:
            output.append(f"Total Configlets: {len(configlets)}\n")

            # Save configlets to files if output directory is specified
            if output_dir:
                configlets_saved_dir = os.path.join(output_dir, 'configlets')
                saved_files = self.save_configlets_to_files(configlets, output_dir)
                output.append(f"Configlets saved to: {configlets_saved_dir}/")
                output.append(f"Total files created: {len(saved_files)}\n")

            for idx, configlet in enumerate(configlets, 1):
                output.append(f"\n[Configlet {idx}]")
                output.append(f"Name:            {configlet.get('name', 'Unknown')}")
                output.append(f"Key:             {configlet.get('key', 'N/A')}")
                output.append(f"Type:            {configlet.get('type', 'N/A')}")
                output.append(f"User:            {configlet.get('user', 'N/A')}")
                output.append(f"Is Auto Builder: {configlet.get('isAutoBuilder', 'N/A')}")

                # Config content preview (10 lines)
                if 'config' in configlet and configlet['config']:
                    config_lines = configlet['config'].split('\n')
                    output.append(f"Config Lines:    {len(config_lines)}")
                    output.append(f"Config Size:     {len(configlet['config'])} bytes")
                    output.append("\nConfig Preview (first 10 lines):")
                    output.append("  " + "-" * 90)
                    for line in config_lines[:10]:
                        output.append(f"  {line}")
                    if len(config_lines) > 10:
                        output.append(f"  ... ({len(config_lines) - 10} more lines)")
                    output.append("  " + "-" * 90)

                    if output_dir:
                        safe_name = "".join(c for c in configlet.get('name', f'configlet_{idx}')
                                          if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
                        output.append(f"  Full config saved to: configlets/{safe_name}_config.txt")
                        output.append(f"  Full metadata saved to: configlets/{safe_name}_metadata.json")
                else:
                    output.append("Config:          <No config content>")
                output.append("")
        else:
            output.append("No configlets found for this device")
        output.append("")

        # Change Controls for this Device
        output.append("─" * 100)
        output.append("CHANGE CONTROLS FOR THIS DEVICE")
        output.append("─" * 100)
        if change_controls:
            output.append(f"Total Change Controls: {len(change_controls)}\n")
            for idx, cc in enumerate(change_controls, 1):
                output.append(f"\n[Change Control {idx}]")
                output.append(f"Description:         {cc.get('description', 'No description')}")
                output.append(f"Work Order ID:       {cc.get('workOrderId', 'N/A')}")
                output.append(f"Work Order State:    {cc.get('workOrderState', 'N/A')}")
                output.append(f"Current Task Name:   {cc.get('currentTaskName', 'N/A')}")
                output.append(f"Current Task Type:   {cc.get('currentTaskType', 'N/A')}")

                # Handle both timestamp field formats
                created_time = cc.get('createdOnInLongFormat') or cc.get('createdOn')
                if created_time:
                    timestamp = datetime.fromtimestamp(created_time / 1000.0)
                    output.append(f"Created On:          {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                executed_time = cc.get('executedOnInLongFormat') or cc.get('executedOn')
                if executed_time:
                    timestamp = datetime.fromtimestamp(executed_time / 1000.0)
                    output.append(f"Executed On:         {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                output.append(f"Created By:          {cc.get('createdBy', 'N/A')}")
                output.append(f"Task Status:         {cc.get('taskStatus', 'N/A')}")

                # Display action details if available (preview only - first 10 actions)
                if 'action_details' in cc and cc['action_details']:
                    action_details = cc['action_details']

                    # Show configured actions (preview)
                    if 'configured_actions' in action_details:
                        actions = action_details['configured_actions']
                        output.append(f"\n  Configured Actions: {len(actions)} action(s)")
                        for action_idx, action in enumerate(actions[:10], 1):
                            if isinstance(action, dict):
                                stage_name = action.get('stage_name', 'Unknown Stage')
                                action_name = action.get('action_name', 'Unknown Action')
                                status = action.get('status', 'Unknown')

                                output.append(f"    [{action_idx}] {stage_name}")
                                output.append(f"        Action Type:  {action_name}")
                                output.append(f"        Status:       {status}")

                                # Show snapshot template name if present
                                if 'snapshot_template_name' in action:
                                    output.append(f"        Snapshot Template: {action['snapshot_template_name']}")

                        if len(actions) > 10:
                            output.append(f"    ... and {len(actions) - 10} more actions")

                output.append("")
        else:
            output.append("No change controls found for this device")
        output.append("")

        output.append("=" * 100)
        output.append("END OF DEVICE REPORT")
        output.append("=" * 100)

        return "\n".join(output)

    def format_output(self, device_name, device, configlets, studios, change_controls, dashboards, snapshot_templates, network_structure, output_dir=None):
        """Format all collected data for nice human-readable output"""

        output = []
        output.append("=" * 100)
        output.append(f"CVP INFORMATION REPORT FOR DEVICE: {device_name}")
        output.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        output.append("=" * 100)
        output.append("")

        # Device Details Section
        output.append("─" * 100)
        output.append("DEVICE DETAILS")
        output.append("─" * 100)
        if device:
            output.append(f"Hostname:           {device.get('hostname', 'N/A')}")
            output.append(f"FQDN:               {device.get('fqdn', 'N/A')}")
            output.append(f"IP Address:         {device.get('ipAddress', 'N/A')}")
            output.append(f"System MAC:         {device.get('systemMacAddress', 'N/A')}")
            output.append(f"Model:              {device.get('modelName', 'N/A')}")
            output.append(f"Serial Number:      {device.get('serialNumber', 'N/A')}")
            output.append(f"Software Version:   {device.get('version', 'N/A')}")
            output.append(f"Status:             {device.get('status', 'N/A')}")
            output.append(f"Streaming Status:   {device.get('streamingStatus', 'N/A')}")
            output.append(f"Boot Time:          {device.get('bootupTimeStamp', 'N/A')}")
            output.append(f"Container ID:       {device.get('parentContainerId', 'N/A')}")
        output.append("")

        # Network Provisioning Structure Section
        output.append("─" * 100)
        output.append("NETWORK PROVISIONING STRUCTURE")
        output.append("─" * 100)
        if network_structure and network_structure.get('roots'):
            def display_tree(node, prefix="", is_last=True):
                """Recursively display tree structure"""
                lines = []

                # Determine the branch characters
                branch = "└── " if is_last else "├── "

                # Display current node
                lines.append(f"{prefix}{branch}{node['name']}")

                # Prepare prefix for children
                extension = "    " if is_last else "│   "
                new_prefix = prefix + extension

                # Display devices in this container
                devices = node['data'].get('devices', [])
                children = node['data'].get('children', [])

                # Combine devices and children for proper tree display
                all_items = []
                for device in devices:
                    all_items.append(('device', device))
                for child in children:
                    all_items.append(('container', child))

                # Display all items
                for idx, (item_type, item) in enumerate(all_items):
                    is_last_item = (idx == len(all_items) - 1)

                    if item_type == 'device':
                        device_branch = "└── " if is_last_item else "├── "
                        lines.append(f"{new_prefix}{device_branch}{item}")
                    else:  # container
                        lines.extend(display_tree(item, new_prefix, is_last_item))

                return lines

            output.append("Container and Device Hierarchy:\n")

            # Display each root container
            roots = network_structure['roots']
            for idx, root in enumerate(roots):
                is_last_root = (idx == len(roots) - 1)
                tree_lines = display_tree(root, "", is_last_root)
                output.extend(tree_lines)

            output.append("")
        else:
            output.append("Network structure not available")
        output.append("")

        # Container Hierarchy & Workspaces Section
        output.append("─" * 100)
        output.append("STUDIOS/WORKSPACES & CONTAINER HIERARCHY")
        output.append("─" * 100)
        if studios:
            # Separate containers and workspaces
            containers = [s for s in studios if s.get('type') == 'Container']
            workspaces = [s for s in studios if s.get('type') == 'Workspace']

            # Show workspaces/studios first (if available)
            if workspaces:
                output.append(f"Workspaces/Studios ({len(workspaces)} total):\n")

                # Save workspace inputs to files if output directory is specified
                if output_dir:
                    saved_files = self.save_workspace_inputs_to_files(workspaces, output_dir)
                    if saved_files:
                        output.append(f"Workspace inputs saved to: {os.path.join(output_dir, 'workspace_inputs')}/")
                        output.append(f"Total files created: {len(saved_files)}\n")
                for idx, workspace in enumerate(workspaces, 1):
                    output.append(f"[Workspace {idx}]")
                    output.append(f"  Name:         {workspace.get('name', 'Unknown')}")
                    output.append(f"  Type:         {workspace.get('type', 'N/A')}")
                    output.append(f"  Workspace ID: {workspace.get('workspace_id', 'N/A')}")
                    if 'build_id' in workspace:
                        output.append(f"  Build ID:     {workspace['build_id']}")
                    output.append(f"  Source:       {workspace.get('source', 'N/A')}")

                    # Show studio template details if available
                    if 'studio_details' in workspace:
                        output.append(f"\n  Studio Template:")
                        studio = workspace['studio_details']

                        # Show key studio information
                        if 'description' in studio:
                            output.append(f"    Description:  {studio['description']}")

                    # Show student's configured input values (THE KEY DATA FOR GRADING)
                    if 'inputs' in workspace:
                        output.append(f"\n  Student Configuration (Input Values):")
                        output.append(f"  {'-' * 90}")
                        inputs = workspace['inputs']

                        # Format inputs nicely for grading
                        def format_inputs(data, indent=4):
                            lines = []
                            if isinstance(data, dict):
                                for key, value in sorted(data.items()):
                                    prefix = " " * indent
                                    if isinstance(value, (dict, list)) and value:
                                        lines.append(f"{prefix}{key}:")
                                        lines.extend(format_inputs(value, indent + 2))
                                    else:
                                        lines.append(f"{prefix}{key}: {value}")
                            elif isinstance(data, list):
                                for idx, item in enumerate(data):
                                    prefix = " " * indent
                                    if isinstance(item, (dict, list)):
                                        lines.append(f"{prefix}[{idx}]:")
                                        lines.extend(format_inputs(item, indent + 2))
                                    else:
                                        lines.append(f"{prefix}- {item}")
                            return lines

                        # Format all input lines
                        all_input_lines = format_inputs(inputs)

                        # Show preview (first 10 lines) in main report
                        preview_lines = all_input_lines[:10]
                        for line in preview_lines:
                            output.append(f"  {line}")

                        # Show truncation message if there are more lines
                        if len(all_input_lines) > 10:
                            output.append(f"  ... ({len(all_input_lines) - 10} more lines)")

                        output.append(f"  {'-' * 90}")

                        # Reference to full file if output directory specified
                        if output_dir:
                            safe_name = "".join(c for c in workspace.get('name', f'workspace_{idx}')
                                              if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
                            output.append(f"  Full inputs saved to: workspace_inputs/{safe_name}_inputs.json")
                    else:
                        output.append(f"\n  Student Configuration: No input values found")

                    # Show workspace state and metadata
                    if 'state' in workspace:
                        output.append(f"\n  Workspace State: {workspace['state']}")
                    if 'build_id' in workspace:
                        output.append(f"  Build ID:        {workspace['build_id']}")

                    output.append("")
            else:
                output.append("Workspaces/Studios: Not available via gRPC API")
                output.append("  Note: This CVP installation may not have workspace/studio feature enabled,")
                output.append("        or gRPC API may not be accessible. Container hierarchy shown below instead.\n")

            # Show container hierarchy
            if containers:
                output.append(f"Container Hierarchy ({len(containers)} level(s)):\n")
                output.append("  This shows the device's organizational structure in CVP:")
                output.append("")
                for idx, container in enumerate(containers, 1):
                    output.append(f"  [{container.get('hierarchy_position', f'Container {idx}')}]")
                    output.append(f"    Name:                  {container.get('name', 'Unknown')}")
                    output.append(f"    Type:                  {container.get('type', 'N/A')}")
                    output.append(f"    Container Key:         {container.get('key', 'N/A')}")
                    output.append(f"    Parent Container:      {container.get('parentName', 'N/A')}")
                    output.append(f"    Associated Configlets: {container.get('associatedConfiglets', 0)}")
                    output.append(f"    Associated Switches:   {container.get('associatedSwitches', 0)}")
                    output.append("")
        else:
            output.append("No organizational structure found (neither workspaces nor containers)")
        output.append("")

        # Configlets Section
        output.append("─" * 100)
        output.append("CONFIGLETS ASSIGNED TO DEVICE")
        output.append("─" * 100)
        if configlets:
            output.append(f"Total Configlets: {len(configlets)}\n")

            # Save configlets to files if output directory is specified
            if output_dir:
                saved_files = self.save_configlets_to_files(configlets, output_dir)
                output.append(f"Configlets saved to: {os.path.join(output_dir, 'configlets')}/")
                output.append(f"Total files created: {len(saved_files)}\n")

            for idx, configlet in enumerate(configlets, 1):
                output.append(f"\n[Configlet {idx}]")
                output.append(f"Name:            {configlet.get('name', 'Unknown')}")
                output.append(f"Key:             {configlet.get('key', 'N/A')}")
                output.append(f"Type:            {configlet.get('type', 'N/A')}")
                output.append(f"Reconciled:      {configlet.get('reconciled', 'N/A')}")
                output.append(f"Date/Time:       {configlet.get('dateTimeInLongFormat', 'N/A')}")
                output.append(f"User:            {configlet.get('user', 'N/A')}")
                output.append(f"Net Element ID:  {configlet.get('netElementId', 'N/A')}")
                output.append(f"Is Default:      {configlet.get('isDefault', 'N/A')}")
                output.append(f"Is Auto Builder: {configlet.get('isAutoBuilder', 'N/A')}")

                # Additional fields that may be present
                if 'containerCount' in configlet:
                    output.append(f"Container Count: {configlet['containerCount']}")
                if 'netElementCount' in configlet:
                    output.append(f"NetElement Count: {configlet['netElementCount']}")
                if 'sslConfig' in configlet:
                    output.append(f"SSL Config:      {configlet['sslConfig']}")

                # Config content
                if 'config' in configlet and configlet['config']:
                    config_lines = configlet['config'].split('\n')
                    output.append(f"Config Lines:    {len(config_lines)}")
                    output.append(f"Config Size:     {len(configlet['config'])} bytes")
                    output.append("\nConfig Preview (first 10 lines):")
                    output.append("  " + "-" * 90)
                    for line in config_lines[:10]:
                        output.append(f"  {line}")
                    if len(config_lines) > 10:
                        output.append(f"  ... ({len(config_lines) - 10} more lines)")
                    output.append("  " + "-" * 90)

                    if output_dir:
                        safe_name = "".join(c for c in configlet.get('name', f'configlet_{idx}')
                                          if c.isalnum() or c in (' ', '-', '_')).strip().replace(' ', '_')
                        output.append(f"  Full config saved to: configlets/{safe_name}_config.txt")
                        output.append(f"  Full metadata saved to: configlets/{safe_name}_metadata.json")
                else:
                    output.append("Config:          <No config content>")
                output.append("")
        else:
            output.append("No configlets found for this device")
        output.append("")

        # Change Controls Section
        output.append("─" * 100)
        output.append("CHANGE CONTROLS FOR THIS DEVICE")
        output.append("─" * 100)
        if change_controls:
            output.append(f"Total Change Controls: {len(change_controls)}\n")
            for idx, cc in enumerate(change_controls, 1):
                output.append(f"\n[Change Control {idx}]")
                output.append(f"Description:         {cc.get('description', 'No description')}")
                output.append(f"Work Order ID:       {cc.get('workOrderId', 'N/A')}")
                output.append(f"Work Order State:    {cc.get('workOrderState', 'N/A')}")
                output.append(f"Current Task Name:   {cc.get('currentTaskName', 'N/A')}")
                output.append(f"Current Task Type:   {cc.get('currentTaskType', 'N/A')}")

                # Handle both timestamp field formats
                created_time = cc.get('createdOnInLongFormat') or cc.get('createdOn')
                if created_time:
                    timestamp = datetime.fromtimestamp(created_time / 1000.0)
                    output.append(f"Created On:          {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                executed_time = cc.get('executedOnInLongFormat') or cc.get('executedOn')
                if executed_time:
                    timestamp = datetime.fromtimestamp(executed_time / 1000.0)
                    output.append(f"Executed On:         {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                completed_time = cc.get('completedOnInLongFormat') or cc.get('completedOn')
                if completed_time:
                    timestamp = datetime.fromtimestamp(completed_time / 1000.0)
                    output.append(f"Completed On:        {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")

                output.append(f"Created By:          {cc.get('createdBy', 'N/A')}")
                output.append(f"Executed By:         {cc.get('executedBy', 'N/A')}")
                output.append(f"Note:                {cc.get('note', 'N/A')}")

                # Task details
                if 'taskStatus' in cc:
                    output.append(f"Task Status:         {cc['taskStatus']}")
                if 'workOrderUserDefinedStatus' in cc:
                    output.append(f"User Status:         {cc['workOrderUserDefinedStatus']}")

                # Device and config info
                if 'netElementId' in cc:
                    output.append(f"Net Element ID:      {cc['netElementId']}")
                if 'ipAddress' in cc:
                    output.append(f"IP Address:          {cc['ipAddress']}")
                if 'name' in cc:
                    output.append(f"Task Name:           {cc['name']}")

                # Additional metadata
                for key in ['ccId', 'ccIdV2', 'templateId', 'deviceId', 'viewId']:
                    if key in cc:
                        output.append(f"{key}: {cc[key]}")

                # Print any other fields that might be useful
                important_fields = ['taskId', 'tempTaskId', 'snapshotId', 'complianceCode']
                for field in important_fields:
                    if field in cc:
                        output.append(f"{field}: {cc[field]}")

                # Display action details if available
                if 'action_details' in cc and cc['action_details']:
                    action_details = cc['action_details']
                    output.append(f"\nActions Taken:")
                    output.append(f"  {'-' * 90}")

                    # Show workflow action
                    if 'workflow_action' in action_details:
                        output.append(f"  Workflow Action:   {action_details['workflow_action']}")

                    # Show configured actions (pre-configured snapshots, etc.)
                    if 'configured_actions' in action_details:
                        actions = action_details['configured_actions']
                        output.append(f"\n  Configured Actions: {len(actions)} action(s)")
                        for idx, action in enumerate(actions, 1):
                            if isinstance(action, dict):
                                stage_name = action.get('stage_name', 'Unknown Stage')
                                action_name = action.get('action_name', 'Unknown Action')
                                status = action.get('status', 'Unknown')

                                output.append(f"    [{idx}] {stage_name}")
                                output.append(f"        Action Type:  {action_name}")
                                output.append(f"        Status:       {status}")

                                # Show timeout if present
                                if 'timeout' in action:
                                    output.append(f"        Timeout:      {action['timeout']} seconds")

                                # Show timing if present
                                if 'start_time' in action and action['start_time']:
                                    output.append(f"        Start Time:   {action['start_time']}")
                                if 'end_time' in action and action['end_time']:
                                    output.append(f"        End Time:     {action['end_time']}")

                                # Show snapshot template name if resolved (show first)
                                if 'snapshot_template_name' in action:
                                    output.append(f"        Snapshot Template: {action['snapshot_template_name']}")

                                # Show args if present (for snapshots, this contains the template)
                                if 'args' in action:
                                    args = action['args']
                                    if isinstance(args, dict):
                                        output.append(f"        Arguments:")
                                        for key, value in args.items():
                                            # Show TemplateID without redundant name
                                            if key == 'TemplateID' and 'snapshot_template_name' in action:
                                                output.append(f"          {key}: {value}")
                                            else:
                                                output.append(f"          {key}: {value}")
                            else:
                                output.append(f"    [{idx}] {action}")

                    # Show template information
                    if 'template' in action_details:
                        template = action_details['template']
                        output.append(f"  Template Applied:  {template.get('name', 'Unknown')}")
                        output.append(f"    Type:            {template.get('type', 'N/A')}")
                        if 'description' in template:
                            output.append(f"    Description:     {template['description']}")
                        if 'key' in template:
                            output.append(f"    Key:             {template['key']}")

                    # Show configlet list (what was applied)
                    if 'configlet_list' in action_details:
                        configlets = action_details['configlet_list']
                        if isinstance(configlets, list):
                            output.append(f"\n  Configlets Applied: {len(configlets)} configlet(s)")
                            for idx, configlet in enumerate(configlets[:10], 1):
                                if isinstance(configlet, dict):
                                    output.append(f"    [{idx}] {configlet.get('name', 'Unknown')}")
                                else:
                                    output.append(f"    [{idx}] {configlet}")
                            if len(configlets) > 10:
                                output.append(f"    ... and {len(configlets) - 10} more")

                    # Show config snapshots
                    if 'config_snapshots' in action_details:
                        snapshots = action_details['config_snapshots']
                        if isinstance(snapshots, list):
                            output.append(f"\n  Config Snapshots:  {len(snapshots)} snapshot(s) captured")
                            for idx, snapshot in enumerate(snapshots[:10], 1):
                                if isinstance(snapshot, dict):
                                    snap_name = snapshot.get('name', snapshot.get('key', 'Unknown'))
                                    output.append(f"    [{idx}] {snap_name}")
                                else:
                                    output.append(f"    [{idx}] {snapshot}")
                            if len(snapshots) > 10:
                                output.append(f"    ... and {len(snapshots) - 10} more")
                        else:
                            output.append(f"\n  Config Snapshots:  {snapshots}")

                    # Show rollback information
                    if 'is_rollback' in action_details and action_details['is_rollback']:
                        output.append(f"\n  Rollback Task:     Yes")
                        if 'rollback_from_snapshot' in action_details:
                            output.append(f"    From Snapshot:   {action_details['rollback_from_snapshot']}")

                    # Show image information
                    if 'image' in action_details:
                        output.append(f"\n  Current Image:     {action_details['image']}")
                    if 'image_to_push' in action_details:
                        output.append(f"  Image to Push:     {action_details['image_to_push']}")

                    # Show designed config preview
                    if 'designed_config' in action_details:
                        config = action_details['designed_config']
                        config_lines = config.split('\n') if isinstance(config, str) else []
                        if config_lines:
                            output.append(f"\n  Designed Config:   {len(config_lines)} lines")
                            output.append(f"    Preview (first 5 lines):")
                            for line in config_lines[:5]:
                                output.append(f"      {line}")
                            if len(config_lines) > 5:
                                output.append(f"      ... ({len(config_lines) - 5} more lines)")

                    # Show running config preview
                    if 'running_config' in action_details:
                        config = action_details['running_config']
                        config_lines = config.split('\n') if isinstance(config, str) else []
                        if config_lines:
                            output.append(f"\n  Running Config:    {len(config_lines)} lines (before change)")
                            output.append(f"    Preview (first 5 lines):")
                            for line in config_lines[:5]:
                                output.append(f"      {line}")
                            if len(config_lines) > 5:
                                output.append(f"      ... ({len(config_lines) - 5} more lines)")

                    output.append(f"  {'-' * 90}")

                output.append("")
        else:
            output.append("No change controls found")
        output.append("")

        # Dashboards Section
        output.append("─" * 100)
        output.append("DASHBOARDS")
        output.append("─" * 100)
        if dashboards:
            output.append(f"Total Dashboards: {len(dashboards)}\n")
            for idx, dashboard in enumerate(dashboards, 1):
                output.append(f"\n[Dashboard {idx}]")

                # Dashboard name and basic info
                output.append(f"Name:               {dashboard.get('name', 'Unknown')}")

                # Dashboard ID from key
                if 'key' in dashboard and 'dashboard_id' in dashboard['key']:
                    output.append(f"Dashboard ID:       {dashboard['key']['dashboard_id']}")

                # Description
                description = dashboard.get('description', '')
                if description:
                    output.append(f"Description:        {description}")

                # Created/Modified info
                if 'created_by' in dashboard:
                    output.append(f"Created By:         {dashboard['created_by']}")
                if 'created_at' in dashboard:
                    output.append(f"Created At:         {dashboard['created_at']}")
                if 'last_modified_by' in dashboard:
                    output.append(f"Last Modified By:   {dashboard['last_modified_by']}")
                if 'last_modified_at' in dashboard:
                    output.append(f"Last Modified At:   {dashboard['last_modified_at']}")

                # Widgets
                if 'widgets' in dashboard:
                    widgets_data = dashboard['widgets']

                    # Handle different widget structures
                    widgets_list = []
                    if isinstance(widgets_data, dict) and 'values' in widgets_data:
                        widgets_list = widgets_data['values']
                    elif isinstance(widgets_data, list):
                        widgets_list = widgets_data

                    if widgets_list:
                        output.append(f"\nWidgets: {len(widgets_list)} widget(s)")

                        for widget_idx, widget in enumerate(widgets_list, 1):
                            output.append(f"\n  [Widget {widget_idx}]")
                            widget_name = widget.get('name', 'Unnamed Widget')
                            if widget_name:
                                output.append(f"    Name:       {widget_name}")

                            if 'type' in widget:
                                output.append(f"    Type:       {widget['type']}")

                            if 'id' in widget:
                                output.append(f"    Widget ID:  {widget['id']}")

                            # Position and dimensions
                            if 'position' in widget:
                                pos = widget['position']
                                output.append(f"    Position:   x={pos.get('x', 0)}, y={pos.get('y', 0)}")

                            if 'dimensions' in widget:
                                dims = widget['dimensions']
                                output.append(f"    Size:       width={dims.get('width', 0)}, height={dims.get('height', 0)}")

                            # Parse and display inputs if present
                            if 'inputs' in widget and widget['inputs']:
                                try:
                                    inputs = json.loads(widget['inputs']) if isinstance(widget['inputs'], str) else widget['inputs']
                                    output.append(f"    Inputs:")

                                    # Show key input fields
                                    if 'metricSource' in inputs:
                                        output.append(f"      Metric Source: {inputs['metricSource']}")
                                    if 'metricKeys' in inputs:
                                        metrics = inputs['metricKeys']
                                        output.append(f"      Metrics: {len(metrics) if isinstance(metrics, list) else 1}")
                                        if isinstance(metrics, list) and len(metrics) <= 5:
                                            for metric in metrics:
                                                output.append(f"        - {metric}")
                                        elif isinstance(metrics, list):
                                            for metric in metrics[:5]:
                                                output.append(f"        - {metric}")
                                            output.append(f"        ... and {len(metrics) - 5} more")

                                    if 'components' in inputs:
                                        components = inputs['components']
                                        if isinstance(components, list):
                                            output.append(f"      Components: {len(components)}")
                                            for comp in components[:3]:
                                                if isinstance(comp, dict):
                                                    device_id = comp.get('deviceId', 'N/A')
                                                    intf = comp.get('intfName', 'N/A')
                                                    output.append(f"        - Device: {device_id}, Interface: {intf}")
                                except:
                                    output.append(f"    Inputs: {widget['inputs'][:100]}...")

                output.append("")
        else:
            output.append("No dashboards found or dashboard API not available")
        output.append("")

        # Snapshot Templates Section
        output.append("─" * 100)
        output.append("CONFIGURED SNAPSHOT TEMPLATES")
        output.append("─" * 100)
        if snapshot_templates:
            output.append(f"Total Snapshot Templates: {len(snapshot_templates)}\n")
            for idx, template in enumerate(snapshot_templates, 1):
                output.append(f"\n[Snapshot Template {idx}]")
                output.append(f"Name:        {template.get('name', 'Unknown')}")
                output.append(f"Template ID: {template.get('id', 'N/A')}")

                # Show commands
                commands = template.get('commands', [])
                if commands:
                    if isinstance(commands, list):
                        output.append(f"Commands:    {len(commands)} command(s)")
                        for cmd_idx, command in enumerate(commands, 1):
                            output.append(f"  [{cmd_idx}] {command}")
                    else:
                        output.append(f"Commands:    {commands}")
                else:
                    output.append(f"Commands:    No commands configured")

                output.append("")
        else:
            output.append("No snapshot templates configured")
        output.append("")

        output.append("=" * 100)
        output.append("END OF REPORT")
        output.append("=" * 100)

        return "\n".join(output)


def main():
    """Main function"""
    # Initialize cloud logging
    logger = setup_cloud_logging('upload_exam_unattended')
    logger.info("Starting exam upload process")

    parser = argparse.ArgumentParser(
        description='Collect information from Arista CloudVision Portal',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Use ACCESS_INFO.yaml (default on ATD jumphost)
  %(prog)s

  # Use ACCESS_INFO.yaml for a specific device
  %(prog)s --device leaf1

  # Override with explicit credentials
  %(prog)s --host cvp.example.com --user admin --password admin123 --device leaf1
  %(prog)s -H 10.0.0.1 -u cvpadmin -p pass -d spine1 -o output.txt
        '''
    )

    parser.add_argument('-H', '--host',
                        help='CVP host/IP address (default: read from /etc/atd/ACCESS_INFO.yaml)')
    parser.add_argument('-u', '--user',
                        help='CVP username (default: read from /etc/atd/ACCESS_INFO.yaml)')
    parser.add_argument('-p', '--password',
                        help='CVP password (default: read from /etc/atd/ACCESS_INFO.yaml)')
    parser.add_argument('-d', '--device',
                        help='Device hostname or FQDN (optional: if not specified, collects for all devices)')
    parser.add_argument('-o', '--output',
                        help='Output file (default: print to screen)')
    parser.add_argument('--output-dir', default='cvp_exam_data',
                        help='Directory to save configlet files and detailed data (default: cvp_exam_data)')
    parser.add_argument('--grpc-port', type=int,
                        help='gRPC port for workspace API (default: tries 443, 8443, 9910)')
    parser.add_argument('--access-info', default='/etc/atd/ACCESS_INFO.yaml',
                        help='Path to ACCESS_INFO.yaml file (default: /etc/atd/ACCESS_INFO.yaml)')
    parser.add_argument('--collect-eapi', action='store_true',
                        help='Collect operational data from switches via eAPI (requires jsonrpclib)')
    parser.add_argument('--create-tarball', action='store_true',
                        help='Create a tarball of all collected data for submission')

    args = parser.parse_args()

    # Determine CVP connection details
    # Priority: command-line args > ACCESS_INFO.yaml
    host = args.host
    username = args.user
    password = args.password
    lab_name = 'unknown'
    topology = 'unknown'

    # If any credentials are missing, try to load from ACCESS_INFO.yaml
    if not host or not username or not password:
        print(f"Loading CVP credentials from {args.access_info}...")
        access_info = load_access_info(args.access_info)
        print(f"✓ Loaded credentials from {access_info}")
        if access_info:
            host = host or access_info['host']
            username = username or access_info['username']
            password = password or access_info['password']
            lab_name = access_info.get('lab_name', 'unknown')
            topology = access_info.get('topology', 'unknown')
            print(f"✓ Loaded credentials from {args.access_info}")
            print(f"  CVP Host: {host}")
            print(f"  Username: {username}")
            print(f"  Lab Name: {lab_name}")
            print(f"  Topology: {topology}\n")
        else:
            print(f"✗ Could not load {args.access_info}")
            print(f"Please provide CVP credentials via command-line arguments:")
            print(f"  --host CVP_HOST --user USERNAME --password PASSWORD")
            sys.exit(1)

    # Verify we have all required credentials
    if not host or not username or not password:
        print("✗ Missing required CVP credentials")
        print("Please provide --host, --user, and --password")
        sys.exit(1)

    # Update output directory to use lab_name if default was used
    if args.output_dir == 'cvp_exam_data' and lab_name != 'unknown':
        args.output_dir = f"{lab_name}_results"

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    print(f"Output directory: {os.path.abspath(args.output_dir)}\n")

    # Check for jsonrpclib availability (required for switch config collection)
    if not JSONRPCLIB_AVAILABLE:
        print("=" * 80)
        print("WARNING: jsonrpclib is NOT installed!")
        print("=" * 80)
        print("Switch running configs will NOT be collected.")
        print("To fix this, run: pip install jsonrpclib-pelix")
        print("=" * 80 + "\n")

    # Create collector and connect
    collector = CVPCollector(host, username, password)

    # Collecting data for entire CVP environment
    print("Collecting data for entire CVP environment\n")

    # Step 1: Collect global CVP data
    print("\n" + "=" * 80)
    print("STEP 1: Collecting global CVP data")
    print("=" * 80 + "\n")

    # Get all workspaces (via gRPC)
    print("Retrieving all workspaces/studios...")
    all_workspaces = collector.get_workspaces_grpc(None, args.grpc_port)
    print(f"✓ Retrieved {len(all_workspaces)} workspace(s)\n")

    # Get all dashboards (already retrieved during workspace collection)
    dashboards = getattr(collector, 'dashboards_grpc', [])
    print(f"✓ Retrieved {len(dashboards)} dashboard(s)\n")

    # Get all snapshot templates
    snapshot_templates = collector.get_all_snapshot_templates()
    print()

    # Get all configlet builders
    print("Retrieving all configlet builders...")
    all_configlet_builders = collector.get_configlet_builders()
    print(f"✓ Retrieved {len(all_configlet_builders)} configlet builder(s)\n")

    # Save configlet builders to files
    if all_configlet_builders:
        saved_builder_files = collector.save_configlet_builders_to_files(all_configlet_builders, args.output_dir)
        print(f"✓ Saved {len(saved_builder_files)} configlet builder file(s)\n")

    # Get all change controls
    print("Retrieving all change controls...")
    all_change_controls = collector.get_all_change_controls()
    print(f"✓ Retrieved {len(all_change_controls)} change control(s)\n")

    # Enrich ALL change controls with detailed action information
    if all_change_controls:
        print(f"Retrieving detailed actions for each change control...")
        for cc in all_change_controls:
            details = collector.get_change_control_details(cc)
            if details:
                cc['action_details'] = details
        print(f"✓ Retrieved action details for change controls\n")

    # Get network provisioning structure (full tree, no specific device)
    network_structure = collector.get_network_provisioning_structure(None)

    # Step 2: Generate overview report
    print("=" * 80)
    print("STEP 2: Generating overview report")
    print("=" * 80 + "\n")

    overview_report = collector.format_overview_report(
        all_workspaces,
        dashboards,
        snapshot_templates,
        all_change_controls,
        network_structure,
        all_configlet_builders,
        args.output_dir
    )

    overview_path = os.path.join(args.output_dir, 'Overview_report.txt')
    with open(overview_path, 'w') as f:
        f.write(overview_report)
    print(f"✓ Overview report saved to: {overview_path}\n")

    # Step 3: Get all devices
    print("=" * 80)
    print("STEP 3: Retrieving all devices")
    print("=" * 80 + "\n")

    all_devices = collector.get_all_devices()
    print(f"✓ Found {len(all_devices)} device(s)\n")

    # Step 4: Process each device (CVP data + switch configs)
    print("=" * 80)
    print("STEP 4: Processing each device")
    print("=" * 80 + "\n")

    device_reports = []
    for idx, device in enumerate(all_devices, 1):
        device_name = device.get('hostname', device.get('fqdn', 'Unknown'))
        print(f"[{idx}/{len(all_devices)}] Processing device: {device_name}")

        # Create device directory
        device_dir = os.path.join(args.output_dir, device_name)
        os.makedirs(device_dir, exist_ok=True)

        # Get device-specific CVP data
        configlets = collector.get_configlets_for_device(device_name)
        print(f"  ✓ Retrieved {len(configlets)} configlet(s)")

        # Get device path in hierarchy
        device_path = collector.get_device_path_in_hierarchy(device)

        # Filter change controls for this device
        device_mac = device.get('systemMacAddress')
        device_change_controls = [
            cc for cc in all_change_controls
            if cc.get('netElementId') == device_mac or
               (cc.get('workOrderDetails', {}).get('netElementId') == device_mac)
        ]
        print(f"  ✓ Found {len(device_change_controls)} change control(s) for this device")

        # Generate device report
        device_report = collector.format_device_report(
            device,
            configlets,
            device_path,
            device_change_controls,
            device_dir
        )

        # Save device report
        device_report_path = os.path.join(device_dir, f'{device_name}_report.txt')
        with open(device_report_path, 'w') as f:
            f.write(device_report)
        print(f"  ✓ Device report saved to: {device_report_path}")

        # ALWAYS collect eAPI data (switch running configs)
        print(f"  Collecting switch running config via eAPI...")
        collector.collect_switch_eapi_data(device, password, device_dir)

        # Save device raw data
        device_raw_data = {
            'device': device,
            'configlets': configlets,
            'device_path': device_path,
            'change_controls': device_change_controls,
            'collection_timestamp': datetime.now().isoformat()
        }
        device_raw_data_file = os.path.join(device_dir, 'raw_data.json')
        with open(device_raw_data_file, 'w') as f:
            json.dump(device_raw_data, f, indent=2)
        print(f"  ✓ Raw data saved to: {device_raw_data_file}\n")

        device_reports.append({
            'device_name': device_name,
            'report_path': device_report_path,
            'device_dir': device_dir
        })

    # Step 5: Summary
    print("\n" + "=" * 80)
    print("COLLECTION COMPLETE")
    print("=" * 80)
    print(f"\nOverview Report: {overview_path}")
    print(f"\nDevice Reports ({len(device_reports)} total):")
    for dr in device_reports:
        print(f"  - {dr['device_name']}: {dr['device_dir']}/")

    print(f"\n{'=' * 80}")
    print("Output Structure:")
    print(f"{'=' * 80}")
    print(f"{args.output_dir}/")
    print(f"├── Overview_report.txt")
    print(f"├── configlet_builders/")
    print(f"│   ├── *.py")
    print(f"│   ├── *.form")
    print(f"│   └── *_metadata.json")
    for dr in device_reports[:3]:  # Show first 3 as examples
        print(f"├── {dr['device_name']}/")
        print(f"│   ├── {dr['device_name']}_report.txt")
        print(f"│   ├── raw_data.json")
        print(f"│   ├── configlets/")
        print(f"│   │   ├── *_config.txt")
        print(f"│   │   └── *_metadata.json")
        print(f"│   ├── eapi_data/")
        print(f"│   │   └── running_config.txt")
        print(f"│   └── workspace_inputs/")
        print(f"│       ├── *_inputs.json")
        print(f"│       └── *_metadata.json")
    if len(device_reports) > 3:
        print(f"└── ... and {len(device_reports) - 3} more device(s)")
    print()

    create_tarball(args.output_dir, lab_name)
    print("Exam data collection complete.")
    logger.info("Exam data collection complete", extra={'labels': {'lab_name': lab_name, 'operation': 'exam-submit'}})

    print("Submitting your exam for grading...")
    gradeExam(access_info['project'], lab_name, access_info['zone'], logger)
    print("Exam submitted for grading.")

    print("Disconnecting you from the lab environment")
    if access_info['customer_email'] != "unknown":
        update_hubspot_handler(access_info['customer_email'], "update_exam_submit", access_info['project'], logger)
    firewall("block-firewall", lab_name, access_info['zone'], access_info['project'], logger)
if __name__ == "__main__":
    main()
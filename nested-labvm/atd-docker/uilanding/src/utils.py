"""
Utility functions for uilanding service.

Pure helper functions extracted from uilanding.py to keep the main module clean.
These functions have no dependency on Tornado, Docker, or other heavy modules.
"""

from datetime import datetime
from base64 import b64decode, b64encode
import json
import os
import secrets
import requests

# Cloud Logging Setup
try:
    from cloud_logging_utils import setup_cloud_logging
    logger = setup_cloud_logging('uilanding')
except Exception:
    import logging as _logging
    logger = _logging.getLogger('uilanding')
    logger.addHandler(_logging.StreamHandler())
    logger.setLevel(_logging.INFO)

CONNECTIVITY_LOG_PATH = '/var/log/atd/connectivity.jsonl'
CONNECTIVITY_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB


def _write_connectivity_log(level, message, labels):
    """Write connectivity events to local JSONL file for offline reporting"""
    try:
        # Rotate if file exceeds max size
        if os.path.exists(CONNECTIVITY_LOG_PATH):
            if os.path.getsize(CONNECTIVITY_LOG_PATH) > CONNECTIVITY_LOG_MAX_BYTES:
                rotated = CONNECTIVITY_LOG_PATH + '.1'
                if os.path.exists(rotated):
                    os.remove(rotated)
                os.rename(CONNECTIVITY_LOG_PATH, rotated)

        os.makedirs(os.path.dirname(CONNECTIVITY_LOG_PATH), exist_ok=True)
        entry = json.dumps({
            'ts': datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%S.%fZ'),
            'level': level,
            'message': message,
            'labels': labels
        })
        with open(CONNECTIVITY_LOG_PATH, 'a') as f:
            f.write(entry + '\n')
    except Exception:
        pass


def safe_log(level, message, **kwargs):
    """Log safely - never crash the application due to logging errors"""
    try:
        labels = {k: str(v) for k, v in kwargs.items()}
        getattr(logger, level)(message, extra={'labels': labels} if labels else {})
        # Write connectivity events to local JSONL file
        if kwargs.get('event') == 'connectivity':
            _write_connectivity_log(level, message, labels)
    except Exception:
        pass


def pS(mtype):
    """
    Function to send output from service file to Syslog
    """
    cur_dt = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mmes = "\t" + mtype
    print("[{0}] {1}".format(cur_dt, mmes.expandtabs(7 - len(cur_dt))))


# ---------------------------------------------------------------------------
# Service URL constants (Docker Desktop primary, Linux Docker bridge fallback)
# ---------------------------------------------------------------------------
CAPTURE_SERVICE_URL = "http://host.docker.internal:8089"
CAPTURE_SERVICE_URL_FALLBACK = "http://172.17.0.1:8089"
CAPTURE_WS_URL = "ws://host.docker.internal:8089/ws"
CAPTURE_WS_URL_FALLBACK = "ws://172.17.0.1:8089/ws"
NODEBUILDER_URL = "http://host.docker.internal:8090"
NODEBUILDER_URL_FALLBACK = "http://172.17.0.1:8090"


def encodeID(tmp_data):
    tmp_str = json.dumps(tmp_data).encode()
    enc_str = b64encode(tmp_str).decode()
    return(enc_str)


def decodeID(tmp_data):
    decrypt_str = b64decode(tmp_data.encode()).decode()
    tmp_json = json.loads(decrypt_str)
    return(tmp_json)


def normalize_device_name(name):
    """
    Normalize device name to consistent capitalization.
    E.g., 'leaf5' -> 'Leaf5', 'memleaf1' -> 'Memleaf1', 'spine1-dc1' -> 'Spine1-DC1'

    Handles patterns like:
    - Simple: leaf1 -> Leaf1, spine2 -> Spine2
    - With suffix: spine1-dc1 -> Spine1-DC1, leaf2-dc2 -> Leaf2-DC2
    - Compound: memleaf1 -> Memleaf1, borderleaf1 -> Borderleaf1
    - Abbreviations: PE1 -> PE1, P3 -> P3 (preserve uppercase abbreviations)
    """
    import re

    if not name:
        return name

    # Split on hyphens to handle suffixes like -DC1, -DC2
    parts = name.split('-')
    result_parts = []

    for part in parts:
        # Check if this part is a datacenter suffix (DC1, DC2, etc.)
        if re.match(r'^[dD][cC]\d+$', part):
            # Uppercase the DC suffix
            result_parts.append(part.upper())
        # Check if this part is an uppercase abbreviation followed by numbers (PE1, P3, CE1, etc.)
        elif re.match(r'^[A-Z]+\d*$', part):
            # Preserve uppercase abbreviations like PE1, P3, CE1
            result_parts.append(part)
        else:
            # Capitalize first letter, keep rest of case
            # This turns 'leaf5' -> 'Leaf5', 'memleaf1' -> 'Memleaf1'
            result_parts.append(part.capitalize())

    return '-'.join(result_parts)


def getAPI(action, topo_api='atd-conftopo'):
    """
    Fetch topology/CVP status from the config topology API service.

    Args:
        action: API action string to encode and send
        topo_api: Hostname of the conftopo service (default: 'atd-conftopo')

    Returns:
        dict: Parsed JSON response, or error dict with status 'DOWN'
    """
    try:
        _action = encodeID(action)
        response = requests.get(f"http://{topo_api}:50010/td-api/conftopo?action={_action}", timeout=5)
        return(json.loads(response.text))
    except Exception as e:
        safe_log('error', f'Error in getAPI: {e}', event='error', handler='getAPI')
        return {'status': 'DOWN', 'error': str(e)}


def getUptime(instanceIP, topo_data=None):
    """
    Function to get response from instances /uptime.

    Args:
        instanceIP: IP/URL for instance (str)
        topo_data: Optional topology data dict containing labels with runtime info.
                   If provided and status is 'init', runtime is read from topo_data['labels']['runtime'].

    Returns:
        dict: Uptime data with keys boottime, uptime, runtime, status
    """
    try:
        response = requests.get(f"https://{instanceIP}/uptime", verify=False, timeout=0.5)
        instance_data = json.loads(response.text)
        if instance_data['status'] == 'init':
            if topo_data and 'labels' in topo_data and 'runtime' in topo_data['labels']:
                instance_data['runtime'] = int(topo_data['labels']['runtime'])
            else:
                instance_data['runtime'] = 12
        else:
            instance_data['runtime'] = 12
        return(instance_data)
    except Exception as e:
        safe_log('warning', f'Uptime fetch failed for {instanceIP}', event='uptime', action='fetch_failed')
        return({
            'boottime': 0,
            'uptime': 0,
            'runtime': 12,
            'status': 'init'
        })


def getEventStatus(instanceName, instanceZone, func_state='', schema=1):
    """
    Function to get the current status of an instance.

    Args:
        instanceName: Name of the GCP instance
        instanceZone: Zone of the GCP instance
        func_state: URL of the Cloud Function endpoint for state queries
        schema: Schema version (1 or 2). Schema 2 appends '-eos' to instance name.

    Returns:
        dict: Instance state data, or False on error
    """
    try:
        if schema == 2:
            response = requests.get(func_state + "?function=state&instance={0}-eos&zone={1}".format(instanceName, instanceZone), timeout=10)
        else:
            response = requests.get(func_state + "?function=state&instance={0}&zone={1}".format(instanceName, instanceZone), timeout=10)
        return(response.json())
    except ValueError as e:
        safe_log('error', f'Error in getEventStatus: ValueError for {instanceName}', event='error', handler='getEventStatus')
        return(False)
    except requests.exceptions.ConnectionError as e:
        safe_log('error', f'Error in getEventStatus: ConnectionError for {instanceName}', event='error', handler='getEventStatus')
        return(False)
    except Exception as e:
        safe_log('error', f'Error in getEventStatus: {e} for {instanceName}', event='error', handler='getEventStatus')
        return(False)


def genCookieSecret():
    """
    Function to generate a cookie_secret
    """
    return(secrets.token_hex(16))


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
            except Exception:
                return {"error": error_msg, "status_code": response.status_code}

    except requests.exceptions.Timeout:
        error_msg = "HubSpot request timed out"
        safe_log('error', f'Error in update_hubspot_handler: {error_msg}', event='error', handler='update_hubspot_handler')
        print(error_msg)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"HubSpot update error: {str(e)}"
        safe_log('error', f'Error in update_hubspot_handler: {error_msg}', event='error', handler='update_hubspot_handler')
        print(error_msg)
        return {"error": error_msg}

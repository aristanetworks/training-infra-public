#!/usr/bin/env python3

"""
CVP Inventory Test
Tests CloudVision Portal device inventory and streaming status
"""

import requests
import logging
import urllib3
import yaml
import os

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration file path
CONFIG_PATH = '/etc/atd/UNIT_TEST_CONFIG.yaml'

# Global config
config = None

# CVP Configuration (will be loaded from config)
CVP_HOST = None
CVP_USERNAME = None
CVP_PASSWORD = None  # Will be loaded from ACCESS_INFO.yaml
ACCESS_INFO_PATH = None
TOPOLOGIES_BASE_PATH = None


def load_config(file_path=CONFIG_PATH):
    """
    Load configuration from UNIT_TEST_CONFIG.yaml

    Args:
        file_path: Path to config file

    Returns:
        dict: Configuration data or None if failed
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"Config file not found: {file_path}")
            return None

        with open(file_path, 'r') as f:
            cfg = yaml.safe_load(f)

        logger.info(f"✓ Configuration loaded from {file_path}")
        return cfg

    except Exception as e:
        logger.error(f"Failed to load config: {str(e)}")
        return None


def load_access_info(file_path=None):
    """
    Load ACCESS_INFO.yaml file

    Args:
        file_path: Path to ACCESS_INFO.yaml

    Returns:
        dict: Parsed YAML data or None if failed
    """
    if file_path is None:
        file_path = ACCESS_INFO_PATH

    try:
        logger.info(f"Loading ACCESS_INFO from: {file_path}")

        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        with open(file_path, 'r') as f:
            access_info = yaml.safe_load(f)

        logger.info("✓ ACCESS_INFO loaded successfully")
        return access_info

    except Exception as e:
        logger.error(f"Failed to load ACCESS_INFO: {str(e)}")
        return None


def load_cvp_credentials(access_info):
    """
    Load CVP web API credentials (arista user) from ACCESS_INFO data

    Args:
        access_info: Parsed ACCESS_INFO.yaml data

    Returns:
        tuple: (username, password) or (None, None) if not found
    """
    try:
        logger.info("Loading CVP web API credentials from ACCESS_INFO")

        # Navigate to login_info -> cvp -> shell -> find arista user credentials
        login_info = access_info.get('login_info', {})
        cvp_info = login_info.get('cvp', {})
        shell_logins = cvp_info.get('shell', [])

        # shell contains a list of login credentials
        if isinstance(shell_logins, list):
            for login in shell_logins:
                if login.get('user') == 'arista':
                    username = login.get('user')
                    password = login.get('pw')
                    logger.info("✓ CVP web API credentials loaded successfully")
                    return username, password
        elif isinstance(shell_logins, dict):
            # Handle case where it's a single dict
            if shell_logins.get('user') == 'arista':
                username = shell_logins.get('user')
                password = shell_logins.get('pw')
                logger.info("✓ CVP web API credentials loaded successfully")
                return username, password

        logger.error("CVP credentials for user 'arista' not found in ACCESS_INFO.yaml")
        return None, None

    except Exception as e:
        logger.error(f"Failed to load CVP credentials: {str(e)}")
        return None, None


def get_topology(access_info):
    """
    Get topology name from ACCESS_INFO

    Args:
        access_info: Parsed ACCESS_INFO.yaml data

    Returns:
        str: Topology name or None if not found
    """
    topology = access_info.get('topology')

    if topology:
        logger.info(f"✓ Topology found: {topology}")
    else:
        logger.error("✗ Topology not found in ACCESS_INFO")

    return topology


def load_topology_file(topology, topologies_base_path=TOPOLOGIES_BASE_PATH):
    """
    Load topo_build.yml from topology directory

    Args:
        topology: Topology name
        topologies_base_path: Base path for topologies

    Returns:
        dict: Parsed topology data or None if failed
    """
    try:
        topo_file_path = os.path.join(topologies_base_path, topology, 'topo_build.yml')
        logger.info(f"Loading topology file from: {topo_file_path}")

        if not os.path.exists(topo_file_path):
            logger.error(f"Topology file not found: {topo_file_path}")
            return None

        with open(topo_file_path, 'r') as f:
            topo_data = yaml.safe_load(f)

        logger.info(f"✓ Topology file loaded successfully")
        logger.info(f"Topology sections: {list(topo_data.keys()) if topo_data else 'None'}")

        return topo_data

    except Exception as e:
        logger.error(f"Failed to load topology file: {str(e)}")
        return None


def count_devices_in_topology(topo_data):
    """
    Count number of devices in topology file

    Args:
        topo_data: Parsed topology data from topo_build.yml

    Returns:
        int: Number of devices or 0 if failed
    """
    try:
        if not topo_data:
            logger.error("Topology data is None")
            return 0

        # Count devices in the 'nodes' section
        nodes = topo_data.get('nodes', [])

        if not isinstance(nodes, list):
            logger.error("'nodes' is not a list in topology file")
            return 0

        device_count = len(nodes)
        logger.info(f"✓ Found {device_count} devices in topology file")

        # Log device names for verification
        device_names = [node.get('name', 'Unknown') for node in nodes]
        logger.info(f"Devices in topology: {', '.join(device_names)}")

        return device_count

    except Exception as e:
        logger.error(f"Failed to count devices in topology: {str(e)}")
        return 0


def validate_device_count(cvp_device_count, topo_device_count):
    """
    Validate that CVP device count matches topology device count

    Args:
        cvp_device_count: Number of devices in CVP inventory
        topo_device_count: Number of devices in topology file

    Returns:
        bool: True if counts match, False otherwise
    """
    logger.info("="*60)
    logger.info("Device Count Validation")
    logger.info("="*60)
    logger.info(f"CVP Inventory Count: {cvp_device_count}")
    logger.info(f"Topology File Count: {topo_device_count}")

    if cvp_device_count == topo_device_count:
        logger.info(f"✓ Device counts match: {cvp_device_count} devices")
        logger.info("="*60)
        return True
    else:
        logger.error(f"✗ Device count mismatch: CVP has {cvp_device_count}, topology has {topo_device_count}")
        logger.info("="*60)
        return False


def cvp_login(host, username, password):
    """
    Login to CVP and get session cookie

    Args:
        host: CVP IP address
        username: CVP username
        password: CVP password

    Returns:
        requests.Session: Authenticated session or None if failed
    """
    try:
        logger.info(f"Logging into CVP at {host} as {username}")

        session = requests.Session()
        session.verify = False  # Disable SSL verification

        # CVP login endpoint
        login_url = f"https://{host}/cvpservice/login/authenticate.do"

        payload = {
            'userId': username,
            'password': password
        }

        response = session.post(login_url, json=payload, timeout=10)

        if response.status_code == 200:
            result = response.json()
            if result.get('sessionId'):
                logger.info("✓ CVP login successful")
                return session
            else:
                logger.error(f"✗ CVP login failed: {result.get('errorMessage', 'Unknown error')}")
                return None
        else:
            logger.error(f"✗ CVP login failed with status code: {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"✗ CVP login error: {str(e)}")
        return None


def get_cvp_inventory(session, host):
    """
    Get device inventory from CVP

    Args:
        session: Authenticated CVP session
        host: CVP IP address

    Returns:
        list: List of devices or None if failed
    """
    try:
        logger.info("Retrieving CVP device inventory")

        # CVP inventory endpoint
        inventory_url = f"https://{host}/cvpservice/inventory/devices"

        response = session.get(inventory_url, timeout=10)

        if response.status_code == 200:
            devices = response.json()
            logger.info(f"✓ Retrieved inventory: {len(devices)} devices")
            return devices
        else:
            logger.error(f"✗ Failed to get inventory: status code {response.status_code}")
            return None

    except Exception as e:
        logger.error(f"✗ Failed to get inventory: {str(e)}")
        return None


def check_streaming_status(devices):
    """
    Check streaming status of all devices

    Args:
        devices: List of device dictionaries from CVP

    Returns:
        tuple: (total_devices, streaming_count, non_streaming_devices)
    """
    if not devices:
        logger.error("No devices to check")
        return 0, 0, []

    total_devices = len(devices)
    streaming_count = 0
    non_streaming_devices = []

    logger.info("="*60)
    logger.info("Device Streaming Status:")
    logger.info("="*60)

    for device in devices:
        device_name = device.get('hostname', device.get('fqdn', 'Unknown'))
        streaming_status = device.get('streamingStatus', 'unknown')

        if streaming_status.lower() == 'active':
            streaming_count += 1
            logger.info(f"✓ {device_name}: {streaming_status}")
        else:
            non_streaming_devices.append({
                'name': device_name,
                'status': streaming_status,
                'ip': device.get('ipAddress', 'N/A')
            })
            logger.warning(f"⚠ {device_name}: {streaming_status}")

    logger.info("="*60)

    return total_devices, streaming_count, non_streaming_devices


def main():
    """Main function for CVP inventory testing"""
    global config, CVP_PASSWORD, CVP_HOST, CVP_USERNAME, ACCESS_INFO_PATH, TOPOLOGIES_BASE_PATH

    # Load configuration
    config = load_config()
    if not config:
        logger.error("Failed to load configuration. Exiting.")
        return 1

    # Extract configuration
    cvp_config = config.get('cvp', {})
    CVP_HOST = cvp_config.get('host', '192.168.0.5')

    paths_config = config.get('paths', {})
    ACCESS_INFO_PATH = paths_config.get('access_info', '/etc/atd/ACCESS_INFO.yaml')
    TOPOLOGIES_BASE_PATH = paths_config.get('topologies', '/opt/atd/topologies/')

    # Load ACCESS_INFO
    logger.info("="*60)
    logger.info("Loading ACCESS_INFO")
    logger.info("="*60)
    access_info = load_access_info()
    if not access_info:
        logger.error("Failed to load ACCESS_INFO. Exiting.")
        return 1

    # Load CVP credentials from ACCESS_INFO
    logger.info("\n" + "="*60)
    logger.info("Loading CVP Credentials")
    logger.info("="*60)
    CVP_USERNAME, CVP_PASSWORD = load_cvp_credentials(access_info)
    if not CVP_USERNAME or not CVP_PASSWORD:
        logger.error("Failed to load CVP credentials. Exiting.")
        return 1

    logger.info("\n" + "="*60)
    logger.info("Starting CVP Inventory Test")
    logger.info("="*60)
    logger.info(f"CVP Host: {CVP_HOST}")
    logger.info(f"CVP Username: {CVP_USERNAME}")
    logger.info("="*60)

    # Get topology
    topology = get_topology(access_info)
    if not topology:
        logger.error("Failed to get topology. Exiting.")
        return 1

    # Load topology file
    logger.info("\n" + "="*60)
    logger.info("Loading Topology File")
    logger.info("="*60)
    topo_data = load_topology_file(topology, TOPOLOGIES_BASE_PATH)
    if not topo_data:
        logger.error("Failed to load topology file. Exiting.")
        return 1

    # Count devices in topology
    topo_device_count = count_devices_in_topology(topo_data)

    # Login to CVP
    logger.info("\n" + "="*60)
    logger.info("Step 1: CVP Login")
    logger.info("="*60)
    session = cvp_login(CVP_HOST, CVP_USERNAME, CVP_PASSWORD)

    if not session:
        logger.error("CVP login failed. Exiting.")
        return 1

    # Get device inventory
    logger.info("\n" + "="*60)
    logger.info("Step 2: Get Device Inventory")
    logger.info("="*60)
    devices = get_cvp_inventory(session, CVP_HOST)

    if devices is None:
        logger.error("Failed to retrieve device inventory. Exiting.")
        return 1

    # Validate device count
    logger.info("\n" + "="*60)
    logger.info("Step 3: Validate Device Count")
    logger.info("="*60)
    device_count_valid = validate_device_count(len(devices), topo_device_count)

    # Check streaming status
    logger.info("\n" + "="*60)
    logger.info("Step 4: Check Streaming Status")
    logger.info("="*60)
    total_devices, streaming_count, non_streaming = check_streaming_status(devices)

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info(f"Topology File Devices: {topo_device_count}")
    logger.info(f"CVP Inventory Devices: {total_devices}")
    logger.info(f"Device Count Match: {'PASS' if device_count_valid else 'FAIL'}")
    logger.info(f"Streaming Active: {streaming_count}")
    logger.info(f"Not Streaming: {len(non_streaming)}")

    if non_streaming:
        logger.warning("\nDevices NOT streaming:")
        for device in non_streaming:
            logger.warning(f"  - {device['name']} ({device['ip']}): {device['status']}")

    all_streaming = (streaming_count == total_devices and total_devices > 0)

    logger.info("="*60)
    logger.info(f"Streaming Status: {'PASS - All devices streaming' if all_streaming else 'FAIL - Not all devices streaming'}")
    logger.info("="*60)

    # Exit with appropriate code
    if device_count_valid and all_streaming:
        logger.info("CVP inventory test PASSED")
        return 0
    else:
        logger.error("CVP inventory test FAILED")
        return 1


if __name__ == "__main__":
    exit(main())

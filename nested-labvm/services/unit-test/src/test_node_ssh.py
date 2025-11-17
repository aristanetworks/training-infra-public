#!/usr/bin/env python3

"""
Node SSH Connection Test
Tests SSH connectivity to all topology nodes
"""

import paramiko
import logging
import socket
import yaml
import os

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

# Node SSH Configuration (will be loaded from config)
ACCESS_INFO_PATH = None
TOPOLOGIES_BASE_PATH = None
NODE_USERNAME = 'arista'
NODE_PASSWORD = None
SSH_PORT = 22
SSH_TIMEOUT = 10


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


def get_node_password(access_info):
    """
    Get node password from ACCESS_INFO

    Args:
        access_info: Parsed ACCESS_INFO.yaml data

    Returns:
        str: Password or None if not found
    """
    try:
        logger.info("Loading node password from ACCESS_INFO")

        # Get password from login_info -> jump_host
        login_info = access_info.get('login_info', {})
        jump_host = login_info.get('jump_host', {})
        password = jump_host.get('pw')

        if password:
            logger.info("✓ Node password loaded successfully")
            return password
        else:
            logger.error("Password not found in login_info -> jump_host -> pw")
            return None

    except Exception as e:
        logger.error(f"Failed to load node password: {str(e)}")
        return None


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


def load_topology_file(topology, topologies_base_path=None):
    """
    Load topo_build.yml from topology directory

    Args:
        topology: Topology name
        topologies_base_path: Base path for topologies

    Returns:
        dict: Parsed topology data or None if failed
    """
    if topologies_base_path is None:
        topologies_base_path = TOPOLOGIES_BASE_PATH

    try:
        topo_file_path = os.path.join(topologies_base_path, topology, 'topo_build.yml')
        logger.info(f"Loading topology file from: {topo_file_path}")

        if not os.path.exists(topo_file_path):
            logger.error(f"Topology file not found: {topo_file_path}")
            return None

        with open(topo_file_path, 'r') as f:
            topo_data = yaml.safe_load(f)

        logger.info(f"✓ Topology file loaded successfully")
        return topo_data

    except Exception as e:
        logger.error(f"Failed to load topology file: {str(e)}")
        return None


def get_node_ips(topo_data):
    """
    Extract node IPs from topology data

    Args:
        topo_data: Parsed topology data from topo_build.yml

    Returns:
        list: List of dicts with node info (name, ip) or empty list if failed
    """
    try:
        if not topo_data:
            logger.error("Topology data is None")
            return []

        nodes = topo_data.get('nodes', [])

        if not isinstance(nodes, list):
            logger.error("'nodes' is not a list in topology file")
            return []

        node_list = []
        for node_entry in nodes:
            # Each node is a dict with node name as key
            # Example: {'A1': {'ip_addr': '192.168.0.21', 'sys_mac': '...', ...}}
            if isinstance(node_entry, dict):
                for node_name, node_data in node_entry.items():
                    if isinstance(node_data, dict):
                        node_ip = node_data.get('ip_addr', node_data.get('ip', None))

                        if node_ip:
                            node_list.append({
                                'name': node_name,
                                'ip': node_ip
                            })
                            logger.info(f"Found node: {node_name} - {node_ip}")
                        else:
                            logger.warning(f"Node {node_name} has no IP address")

        logger.info(f"✓ Found {len(node_list)} nodes with IP addresses")
        return node_list

    except Exception as e:
        logger.error(f"Failed to extract node IPs: {str(e)}")
        return []


def test_node_ssh(node_name, node_ip, username, password, port=SSH_PORT, timeout=SSH_TIMEOUT):
    """
    Test SSH connection to a node

    Args:
        node_name: Name of the node
        node_ip: IP address of the node
        username: SSH username
        password: SSH password
        port: SSH port (default: 22)
        timeout: Connection timeout in seconds

    Returns:
        bool: True if connection successful, False otherwise
    """
    ssh_client = None

    try:
        logger.info(f"Testing SSH to {node_name} ({node_ip})")

        # Create SSH client
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect to node
        ssh_client.connect(
            hostname=node_ip,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )

        # Test command execution
        stdin, stdout, stderr = ssh_client.exec_command("show version | grep Software")
        output = stdout.read().decode('utf-8').strip()

        if output:
            logger.info(f"✓ SSH to {node_name} ({node_ip}) successful")
            logger.info(f"  {output}")
            ssh_client.close()
            return True
        else:
            logger.warning(f"⚠ SSH to {node_name} ({node_ip}) connected but command failed")
            ssh_client.close()
            return True  # Still consider it a pass if we can connect

    except paramiko.AuthenticationException:
        logger.error(f"✗ SSH authentication failed for {node_name} ({node_ip})")
        if ssh_client:
            ssh_client.close()
        return False

    except socket.timeout:
        logger.error(f"✗ SSH connection to {node_name} ({node_ip}) timed out")
        if ssh_client:
            ssh_client.close()
        return False

    except Exception as e:
        logger.error(f"✗ SSH to {node_name} ({node_ip}) failed: {str(e)}")
        if ssh_client:
            ssh_client.close()
        return False


def main():
    """Main function for node SSH testing"""
    global config, ACCESS_INFO_PATH, TOPOLOGIES_BASE_PATH, NODE_PASSWORD

    logger.info("="*60)
    logger.info("Starting Node SSH Connection Test")
    logger.info("="*60)

    # Load configuration
    config = load_config()
    if not config:
        logger.error("Failed to load configuration. Exiting.")
        return 1

    # Extract paths configuration
    paths_config = config.get('paths', {})
    ACCESS_INFO_PATH = paths_config.get('access_info', '/etc/atd/ACCESS_INFO.yaml')
    TOPOLOGIES_BASE_PATH = paths_config.get('topologies', '/opt/atd/topologies/')

    # Load ACCESS_INFO
    logger.info("\n" + "="*60)
    logger.info("Loading ACCESS_INFO")
    logger.info("="*60)
    access_info = load_access_info()
    if not access_info:
        logger.error("Failed to load ACCESS_INFO. Exiting.")
        return 1

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

    # Get node IPs
    logger.info("\n" + "="*60)
    logger.info("Extracting Node Information")
    logger.info("="*60)
    nodes = get_node_ips(topo_data)
    if not nodes:
        logger.error("No nodes found in topology. Exiting.")
        return 1

    # Get node password
    logger.info("\n" + "="*60)
    logger.info("Loading Node Credentials")
    logger.info("="*60)
    NODE_PASSWORD = get_node_password(access_info)
    if not NODE_PASSWORD:
        logger.error("Failed to load node password. Exiting.")
        return 1

    # Test SSH to each node
    logger.info("\n" + "="*60)
    logger.info("Testing SSH Connections")
    logger.info("="*60)

    results = []
    for node in nodes:
        success = test_node_ssh(
            node['name'],
            node['ip'],
            NODE_USERNAME,
            NODE_PASSWORD
        )
        results.append({
            'name': node['name'],
            'ip': node['ip'],
            'success': success
        })

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info(f"Total Nodes: {len(results)}")

    successful = [r for r in results if r['success']]
    failed = [r for r in results if not r['success']]

    logger.info(f"Successful Connections: {len(successful)}")
    logger.info(f"Failed Connections: {len(failed)}")

    if successful:
        logger.info("\n✓ Successful nodes:")
        for node in successful:
            logger.info(f"  - {node['name']} ({node['ip']})")

    if failed:
        logger.error("\n✗ Failed nodes:")
        for node in failed:
            logger.error(f"  - {node['name']} ({node['ip']})")

    logger.info("="*60)

    # Exit with appropriate code
    if len(failed) == 0:
        logger.info("All node SSH tests PASSED")
        return 0
    else:
        logger.error("Some node SSH tests FAILED")
        return 1


if __name__ == "__main__":
    exit(main())

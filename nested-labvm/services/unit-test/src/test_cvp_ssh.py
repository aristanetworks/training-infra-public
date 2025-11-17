#!/usr/bin/env python3

"""
CVP SSH Connection Test
Tests SSH connectivity to CVP server
"""

import paramiko
import logging
import socket
import time
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

# CVP SSH Configuration (will be loaded from config and ACCESS_INFO)
CVP_HOST = None
CVP_USERNAME = None
CVP_PASSWORD = None
CVP_SSH_PORT = None
SSH_TIMEOUT = None
ACCESS_INFO_PATH = None


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


def load_cvp_ssh_credentials(access_info):
    """
    Load CVP SSH credentials (root user) from ACCESS_INFO data

    Args:
        access_info: Parsed ACCESS_INFO.yaml data

    Returns:
        tuple: (username, password) or (None, None) if not found
    """
    try:
        logger.info("Loading CVP SSH credentials from ACCESS_INFO")

        # Navigate to login_info -> cvp -> shell -> find root user credentials
        login_info = access_info.get('login_info', {})
        cvp_info = login_info.get('cvp', {})
        shell_logins = cvp_info.get('shell', [])

        # shell contains a list of login credentials
        if isinstance(shell_logins, list):
            for login in shell_logins:
                if login.get('user') == 'root':
                    username = login.get('user')
                    password = login.get('pw')
                    logger.info("✓ CVP SSH credentials loaded successfully")
                    return username, password
        elif isinstance(shell_logins, dict):
            # Handle case where it's a single dict
            if shell_logins.get('user') == 'root':
                username = shell_logins.get('user')
                password = shell_logins.get('pw')
                logger.info("✓ CVP SSH credentials loaded successfully")
                return username, password

        logger.error("CVP SSH credentials for user 'root' not found in ACCESS_INFO.yaml")
        return None, None

    except Exception as e:
        logger.error(f"Failed to load CVP SSH credentials: {str(e)}")
        return None, None


def test_cvp_network_connectivity(host=CVP_HOST, port=CVP_SSH_PORT, timeout=SSH_TIMEOUT):
    """
    Test basic network connectivity to CVP

    Args:
        host: CVP IP address
        port: SSH port (default: 22)
        timeout: Connection timeout in seconds

    Returns:
        bool: True if host is reachable, False otherwise
    """
    try:
        logger.info(f"Testing network connectivity to {host}:{port}")

        # Create a socket connection
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)

        result = sock.connect_ex((host, port))
        sock.close()

        if result == 0:
            logger.info(f"✓ Network connectivity to {host}:{port} successful")
            return True
        else:
            logger.error(f"✗ Network connectivity to {host}:{port} failed")
            return False

    except socket.timeout:
        logger.error(f"✗ Connection to {host}:{port} timed out")
        return False
    except Exception as e:
        logger.error(f"✗ Network connectivity test failed: {str(e)}")
        return False


def test_cvp_ssh_login(host=CVP_HOST, username=CVP_USERNAME, password=CVP_PASSWORD,
                       port=CVP_SSH_PORT, timeout=SSH_TIMEOUT):
    """
    Test SSH login to CVP server

    Args:
        host: CVP IP address
        username: SSH username
        password: SSH password
        port: SSH port (default: 22)
        timeout: Connection timeout in seconds

    Returns:
        tuple: (success: bool, ssh_client: paramiko.SSHClient or None)
    """
    ssh_client = None

    try:
        logger.info(f"Attempting SSH login to {username}@{host}:{port}")

        # Create SSH client
        ssh_client = paramiko.SSHClient()

        # Auto-add host key (for testing purposes)
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        # Connect to CVP
        ssh_client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False
        )

        logger.info(f"✓ SSH login successful to {username}@{host}")
        return True, ssh_client

    except paramiko.AuthenticationException:
        logger.error(f"✗ SSH authentication failed for {username}@{host}")
        logger.error("Invalid username or password")
        if ssh_client:
            ssh_client.close()
        return False, None

    except paramiko.SSHException as e:
        logger.error(f"✗ SSH connection error: {str(e)}")
        if ssh_client:
            ssh_client.close()
        return False, None

    except socket.timeout:
        logger.error(f"✗ SSH connection to {host}:{port} timed out")
        if ssh_client:
            ssh_client.close()
        return False, None

    except Exception as e:
        logger.error(f"✗ SSH login failed: {str(e)}")
        if ssh_client:
            ssh_client.close()
        return False, None


def test_cvp_ram_usage(ssh_client, threshold_percent=80):
    """
    Check CVP RAM usage and alert if above threshold

    Args:
        ssh_client: Active paramiko.SSHClient connection
        threshold_percent: RAM usage threshold percentage (default: 80)

    Returns:
        tuple: (success: bool, ram_usage_percent: float or None)
    """
    if not ssh_client:
        logger.error("✗ SSH client is None, cannot check RAM usage")
        return False, None

    try:
        logger.info(f"Checking CVP RAM usage (threshold: {threshold_percent}%)")

        # Execute free command to get memory usage
        stdin, stdout, stderr = ssh_client.exec_command("free | grep Mem")
        output = stdout.read().decode('utf-8').strip()

        if not output:
            logger.error("✗ Failed to get memory information")
            return False, None

        # Parse memory output
        # Format: Mem:  total  used  free  shared  buff/cache  available
        parts = output.split()
        if len(parts) < 3:
            logger.error("✗ Unexpected memory output format")
            return False, None

        total_mem = int(parts[1])
        used_mem = int(parts[2])
        ram_usage_percent = (used_mem / total_mem) * 100

        logger.info(f"RAM Usage: {ram_usage_percent:.2f}% ({used_mem}/{total_mem})")

        if ram_usage_percent > threshold_percent:
            logger.warning(f"⚠ WARNING: RAM usage ({ram_usage_percent:.2f}%) exceeds threshold ({threshold_percent}%)")
            return True, ram_usage_percent  # Still return True as check succeeded, but with warning
        else:
            logger.info(f"✓ RAM usage ({ram_usage_percent:.2f}%) is within acceptable limits")
            return True, ram_usage_percent

    except Exception as e:
        logger.error(f"✗ Failed to check RAM usage: {str(e)}")
        return False, None


def test_cvp_system_info(ssh_client):
    """
    Get CVP system information via SSH

    Args:
        ssh_client: Active paramiko.SSHClient connection

    Returns:
        bool: True if all info commands succeeded, False otherwise
    """
    if not ssh_client:
        logger.error("✗ SSH client is None, cannot get system info")
        return False

    logger.info("="*60)
    logger.info("Retrieving CVP System Information")
    logger.info("="*60)

    commands = {
        "Hostname": "hostname",
        "CVP Version": "cat /cvpi/version 2>/dev/null || echo 'Version file not found'",
        "Uptime": "uptime",
        "Disk Usage": "df -h / | tail -1",
        "Memory Usage": "free -h | grep Mem"
    }

    all_success = True

    for description, command in commands.items():
        try:
            stdin, stdout, stderr = ssh_client.exec_command(command)
            output = stdout.read().decode('utf-8').strip()

            logger.info(f"{description}: {output}")

        except Exception as e:
            logger.error(f"Failed to get {description}: {str(e)}")
            all_success = False

    logger.info("="*60)

    return all_success


def main():
    """Main function for CVP SSH testing"""
    global config, CVP_HOST, CVP_USERNAME, CVP_PASSWORD, CVP_SSH_PORT, SSH_TIMEOUT, ACCESS_INFO_PATH

    # Load configuration
    config = load_config()
    if not config:
        logger.error("Failed to load configuration. Exiting.")
        return 1

    # Extract CVP configuration
    cvp_config = config.get('cvp', {})
    CVP_HOST = cvp_config.get('host', '192.168.0.5')
    ssh_config = cvp_config.get('ssh', {})
    CVP_SSH_PORT = ssh_config.get('port', 22)
    SSH_TIMEOUT = ssh_config.get('timeout', 10)
    ram_threshold = cvp_config.get('ram_threshold_percent', 80)

    paths_config = config.get('paths', {})
    ACCESS_INFO_PATH = paths_config.get('access_info', '/etc/atd/ACCESS_INFO.yaml')

    # Load ACCESS_INFO
    logger.info("\n" + "="*60)
    logger.info("Loading ACCESS_INFO")
    logger.info("="*60)
    access_info = load_access_info()
    if not access_info:
        logger.error("Failed to load ACCESS_INFO. Exiting.")
        return 1

    # Load CVP SSH credentials from ACCESS_INFO
    CVP_USERNAME, CVP_PASSWORD = load_cvp_ssh_credentials(access_info)
    if not CVP_USERNAME or not CVP_PASSWORD:
        logger.error("Failed to load CVP SSH credentials. Exiting.")
        return 1

    logger.info("="*60)
    logger.info("Starting CVP SSH Connection Test")
    logger.info("="*60)
    logger.info(f"Target: {CVP_USERNAME}@{CVP_HOST}:{CVP_SSH_PORT}")
    logger.info("="*60)

    # Test 1: Network connectivity
    logger.info("\n" + "="*60)
    logger.info("Test 1: Network Connectivity")
    logger.info("="*60)
    network_test = test_cvp_network_connectivity(CVP_HOST, CVP_SSH_PORT, SSH_TIMEOUT)

    if not network_test:
        logger.error("Network connectivity test failed. Cannot proceed with SSH tests.")
        logger.info("="*60)
        logger.error("CVP SSH Test FAILED")
        logger.info("="*60)
        return 1

    # Test 2: SSH login
    logger.info("\n" + "="*60)
    logger.info("Test 2: SSH Login")
    logger.info("="*60)
    login_success, ssh_client = test_cvp_ssh_login(CVP_HOST, CVP_USERNAME, CVP_PASSWORD, CVP_SSH_PORT, SSH_TIMEOUT)

    if not login_success:
        logger.error("SSH login test failed.")
        logger.info("="*60)
        logger.error("CVP SSH Test FAILED")
        logger.info("="*60)
        return 1

    # Test 3: RAM Usage Check
    logger.info("\n" + "="*60)
    logger.info("Test 3: RAM Usage Check")
    logger.info("="*60)
    ram_check_success, ram_usage = test_cvp_ram_usage(ssh_client, threshold_percent=ram_threshold)

    # Test 4: System information
    logger.info("\n" + "="*60)
    logger.info("Test 4: System Information")
    logger.info("="*60)
    sysinfo_success = test_cvp_system_info(ssh_client)

    # Close SSH connection
    if ssh_client:
        logger.info("\nClosing SSH connection...")
        ssh_client.close()
        logger.info("✓ SSH connection closed")

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info(f"Network Connectivity: {'PASS' if network_test else 'FAIL'}")
    logger.info(f"SSH Login: {'PASS' if login_success else 'FAIL'}")
    logger.info(f"RAM Usage Check: {'PASS' if ram_check_success else 'FAIL'}")
    if ram_check_success and ram_usage is not None:
        if ram_usage > 80:
            logger.warning(f"  ⚠ RAM Usage: {ram_usage:.2f}% (exceeds 80% threshold)")
        else:
            logger.info(f"  RAM Usage: {ram_usage:.2f}%")
    logger.info(f"System Information: {'PASS' if sysinfo_success else 'FAIL'}")
    logger.info("="*60)

    # Exit with appropriate code
    if network_test and login_success and ram_check_success and sysinfo_success:
        logger.info("All CVP SSH tests PASSED")
        return 0
    else:
        logger.error("Some CVP SSH tests FAILED")
        return 1


if __name__ == "__main__":
    exit(main())

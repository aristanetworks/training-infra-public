#!/usr/bin/env python3
"""
CVP Device Re-registration Script
This script checks streaming status of devices registered to CVP
and re-registers devices with inactive streaming status.

Uses the cvprac library for proper CVP API integration.
"""

from cvprac.cvp_client import CvpClient
from cvprac.cvp_client_errors import CvpApiError, CvpRequestError
import urllib3
import sys
import time
import argparse
import yaml
import socket
import logging
import subprocess
from datetime import datetime
from typing import List, Dict, Optional

# Suppress gRPC fork warnings to reduce log noise
import os
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GRPC_TRACE'] = ''

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Try to import Cloud Logging (optional dependency)
try:
    from google.cloud import logging as cloud_logging
    CLOUD_LOGGING_AVAILABLE = True
except ImportError:
    CLOUD_LOGGING_AVAILABLE = False

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration file path
ACCESS_INFO_FILE = "/etc/atd/ACCESS_INFO.yaml"


# =============================================================================
# Cloud Logging Setup
# =============================================================================

class CloudLoggingManager:
    """Manages Google Cloud Logging integration"""

    _instance = None
    _initialized = False
    _setup_done = False  # Track if setup has been called

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if CloudLoggingManager._initialized:
            return

        self.client = None
        self.hostname = socket.gethostname()
        self.cloud_handler = None
        self.logger = logging.getLogger(self.__class__.__name__)

        CloudLoggingManager._initialized = True

    def setup(self, service_name: str = 'cvp-device-registration', additional_labels: Dict[str, str] = None) -> bool:
        """
        Setup Cloud Logging with structured labels.

        Args:
            service_name: Name of the service (e.g., 'cvp-device-registration')
            additional_labels: Additional labels to include in logs

        Returns:
            True if setup successful, False otherwise
        """
        # If setup has already been called, skip to avoid duplicate handlers
        if CloudLoggingManager._setup_done:
            self.logger.debug(f"Cloud Logging already setup, skipping duplicate setup for {service_name}")
            return True

        if not CLOUD_LOGGING_AVAILABLE:
            self.logger.warning("Cloud Logging not available (google-cloud-logging not installed)")
            return False

        try:
            self.client = cloud_logging.Client()

            # Build labels
            labels = {
                'service': service_name,
                'lab_name': self.hostname,
                'environment': 'production',
                'component': 'cvp-device-registration'
            }

            if additional_labels:
                labels.update(additional_labels)

            # Create Cloud Logging handler
            self.cloud_handler = self.client.get_default_handler(labels=labels)
            self.cloud_handler.setLevel(logging.INFO)

            # Add to root logger so all loggers use it
            root_logger = logging.getLogger()
            root_logger.addHandler(self.cloud_handler)

            # Mark setup as done to prevent duplicate handlers
            CloudLoggingManager._setup_done = True

            self.logger.info(f"Cloud Logging enabled for service: {service_name}, lab: {self.hostname}")
            return True

        except Exception as e:
            self.logger.warning(f"Cloud Logging setup failed (continuing with local logs): {e}")
            return False

    def log_structured(self, message: str, severity: str = 'INFO',
                       labels: Dict[str, str] = None, **kwargs) -> None:
        """
        Log a structured message directly to Cloud Logging.

        Args:
            message: Log message
            severity: Log severity (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            labels: Additional labels for this specific log entry
            **kwargs: Additional structured data to include
        """
        if not CLOUD_LOGGING_AVAILABLE or not self.client:
            # Fall back to standard logging
            log_level = getattr(logging, severity.upper(), logging.INFO)
            logger.log(log_level, message)
            return

        try:
            cloud_logger = self.client.logger('cvp-device-registration')

            # Merge base labels with provided labels
            all_labels = {
                'lab_name': self.hostname,
                'component': 'cvp-device-registration'
            }
            if labels:
                all_labels.update(labels)

            # Create structured payload
            payload = {
                'message': message,
                'hostname': self.hostname,
                'timestamp': datetime.utcnow().isoformat(),
                **kwargs
            }

            cloud_logger.log_struct(payload, severity=severity, labels=all_labels)

        except Exception as e:
            # Fall back to standard logging
            logger.warning(f"Cloud logging failed, using local: {e}")
            log_level = getattr(logging, severity.upper(), logging.INFO)
            logger.log(log_level, message)


def reset_cvp_updater_container() -> bool:
    """
    Reset the atd-cvpupdater container by clearing its state file and restarting it.
    This forces the container to re-sync with CVP after device re-registration.

    Returns:
        bool: True if successful, False otherwise
    """
    print("\n" + "=" * 80)
    print("🔄 RESETTING CVP UPDATER CONTAINER")
    print("=" * 80)

    try:
        # Step 1: Remove the .cvpState.txt file inside the container
        print("   Removing CVP state file...")
        rm_cmd = [
            "sudo", "docker", "exec", "atd-cvpupdater",
            "rm", "-f", "/home/arista/CVP_DATA/.cvpState.txt"
        ]
        result = subprocess.run(rm_cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            # Check if container doesn't exist
            if "No such container" in result.stderr:
                print("   ⚠ atd-cvpupdater container not found - skipping reset")
                logger.warning("atd-cvpupdater container not found")
                return False
            else:
                print(f"   ⚠ Warning removing state file: {result.stderr.strip()}")
                # Continue anyway - file might not exist
        else:
            print("   ✓ CVP state file removed")

        # Step 2: Restart the container
        print("   Restarting atd-cvpupdater container...")
        restart_cmd = ["sudo", "docker", "restart", "atd-cvpupdater"]
        result = subprocess.run(restart_cmd, capture_output=True, text=True, timeout=60)

        if result.returncode != 0:
            print(f"   ✗ Failed to restart container: {result.stderr.strip()}")
            logger.error(f"Failed to restart atd-cvpupdater: {result.stderr}")
            return False

        print("   ✓ atd-cvpupdater container restarted successfully")
        logger.info("atd-cvpupdater container reset and restarted successfully")

        return True

    except subprocess.TimeoutExpired:
        print("   ✗ Command timed out")
        logger.error("Docker command timed out during cvpupdater reset")
        return False
    except FileNotFoundError:
        print("   ✗ Docker command not found - are you running on the correct host?")
        logger.error("Docker command not found")
        return False
    except Exception as e:
        print(f"   ✗ Error resetting cvpupdater: {e}")
        logger.error(f"Error resetting cvpupdater: {e}")
        return False


def get_cvp_credentials_from_file(username: str = "arista") -> Optional[Dict[str, str]]:
    """
    Read CVP credentials from ACCESS_INFO.yaml file.

    Args:
        username: CVP username to lookup (default: arista)

    Returns:
        Dictionary with 'username' and 'password', or None if not found
    """
    try:
        with open(ACCESS_INFO_FILE, 'r') as f:
            config = yaml.safe_load(f)

        # Navigate to login_info.cvp.shell
        cvp_shell = config.get('login_info', {}).get('cvp', {}).get('shell', [])

        # Shell is a list of user credentials
        for entry in cvp_shell:
            if entry.get('user') == username:
                return {
                    'username': username,
                    'password': entry.get('pw')
                }

        print(f"⚠ Warning: User '{username}' not found in {ACCESS_INFO_FILE}")
        return None

    except FileNotFoundError:
        print(f"⚠ Warning: {ACCESS_INFO_FILE} not found")
        return None
    except yaml.YAMLError as e:
        print(f"⚠ Warning: Error parsing {ACCESS_INFO_FILE}: {e}")
        return None
    except Exception as e:
        print(f"⚠ Warning: Error reading credentials: {e}")
        return None


def get_cvp_host_from_file() -> Optional[str]:
    """
    Read CVP host IP from ACCESS_INFO.yaml file.

    Returns:
        CVP host IP address or None if not found
    """
    try:
        with open(ACCESS_INFO_FILE, 'r') as f:
            config = yaml.safe_load(f)

        # Navigate to nodes.cvp[0].ip
        cvp_nodes = config.get('nodes', {}).get('cvp', [])
        if cvp_nodes and len(cvp_nodes) > 0:
            return cvp_nodes[0].get('ip')

        return None

    except Exception:
        return None


class CVPDeviceManager:
    def __init__(self, cvp_host: str, username: str, password: str):
        """
        Initialize CVP device manager.

        Args:
            cvp_host: CVP server IP or hostname
            username: CVP username
            password: CVP password
        """
        self.cvp_host = cvp_host
        self.username = username
        self.password = password
        self.client = CvpClient()
        self.undefined_container_key = None

        # Initialize Cloud Logging
        self.cloud_logging = CloudLoggingManager()
        self.cloud_logging.setup(
            service_name='cvp-device-registration',
            additional_labels={'operation': 'device-management', 'cvp_host': cvp_host}
        )

    def connect(self, max_retries: int = 1, retry_interval: int = 30) -> bool:
        """
        Connect to CVP and authenticate.

        Args:
            max_retries: Maximum number of connection attempts (default: 1)
            retry_interval: Seconds to wait between retries (default: 30)

        Returns:
            bool: True if connection successful, False otherwise
        """
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1:
                    print(f"⏳ Attempt {attempt}/{max_retries}: Connecting to CVP...")

                self.client.connect(
                    nodes=[self.cvp_host],
                    username=self.username,
                    password=self.password,
                    protocol='https',
                    port=443,
                    connect_timeout=10,   # 10 second connection timeout
                    request_timeout=30    # 30 second request timeout (default)
                )
                print(f"✓ Successfully connected to CVP at {self.cvp_host}")

                # Log successful connection to Cloud Logging
                self.cloud_logging.log_structured(
                    f"Successfully connected to CVP at {self.cvp_host}",
                    severity='INFO',
                    labels={'service': 'cvp-device-registration', 'event': 'connection-success'},
                    cvp_host=self.cvp_host,
                    username=self.username,
                    attempts=attempt
                )
                return True

            except (CvpApiError, CvpRequestError, Exception) as e:
                if attempt < max_retries:
                    print(f"⚠ Connection attempt {attempt}/{max_retries} failed: {e}")
                    print(f"   Waiting {retry_interval} seconds before retry...")
                    time.sleep(retry_interval)
                    # Create a new client for retry
                    self.client = CvpClient()
                else:
                    print(f"✗ Connection failed after {max_retries} attempts: {e}")

                    # Log connection failure to Cloud Logging
                    self.cloud_logging.log_structured(
                        f"CVP connection failed after {max_retries} attempts: {e}",
                        severity='ERROR',
                        labels={'service': 'cvp-device-registration', 'event': 'connection-failed'},
                        cvp_host=self.cvp_host,
                        username=self.username,
                        error=str(e),
                        attempts=attempt
                    )
                    return False

        return False

    def get_undefined_container(self) -> bool:
        """
        Get the Undefined container key needed for device operations.

        Returns:
            bool: True if container found, False otherwise
        """
        try:
            containers = self.client.api.get_containers()

            for container in containers.get('data', []):
                if container.get('Name') == 'Undefined':
                    self.undefined_container_key = container.get('Key')
                    return True

            print("✗ Could not find Undefined container")
            return False

        except Exception as e:
            print(f"✗ Error getting containers: {e}")
            return False

    def get_devices(self) -> List[Dict]:
        """
        Get all devices registered to CVP.

        Returns:
            List of device dictionaries
        """
        try:
            devices = self.client.api.get_inventory()
            print(f"✓ Retrieved {len(devices)} devices from CVP")
            return devices

        except Exception as e:
            print(f"✗ Failed to get devices: {e}")
            return []

    def get_inactive_streaming_devices(self, devices: List[Dict]) -> List[Dict]:
        """
        Filter devices with inactive streaming status.

        Args:
            devices: List of all devices

        Returns:
            List of devices with inactive streaming
        """
        inactive_devices = [
            device for device in devices
            if device.get('streamingStatus') == 'inactive'
        ]

        print(f"\n📊 Streaming Status Summary:")
        print(f"   Total devices: {len(devices)}")
        print(f"   Active streaming: {len(devices) - len(inactive_devices)}")
        print(f"   Inactive streaming: {len(inactive_devices)}")

        # Log device status to Cloud Logging
        self.cloud_logging.log_structured(
            f"Device streaming status check: {len(inactive_devices)} inactive out of {len(devices)} total",
            severity='INFO',
            labels={'service': 'cvp-device-registration', 'event': 'status-check'},
            total_devices=len(devices),
            active_devices=len(devices) - len(inactive_devices),
            inactive_devices=len(inactive_devices)
        )

        return inactive_devices

    def reregister_device(self, device_ip: str, device_hostname: str) -> bool:
        """
        Re-register a device to CVP by adding it to inventory.
        This triggers device re-onboarding and activates streaming.

        Args:
            device_ip: Device IP address
            device_hostname: Device hostname

        Returns:
            bool: True if request was sent successfully, False otherwise
        """
        try:
            # Use the standard add_device_to_inventory with wait=False
            # This is the proven working approach
            self.client.api.add_device_to_inventory(
                device_ip=device_ip,
                parent_name='Undefined',
                parent_key=self.undefined_container_key,
                wait=False
            )

            print(f"  ✓ Re-registered {device_hostname} ({device_ip})")
            self.cloud_logging.log_structured(
                f"Successfully re-registered device {device_hostname}",
                severity='INFO',
                labels={'service': 'cvp-device-registration', 'event': 'device-reregistered'},
                device_hostname=device_hostname,
                device_ip=device_ip
            )
            return True

        except Exception as e:
            error_str = str(e).lower()

            # Check if this is a timeout error - registration often succeeds despite timeout
            if 'timeout' in error_str or 'timed out' in error_str or 'read timed out' in error_str:
                print(f"  ✓ Request sent for {device_hostname} ({device_ip}) - processing in background")
                self.cloud_logging.log_structured(
                    f"Registration request sent for {device_hostname} (timeout expected)",
                    severity='INFO',
                    labels={'service': 'cvp-device-registration', 'event': 'device-reregistered'},
                    device_hostname=device_hostname,
                    device_ip=device_ip,
                    note='timeout_expected'
                )
                return True  # Request was sent, CVP will process it

            # For other errors, log as failure
            print(f"  ✗ Failed to re-register {device_hostname}: {e}")
            self.cloud_logging.log_structured(
                f"Failed to re-register device {device_hostname}: {e}",
                severity='ERROR',
                labels={'service': 'cvp-device-registration', 'event': 'device-reregistration-failed'},
                device_hostname=device_hostname,
                device_ip=device_ip,
                error=str(e)
            )
            return False

    def process_inactive_devices(
        self,
        inactive_devices: List[Dict],
        auto_confirm: bool = False,
        batch_size: int = 5
    ) -> Dict:
        """
        Process all inactive devices and attempt re-registration.

        Args:
            inactive_devices: List of devices with inactive streaming
            auto_confirm: If True, skip user confirmation
            batch_size: Number of devices to process before pausing

        Returns:
            Dictionary with success/failure counts and list of processed hostnames
        """
        if not inactive_devices:
            print("\n✓ All devices have active streaming. No action needed.")
            return {"success": 0, "failed": 0, "processed_hostnames": []}

        print(f"\n📋 Devices with inactive streaming:")
        print("-" * 80)
        for idx, device in enumerate(inactive_devices, 1):
            print(f"{idx:3d}. {device['hostname']:20s} | {device['ipAddress']:15s} | {device['serialNumber']}")
        print("-" * 80)

        if not auto_confirm:
            try:
                response = input(f"\nProceed to re-register {len(inactive_devices)} devices? (yes/no): ")
                if response.lower() not in ['yes', 'y']:
                    print("Operation cancelled by user.")
                    return {"success": 0, "failed": 0, "processed_hostnames": []}
            except (EOFError, KeyboardInterrupt):
                print("\nOperation cancelled by user.")
                return {"success": 0, "failed": 0, "processed_hostnames": []}

        print("\n🔄 Starting device re-registration process...\n")

        # Log start of re-registration process to Cloud Logging
        self.cloud_logging.log_structured(
            f"Starting re-registration process for {len(inactive_devices)} devices",
            severity='INFO',
            labels={'service': 'cvp-device-registration', 'event': 'reregistration-started'},
            device_count=len(inactive_devices)
        )

        success_count = 0
        failed_count = 0
        processed_hostnames = []

        # Use ThreadPoolExecutor to run registrations in parallel
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def register_single_device(device):
            """Worker function to register a single device"""
            hostname = device.get('hostname', 'Unknown')
            ip_address = device.get('ipAddress', '')
            success = self.reregister_device(ip_address, hostname)
            return hostname, success

        # Run all devices in parallel with max_workers to control concurrency
        max_workers = min(batch_size, len(inactive_devices))
        print(f"   Running {len(inactive_devices)} devices in parallel (max {max_workers} concurrent)...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_device = {
                executor.submit(register_single_device, device): device
                for device in inactive_devices
            }

            # Process results as they complete
            for idx, future in enumerate(as_completed(future_to_device), 1):
                device = future_to_device[future]
                hostname = device.get('hostname', 'Unknown')
                try:
                    result_hostname, success = future.result()
                    if success:
                        success_count += 1
                        processed_hostnames.append(result_hostname)
                    else:
                        failed_count += 1
                    print(f"[{idx}/{len(inactive_devices)}] Completed {result_hostname}")
                except Exception as e:
                    failed_count += 1
                    print(f"[{idx}/{len(inactive_devices)}] ✗ Error processing {hostname}: {e}")

        # Log completion of re-registration process to Cloud Logging
        self.cloud_logging.log_structured(
            f"Re-registration process completed: {success_count} successful, {failed_count} failed",
            severity='INFO' if failed_count == 0 else 'WARNING',
            labels={'service': 'cvp-device-registration', 'event': 'reregistration-completed'},
            success_count=success_count,
            failed_count=failed_count,
            total_processed=success_count + failed_count
        )

        return {"success": success_count, "failed": failed_count, "processed_hostnames": processed_hostnames}

    def verify_results(self, processed_count: int, wait_time: int = 15) -> Dict:
        """
        Wait and verify that devices have been re-registered successfully.

        Args:
            processed_count: Number of devices that were processed
            wait_time: Time to wait before checking (seconds)

        Returns:
            Dictionary with updated status counts
        """
        if processed_count == 0:
            return {"active": 0, "inactive": 0}

        print(f"\n⏳ Waiting {wait_time} seconds for devices to update...")
        time.sleep(wait_time)

        print("🔍 Verifying device streaming status...")
        devices = self.get_devices()

        if devices:
            inactive = [d for d in devices if d.get('streamingStatus') == 'inactive']
            active = len(devices) - len(inactive)

            print(f"\n📊 Updated Status:")
            print(f"   Active streaming: {active}")
            print(f"   Inactive streaming: {len(inactive)}")

            return {"active": active, "inactive": len(inactive)}

        return {"active": 0, "inactive": 0}

    def verify_all_devices_registered(
        self,
        expected_hostnames: List[str],
        max_retries: int = 5,
        wait_between_retries: int = 30
    ) -> Dict:
        """
        Verify that all processed devices are now registered and streaming.
        Retries multiple times to allow CVP time to complete registration.

        Args:
            expected_hostnames: List of device hostnames that were processed
            max_retries: Maximum number of verification attempts (default: 5)
            wait_between_retries: Seconds to wait between retries (default: 30)

        Returns:
            Dictionary with verification results
        """
        if not expected_hostnames:
            return {"verified": 0, "still_inactive": 0, "not_found": 0, "success": True}

        print(f"\n{'=' * 80}")
        print("🔍 VERIFICATION: Checking if all devices are now registered and streaming")
        print(f"{'=' * 80}")
        print(f"   Devices to verify: {len(expected_hostnames)}")
        print(f"   Max retries: {max_retries}")
        print(f"   Wait between retries: {wait_between_retries} seconds")

        # Log verification start
        self.cloud_logging.log_structured(
            f"Starting device registration verification for {len(expected_hostnames)} devices",
            severity='INFO',
            labels={'service': 'cvp-device-registration', 'event': 'verification-started'},
            device_count=len(expected_hostnames),
            max_retries=max_retries
        )

        for attempt in range(1, max_retries + 1):
            print(f"\n⏳ Attempt {attempt}/{max_retries}: Waiting {wait_between_retries} seconds before checking...")
            time.sleep(wait_between_retries)

            print(f"🔍 Checking device streaming status (attempt {attempt}/{max_retries})...")

            try:
                devices = self.get_devices()
                if not devices:
                    print(f"   ⚠ Could not retrieve devices from CVP")
                    continue

                # Build a lookup by hostname
                device_by_hostname = {d.get('hostname', ''): d for d in devices}

                verified_count = 0
                still_inactive = []
                not_found = []

                for hostname in expected_hostnames:
                    device = device_by_hostname.get(hostname)
                    if not device:
                        not_found.append(hostname)
                    elif device.get('streamingStatus') == 'active':
                        verified_count += 1
                    else:
                        still_inactive.append(hostname)

                # Print status
                print(f"\n   📊 Verification Status (Attempt {attempt}/{max_retries}):")
                print(f"      ✓ Active streaming: {verified_count}/{len(expected_hostnames)}")
                if still_inactive:
                    print(f"      ⚠ Still inactive: {len(still_inactive)} - {still_inactive}")
                if not_found:
                    print(f"      ✗ Not found in CVP: {len(not_found)} - {not_found}")

                # Check if all devices are now active
                if verified_count == len(expected_hostnames):
                    print(f"\n   ✅ SUCCESS: All {verified_count} devices are now streaming!")

                    # Log success
                    self.cloud_logging.log_structured(
                        f"Verification successful: All {verified_count} devices are now streaming",
                        severity='INFO',
                        labels={'service': 'cvp-device-registration', 'event': 'verification-success'},
                        verified_count=verified_count,
                        attempts=attempt
                    )

                    return {
                        "verified": verified_count,
                        "still_inactive": 0,
                        "not_found": len(not_found),
                        "success": True,
                        "attempts": attempt
                    }

                # If this is the last attempt and still have inactive devices
                if attempt == max_retries:
                    print(f"\n   ⚠ Verification incomplete after {max_retries} attempts")
                    if still_inactive:
                        print(f"      Devices still inactive: {still_inactive}")

                    # Log incomplete verification
                    self.cloud_logging.log_structured(
                        f"Verification incomplete: {len(still_inactive)} devices still inactive after {max_retries} attempts",
                        severity='WARNING',
                        labels={'service': 'cvp-device-registration', 'event': 'verification-incomplete'},
                        verified_count=verified_count,
                        still_inactive_count=len(still_inactive),
                        still_inactive_devices=still_inactive,
                        attempts=max_retries
                    )

                    return {
                        "verified": verified_count,
                        "still_inactive": len(still_inactive),
                        "still_inactive_devices": still_inactive,
                        "not_found": len(not_found),
                        "success": False,
                        "attempts": attempt
                    }

            except Exception as e:
                print(f"   ✗ Error during verification: {e}")
                logger.error(f"Verification error on attempt {attempt}: {e}")

        # Should not reach here, but just in case
        return {
            "verified": 0,
            "still_inactive": len(expected_hostnames),
            "not_found": 0,
            "success": False,
            "attempts": max_retries
        }


def main():
    """Main function to execute the device re-registration workflow."""

    # Initialize Cloud Logging for main script execution
    cloud_logging = CloudLoggingManager()
    cloud_logging.setup(
        service_name='cvp-device-registration',
        additional_labels={'operation': 'script-execution'}
    )

    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='CVP Device Re-registration Tool - Re-register devices with inactive streaming',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Interactive mode (will prompt for confirmation)
  python3 cvp_device_reregister.py

  # Auto-confirm mode (no prompts)
  python3 cvp_device_reregister.py -y

  # Custom CVP host
  python3 cvp_device_reregister.py --host 192.168.1.100 -y

  # Process devices in larger batches
  python3 cvp_device_reregister.py -y --batch-size 10
        '''
    )
    parser.add_argument(
        '-y', '--yes', '--auto-confirm',
        action='store_true',
        dest='auto_confirm',
        help='Auto-confirm re-registration without prompting (non-interactive mode)'
    )
    parser.add_argument(
        '--host',
        default=None,
        help='CVP host IP or hostname (default: read from ACCESS_INFO.yaml)'
    )
    parser.add_argument(
        '--username',
        default='arista',
        help='CVP username (default: arista)'
    )
    parser.add_argument(
        '--password',
        default=None,
        help='CVP password (default: read from ACCESS_INFO.yaml)'
    )
    parser.add_argument(
        '--batch-size',
        type=int,
        default=10,
        help='Number of devices to process in parallel (default: 10)'
    )
    parser.add_argument(
        '--skip-verify',
        action='store_true',
        dest='skip_verify',
        help='Skip post-registration verification (not recommended)'
    )
    parser.add_argument(
        '--verify-retries',
        type=int,
        default=5,
        help='Number of verification retries (default: 5)'
    )
    parser.add_argument(
        '--verify-interval',
        type=int,
        default=30,
        help='Seconds between verification retries (default: 30)'
    )
    parser.add_argument(
        '--wait-for-cvp',
        action='store_true',
        dest='wait_for_cvp',
        help='Wait for CVP to be available before starting (retries every 30 seconds)'
    )
    parser.add_argument(
        '--cvp-retries',
        type=int,
        default=60,
        help='Max retries waiting for CVP (default: 60, ~30 minutes with 30s interval)'
    )
    parser.add_argument(
        '--cvp-retry-interval',
        type=int,
        default=30,
        help='Seconds between CVP connection retries (default: 30)'
    )

    args = parser.parse_args()

    print("=" * 80)
    print("CVP Device Re-registration Tool (using cvprac)")
    print("=" * 80)

    # Log script start to Cloud Logging
    cloud_logging.log_structured(
        "CVP Device Re-registration script started",
        severity='INFO',
        labels={'service': 'cvp-device-registration', 'event': 'script-started'}
    )

    # Determine CVP host
    cvp_host = args.host
    if not cvp_host:
        cvp_host = get_cvp_host_from_file()
        if cvp_host:
            print(f"✓ Using CVP host from {ACCESS_INFO_FILE}: {cvp_host}")
        else:
            print(f"✗ Error: No CVP host specified and could not read from {ACCESS_INFO_FILE}")
            sys.exit(1)

    # Determine CVP credentials
    cvp_username = args.username
    cvp_password = args.password

    if not cvp_password:
        # Read credentials from file
        credentials = get_cvp_credentials_from_file(cvp_username)
        if credentials and credentials.get('password'):
            cvp_password = credentials['password']
            print(f"✓ Using CVP credentials from {ACCESS_INFO_FILE} for user: {cvp_username}")
        else:
            print(f"✗ Error: No password specified and could not read from {ACCESS_INFO_FILE}")
            print(f"   Use --password to specify password manually")
            sys.exit(1)

    # Initialize CVP manager
    manager = CVPDeviceManager(cvp_host, cvp_username, cvp_password)

    # Connect to CVP (with optional wait-for-CVP retry logic)
    if args.wait_for_cvp:
        print(f"\n⏳ Waiting for CVP to be available (max {args.cvp_retries} retries, {args.cvp_retry_interval}s interval)...")
        cloud_logging.log_structured(
            "Waiting for CVP to be available",
            severity='INFO',
            labels={'service': 'cvp-device-registration', 'event': 'waiting-for-cvp'},
            max_retries=args.cvp_retries,
            retry_interval=args.cvp_retry_interval
        )
        if not manager.connect(max_retries=args.cvp_retries, retry_interval=args.cvp_retry_interval):
            print("\n✗ Failed to connect to CVP after maximum retries. Exiting.")
            sys.exit(1)
    else:
        if not manager.connect():
            print("\n✗ Failed to connect to CVP. Exiting.")
            sys.exit(1)

    # Wait for devices to stabilize after CVP is up
    # Devices may initially show as active but become inactive after some time
    print("\n⏳ Waiting 90 seconds for devices to stabilize after CVP startup...")
    logger.info("Waiting 90 seconds for devices to stabilize after CVP startup")
    cloud_logging.log_structured(
        "Waiting 90 seconds for devices to stabilize after CVP startup",
        severity='INFO',
        labels={'service': 'cvp-device-registration', 'event': 'device-stabilization-wait'}
    )
    time.sleep(90)

    # Get Undefined container
    if not manager.get_undefined_container():
        print("\n✗ Failed to get Undefined container. Exiting.")
        sys.exit(1)

    # Get all devices
    devices = manager.get_devices()
    if not devices:
        print("\n✗ No devices found or failed to retrieve devices. Exiting.")
        sys.exit(1)

    # Filter inactive streaming devices
    inactive_devices = manager.get_inactive_streaming_devices(devices)

    # Process inactive devices
    results = manager.process_inactive_devices(
        inactive_devices,
        auto_confirm=args.auto_confirm,
        batch_size=args.batch_size
    )

    # Print summary
    print("\n" + "=" * 80)
    print("📊 Re-registration Summary")
    print("=" * 80)
    print(f"Total devices processed: {results['success'] + results['failed']}")
    print(f"✓ Successfully re-registered: {results['success']}")
    print(f"✗ Failed re-registration: {results['failed']}")
    print("=" * 80)

    # Verify if devices were processed (retry 5 times, 30 sec intervals by default)
    if results['success'] > 0 and not args.skip_verify:
        verification_results = manager.verify_all_devices_registered(
            expected_hostnames=results.get('processed_hostnames', []),
            max_retries=args.verify_retries,
            wait_between_retries=args.verify_interval
        )

        # Print final verification summary
        print("\n" + "=" * 80)
        print("📊 FINAL VERIFICATION SUMMARY")
        print("=" * 80)
        print(f"   Devices verified as active: {verification_results['verified']}")
        print(f"   Devices still inactive: {verification_results['still_inactive']}")
        if verification_results.get('still_inactive_devices'):
            print(f"   Still inactive devices: {verification_results['still_inactive_devices']}")
        print(f"   Verification attempts: {verification_results['attempts']}")
        print(f"   Overall success: {'✅ YES' if verification_results['success'] else '⚠ PARTIAL'}")
        print("=" * 80)

        # Log final verification results
        cloud_logging.log_structured(
            f"Final verification: {verification_results['verified']} verified, {verification_results['still_inactive']} still inactive",
            severity='INFO' if verification_results['success'] else 'WARNING',
            labels={'service': 'cvp-device-registration', 'event': 'final-verification'},
            verified_count=verification_results['verified'],
            still_inactive=verification_results['still_inactive'],
            success=verification_results['success']
        )

        # Reset CVP updater container only if ALL devices are successfully streaming
        if verification_results['success'] and verification_results['verified'] > 0:
            print("\n✓ All devices verified as streaming - resetting CVP updater container...")

            cvp_updater_reset = reset_cvp_updater_container()

            if cvp_updater_reset:
                cloud_logging.log_structured(
                    "CVP updater container reset successfully after device re-registration",
                    severity='INFO',
                    labels={'service': 'cvp-device-registration', 'event': 'cvpupdater-reset'},
                    devices_verified=verification_results['verified']
                )
            else:
                cloud_logging.log_structured(
                    "Failed to reset CVP updater container",
                    severity='WARNING',
                    labels={'service': 'cvp-device-registration', 'event': 'cvpupdater-reset-failed'}
                )
        else:
            print("\n⚠ Not all devices verified as streaming - skipping CVP updater reset")
            print("   CVP updater will be reset only when all devices are confirmed active")

    print("\n✅ Script execution completed.")

    # Log script completion to Cloud Logging
    cloud_logging.log_structured(
        f"CVP Device Re-registration script completed successfully",
        severity='INFO',
        labels={'service': 'cvp-device-registration', 'event': 'script-completed', 'status': 'success'},
        total_processed=results['success'] + results['failed'],
        success_count=results['success'],
        failed_count=results['failed'],
        cvp_host=cvp_host
    )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠ Script interrupted by user. Exiting.")

        # Log interruption to Cloud Logging
        cloud_logging = CloudLoggingManager()
        cloud_logging.setup(service_name='cvp-device-registration')
        cloud_logging.log_structured(
            "Script interrupted by user",
            severity='WARNING',
            labels={'service': 'cvp-device-registration', 'event': 'script-interrupted'}
        )
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ Unexpected error: {e}")
        import traceback
        traceback.print_exc()

        # Log error to Cloud Logging
        cloud_logging = CloudLoggingManager()
        cloud_logging.setup(service_name='cvp-device-registration')
        cloud_logging.log_structured(
            f"Script failed with unexpected error: {e}",
            severity='ERROR',
            labels={'service': 'cvp-device-registration', 'event': 'script-failed', 'status': 'error'},
            error=str(e),
            traceback=traceback.format_exc()
        )
        sys.exit(1)

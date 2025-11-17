#!/usr/bin/env python3

"""
Unit Testing Script for ATD Lab Access Info
Reads and processes ACCESS_INFO.yaml file
"""

import yaml
import os
import logging
import subprocess
import re

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration file path
CONFIG_PATH = '/etc/atd/UNIT_TEST_CONFIG.yaml'

# Global config - will be loaded from UNIT_TEST_CONFIG.yaml
config = None


def load_unit_test_config(file_path=CONFIG_PATH):
    """
    Load and parse the UNIT_TEST_CONFIG.yaml file

    Args:
        file_path: Path to the UNIT_TEST_CONFIG.yaml file

    Returns:
        dict: Parsed YAML data or None if file not found/invalid
    """
    try:
        logger.info(f"Attempting to load unit test config from: {file_path}")

        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        # Read and parse YAML file
        with open(file_path, 'r') as f:
            test_config = yaml.safe_load(f)

        logger.info(f"Successfully loaded unit test config from {file_path}")
        logger.info(f"Config sections: {list(test_config.keys()) if test_config else 'None'}")

        return test_config

    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error: {str(e)}")
        return None
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error loading unit test config: {str(e)}")
        return None


def load_access_info(file_path=None):
    """
    Load and parse the ACCESS_INFO.yaml file

    Args:
        file_path: Path to the ACCESS_INFO.yaml file (uses config if None)

    Returns:
        dict: Parsed YAML data or None if file not found/invalid
    """
    if file_path is None:
        file_path = config.get('paths', {}).get('access_info', '/etc/atd/ACCESS_INFO.yaml')

    try:
        logger.info(f"Attempting to load access info from: {file_path}")

        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        # Read and parse YAML file
        with open(file_path, 'r') as f:
            access_info = yaml.safe_load(f)

        logger.info(f"Successfully loaded access info from {file_path}")
        logger.info(f"Keys found: {list(access_info.keys()) if access_info else 'None'}")

        return access_info

    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error: {str(e)}")
        return None
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error loading access info: {str(e)}")
        return None


def get_topology(access_info):
    """
    Get topology value from access info

    Args:
        access_info: Parsed ACCESS_INFO.yaml dict

    Returns:
        str: Topology name or None if not found
    """
    if not access_info:
        logger.error("Access info is None")
        return None

    topology = access_info.get('topology')

    if topology:
        logger.info(f"Topology found: {topology}")
    else:
        logger.error("Topology key not found in access info")

    return topology


def get_labguides_modules(access_info):
    """
    Get labguides_modules value from access info

    Args:
        access_info: Parsed ACCESS_INFO.yaml dict

    Returns:
        list: List of labguide modules or None if not found
    """
    if not access_info:
        logger.error("Access info is None")
        return None

    labguides_modules = access_info.get('labguides_modules')

    if labguides_modules:
        logger.info(f"Labguides modules found: {len(labguides_modules)} modules")
        logger.info(f"Modules: {labguides_modules}")
    else:
        logger.error("labguides_modules key not found in access info")

    return labguides_modules


def get_cvp_version(access_info):
    """
    Get CVP version from access info

    Args:
        access_info: Parsed ACCESS_INFO.yaml dict

    Returns:
        str: CVP version or None if not found
    """
    if not access_info:
        logger.error("Access info is None")
        return None

    cvp_version = access_info.get('cvp')

    if cvp_version:
        logger.info(f"CVP version found: {cvp_version}")
    else:
        logger.error("cvp key not found in access info")

    return cvp_version


def validate_topology_folder(topology, topologies_base_path=None):
    """
    Validate that topology folder exists in /opt/atd/topologies/

    Args:
        topology: Topology name to validate
        topologies_base_path: Base path for topologies (uses config if None)

    Returns:
        bool: True if folder exists, False otherwise
    """
    if not topology:
        logger.error("Topology is None or empty")
        return False

    if topologies_base_path is None:
        topologies_base_path = config.get('paths', {}).get('topologies', '/opt/atd/topologies/')

    topology_path = os.path.join(topologies_base_path, topology)

    logger.info(f"Checking if topology folder exists: {topology_path}")

    if os.path.exists(topology_path) and os.path.isdir(topology_path):
        logger.info(f"✓ Topology folder exists: {topology_path}")
        return True
    else:
        logger.error(f"✗ Topology folder NOT found: {topology_path}")
        return False


def validate_labguides_modules(labguides_modules):
    """
    Validate that labguides_modules is not empty

    Args:
        labguides_modules: List of labguide modules

    Returns:
        bool: True if not empty, False otherwise
    """
    if not labguides_modules:
        logger.error("✗ labguides_modules is None or empty")
        return False

    if not isinstance(labguides_modules, list):
        logger.error(f"✗ labguides_modules is not a list, type: {type(labguides_modules)}")
        return False

    if len(labguides_modules) == 0:
        logger.error("✗ labguides_modules list is empty")
        return False

    logger.info(f"✓ labguides_modules is valid with {len(labguides_modules)} modules")
    return True


def load_atd_repo_info(file_path=None):
    """
    Load and parse the ATD_REPO.yaml file

    Args:
        file_path: Path to the ATD_REPO.yaml file (uses config if None)

    Returns:
        dict: Parsed YAML data or None if file not found/invalid
    """
    if file_path is None:
        file_path = config.get('paths', {}).get('atd_repo', '/etc/atd/ATD_REPO.yaml')

    try:
        logger.info(f"Attempting to load ATD repo info from: {file_path}")

        # Check if file exists
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        # Read and parse YAML file
        with open(file_path, 'r') as f:
            atd_repo_info = yaml.safe_load(f)

        logger.info(f"Successfully loaded ATD repo info from {file_path}")
        logger.info(f"Keys found: {list(atd_repo_info.keys()) if atd_repo_info else 'None'}")

        return atd_repo_info

    except yaml.YAMLError as e:
        logger.error(f"YAML parsing error: {str(e)}")
        return None
    except PermissionError as e:
        logger.error(f"Permission denied: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error loading ATD repo info: {str(e)}")
        return None


def get_atd_public_branch(atd_repo_info):
    """
    Get atd-public-branch value from ATD repo info

    Args:
        atd_repo_info: Parsed ATD_REPO.yaml dict

    Returns:
        str: Branch name or None if not found
    """
    if not atd_repo_info:
        logger.error("ATD repo info is None")
        return None

    branch = atd_repo_info.get('atd-public-branch')

    if branch:
        logger.info(f"ATD public branch found: {branch}")
    else:
        logger.error("atd-public-branch key not found in ATD repo info")

    return branch


def validate_branch_not_nested(branch, access_info):
    """
    Validate that atd-public-branch does not contain the word "nested"
    Skip validation if project is "atd-testdrivetraining-dev"

    Args:
        branch: Branch name to validate
        access_info: Parsed ACCESS_INFO.yaml data

    Returns:
        bool: True if "nested" is NOT in branch name or if project is dev, False otherwise
    """
    if not branch:
        logger.error("Branch is None or empty")
        return False

    if not isinstance(branch, str):
        logger.error(f"Branch is not a string, type: {type(branch)}")
        return False

    # Check if project is atd-testdrivetraining-dev
    project = access_info.get('project', '')
    if project == 'atd-testdrivetraining-dev':
        logger.info(f"ℹ Project is '{project}' - skipping nested branch validation")
        logger.info(f"✓ Branch validation skipped (dev environment): {branch}")
        return True

    # Check if "nested" is in the branch name (case-insensitive)
    if "nested" in branch.lower():
        logger.error(f"✗ Branch contains 'nested': {branch}")
        return False

    logger.info(f"✓ Branch does not contain 'nested': {branch}")
    return True


def get_cvp_disk_capacity():
    """
    Get CVP disk capacity using virsh domblkinfo command

    Returns:
        tuple: (capacity_bytes, capacity_gb) or (None, None) if failed
    """
    try:
        logger.info("Checking CVP disk capacity using virsh")

        # Run virsh domblkinfo command
        cmd = ['sudo', 'virsh', 'domblkinfo', 'cvp1', '/var/lib/libvirt/images/cvp1/disk2.qcow2']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.error(f"virsh command failed: {result.stderr}")
            return None, None

        # Parse output to get Capacity
        output = result.stdout
        logger.info(f"virsh output:\n{output}")

        # Look for "Capacity:" line
        capacity_match = re.search(r'Capacity:\s+(\d+)', output)

        if not capacity_match:
            logger.error("Could not find Capacity in virsh output")
            return None, None

        capacity_bytes = int(capacity_match.group(1))
        capacity_gb = capacity_bytes / (1024 ** 3)  # Convert bytes to GB

        logger.info(f"CVP disk capacity: {capacity_bytes} bytes ({capacity_gb:.2f} GB)")

        return capacity_bytes, capacity_gb

    except subprocess.TimeoutExpired:
        logger.error("virsh command timed out")
        return None, None
    except Exception as e:
        logger.error(f"Error getting CVP disk capacity: {str(e)}")
        return None, None


def validate_cvp_disk_capacity(min_capacity_gb=None):
    """
    Validate that CVP disk capacity is greater than minimum required

    Args:
        min_capacity_gb: Minimum required capacity in GB (uses config if None)

    Returns:
        bool: True if capacity >= min_capacity_gb, False otherwise
    """
    if min_capacity_gb is None:
        min_capacity_gb = config.get('cvp_disk', {}).get('min_capacity_gb', 175)

    capacity_bytes, capacity_gb = get_cvp_disk_capacity()

    if capacity_gb is None:
        logger.error("✗ Failed to get CVP disk capacity")
        return False

    if capacity_gb >= min_capacity_gb:
        logger.info(f"✓ CVP disk capacity ({capacity_gb:.2f} GB) is >= {min_capacity_gb} GB")
        return True
    else:
        logger.error(f"✗ CVP disk capacity ({capacity_gb:.2f} GB) is < {min_capacity_gb} GB")
        return False




def get_cvp_dominfo():
    """
    Get CVP domain info using virsh dominfo command

    Returns:
        dict: Dictionary with 'ram_gb' and 'cpu_cores' or None if failed
    """
    try:
        logger.info("Checking CVP domain info using virsh")

        # Run virsh dominfo command
        cmd = ['sudo', 'virsh', 'dominfo', 'cvp1']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            logger.error(f"virsh dominfo command failed: {result.stderr}")
            return None

        # Parse output to get Max memory and CPU(s)
        output = result.stdout
        logger.info(f"virsh dominfo output:\n{output}")

        # Look for "Max memory:" line (in KiB)
        memory_match = re.search(r'Max memory:\s+(\d+)\s+KiB', output)
        # Look for "CPU(s):" line
        cpu_match = re.search(r'CPU\(s\):\s+(\d+)', output)

        if not memory_match:
            logger.error("Could not find Max memory in virsh dominfo output")
            return None

        if not cpu_match:
            logger.error("Could not find CPU(s) in virsh dominfo output")
            return None

        memory_kib = int(memory_match.group(1))
        memory_gb = memory_kib / (1024 ** 2)  # Convert KiB to GB
        cpu_cores = int(cpu_match.group(1))

        logger.info(f"CVP RAM: {memory_gb:.2f} GB, CPU cores: {cpu_cores}")

        return {
            'ram_gb': memory_gb,
            'cpu_cores': cpu_cores
        }

    except subprocess.TimeoutExpired:
        logger.error("virsh dominfo command timed out")
        return None
    except Exception as e:
        logger.error(f"Error getting CVP domain info: {str(e)}")
        return None


def validate_cvp_resources(cvp_version):
    """
    Validate that CVP RAM and CPU meet minimum requirements from config

    Args:
        cvp_version: CVP version string (e.g., "2025.1.0")

    Returns:
        bool: True if resources meet requirements, False otherwise
    """
    if not cvp_version:
        logger.error("✗ CVP version is None")
        return False

    # Get required values from config for this version
    cvp_all_versions = config.get('cvp_hardware', {})
    version_requirements = cvp_all_versions.get(cvp_version)

    if not version_requirements:
        logger.error(f"✗ CVP config missing requirements for version {cvp_version}")
        logger.error(f"Available versions: {list(cvp_all_versions.keys())}")
        return False

    min_ram_gb = version_requirements.get('min_ram_gb')
    min_cpu_cores = version_requirements.get('min_cpu_cores')

    if min_ram_gb is None or min_cpu_cores is None:
        logger.error(f"✗ CVP config for version {cvp_version} missing min_ram_gb or min_cpu_cores")
        return False

    logger.info(f"CVP version: {cvp_version}")
    logger.info(f"Required: RAM >= {min_ram_gb} GB, CPU >= {min_cpu_cores} cores")

    # Get actual CVP resources
    cvp_info = get_cvp_dominfo()

    if not cvp_info:
        logger.error("✗ Failed to get CVP domain info")
        return False

    actual_ram_gb = cvp_info['ram_gb']
    actual_cpu_cores = cvp_info['cpu_cores']

    # Validate RAM
    ram_valid = actual_ram_gb >= min_ram_gb
    if ram_valid:
        logger.info(f"✓ CVP RAM ({actual_ram_gb:.2f} GB) is >= {min_ram_gb} GB")
    else:
        logger.error(f"✗ CVP RAM ({actual_ram_gb:.2f} GB) is < {min_ram_gb} GB")

    # Validate CPU
    cpu_valid = actual_cpu_cores >= min_cpu_cores
    if cpu_valid:
        logger.info(f"✓ CVP CPU cores ({actual_cpu_cores}) is >= {min_cpu_cores}")
    else:
        logger.error(f"✗ CVP CPU cores ({actual_cpu_cores}) is < {min_cpu_cores}")

    return ram_valid and cpu_valid


def get_customer_details(access_info):
    """
    Get customer details from ACCESS_INFO

    Args:
        access_info: Parsed ACCESS_INFO.yaml dict

    Returns:
        dict: Customer details with keys: full_name, email, exam_id, attempt_id
              Returns None if customer_details not found
    """
    if not access_info:
        logger.error("Access info is None")
        return None

    customer_details = access_info.get('customer_details')

    if not customer_details:
        logger.warning("customer_details key not found in ACCESS_INFO")
        return None

    if not isinstance(customer_details, dict):
        logger.error(f"customer_details is not a dict, type: {type(customer_details)}")
        return None

    # Extract required fields
    details = {
        'full_name': customer_details.get('exam_taker_full_name', 'N/A'),
        'email': customer_details.get('exam_taker_email', 'N/A'),
        'exam_id': customer_details.get('external_exam_id', 'N/A'),
        'attempt_id': customer_details.get('exam_taker_attempt_id', 'N/A')
    }

    logger.info(f"✓ Customer details found:")
    logger.info(f"  Full Name: {details['full_name']}")
    logger.info(f"  Email: {details['email']}")
    logger.info(f"  Exam ID: {details['exam_id']}")
    logger.info(f"  Attempt ID: {details['attempt_id']}")

    return details


def main():
    """Main function for testing"""
    global config

    logger.info("="*60)
    logger.info("Starting ATD Configuration Validation")
    logger.info("="*60)

    # Load unit test config first
    config = load_unit_test_config()

    if not config:
        logger.error("Failed to load unit test config. Exiting.")
        return 1

    # Load access info
    access_info = load_access_info()

    if not access_info:
        logger.error("Failed to load access info. Exiting.")
        return 1

    # Load ATD repo info
    atd_repo_info = load_atd_repo_info()

    if not atd_repo_info:
        logger.error("Failed to load ATD repo info. Exiting.")
        return 1

    logger.info("\n" + "="*60)
    logger.info("Extracting variables from ACCESS_INFO.yaml")
    logger.info("="*60)

    # Get topology
    topology = get_topology(access_info)

    # Get labguides_modules
    labguides_modules = get_labguides_modules(access_info)

    # Get CVP version
    cvp_version = get_cvp_version(access_info)

    # Get customer details
    customer_details = get_customer_details(access_info)

    logger.info("\n" + "="*60)
    logger.info("Extracting variables from ATD_REPO.yaml")
    logger.info("="*60)

    # Get atd-public-branch
    atd_public_branch = get_atd_public_branch(atd_repo_info)

    logger.info("\n" + "="*60)
    logger.info("Running validations")
    logger.info("="*60)

    # Validate topology folder
    topology_valid = validate_topology_folder(topology)

    # Validate labguides_modules
    labguides_valid = validate_labguides_modules(labguides_modules)

    # Validate branch does not contain "nested" (skip for dev project)
    branch_valid = validate_branch_not_nested(atd_public_branch, access_info)

    # Validate CVP disk capacity
    cvp_disk_valid = validate_cvp_disk_capacity()

    # Validate CVP RAM and CPU resources
    cvp_resources_valid = validate_cvp_resources(cvp_version)

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Validation Summary")
    logger.info("="*60)

    # Customer Information
    if customer_details:
        logger.info("\nCustomer Information:")
        logger.info(f"  Full Name: {customer_details['full_name']}")
        logger.info(f"  Email: {customer_details['email']}")
        logger.info(f"  Exam ID: {customer_details['exam_id']}")
        logger.info(f"  Attempt ID: {customer_details['attempt_id']}")
    else:
        logger.info("\nCustomer Information: Not available")

    # Validation Results
    logger.info("\nValidation Results:")
    logger.info(f"  Topology: {topology}")
    logger.info(f"  Topology folder exists: {'PASS' if topology_valid else 'FAIL'}")
    logger.info(f"  Labguides modules count: {len(labguides_modules) if labguides_modules else 0}")
    logger.info(f"  Labguides modules valid: {'PASS' if labguides_valid else 'FAIL'}")
    logger.info(f"  ATD public branch: {atd_public_branch}")
    logger.info(f"  Branch does not contain 'nested': {'PASS' if branch_valid else 'FAIL'}")
    logger.info(f"  CVP version: {cvp_version}")
    min_capacity_gb = config.get('cvp_disk', {}).get('min_capacity_gb', 175)
    logger.info(f"  CVP disk capacity >= {min_capacity_gb} GB: {'PASS' if cvp_disk_valid else 'FAIL'}")
    logger.info(f"  CVP RAM and CPU meet requirements (version {cvp_version}): {'PASS' if cvp_resources_valid else 'FAIL'}")
    logger.info("="*60)

    # Exit with appropriate code
    if topology_valid and labguides_valid and branch_valid and cvp_disk_valid and cvp_resources_valid:
        logger.info("All validations PASSED")
        return 0
    else:
        logger.error("Some validations FAILED")
        return 1


if __name__ == "__main__":
    exit(main())

#!/usr/bin/env python3
"""
ATD Manager - Python implementation of atdStartup functionality

This module provides a clean, maintainable Python implementation of the ATD
(Arista Training Labs) startup functionality.

Note: Git update operations are handled by atdUpdate.sh (bash script).
      This module is called by atdStartup.sh after git pull completes.

Classes:
    - ConfigManager: Handles YAML configuration file operations
    - GitManager: Manages git repository operations
    - DockerManager: Manages Docker container operations
    - NetworkManager: Handles network configuration (NAT, routes)
    - SystemdManager: Manages systemd services and timers
    - FileManager: Handles file operations (rsync, copy, sed replacements)
    - LabguideManager: Manages lab guide downloads and extraction
    - ExamManager: Handles exam-related functionality
    - ATDStartup: Main startup orchestrator

Usage:
    # Run startup
    python3 atd_manager.py startup

    # Check container health
    python3 atd_manager.py check-containers

    # Check exam submission
    python3 atd_manager.py check-exam
"""

import os
import sys
import subprocess
import shutil
import tarfile
import time
import logging
import argparse
import re
import socket
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

import yaml

# Suppress gRPC fork warnings to reduce log noise
# These INFO-level messages appear when subprocess calls happen during Cloud Logging
os.environ['GRPC_VERBOSITY'] = 'ERROR'
os.environ['GRPC_TRACE'] = ''

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

    def setup(self, service_name: str = 'atd-manager', additional_labels: Dict[str, str] = None) -> bool:
        """
        Setup Cloud Logging with structured labels.

        Args:
            service_name: Name of the service (e.g., 'atd-startup', 'atd-update')
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
                'component': 'atd-manager'
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

    def get_logger(self, name: str, service_name: str = None) -> logging.Logger:
        """
        Get a logger with optional Cloud Logging integration.

        Args:
            name: Logger name
            service_name: Service name for labeling

        Returns:
            Configured logger
        """
        log = logging.getLogger(name)

        # If Cloud Logging is available and we have a specific service name,
        # we could add service-specific labeling here if needed
        return log

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
            cloud_logger = self.client.logger('atd-manager')

            # Merge base labels with provided labels
            all_labels = {
                'lab_name': self.hostname,
                'component': 'atd-manager'
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


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class AccessInfo:
    """Data class holding ACCESS_INFO.yaml contents"""
    topology: str = ''
    password: str = ''
    project: str = ''
    cvp_version: str = ''
    machine_name: str = ''
    lab_type: str = 'Lab'
    exam_duration: int = 0
    eos_type: str = 'veos'
    zone: str = ''
    host: str = ''
    username: str = ''

    @classmethod
    def from_yaml(cls, yaml_path: str) -> 'AccessInfo':
        """Load AccessInfo from YAML file"""
        try:
            with open(yaml_path, 'r') as f:
                data = yaml.safe_load(f)

            # Extract nested values safely
            login_info = data.get('login_info', {})
            jump_host = login_info.get('jump_host', {})
            customer_details = data.get('customer_details', {})

            return cls(
                topology=data.get('topology', ''),
                password=jump_host.get('pw', ''),
                project=data.get('project', ''),
                cvp_version=data.get('cvp', ''),
                machine_name=data.get('name', ''),
                lab_type=customer_details.get('lab_type', 'Lab'),
                exam_duration=int(customer_details.get('exam_hours', 0)),
                eos_type=data.get('eos_type', 'veos'),
                zone=data.get('zone', ''),
                host=data.get('cvp_host', ''),
                username=data.get('username', 'arista')
            )
        except Exception as e:
            logger.error(f"Failed to load ACCESS_INFO.yaml: {e}")
            raise


@dataclass
class ATDConfig:
    """Configuration paths and constants for ATD"""
    access_info_path: str = '/etc/atd/ACCESS_INFO.yaml'
    atd_repo_path: str = '/etc/atd/ATD_REPO.yaml'
    base_topo_path: str = '/etc/atd/base_topo.yml'
    base_topo_url: str = 'https://raw.githubusercontent.com/aristanetworks/training-infra-public/nested-release/topologies/base_topo.yml'
    atd_opt_path: str = '/opt/atd'
    labguide_directory: str = '/opt/labguides/web/'
    docker_compose_path: str = '/opt/atd/nested-labvm/atd-docker'
    arista_home: str = '/home/arista'
    atdadmin_home: str = '/home/atdadmin'
    metadata_path: str = '/opt/atd/topologies/metadata.yml'
    unit_test_config_path: str = '/opt/atd/nested-labvm/services/unit-test/UNIT_TEST_CONFIG.yaml'
    log_dir: str = '/etc/atd/logs'
    exam_log_file: str = '/var/log/exam-submission-check.log'
    default_repo: str = 'https://github.com/aristanetworks/atd-public.git'
    branch_override_path: str = '/etc/atd/BRANCH_OVERRIDE'


# =============================================================================
# Configuration Manager
# =============================================================================

class ConfigManager:
    """Handles YAML configuration file operations"""

    def __init__(self, config: ATDConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def load_yaml(self, path: str) -> Dict[str, Any]:
        """Load a YAML file and return its contents"""
        try:
            with open(path, 'r') as f:
                return yaml.safe_load(f) or {}
        except FileNotFoundError:
            self.logger.warning(f"YAML file not found: {path}")
            return {}
        except Exception as e:
            self.logger.error(f"Failed to load YAML file {path}: {e}")
            return {}

    def save_yaml(self, path: str, data: Dict[str, Any]) -> bool:
        """Save data to a YAML file"""
        try:
            with open(path, 'w') as f:
                yaml.dump(data, f, default_flow_style=False)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save YAML file {path}: {e}")
            return False

    def get_value(self, path: str, key_path: str, default: Any = None) -> Any:
        """Get a nested value from a YAML file using dot notation"""
        data = self.load_yaml(path)
        keys = key_path.split('.')

        for key in keys:
            if isinstance(data, dict):
                data = data.get(key, default)
            else:
                return default

        return data if data is not None else default

    def update_access_info_field(self, field: str, value: Any) -> bool:
        """Update or add a field in ACCESS_INFO.yaml"""
        path = self.config.access_info_path

        try:
            # Read existing file
            with open(path, 'r') as f:
                content = f.read()

            # Check if field exists
            pattern = rf'^{field}:.*$'
            if re.search(pattern, content, re.MULTILINE):
                # Update existing value
                content = re.sub(pattern, f'{field}: {value}', content, flags=re.MULTILINE)
            else:
                # Add new entry
                content += f'\n{field}: {value}'

            # Write back
            with open(path, 'w') as f:
                f.write(content)

            self.logger.info(f"Updated {field} to {value} in ACCESS_INFO.yaml")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update ACCESS_INFO.yaml: {e}")
            return False

    def update_atd_repo_branch(self, branch: str) -> bool:
        """Update the branch in ATD_REPO.yaml"""
        path = self.config.atd_repo_path

        try:
            with open(path, 'r') as f:
                content = f.read()

            # Update or add branch
            if 'atd-public-branch:' in content:
                content = re.sub(
                    r'^atd-public-branch:.*$',
                    f'atd-public-branch: {branch}',
                    content,
                    flags=re.MULTILINE
                )
            else:
                content += f'\natd-public-branch: {branch}'

            with open(path, 'w') as f:
                f.write(content)

            self.logger.info(f"Updated ATD branch to {branch}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to update ATD_REPO.yaml: {e}")
            return False

    def download_base_topo(self) -> bool:
        """Download the base topology YAML file"""
        try:
            result = subprocess.run(
                ['curl', '-o', self.config.base_topo_path, self.config.base_topo_url],
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.logger.info("Downloaded base_topo.yml successfully")
                return True
            else:
                self.logger.error(f"Failed to download base_topo.yml: {result.stderr}")
                return False
        except Exception as e:
            self.logger.error(f"Exception downloading base_topo.yml: {e}")
            return False

    def get_branch_name(self, access_info: AccessInfo) -> Optional[str]:
        """Determine the correct branch name based on topology and environment"""
        base_topo = self.load_yaml(self.config.base_topo_path)

        eos_type = access_info.eos_type
        if eos_type == 'container-labs':
            eos_type = 'ceos'

        project = access_info.project
        if project == 'atd-testdrivetraining-prod':
            project = 'prod'
        else:
            project = 'dev'

        cvp_ver = str(access_info.cvp_version)

        try:
            # Navigate the nested structure
            topo_data = base_topo.get('topologies', {}).get(access_info.topology, {})
            env_data = topo_data.get(project, {}).get(eos_type, {})
            cvp_data = env_data.get('cvp', {})

            # Try to find matching CVP version
            for version, config in cvp_data.items():
                if str(version) == cvp_ver:
                    return config.get('branch')

            return None
        except Exception as e:
            self.logger.warning(f"Could not determine branch name: {e}")
            return None


# =============================================================================
# Git Manager
# =============================================================================

class GitManager:
    """Manages git repository operations"""

    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        self.logger = logging.getLogger(self.__class__.__name__)

    def run_git(self, *args) -> Tuple[bool, str]:
        """Run a git command in the repository directory"""
        try:
            result = subprocess.run(
                ['git'] + list(args),
                cwd=self.repo_path,
                capture_output=True,
                text=True
            )
            return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return False, str(e)

    def get_remote_url(self) -> str:
        """Get the current remote origin URL"""
        success, output = self.run_git('remote', 'get-url', 'origin')
        return output if success else ''

    def set_remote_url(self, url: str) -> bool:
        """Set the remote origin URL"""
        success, _ = self.run_git('remote', 'set-url', 'origin', url)
        if success:
            self.logger.info(f"Updated remote URL to {url}")
        return success

    def get_current_branch(self) -> str:
        """Get the current branch name"""
        success, output = self.run_git('branch', '--show-current')
        return output if success else ''

    def fetch(self) -> bool:
        """Fetch updates from remote"""
        success, _ = self.run_git('fetch')
        return success

    def checkout(self, ref: str) -> bool:
        """Checkout a branch or tag"""
        # Log any local changes that will be discarded
        _, status_output = self.run_git('status', '--porcelain')
        if status_output:
            self.logger.warning(f"Discarding local changes before checkout:\n{status_output}")

        self.run_git('checkout', '.')
        success, _ = self.run_git('checkout', ref)
        if success:
            self.logger.info(f"Checked out {ref}")
        return success

    def pull(self) -> bool:
        """Pull latest changes"""
        success, _ = self.run_git('pull')
        return success

    def update_to_branch(self, target_branch: str, target_repo: str) -> bool:
        """Update repository to the target branch and repo"""
        # Check and update remote if needed
        current_remote = self.get_remote_url()
        if current_remote != target_repo:
            self.logger.info(f"Repos do not match, updating to {target_repo}")
            self.set_remote_url(target_repo)

        # Fetch updates
        self.fetch()

        # Check and update branch if needed
        current_branch = self.get_current_branch()
        if current_branch == target_branch:
            self.logger.info("Target branch matches current branch")
            self.run_git('checkout', '.')
            self.pull()
        else:
            self.logger.info(f"Branches do not match, updating to branch {target_branch}")
            self.checkout(target_branch)
            self.pull()

        return True


# =============================================================================
# Docker Manager
# =============================================================================

class DockerManager:
    """Manages Docker container operations"""

    def __init__(self, compose_path: str):
        self.compose_path = compose_path
        self.logger = logging.getLogger(self.__class__.__name__)

    def run_docker(self, *args, sudo: bool = True) -> Tuple[bool, str]:
        """Run a docker command"""
        cmd = ['sudo', 'docker'] if sudo else ['docker']
        cmd.extend(args)

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return False, str(e)

    def is_container_running(self, container_name: str) -> bool:
        """Check if a container is running"""
        success, output = self.run_docker('ps', '--format', '{{.Names}}')
        if success:
            return container_name in output.split('\n')
        return False

    def compose_up(self, force_recreate: bool = True, remove_orphans: bool = True) -> bool:
        """Run docker compose up"""
        cmd = ['docker', 'compose', 'up', '-d']
        if force_recreate:
            cmd.append('--force-recreate')
        if remove_orphans:
            cmd.append('--remove-orphans')

        try:
            result = subprocess.run(
                cmd,
                cwd=self.compose_path,
                capture_output=True,
                text=True
            )
            if result.returncode == 0:
                self.logger.info("Docker compose up completed successfully")
            else:
                self.logger.error(f"Docker compose up failed: {result.stderr}")
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Docker compose up exception: {e}")
            return False

    def prune_images(self) -> bool:
        """Prune unused Docker images"""
        try:
            result = subprocess.run(
                ['docker', 'image', 'prune', '-f'],
                capture_output=True,
                text=True,
                input='y\n'
            )
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Docker image prune exception: {e}")
            return False

    def prune_containers(self) -> bool:
        """Prune stopped containers"""
        success, _ = self.run_docker('container', 'prune', '-f')
        return success

    def exec_command(self, container: str, command: List[str], detach: bool = False) -> Tuple[bool, str]:
        """Execute a command inside a container"""
        args = ['exec']
        if detach:
            args.append('-d')
        args.append(container)
        args.extend(command)

        return self.run_docker(*args)

    def update_compose_image(self, old_image: str, new_image: str) -> bool:
        """Update an image reference in docker-compose.yml"""
        compose_file = os.path.join(self.compose_path, 'docker-compose.yml')

        try:
            with open(compose_file, 'r') as f:
                content = f.read()

            content = content.replace(old_image, new_image)

            with open(compose_file, 'w') as f:
                f.write(content)

            self.logger.info(f"Updated compose image from {old_image} to {new_image}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to update compose image: {e}")
            return False


class ContainerHealthChecker:
    """Checks container health and handles failures"""

    def __init__(self, docker_manager: DockerManager, max_retries: int = 5, retry_delay: int = 10):
        self.docker = docker_manager
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize Cloud Logging
        self.cloud_logging = CloudLoggingManager()
        self.cloud_logging.setup(
            service_name='atd-container-health',
            additional_labels={'operation': 'health-check'}
        )

    def check_container(self, container_name: str) -> bool:
        """
        Check if a container is running with retries.
        Returns True if container is running, False otherwise.
        """
        for attempt in range(1, self.max_retries + 1):
            self.logger.info(f"Attempt {attempt} of {self.max_retries}")

            if self.docker.is_container_running(container_name):
                self.logger.info(f"{container_name} container is running.")

                # Log success to Cloud Logging
                self.cloud_logging.log_structured(
                    f"Container {container_name} health check passed",
                    severity='INFO',
                    labels={'service': 'atd-container-health', 'container': container_name, 'status': 'healthy'},
                    container=container_name,
                    attempt=attempt
                )
                return True

            self.logger.warning(f"{container_name} container failed to start. Retrying...")

            # Log retry to Cloud Logging
            self.cloud_logging.log_structured(
                f"Container {container_name} not running, attempt {attempt}/{self.max_retries}",
                severity='WARNING',
                labels={'service': 'atd-container-health', 'container': container_name, 'status': 'retrying'},
                container=container_name,
                attempt=attempt,
                max_retries=self.max_retries
            )

            if attempt == self.max_retries:
                self.logger.error(
                    f"{container_name} container failed to start after {self.max_retries} attempts. "
                    "Pruning all containers."
                )

                # Log failure to Cloud Logging
                self.cloud_logging.log_structured(
                    f"Container {container_name} failed after {self.max_retries} attempts - pruning containers",
                    severity='ERROR',
                    labels={'service': 'atd-container-health', 'container': container_name, 'status': 'failed'},
                    container=container_name,
                    max_retries=self.max_retries
                )

                self.docker.prune_containers()
                return False

            time.sleep(self.retry_delay)

        return False


# =============================================================================
# Network Manager
# =============================================================================

class NetworkManager:
    """Handles network configuration (NAT, routes)"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def run_command(self, *args, sudo: bool = True) -> Tuple[bool, str]:
        """Run a system command"""
        cmd = list(args)
        if sudo:
            cmd = ['sudo'] + cmd

        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return False, str(e)

    def enable_ip_forwarding(self) -> bool:
        """Enable IP forwarding"""
        success, _ = self.run_command('sysctl', '-w', 'net.ipv4.ip_forward=1')
        if success:
            self.logger.info("Enabled IP forwarding")
        return success

    def setup_nat(self, internal_interface: str = 'vmgmt', external_interface: str = 'eth0') -> bool:
        """Setup NAT masquerading"""
        self.logger.info("Setting up NAT")

        # Enable IP forwarding
        if not self.enable_ip_forwarding():
            return False

        # Setup iptables rules
        commands = [
            ['iptables', '-t', 'nat', '-A', 'POSTROUTING', '-o', external_interface, '-j', 'MASQUERADE'],
            ['iptables', '-A', 'FORWARD', '-i', internal_interface, '-o', external_interface, '-j', 'ACCEPT'],
            ['iptables', '-A', 'FORWARD', '-m', 'state', '--state', 'ESTABLISHED,RELATED', '-o', internal_interface, '-j', 'ACCEPT']
        ]

        for cmd in commands:
            success, _ = self.run_command(*cmd)
            if not success:
                self.logger.error(f"Failed to run: {' '.join(cmd)}")
                return False

        self.logger.info("NAT setup completed")
        return True

    def update_routes_for_almalinux(self) -> bool:
        """Update routes specifically for AlmaLinux"""
        # Check if running AlmaLinux
        try:
            with open('/etc/os-release', 'r') as f:
                content = f.read()

            if 'AlmaLinux' not in content:
                return True  # Not AlmaLinux, nothing to do

            self.logger.info("Detected AlmaLinux, updating routes")

            # Delete old route and add new one
            self.run_command('ip', 'route', 'del', '192.168.0.0/24', 'dev', 'vmgmt')
            success, _ = self.run_command('ip', 'route', 'add', '192.168.0.0/25', 'dev', 'vmgmt')

            return success

        except Exception as e:
            self.logger.error(f"Failed to update routes: {e}")
            return False


# =============================================================================
# Systemd Manager
# =============================================================================

class SystemdManager:
    """Manages systemd services and timers"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def run_systemctl(self, *args) -> Tuple[bool, str]:
        """Run a systemctl command"""
        try:
            result = subprocess.run(
                ['sudo', 'systemctl'] + list(args),
                capture_output=True,
                text=True
            )
            return result.returncode == 0, result.stdout.strip() or result.stderr.strip()
        except Exception as e:
            return False, str(e)

    def daemon_reload(self) -> bool:
        """Reload systemd daemon"""
        success, _ = self.run_systemctl('daemon-reload')
        if success:
            self.logger.info("Systemd daemon reloaded")
        return success

    def enable(self, unit: str) -> bool:
        """Enable a systemd unit"""
        success, _ = self.run_systemctl('enable', unit)
        if success:
            self.logger.info(f"Enabled {unit}")
        return success

    def start(self, unit: str) -> bool:
        """Start a systemd unit"""
        success, _ = self.run_systemctl('start', unit)
        if success:
            self.logger.info(f"Started {unit}")
        return success

    def stop(self, unit: str) -> bool:
        """Stop a systemd unit"""
        success, _ = self.run_systemctl('stop', unit)
        if success:
            self.logger.info(f"Stopped {unit}")
        return success

    def disable(self, unit: str) -> bool:
        """Disable a systemd unit"""
        success, _ = self.run_systemctl('disable', unit)
        if success:
            self.logger.info(f"Disabled {unit}")
        return success

    def restart(self, unit: str) -> bool:
        """Restart a systemd unit"""
        success, _ = self.run_systemctl('restart', unit)
        if success:
            self.logger.info(f"Restarted {unit}")
        return success

    def setup_timer(self, service_file: str, timer_file: str) -> bool:
        """Install and enable a timer with its service"""
        service_name = os.path.basename(service_file)
        timer_name = os.path.basename(timer_file)

        # Copy files to systemd directory
        for src in [service_file, timer_file]:
            dst = f'/etc/systemd/system/{os.path.basename(src)}'
            try:
                shutil.copy2(src, dst)
            except Exception as e:
                self.logger.error(f"Failed to copy {src} to {dst}: {e}")
                return False

        # Reload, enable, and start
        self.daemon_reload()
        self.enable(timer_name)
        self.start(timer_name)

        return True


# =============================================================================
# File Manager
# =============================================================================

class FileManager:
    """Handles file operations (rsync, copy, sed replacements)"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def rsync(self, src: str, dst: str, archive: bool = True, verbose: bool = True, update: bool = False) -> bool:
        """Rsync files/directories"""
        args = ['rsync']
        if archive:
            args.append('-a')
        if verbose:
            args.append('-v')
        if update:
            args.append('--update')
        args.extend([src, dst])

        try:
            result = subprocess.run(args, capture_output=True, text=True)
            return result.returncode == 0
        except Exception as e:
            self.logger.error(f"Rsync failed: {e}")
            return False

    def ensure_directory(self, path: str, mode: int = 0o755) -> bool:
        """Ensure a directory exists with proper permissions"""
        try:
            os.makedirs(path, mode=mode, exist_ok=True)
            return True
        except Exception as e:
            self.logger.error(f"Failed to create directory {path}: {e}")
            return False

    def _is_binary_file(self, file_path: str) -> bool:
        """Check if a file is binary by looking for null bytes in first 8KB"""
        try:
            with open(file_path, 'rb') as f:
                chunk = f.read(8192)
            return b'\x00' in chunk
        except Exception:
            return True

    def sed_replace(self, file_path: str, pattern: str, replacement: str) -> bool:
        """Perform sed-like replacement in a file"""
        if self._is_binary_file(file_path):
            self.logger.debug(f"Skipping binary file: {file_path}")
            return False

        try:
            with open(file_path, 'r') as f:
                content = f.read()

            content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

            with open(file_path, 'w') as f:
                f.write(content)

            return True
        except UnicodeDecodeError:
            self.logger.debug(f"Skipping binary file (decode error): {file_path}")
            return False
        except Exception as e:
            self.logger.error(f"Sed replace failed for {file_path}: {e}")
            return False

    def find_and_replace(self, directory: str, pattern: str, replacement: str) -> int:
        """Find all files in directory and perform replacement"""
        count = 0
        try:
            for root, _, files in os.walk(directory):
                for filename in files:
                    filepath = os.path.join(root, filename)
                    if self.sed_replace(filepath, pattern, replacement):
                        count += 1
        except Exception as e:
            self.logger.error(f"Find and replace failed: {e}")

        return count

    def set_ownership(self, path: str, user: str, group: str, recursive: bool = False) -> bool:
        """Set file/directory ownership"""
        try:
            import pwd
            import grp

            uid = pwd.getpwnam(user).pw_uid
            gid = grp.getgrnam(group).gr_gid

            if recursive and os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    os.chown(root, uid, gid)
                    for d in dirs:
                        os.chown(os.path.join(root, d), uid, gid)
                    for f in files:
                        os.chown(os.path.join(root, f), uid, gid)
            else:
                os.chown(path, uid, gid)

            return True
        except Exception as e:
            self.logger.error(f"Failed to set ownership: {e}")
            return False

    def chmod(self, path: str, mode: int) -> bool:
        """Change file permissions"""
        try:
            os.chmod(path, mode)
            return True
        except Exception as e:
            self.logger.error(f"Failed to chmod {path}: {e}")
            return False

    def get_ssh_public_key(self, user: str = 'arista') -> Optional[str]:
        """Get the SSH public key for a user"""
        key_path = f'/home/{user}/.ssh/id_rsa.pub'
        try:
            with open(key_path, 'r') as f:
                return f.read().strip()
        except Exception as e:
            self.logger.error(f"Failed to read SSH key: {e}")
            return None


# =============================================================================
# Labguide Manager
# =============================================================================

class LabguideManager:
    """Manages lab guide downloads and extraction"""

    def __init__(self, config: ATDConfig):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)

    def get_labguide_url(self, project: str, topology: str) -> Optional[str]:
        """Get the labguide URL from metadata"""
        try:
            with open(self.config.metadata_path, 'r') as f:
                metadata = yaml.safe_load(f)

            return metadata.get('topologies', {}).get(project, {}).get(topology, {}).get('labguide_zipfile_url')
        except Exception as e:
            self.logger.error(f"Failed to get labguide URL: {e}")
            return None

    def download_and_extract(self, url: str) -> bool:
        """Download and extract labguide from URL"""
        if not url:
            self.logger.warning("No labguide URL provided")
            return False

        # Extract filename from URL
        filename = url.split('/')[-1]
        target_dir = self.config.labguide_directory
        target_file = os.path.join(target_dir, filename)

        # Create directory if it doesn't exist
        os.makedirs(target_dir, exist_ok=True)

        # Check if file already exists
        if os.path.exists(target_file):
            self.logger.info(f"File {filename} already exists. Nothing to do.")
            return True

        # Remove existing files
        for item in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item)
            if os.path.isfile(item_path):
                os.remove(item_path)
            elif os.path.isdir(item_path):
                shutil.rmtree(item_path)

        # Download using gsutil
        try:
            result = subprocess.run(
                ['gsutil', 'cp', url, target_dir],
                capture_output=True,
                text=True
            )

            if result.returncode != 0:
                self.logger.error(f"Failed to download labguide: {result.stderr}")
                return False

            # Extract tarball with path traversal protection
            with tarfile.open(target_file, 'r:gz') as tar:
                for member in tar.getmembers():
                    member_path = os.path.join(target_dir, member.name)
                    if not os.path.realpath(member_path).startswith(os.path.realpath(target_dir)):
                        self.logger.error(f"Tar path traversal blocked: {member.name}")
                        raise ValueError(f"Tar member {member.name} would extract outside target directory")
                tar.extractall(target_dir)

            self.logger.info(f"File {filename} downloaded and extracted to {target_dir}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to download/extract labguide: {e}")
            return False


# =============================================================================
# Exam Manager
# =============================================================================

class ExamManager:
    """Handles exam-related functionality"""

    def __init__(self, config: ATDConfig, config_manager: ConfigManager, docker_manager: DockerManager, systemd_manager: SystemdManager):
        self.config = config
        self.config_manager = config_manager
        self.docker = docker_manager
        self.systemd = systemd_manager
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize Cloud Logging
        self.cloud_logging = CloudLoggingManager()
        self.cloud_logging.setup(
            service_name='atd-exam',
            additional_labels={'operation': 'exam-management'}
        )

    def setup_exam_config(self, access_info: AccessInfo) -> None:
        """Setup exam configuration based on lab type"""
        if access_info.lab_type == 'Exam':
            self.logger.info(f"Exam Duration: {access_info.exam_duration}")
            self.config_manager.update_access_info_field('exam_duration', access_info.exam_duration)
            self.config_manager.update_access_info_field('examButtonNeeded', 'True')
            self.logger.info(
                f"Current topology is exam topology: {access_info.machine_name}, "
                f"exam duration: {access_info.exam_duration}"
            )
        else:
            self.config_manager.update_access_info_field('exam_duration', 0)
            self.config_manager.update_access_info_field('examButtonNeeded', 'False')

    def log_message(self, message: str) -> None:
        """Log a message to the exam log file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with open(self.config.exam_log_file, 'a') as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception as e:
            self.logger.error(f"Failed to write to exam log: {e}")

    def check_and_submit_exam(self) -> bool:
        """
        Check if exam time has expired and submit if needed.
        This is the Python equivalent of examSubmitContainer.sh
        """
        self.log_message("Starting exam-submission-check.service")

        # Get end exam time from ACCESS_INFO.yaml
        end_exam_time = self.config_manager.get_value(
            self.config.access_info_path,
            'endExamTime',
            default=0
        )

        try:
            end_exam_time = int(end_exam_time)
        except (ValueError, TypeError):
            end_exam_time = 0

        if end_exam_time == 0:
            self.log_message("Exam end time is 0. Exiting.")
            return True

        current_time = int(time.time())

        if current_time > end_exam_time:
            self.log_message("Exam duration expired. Executing container command...")

            # Log exam expiry to Cloud Logging
            self.cloud_logging.log_structured(
                "Exam duration expired, initiating submission",
                severity='INFO',
                labels={'service': 'atd-exam', 'event': 'exam-expired'},
                end_exam_time=end_exam_time,
                current_time=current_time
            )

            # Run upload command in container (matches examSubmitContainer.sh)
            success, output = self.docker.exec_command(
                'atd-login',
                ['sudo', 'python3', '-m', 'exam_upload_v2.main'],
                detach=True
            )

            if success:
                self.log_message("Successfully executed Docker command inside atd-login container.")
                self.cloud_logging.log_structured(
                    "Exam submission initiated successfully",
                    severity='INFO',
                    labels={'service': 'atd-exam', 'event': 'submission-success'}
                )
            else:
                self.log_message(f"ERROR: Docker command execution failed! {output}")
                self.cloud_logging.log_structured(
                    f"Exam submission failed: {output}",
                    severity='ERROR',
                    labels={'service': 'atd-exam', 'event': 'submission-failed'},
                    error=output
                )

            # Stop and disable the timer
            if self.systemd.stop('exam-submission-check.timer') and self.systemd.disable('exam-submission-check.timer'):
                self.log_message("Successfully stopped and disabled deploy-check timer.")
            else:
                self.log_message("ERROR: Failed to stop and disable deploy-check timer!")

            return success
        else:
            # Calculate remaining time
            time_remaining = end_exam_time - current_time
            hours_remaining = time_remaining // 3600
            minutes_remaining = (time_remaining % 3600) // 60
            end_time_readable = datetime.fromtimestamp(end_exam_time).strftime('%Y-%m-%d %H:%M:%S')

            self.log_message(
                f"Exam duration has not expired yet. Exam ends at {end_time_readable} "
                f"(in {hours_remaining} hours and {minutes_remaining} minutes). Exiting."
            )

            # Log remaining time to Cloud Logging (as debug/info)
            self.cloud_logging.log_structured(
                f"Exam check: {hours_remaining}h {minutes_remaining}m remaining",
                severity='INFO',
                labels={'service': 'atd-exam', 'event': 'exam-check'},
                hours_remaining=hours_remaining,
                minutes_remaining=minutes_remaining,
                end_exam_time=end_exam_time
            )
            return True


# =============================================================================
# Docker Authentication Manager
# =============================================================================

class DockerAuthManager:
    """Manages Docker registry authentication"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)

    def is_authenticated(self, user: str = 'atdadmin') -> bool:
        """Check if Docker auth file exists for user"""
        auth_file = f'/home/{user}/.docker/config.json'
        return os.path.exists(auth_file)

    def configure_auth(self, registries: List[str] = None) -> bool:
        """Configure Docker authentication for GCR"""
        if registries is None:
            registries = ['gcr.io', 'us.gcr.io']

        registry_str = ','.join(registries)

        try:
            # Configure for root
            subprocess.run(
                ['gcloud', 'auth', 'configure-docker', registry_str, '--quiet'],
                capture_output=True,
                text=True
            )

            # Configure for atdadmin
            subprocess.run(
                ['su', 'atdadmin', '-c', f'gcloud auth configure-docker {registry_str} --quiet'],
                capture_output=True,
                text=True
            )

            self.logger.info("Docker authentication configured")
            return True

        except Exception as e:
            self.logger.error(f"Failed to configure Docker auth: {e}")
            return False


# =============================================================================
# Main ATD Startup Class
# =============================================================================

class ATDStartup:
    """Main startup orchestrator - Python equivalent of atdStartup.sh"""

    def __init__(self, config: ATDConfig = None):
        self.config = config or ATDConfig()
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize Cloud Logging
        self.cloud_logging = CloudLoggingManager()
        self.cloud_logging.setup(
            service_name='atd-startup',
            additional_labels={'operation': 'startup'}
        )

        # Initialize managers
        self.config_manager = ConfigManager(self.config)
        self.file_manager = FileManager()
        self.network_manager = NetworkManager()
        self.systemd_manager = SystemdManager()
        self.docker_manager = DockerManager(self.config.docker_compose_path)
        self.docker_auth = DockerAuthManager()
        self.labguide_manager = LabguideManager(self.config)
        self.git_manager = GitManager(self.config.atd_opt_path)

        # Will be initialized after loading access info
        self.exam_manager = None
        self.access_info = None

    def run(self) -> bool:
        """Execute the full startup sequence"""
        self.logger.info("Starting ATD Startup")

        # Log startup event to Cloud Logging
        self.cloud_logging.log_structured(
            "ATD Startup initiated",
            severity='INFO',
            labels={'service': 'atd-startup', 'phase': 'init'}
        )

        try:
            # Step 1: Download base topology
            self.config_manager.download_base_topo()

            # Step 2: Load access info
            self.access_info = AccessInfo.from_yaml(self.config.access_info_path)

            # Initialize exam manager
            self.exam_manager = ExamManager(
                self.config,
                self.config_manager,
                self.docker_manager,
                self.systemd_manager
            )

            # Step 3: Setup exam configuration
            self.exam_manager.setup_exam_config(self.access_info)

            # Step 4: Determine and update branch name
            # Check for branch override file (used for testing/development)
            # Create /etc/atd/BRANCH_OVERRIDE with desired branch name to skip base_topo lookup
            branch = None
            override_path = self.config.branch_override_path
            if os.path.exists(override_path):
                try:
                    with open(override_path, 'r') as f:
                        override_branch = f.read().strip()
                    if override_branch:
                        branch = override_branch
                        self.logger.info(f"Branch override active: using '{branch}' from {override_path}")
                        self.cloud_logging.log_structured(
                            f"Branch override active: {branch}",
                            severity='WARNING',
                            labels={'service': 'atd-startup', 'phase': 'branch-override'},
                            override_branch=branch
                        )
                    else:
                        self.logger.warning(f"Branch override file exists but is empty, ignoring")
                        self.cloud_logging.log_structured(
                            "Branch override file exists but is empty, falling back to base_topo",
                            severity='WARNING',
                            labels={'service': 'atd-startup', 'phase': 'branch-override', 'status': 'empty-file'}
                        )
                except Exception as e:
                    self.logger.warning(f"Failed to read branch override file: {e}")
                    self.cloud_logging.log_structured(
                        f"Failed to read branch override file: {e}",
                        severity='ERROR',
                        labels={'service': 'atd-startup', 'phase': 'branch-override', 'status': 'read-error'},
                        error=str(e)
                    )

            if not branch:
                branch = self.config_manager.get_branch_name(self.access_info)

            if branch:
                self.config_manager.update_atd_repo_branch(branch)
                self.logger.info(f"Changing branch name to {branch}")

                # Switch to the new branch if it differs from the current one
                current_branch = self.git_manager.get_current_branch()
                if current_branch != branch:
                    self.logger.info(f"Branch changed from {current_branch} to {branch}, switching...")
                    self.git_manager.fetch()
                    if self.git_manager.checkout(branch):
                        self.git_manager.pull()
                        self.logger.info(f"Successfully switched to branch {branch}")
                    else:
                        self.logger.error(f"Failed to checkout branch {branch}")
            else:
                self.logger.info("Not changing any branch name")

            # Step 5: Setup NAT if cloudeos platform
            self._setup_network()

            # Step 6: Download and setup labguide
            self._setup_labguide()

            # Step 7: Update SSH key in configlet
            self._update_ssh_key()

            # Step 8: Replace password placeholders
            self._replace_password_placeholders()

            # Step 9: Setup Docker authentication
            if not self.docker_auth.is_authenticated():
                self.logger.info("Docker auth file not found, creating...")
                self.docker_auth.configure_auth()

            # Step 10: Update configlets for ceos/veos mgmt numbering
            self._update_mgmt_interface()

            # Step 11: Copy topology files
            self._copy_topology_files()

            # Step 12: Setup Docker containers
            self._setup_docker()

            # Step 13: (removed — sshd restart moved to Step 20 after config change)

            # Step 14: Run container labs setup if present
            self._run_container_labs_setup()

            # Step 15: Update routes for AlmaLinux
            self.network_manager.update_routes_for_almalinux()

            # Step 16: Run cEOS startup if present
            self._run_ceos_startup()

            # Step 17: Check unhealthy containers
            self._check_unhealthy_containers()

            # Step 18: Setup exam submission timer
            self._setup_exam_timer()

            # Step 19: Setup unit test timer
            self._setup_unit_test_timer()

            # Step 20: Setup instance metrics timer
            self._setup_metrics_timer()

            # Step 21: Enable password authentication for SSH
            self._enable_ssh_password_auth()

            self.logger.info("ATD Startup completed successfully")

            # Log success to Cloud Logging
            self.cloud_logging.log_structured(
                "ATD Startup completed successfully",
                severity='INFO',
                labels={'service': 'atd-startup', 'phase': 'complete', 'status': 'success'},
                topology=self.access_info.topology if self.access_info else 'unknown',
                lab_type=self.access_info.lab_type if self.access_info else 'unknown'
            )
            return True

        except Exception as e:
            self.logger.error(f"ATD Startup failed: {e}")

            # Log failure to Cloud Logging
            self.cloud_logging.log_structured(
                f"ATD Startup failed: {e}",
                severity='ERROR',
                labels={'service': 'atd-startup', 'phase': 'complete', 'status': 'failed'},
                error=str(e)
            )
            return False

    def _setup_network(self) -> None:
        """Setup network configuration based on platform"""
        platform = self.config_manager.get_value(
            self.config.base_topo_path,
            f'topologies.{self.access_info.topology}.platform'
        )

        if platform == 'cloudeos':
            self.logger.info("Setting up NAT for cloudeos topology")
            self.network_manager.setup_nat()
        else:
            self.logger.info("Not doing NAT here")

    def _setup_labguide(self) -> None:
        """Download and setup labguide"""
        url = self.labguide_manager.get_labguide_url(
            self.access_info.project,
            self.access_info.topology
        )
        if url:
            self.labguide_manager.download_and_extract(url)

    def _update_ssh_key(self) -> None:
        """Update SSH key in EOS configlet"""
        ssh_key = self.file_manager.get_ssh_public_key('arista')
        if ssh_key:
            configlet_path = f'{self.config.atd_opt_path}/topologies/{self.access_info.topology}/configlets/ATD-INFRA'
            self.file_manager.sed_replace(
                configlet_path,
                r'^username arista ssh-key.*$',
                f'username arista ssh-key {ssh_key}'
            )

    def _replace_password_placeholders(self) -> None:
        """Replace password placeholders in files"""
        password = self.access_info.password

        # Replace in atd-docker files
        docker_dir = f'{self.config.atd_opt_path}/nested-labvm/atd-docker'
        self.file_manager.find_and_replace(docker_dir, r'\{ARISTA_REPLACE\}', password)

        # Replace in topology files
        topo_files_dir = f'{self.config.atd_opt_path}/topologies/{self.access_info.topology}/files'
        self.file_manager.find_and_replace(topo_files_dir, r'\{ARISTA_REPLACE\}', password)

    def _update_mgmt_interface(self) -> None:
        """Update management interface naming for ceos"""
        eos_type = self.access_info.eos_type
        if eos_type in ['ceos', 'container-labs']:
            configlet_dir = f'{self.config.atd_opt_path}/topologies/{self.access_info.topology}/configlets'
            for filename in os.listdir(configlet_dir):
                filepath = os.path.join(configlet_dir, filename)
                if os.path.isfile(filepath):
                    self.file_manager.sed_replace(filepath, 'Management1', 'Management0')

    def _copy_topology_files(self) -> None:
        """Copy topology files to appropriate locations"""
        topo = self.access_info.topology
        topo_base = f'{self.config.atd_opt_path}/topologies/{topo}'

        # Copy topo image to app directory
        self.file_manager.rsync(
            f'{topo_base}/atd-topo.png',
            f'{topo_base}/files/apps/uilanding/'
        )

        # Add files to arista home
        self.file_manager.rsync(
            f'{topo_base}/files/',
            f'{self.config.arista_home}/arista-dir',
            update=True
        )
        self.file_manager.rsync(
            f'{topo_base}/files/infra',
            f'{self.config.arista_home}/'
        )

        # Copy scripts if present
        scripts_dir = f'{topo_base}/files/scripts'
        if os.path.isdir(scripts_dir):
            self.file_manager.rsync(
                scripts_dir,
                f'{self.config.arista_home}/GUI_Desktop/'
            )

        # Setup datacenter repo if needed
        repo_dir = f'{self.config.arista_home}/arista-dir/apps/coder/labfiles/lab6/repo'
        if not os.path.isdir(repo_dir) and topo == 'datacenter':
            os.makedirs(repo_dir, exist_ok=True)
            subprocess.run(['git', 'init', '--bare'], cwd=repo_dir)

        # Set ownership
        self.file_manager.set_ownership(self.config.arista_home, 'arista', 'arista', recursive=True)

    def _setup_docker(self) -> None:
        """Setup Docker containers"""
        # Export user IDs for coder container
        os.environ['ArID'] = str(subprocess.run(['id', '-u', 'arista'], capture_output=True, text=True).stdout.strip())
        os.environ['ArGD'] = str(subprocess.run(['id', '-g', 'arista'], capture_output=True, text=True).stdout.strip())
        os.environ['AtID'] = str(subprocess.run(['id', '-u', 'atdadmin'], capture_output=True, text=True).stdout.strip())
        os.environ['AtGD'] = str(subprocess.run(['id', '-g', 'atdadmin'], capture_output=True, text=True).stdout.strip())

        # Check if cloudeos platform and update compose file
        platform = self.config_manager.get_value(
            self.config.base_topo_path,
            f'topologies.{self.access_info.topology}.platform'
        )

        if platform == 'cloudeos':
            self.docker_manager.update_compose_image(
                'us.gcr.io/atd-testdrivetraining-dev/atddocker_coder:1.0.2',
                'us.gcr.io/atd-testdrivetraining-dev/atddocker_coder:1.0.2-path-finder'
            )

        # Run docker compose
        self.docker_manager.compose_up()
        self.docker_manager.prune_images()

    def _run_container_labs_setup(self) -> None:
        """Run container labs setup if present"""
        setup_script = '/opt/clab/scripts/containerlabs_setup.py'
        veth_script = '/opt/clab/scripts/veth-connection.sh'

        if os.path.exists(setup_script):
            subprocess.run(['bash', veth_script], capture_output=True)

    def _run_ceos_startup(self) -> None:
        """Run cEOS startup if present"""
        ceos_flag = '/opt/ceos/scripts/.ceos.txt'
        ceos_startup = '/opt/ceos/scripts/Startup.sh'
        ceos_timeout = 300  # 5 minutes

        if os.path.exists(ceos_flag):
            # Wait for startup script to appear with timeout
            elapsed = 0
            while not os.path.exists(ceos_startup):
                if elapsed >= ceos_timeout:
                    self.logger.error(f"cEOS Startup.sh did not appear after {ceos_timeout}s, skipping")
                    self.cloud_logging.log_structured(
                        f"cEOS Startup.sh wait timed out after {ceos_timeout}s",
                        severity='ERROR',
                        labels={'service': 'atd-startup', 'phase': 'ceos-startup', 'status': 'timeout'}
                    )
                    return
                self.logger.info("Pausing until cEOS Startup.sh exists.")
                time.sleep(1)
                elapsed += 1

            subprocess.run(['bash', ceos_startup])

    def _check_unhealthy_containers(self) -> None:
        """Check and handle unhealthy containers"""
        self.logger.info("Executing script to check unhealthy containers")

        recovery_flag = '/tmp/atd_recovery_attempt'

        # Run health check
        checker = ContainerHealthChecker(self.docker_manager)
        if not checker.check_container('atd-coder'):
            # Guard against infinite recursion
            if os.path.exists(recovery_flag):
                self.logger.error(
                    "Coder container unhealthy but recovery already attempted. "
                    "Skipping recursive atdUpdate to prevent infinite loop."
                )
                self.cloud_logging.log_structured(
                    "Container recovery failed - recursion guard prevented infinite loop",
                    severity='CRITICAL',
                    labels={'service': 'atd-startup', 'phase': 'container-health', 'status': 'recursion-blocked'}
                )
                return

            self.logger.warning("Coder container unhealthy, running atdUpdate (first attempt)")
            self.cloud_logging.log_structured(
                "Coder container unhealthy, attempting recovery via atdUpdate",
                severity='WARNING',
                labels={'service': 'atd-startup', 'phase': 'container-health', 'status': 'recovery-start'}
            )

            # Set flag before recursive call
            try:
                with open(recovery_flag, 'w') as f:
                    f.write(str(int(time.time())))
            except Exception:
                pass

            subprocess.run(['bash', '/usr/local/bin/atdUpdate.sh'])

            # Clean up flag after successful recovery
            try:
                os.remove(recovery_flag)
            except OSError:
                pass

    def _setup_exam_timer(self) -> None:
        """Setup exam submission timer"""
        self.file_manager.rsync(
            f'{self.config.atd_opt_path}/nested-labvm/services/atdStartup/examSubmitContainer.sh',
            '/usr/local/bin/'
        )

        self.systemd_manager.setup_timer(
            f'{self.config.atd_opt_path}/nested-labvm/services/atdStartup/exam-submission-check.service',
            f'{self.config.atd_opt_path}/nested-labvm/services/atdStartup/exam-submission-check.timer'
        )

    def _setup_unit_test_timer(self) -> None:
        """Setup unit test timer"""
        # Copy config and create log directory
        shutil.copy(self.config.unit_test_config_path, '/etc/atd/')
        self.file_manager.ensure_directory(self.config.log_dir, mode=0o755)

        # Copy and setup runner script
        self.logger.info("Installing unit test runner to /usr/local/bin/")
        runner_src = f'{self.config.atd_opt_path}/nested-labvm/services/unit-test/run_unit_tests.sh'
        shutil.copy(runner_src, '/usr/local/bin/')
        self.file_manager.chmod('/usr/local/bin/run_unit_tests.sh', 0o755)

        # Setup timers
        self.logger.info("Installing unit test timer service")
        unit_test_dir = f'{self.config.atd_opt_path}/nested-labvm/services/unit-test'

        for timer_file in ['atd-unit-test.timer', 'atd-unit-test-60min.timer']:
            timer_path = os.path.join(unit_test_dir, timer_file)
            if os.path.exists(timer_path):
                shutil.copy(timer_path, '/etc/systemd/system/')

        shutil.copy(os.path.join(unit_test_dir, 'atd-unit-test.service'), '/etc/systemd/system/')

        self.systemd_manager.daemon_reload()
        # self.systemd_manager.enable('atd-unit-test.timer')
        # self.systemd_manager.enable('atd-unit-test-60min.timer')
        # self.systemd_manager.start('atd-unit-test.timer')
        # self.systemd_manager.start('atd-unit-test-60min.timer')

    def _setup_metrics_timer(self) -> None:
        """Setup instance metrics collection timer"""
        metrics_script = f'{self.config.atd_opt_path}/nested-labvm/services/instance-metrics/instance_metrics.py'
        self.file_manager.rsync(metrics_script, '/usr/local/bin/')
        self.file_manager.chmod('/usr/local/bin/instance_metrics.py', 0o755)

        self.systemd_manager.setup_timer(
            f'{self.config.atd_opt_path}/nested-labvm/services/atdStartup/instance-metrics.service',
            f'{self.config.atd_opt_path}/nested-labvm/services/atdStartup/instance-metrics.timer'
        )

    def _enable_ssh_password_auth(self) -> None:
        """Enable password authentication for SSH"""
        self.logger.info("Enabling password authentication for SSH")
        self.file_manager.sed_replace(
            '/etc/ssh/sshd_config',
            r'^PasswordAuthentication no',
            'PasswordAuthentication yes'
        )
        self.systemd_manager.restart('sshd')


# =============================================================================
# Main Entry Point
# =============================================================================

def main():
    """Main entry point for the ATD Manager"""
    parser = argparse.ArgumentParser(
        description='ATD Manager - Manage ATD startup and maintenance tasks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 atd_manager.py startup           # Run ATD startup sequence
    python3 atd_manager.py check-containers  # Check container health
    python3 atd_manager.py check-exam        # Check exam submission status
        """
    )

    parser.add_argument(
        'command',
        choices=['startup', 'check-containers', 'check-exam'],
        help='Command to execute'
    )

    parser.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    args = parser.parse_args()

    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    config = ATDConfig()

    if args.command == 'startup':
        startup = ATDStartup(config)
        success = startup.run()
        sys.exit(0 if success else 1)

    elif args.command == 'check-containers':
        docker_manager = DockerManager(config.docker_compose_path)
        checker = ContainerHealthChecker(docker_manager)

        # Check common containers
        containers = ['atd-coder', 'atd-login', 'atd-guac']
        all_healthy = True

        for container in containers:
            if docker_manager.is_container_running(container):
                print(f"✓ {container} is running")
            else:
                print(f"✗ {container} is NOT running")
                all_healthy = False

        sys.exit(0 if all_healthy else 1)

    elif args.command == 'check-exam':
        config_manager = ConfigManager(config)
        docker_manager = DockerManager(config.docker_compose_path)
        systemd_manager = SystemdManager()

        exam_manager = ExamManager(config, config_manager, docker_manager, systemd_manager)
        success = exam_manager.check_and_submit_exam()
        sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
ATD Topology Converter v2
Converts ATD lab from one topology to another

This script integrates with the existing atdUpdate/atdStartup workflow:
1. Pre-flight checks (validate everything before making changes)
2. Backup current state
3. Clean up CVP (remove old devices to prevent duplicates)
4. Destroys existing VMs and OVS networks
5. Updates ACCESS_INFO.yaml with new topology
6. Calls atdStartup.sh to rebuild everything
7. Wait for kvmbuilder to generate scripts
8. Create OVS bridges and VMs
9. Reset CVP configuration flag to trigger reconfiguration

Rollback capability:
- If conversion fails after Phase 4 (ACCESS_INFO updated), can restore from backup
- State is tracked to allow resumption of failed conversions
"""

import os
import sys
import subprocess
import shutil
import time
import logging
import json
from pathlib import Path
from datetime import datetime
from ruamel.yaml import YAML

# Configuration
ACCESS_INFO_FILE = '/etc/atd/ACCESS_INFO.yaml'
TOPOLOGIES_DIR = '/opt/atd/topologies'
ATD_STARTUP_SCRIPT = '/usr/local/bin/atdStartup.sh'
ATD_UPDATE_SCRIPT = '/usr/local/bin/atdUpdate.sh'
KVM_SCRIPTS_DIR = '/home/atdadmin/KVM_scripts'
STATE_FILE = '/var/log/topology_converter_state.json'
BACKUP_DIR = '/var/log/topology_converter_backups'

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler('/var/log/topology_conversion.log'),
        logging.StreamHandler(sys.stdout)
    ]
)


class ConversionState:
    """Track conversion state for resumption and rollback"""

    PHASES = [
        'preflight',
        'backup',
        'cvp_cleanup',
        'destroy_vms',
        'destroy_ovs',
        'update_config',
        'libvirtd',
        'atd_startup',
        'wait_kvmbuilder',
        'create_ovs',
        'create_vms',
        'cvp_reset',
        'completed'
    ]

    def __init__(self):
        self.source_topology = None
        self.target_topology = None
        self.backup_file = None
        self.current_phase = None
        self.started_at = None
        self.completed_phases = []
        self.errors = []

    def save(self):
        """Save state to file"""
        state = {
            'source_topology': self.source_topology,
            'target_topology': self.target_topology,
            'backup_file': self.backup_file,
            'current_phase': self.current_phase,
            'started_at': self.started_at,
            'completed_phases': self.completed_phases,
            'errors': self.errors
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

    def load(self):
        """Load state from file"""
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                self.source_topology = state.get('source_topology')
                self.target_topology = state.get('target_topology')
                self.backup_file = state.get('backup_file')
                self.current_phase = state.get('current_phase')
                self.started_at = state.get('started_at')
                self.completed_phases = state.get('completed_phases', [])
                self.errors = state.get('errors', [])
                return True
        return False

    def clear(self):
        """Clear state file"""
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    def mark_phase_complete(self, phase):
        """Mark a phase as complete"""
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)
        self.current_phase = phase
        self.save()

    def add_error(self, error):
        """Add an error to the state"""
        self.errors.append({
            'time': datetime.now().isoformat(),
            'error': str(error)
        })
        self.save()


class TopologyConverter:
    """Handles conversion between ATD topologies"""

    def __init__(self, source_topology=None, target_topology=None):
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.yaml.default_flow_style = False
        self.logger = logging.getLogger(self.__class__.__name__)
        self.state = ConversionState()

        # Read current topology from ACCESS_INFO.yaml if source not specified
        if source_topology is None:
            self.source_topology = self._get_current_topology()
        else:
            self.source_topology = source_topology

        self.target_topology = target_topology

        # Initialize state
        self.state.source_topology = self.source_topology
        self.state.target_topology = self.target_topology
        self.state.started_at = datetime.now().isoformat()

        self.logger.info(f"Initialized converter: {self.source_topology} → {self.target_topology}")

    def _get_current_topology(self):
        """Read current topology from ACCESS_INFO.yaml"""
        try:
            with open(ACCESS_INFO_FILE, 'r') as f:
                data = self.yaml.load(f)
                return data.get('topology', '')
        except Exception as e:
            self.logger.error(f"Failed to read ACCESS_INFO.yaml: {e}")
            return None

    def _run_command(self, cmd, shell=True, check=True, timeout=300, capture=True):
        """Run a shell command and return output"""
        try:
            self.logger.info(f"Executing: {cmd}")
            result = subprocess.run(
                cmd,
                shell=shell,
                check=check,
                capture_output=capture,
                text=True,
                timeout=timeout
            )
            if result.stdout and result.stdout.strip():
                self.logger.debug(result.stdout)
            return result
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Command failed: {cmd}")
            if e.stderr:
                self.logger.error(f"Error: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            self.logger.error(f"Command timed out: {cmd}")
            raise

    # =========================================================================
    # PRE-FLIGHT CHECKS
    # =========================================================================

    def preflight_checks(self):
        """
        Run all pre-flight checks before making any changes.
        This ensures we can complete the conversion before starting.
        """
        self.logger.info("Running pre-flight checks...")
        checks_passed = True

        # Check 1: Validate topologies exist
        if not self._check_topologies_exist():
            checks_passed = False

        # Check 2: Check if same topology
        if self.source_topology == self.target_topology:
            self.logger.error(f"Source and target topology are the same: {self.source_topology}")
            self.logger.error("No conversion needed.")
            checks_passed = False

        # Check 3: Check libvirtd is available
        if not self._check_libvirtd_available():
            checks_passed = False

        # Check 4: Check OVS is available
        if not self._check_ovs_available():
            checks_passed = False

        # Check 5: Check Docker is available
        if not self._check_docker_available():
            checks_passed = False

        # Check 6: Check atdStartup.sh exists
        if not os.path.exists(ATD_STARTUP_SCRIPT):
            self.logger.error(f"atdStartup.sh not found at {ATD_STARTUP_SCRIPT}")
            checks_passed = False

        # Check 7: Check CVP VM exists (for vEOS topologies)
        if not self._check_cvp_exists():
            self.logger.warning("CVP VM not found - CVP configuration will be skipped")

        # Check 8: Check disk space
        if not self._check_disk_space():
            checks_passed = False

        if checks_passed:
            self.logger.info("✓ All pre-flight checks passed")
        else:
            self.logger.error("✗ Pre-flight checks failed")

        return checks_passed

    def _check_topologies_exist(self):
        """Check that source and target topologies exist"""
        source_path = Path(TOPOLOGIES_DIR) / self.source_topology
        target_path = Path(TOPOLOGIES_DIR) / self.target_topology

        if not self.source_topology:
            self.logger.error("Source topology not found in ACCESS_INFO.yaml")
            return False

        if not source_path.exists():
            self.logger.error(f"Source topology not found: {source_path}")
            return False

        if not target_path.exists():
            self.logger.error(f"Target topology not found: {target_path}")
            return False

        # Check required files in target topology
        required_files = [
            'topo_build.yml',
            'files/cvp/cvp_info.yaml'
        ]

        for req_file in required_files:
            file_path = target_path / req_file
            if not file_path.exists():
                self.logger.error(f"Required file missing in target topology: {file_path}")
                return False

        self.logger.info("  ✓ Topologies exist and have required files")
        return True

    def _check_libvirtd_available(self):
        """Check if libvirtd is available"""
        result = self._run_command("which virsh", check=False)
        if result.returncode != 0:
            self.logger.error("virsh command not found - libvirt not installed")
            return False
        self.logger.info("  ✓ libvirt is available")
        return True

    def _check_ovs_available(self):
        """Check if OVS is available"""
        result = self._run_command("which ovs-vsctl", check=False)
        if result.returncode != 0:
            self.logger.error("ovs-vsctl command not found - OVS not installed")
            return False
        self.logger.info("  ✓ Open vSwitch is available")
        return True

    def _check_docker_available(self):
        """Check if Docker is available"""
        result = self._run_command("docker ps", check=False)
        if result.returncode != 0:
            self.logger.error("Docker is not running or not accessible")
            return False
        self.logger.info("  ✓ Docker is available")
        return True

    def _check_cvp_exists(self):
        """Check if CVP VM exists"""
        result = self._run_command("virsh list --all --name | grep -i cvp", check=False)
        if result.returncode != 0 or not result.stdout.strip():
            return False
        self.logger.info("  ✓ CVP VM exists")
        return True

    def _check_disk_space(self, min_gb=10):
        """Check if there's enough disk space"""
        result = self._run_command("df -BG / | tail -1 | awk '{print $4}'", check=False)
        if result.returncode == 0:
            available = result.stdout.strip().replace('G', '')
            try:
                if int(available) < min_gb:
                    self.logger.error(f"Insufficient disk space: {available}GB available, {min_gb}GB required")
                    return False
            except ValueError:
                pass
        self.logger.info(f"  ✓ Disk space is adequate")
        return True

    # =========================================================================
    # TOPOLOGY INFO
    # =========================================================================

    def get_topology_info(self, topology_name):
        """Get information about a topology"""
        topo_path = Path(TOPOLOGIES_DIR) / topology_name
        topo_build_path = topo_path / 'topo_build.yml'

        info = {
            'name': topology_name,
            'path': str(topo_path),
            'nodes': [],
            'node_count': 0,
            'configlet_count': 0,
            'eos_type': 'unknown',
            'base_type': 'unknown'
        }

        # Read topo_build.yml
        if topo_build_path.exists():
            with open(topo_build_path, 'r') as f:
                topo_data = self.yaml.load(f)
                if 'nodes' in topo_data:
                    info['nodes'] = [list(node.keys())[0] for node in topo_data['nodes']]
                    info['node_count'] = len(info['nodes'])

        # Read cvp_info.yaml
        cvp_info_path = topo_path / 'files/cvp/cvp_info.yaml'
        if cvp_info_path.exists():
            with open(cvp_info_path, 'r') as f:
                cvp_data = self.yaml.load(f)
                # Count containers
                if 'cvp_info' in cvp_data and 'containers' in cvp_data['cvp_info']:
                    info['container_count'] = len(cvp_data['cvp_info']['containers'])

        # Count configlets
        configlet_dir = topo_path / 'configlets'
        if configlet_dir.exists():
            info['configlet_count'] = len([f for f in configlet_dir.iterdir() if f.is_file()])

        # Try to determine eos_type from ACCESS_INFO if this is current topology
        try:
            with open(ACCESS_INFO_FILE, 'r') as f:
                access_info = self.yaml.load(f)
                if access_info.get('topology') == topology_name:
                    info['eos_type'] = access_info.get('eos_type', 'unknown')
        except:
            pass

        return info

    # =========================================================================
    # BACKUP & RESTORE
    # =========================================================================

    def backup_current_state(self):
        """Backup current state for potential rollback"""
        self.logger.info("Creating backup of current state...")

        # Create backup directory
        os.makedirs(BACKUP_DIR, exist_ok=True)

        timestamp = int(time.time())
        backup_subdir = f"{BACKUP_DIR}/backup_{timestamp}"
        os.makedirs(backup_subdir, exist_ok=True)

        # Backup ACCESS_INFO.yaml
        backup_access_info = f"{backup_subdir}/ACCESS_INFO.yaml"
        shutil.copy2(ACCESS_INFO_FILE, backup_access_info)
        self.logger.info(f"  ✓ Backed up ACCESS_INFO.yaml")

        # Save current VM list
        result = self._run_command("virsh list --all --name", check=False)
        if result.returncode == 0:
            with open(f"{backup_subdir}/vm_list.txt", 'w') as f:
                f.write(result.stdout)
            self.logger.info(f"  ✓ Saved VM list")

        # Save current OVS bridges
        result = self._run_command("ovs-vsctl list-br", check=False)
        if result.returncode == 0:
            with open(f"{backup_subdir}/ovs_bridges.txt", 'w') as f:
                f.write(result.stdout)
            self.logger.info(f"  ✓ Saved OVS bridge list")

        # Save metadata
        metadata = {
            'timestamp': timestamp,
            'source_topology': self.source_topology,
            'target_topology': self.target_topology,
            'created_at': datetime.now().isoformat()
        }
        with open(f"{backup_subdir}/metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        self.state.backup_file = backup_subdir
        self.logger.info(f"✓ Backup saved to: {backup_subdir}")
        return backup_subdir

    def restore_from_backup(self, backup_path=None):
        """Restore ACCESS_INFO.yaml from backup"""
        if backup_path is None:
            backup_path = self.state.backup_file

        if not backup_path or not os.path.exists(backup_path):
            self.logger.error("No backup found to restore from")
            return False

        backup_access_info = f"{backup_path}/ACCESS_INFO.yaml"
        if os.path.exists(backup_access_info):
            shutil.copy2(backup_access_info, ACCESS_INFO_FILE)
            self.logger.info(f"✓ Restored ACCESS_INFO.yaml from {backup_access_info}")
            return True
        else:
            self.logger.error(f"Backup file not found: {backup_access_info}")
            return False

    # =========================================================================
    # CVP CLEANUP
    # =========================================================================

    def cleanup_cvp_devices(self):
        """
        Remove all EOS devices from CVP inventory.

        Must run AFTER VMs are destroyed (TerminAttr dead) so devices
        cannot re-register. CVP VM itself is preserved.
        """
        self.logger.info("Cleaning up CVP devices...")

        try:
            from cvprac.cvp_client import CvpClient

            yaml = YAML()
            with open(ACCESS_INFO_FILE, 'r') as f:
                data = yaml.load(f)

            cvp_nodes = data.get('nodes', {}).get('cvp', [])
            if not cvp_nodes:
                self.logger.warning("  No CVP node found in ACCESS_INFO — skipping cleanup")
                return

            cvp_ip = cvp_nodes[0]['ip']
            password = data['login_info']['jump_host']['pw']

            client = CvpClient()
            client.connect([cvp_ip], 'arista', password)

            devices = client.api.get_inventory()
            self.logger.info(f"  Found {len(devices)} devices in CVP inventory")

            if not devices:
                self.logger.info("  ✓ CVP inventory already empty")
                return

            removed = 0
            failed = 0
            for device in devices:
                hostname = device.get('hostname', 'unknown')
                mac = device.get('systemMacAddress', '')
                serial = device.get('serialNumber', '')

                self.logger.info(f"  Removing: {hostname}")
                try:
                    # Try decommission first — prevents auto re-registration
                    try:
                        client.api.device_decommissioning(serial, hostname + '_decom')
                        removed += 1
                        continue
                    except Exception:
                        pass

                    # Fallback to delete
                    client.api.delete_device(mac)
                    removed += 1
                except Exception as e:
                    self.logger.warning(f"  Failed to remove {hostname}: {e}")
                    failed += 1

            self.logger.info(f"  ✓ Removed {removed} devices from CVP ({failed} failed)")

            # Brief pause to let CVP process deletions
            if removed > 0:
                time.sleep(5)

        except ImportError:
            self.logger.warning("  cvprac not installed — skipping CVP cleanup")
        except Exception as e:
            # Non-fatal: conversion can proceed without CVP cleanup
            self.logger.warning(f"  CVP cleanup failed (non-fatal): {e}")
            self.logger.warning("  Continuing with conversion...")

    # =========================================================================
    # DESTRUCTION PHASE
    # =========================================================================

    def destroy_vms(self):
        """Destroy all running VMs except CVP"""
        self.logger.info("Destroying existing VMs...")

        # Get list of VMs
        result = self._run_command("virsh list --all --name", check=False)
        vms = [vm.strip() for vm in result.stdout.split('\n') if vm.strip()]

        if not vms:
            self.logger.info("✓ No VMs found to destroy")
            return

        destroyed = 0
        skipped = 0
        for vm in vms:
            # Skip CVP - it should persist across topology changes
            if 'cvp' in vm.lower():
                self.logger.info(f"  Skipping CVP VM: {vm}")
                skipped += 1
                continue

            self.logger.info(f"  Destroying VM: {vm}")
            # Try to destroy (stop) the VM
            self._run_command(f"virsh destroy {vm}", check=False)
            time.sleep(0.5)
            # Undefine (delete) the VM
            result = self._run_command(f"virsh undefine {vm}", check=False)
            if result.returncode == 0:
                destroyed += 1

        self.logger.info(f"✓ Destroyed {destroyed} VMs (skipped {skipped})")

    def destroy_ovs_networks(self):
        """Destroy all OVS bridges"""
        self.logger.info("Destroying OVS networks...")

        # Get list of bridges
        result = self._run_command("ovs-vsctl list-br", check=False)
        if result.returncode != 0:
            self.logger.warning("Failed to list OVS bridges (may not be running)")
            return

        bridges = [br.strip() for br in result.stdout.split('\n') if br.strip()]

        if not bridges:
            self.logger.info("✓ No OVS bridges found")
            return

        destroyed = 0
        for bridge in bridges:
            self.logger.info(f"  Deleting bridge: {bridge}")
            result = self._run_command(f"ovs-vsctl del-br {bridge}", check=False)
            if result.returncode == 0:
                destroyed += 1

        self.logger.info(f"✓ Destroyed {destroyed} OVS bridges")

    # =========================================================================
    # CONFIGURATION UPDATE
    # =========================================================================

    def update_access_info(self):
        """Update ACCESS_INFO.yaml with new topology"""
        self.logger.info(f"Updating ACCESS_INFO.yaml: {self.source_topology} → {self.target_topology}")

        with open(ACCESS_INFO_FILE, 'r') as f:
            data = self.yaml.load(f)

        # Update topology name
        old_topology = data.get('topology')
        data['topology'] = self.target_topology

        # Write back
        with open(ACCESS_INFO_FILE, 'w') as f:
            self.yaml.dump(data, f)

        # Verify
        with open(ACCESS_INFO_FILE, 'r') as f:
            verify_data = self.yaml.load(f)
            if verify_data.get('topology') == self.target_topology:
                self.logger.info(f"✓ ACCESS_INFO.yaml updated successfully")
                self.logger.info(f"  Old: {old_topology}")
                self.logger.info(f"  New: {self.target_topology}")
            else:
                raise ValueError("Failed to update ACCESS_INFO.yaml")

    # =========================================================================
    # BUILD PHASE
    # =========================================================================

    def ensure_libvirtd_running(self):
        """Ensure libvirtd service is running before creating VMs"""
        self.logger.info("Ensuring libvirtd service is running...")

        # Check if libvirtd is running
        result = self._run_command("systemctl is-active libvirtd", check=False)

        if result.returncode != 0 or 'inactive' in result.stdout.lower():
            self.logger.info("  libvirtd is not running, starting it...")
            self._run_command("systemctl start libvirtd", check=True)
            time.sleep(2)
            self.logger.info("✓ libvirtd started")
        else:
            self.logger.info("✓ libvirtd is already running")

    def run_atd_startup(self):
        """
        Run atdStartup.sh to rebuild the environment

        This will:
        - Download base topology
        - Setup Docker containers
        - Copy topology files
        - Configure network
        - And much more...

        WARNING: This will restart Docker containers including uilanding!
        """
        self.logger.info("Running atdStartup.sh to rebuild environment...")
        self.logger.info("WARNING: This will restart Docker containers!")
        self.logger.info("This may take several minutes...")

        if not os.path.exists(ATD_STARTUP_SCRIPT):
            raise FileNotFoundError(f"atdStartup.sh not found at {ATD_STARTUP_SCRIPT}")

        # Run atdStartup.sh without capturing output so we can see progress
        self.logger.info("=" * 60)
        result = self._run_command(
            f"bash {ATD_STARTUP_SCRIPT}",
            check=False,  # Don't fail on non-zero exit - atdStartup has various exit codes
            timeout=1800,  # 30 minutes timeout
            capture=False
        )
        self.logger.info("=" * 60)

        if result.returncode == 0:
            self.logger.info("✓ atdStartup.sh completed successfully")
        else:
            self.logger.warning(f"atdStartup.sh exited with code {result.returncode}")
            self.logger.warning("This may be expected - continuing with conversion")

    def wait_for_kvmbuilder(self, timeout=120):
        """Wait for kvmbuilder to generate scripts"""
        self.logger.info("Waiting for kvmbuilder to generate scripts...")

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.target_topology}'
        ovs_script = f'{scripts_dir}/{self.target_topology}-ovs-create.sh'
        kvm_script = f'{scripts_dir}/{self.target_topology}-kvm-create.sh'

        start_time = time.time()
        while time.time() - start_time < timeout:
            if os.path.exists(ovs_script) and os.path.exists(kvm_script):
                self.logger.info(f"✓ KVM scripts generated in {scripts_dir}")
                return True
            time.sleep(5)
            elapsed = int(time.time() - start_time)
            self.logger.info(f"  Waiting for scripts... ({elapsed}s)")

        self.logger.error(f"Timeout waiting for kvmbuilder scripts after {timeout}s")
        return False

    def create_ovs_bridges(self):
        """Create OVS bridges for the target topology"""
        self.logger.info("Creating OVS bridges...")

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.target_topology}'
        ovs_script = f'{scripts_dir}/{self.target_topology}-ovs-create.sh'

        if not os.path.exists(ovs_script):
            self.logger.error(f"OVS create script not found: {ovs_script}")
            return False

        self.logger.info(f"  Running: {ovs_script}")
        result = self._run_command(f"bash {ovs_script}", check=False)

        if result.returncode == 0:
            self.logger.info("✓ OVS bridges created successfully")
            return True
        else:
            self.logger.error(f"OVS script failed with code {result.returncode}")
            if result.stderr:
                self.logger.error(f"Error: {result.stderr}")
            return False

    def create_vms(self):
        """Create and start VMs for the target topology"""
        self.logger.info("Creating and starting VMs...")

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.target_topology}'
        kvm_script = f'{scripts_dir}/{self.target_topology}-kvm-create.sh'

        if not os.path.exists(kvm_script):
            self.logger.error(f"KVM create script not found: {kvm_script}")
            return False

        self.logger.info(f"  Running: {kvm_script}")
        # Must run from the scripts directory for XML files to be found
        result = self._run_command(
            f"cd {scripts_dir} && bash {self.target_topology}-kvm-create.sh",
            check=False,
            timeout=600  # 10 minutes for VM creation
        )

        if result.returncode != 0:
            self.logger.warning(f"KVM script returned code {result.returncode}")

        # Verify VMs are running
        result = self._run_command("virsh list --all", check=False)
        if result.returncode == 0:
            running_vms = [line for line in result.stdout.split('\n') if 'running' in line.lower()]
            self.logger.info(f"✓ {len(running_vms)} VMs are now running")
            return len(running_vms) > 0
        return False

    def verify_vms_running(self, expected_count=None):
        """Verify that VMs are running"""
        self.logger.info("Verifying VMs are running...")

        result = self._run_command("virsh list --all", check=False)
        if result.returncode != 0:
            self.logger.error("Failed to list VMs")
            return False

        running_vms = [line for line in result.stdout.split('\n') if 'running' in line.lower()]
        self.logger.info(f"  Found {len(running_vms)} running VMs")

        if expected_count and len(running_vms) < expected_count:
            self.logger.warning(f"Expected at least {expected_count} VMs, found {len(running_vms)}")
            return False

        return len(running_vms) > 0

    # =========================================================================
    # CVP RECONFIGURATION
    # =========================================================================

    def reset_cvp_config(self):
        """Reset CVP configuration flag to trigger reconfiguration"""
        self.logger.info("Resetting CVP configuration...")

        # Remove the flag file in cvpupdater container
        result = self._run_command(
            "docker exec atd-cvpupdater rm -f /home/arista/CVP_DATA/.cvpState.txt",
            check=False
        )

        if result.returncode == 0:
            self.logger.info("✓ CVP configuration flag removed")
        else:
            self.logger.warning("Failed to remove CVP flag (container may not be running yet)")

    def restart_cvpupdater(self):
        """Restart CVP updater to trigger reconfiguration"""
        self.logger.info("Restarting CVP updater...")

        result = self._run_command("docker restart atd-cvpupdater", check=False)

        if result.returncode == 0:
            self.logger.info("✓ CVP updater restarted")
            self.logger.info("  CVP will now register devices and push configlets")
            self.logger.info("  This takes several minutes - monitor with: docker logs -f atd-cvpupdater")
        else:
            self.logger.warning("Failed to restart CVP updater")

    def monitor_cvpupdater(self, timeout=900):
        """Monitor CVP updater logs for completion"""
        self.logger.info("Monitoring CVP updater (this may take several minutes)...")

        start_time = time.time()
        last_log_time = 0

        while time.time() - start_time < timeout:
            # Get recent logs
            result = self._run_command(
                "docker logs atd-cvpupdater 2>&1 | tail -10",
                check=False
            )

            if result.returncode == 0:
                output = result.stdout

                # Check for completion
                if "Completed CVP Configuration" in output:
                    self.logger.info("✓ CVP configuration completed successfully")
                    return True

                # Check for errors
                if "CVP is already configured" in output:
                    self.logger.info("✓ CVP already configured")
                    return True

                # Show progress every 30 seconds
                current_time = time.time()
                if current_time - last_log_time > 30:
                    # Extract last meaningful line
                    lines = [l.strip() for l in output.split('\n') if l.strip()]
                    if lines:
                        self.logger.info(f"  Status: {lines[-1][:100]}...")
                    last_log_time = current_time

            time.sleep(10)

        self.logger.warning("CVP updater monitoring timed out")
        self.logger.info("Check status manually with: docker logs atd-cvpupdater")
        return False

    # =========================================================================
    # MAIN CONVERSION FLOW
    # =========================================================================

    def convert(self, run_atd_startup=True, monitor_cvp=True, skip_phases=None):
        """
        Execute full topology conversion

        Args:
            run_atd_startup: Run atdStartup.sh to rebuild environment
            monitor_cvp: Monitor CVP updater completion
            skip_phases: List of phase names to skip (for resumption)
        """
        skip_phases = skip_phases or []

        try:
            self.logger.info("=" * 60)
            self.logger.info("ATD TOPOLOGY CONVERTER v2")
            self.logger.info("=" * 60)
            self.logger.info(f"Source: {self.source_topology}")
            self.logger.info(f"Target: {self.target_topology}")
            self.logger.info("=" * 60)
            self.logger.info("")

            # Phase 1: Pre-flight checks
            if 'preflight' not in skip_phases:
                self.logger.info("Phase 1: Pre-flight Checks")
                self.logger.info("-" * 60)
                if not self.preflight_checks():
                    raise ValueError("Pre-flight checks failed - aborting conversion")
                self.state.mark_phase_complete('preflight')
                self.logger.info("")

            # Get topology info for display
            source_info = self.get_topology_info(self.source_topology)
            target_info = self.get_topology_info(self.target_topology)

            self.logger.info(f"Source topology: {source_info['name']}")
            self.logger.info(f"  Nodes: {source_info['node_count']}")
            if source_info['nodes']:
                self.logger.info(f"  Devices: {', '.join(source_info['nodes'][:8])}")
                if len(source_info['nodes']) > 8:
                    self.logger.info(f"           (and {len(source_info['nodes']) - 8} more...)")

            self.logger.info(f"\nTarget topology: {target_info['name']}")
            self.logger.info(f"  Nodes: {target_info['node_count']}")
            if target_info['nodes']:
                self.logger.info(f"  Devices: {', '.join(target_info['nodes'][:8])}")
                if len(target_info['nodes']) > 8:
                    self.logger.info(f"           (and {len(target_info['nodes']) - 8} more...)")
            self.logger.info("")

            # Phase 2: Backup
            if 'backup' not in skip_phases:
                self.logger.info("Phase 2: Backup Current State")
                self.logger.info("-" * 60)
                backup_dir = self.backup_current_state()
                self.state.mark_phase_complete('backup')
                self.logger.info("")

            # Phase 3: Destroy VMs
            if 'destroy_vms' not in skip_phases:
                self.logger.info("Phase 3: Destroy Current VMs")
                self.logger.info("-" * 60)
                self.destroy_vms()
                self.state.mark_phase_complete('destroy_vms')
                self.logger.info("")

            # Phase 4: CVP Cleanup (after VMs destroyed, TerminAttr is dead)
            if 'cvp_cleanup' not in skip_phases:
                self.logger.info("Phase 4: CVP Device Cleanup")
                self.logger.info("-" * 60)
                self.cleanup_cvp_devices()
                self.state.mark_phase_complete('cvp_cleanup')
                self.logger.info("")

            # Phase 5: Destroy OVS
            if 'destroy_ovs' not in skip_phases:
                self.logger.info("Phase 5: Destroy OVS Networks")
                self.logger.info("-" * 60)
                self.destroy_ovs_networks()
                self.state.mark_phase_complete('destroy_ovs')
                self.logger.info("")

            # Phase 6: Update configuration
            if 'update_config' not in skip_phases:
                self.logger.info("Phase 6: Update Configuration")
                self.logger.info("-" * 60)
                self.update_access_info()
                self.state.mark_phase_complete('update_config')
                self.logger.info("")

            # Phase 7: Ensure libvirtd is running
            if 'libvirtd' not in skip_phases:
                self.logger.info("Phase 7: Ensure Libvirtd Running")
                self.logger.info("-" * 60)
                self.ensure_libvirtd_running()
                self.state.mark_phase_complete('libvirtd')
                self.logger.info("")

            # Phase 8: Run atdStartup
            if run_atd_startup and 'atd_startup' not in skip_phases:
                self.logger.info("Phase 8: Run atdStartup.sh")
                self.logger.info("-" * 60)
                self.logger.info("WARNING: Docker containers will be restarted!")
                self.run_atd_startup()
                self.state.mark_phase_complete('atd_startup')
                self.logger.info("")
            elif 'atd_startup' in skip_phases:
                self.logger.info("Phase 8: Run atdStartup.sh (SKIPPED)")
                self.logger.info("-" * 60)
                self.logger.info("")
            else:
                self.logger.info("Phase 8: Build New Topology (SKIPPED)")
                self.logger.info("-" * 60)
                self.logger.warning("atdStartup.sh execution skipped!")
                self.logger.warning("You need to manually run: bash /usr/local/bin/atdStartup.sh")
                self.logger.info("")

            # Phase 9: Wait for kvmbuilder
            if 'wait_kvmbuilder' not in skip_phases:
                self.logger.info("Phase 9: Wait for KVM Builder")
                self.logger.info("-" * 60)
                if not self.wait_for_kvmbuilder():
                    self.logger.warning("KVM scripts not found - VMs may need manual creation")
                self.state.mark_phase_complete('wait_kvmbuilder')
                self.logger.info("")

            # Phase 10: Create OVS bridges
            if 'create_ovs' not in skip_phases:
                self.logger.info("Phase 10: Create OVS Bridges")
                self.logger.info("-" * 60)
                self.create_ovs_bridges()
                self.state.mark_phase_complete('create_ovs')
                self.logger.info("")

            # Phase 11: Create VMs
            if 'create_vms' not in skip_phases:
                self.logger.info("Phase 11: Create and Start VMs")
                self.logger.info("-" * 60)
                self.create_vms()
                self.state.mark_phase_complete('create_vms')
                self.logger.info("")

            # Phase 12: Reset and restart CVP
            if 'cvp_reset' not in skip_phases:
                self.logger.info("Phase 12: Reconfigure CVP")
                self.logger.info("-" * 60)
                self.reset_cvp_config()
                time.sleep(2)
                self.restart_cvpupdater()
                self.state.mark_phase_complete('cvp_reset')
                self.logger.info("")

            # Phase 13: Monitor CVP (optional)
            if monitor_cvp:
                self.logger.info("Phase 13: Monitor CVP Updater")
                self.logger.info("-" * 60)
                self.monitor_cvpupdater()
                self.logger.info("")

            # Done
            self.state.mark_phase_complete('completed')
            self.state.clear()  # Clear state file on success

            self.logger.info("=" * 60)
            self.logger.info("TOPOLOGY CONVERSION COMPLETED!")
            self.logger.info("=" * 60)
            self.logger.info(f"✓ Converted from: {self.source_topology}")
            self.logger.info(f"✓ Converted to:   {self.target_topology}")
            self.logger.info(f"✓ Backup saved:   {self.state.backup_file}")
            self.logger.info("")
            self.logger.info("Note: CVP may still be pushing configs to devices.")
            self.logger.info("      Check CVP tasks or run: docker logs -f atd-cvpupdater")
            self.logger.info("=" * 60)

            return True

        except Exception as e:
            self.state.add_error(e)
            self.logger.error("=" * 60)
            self.logger.error(f"TOPOLOGY CONVERSION FAILED: {e}")
            self.logger.error("=" * 60)
            self.logger.error("")
            self.logger.error("Recovery options:")
            self.logger.error(f"  1. Restore backup: Copy {self.state.backup_file}/ACCESS_INFO.yaml to {ACCESS_INFO_FILE}")
            self.logger.error(f"  2. Resume conversion: Run script again with --resume flag")
            self.logger.error(f"  3. Manual recovery: Check /var/log/topology_conversion.log for details")
            self.logger.error("")
            raise


def main():
    """Main entry point for CLI usage"""
    import argparse

    parser = argparse.ArgumentParser(
        description='Convert ATD lab from one topology to another',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Convert to a different topology
    sudo python3 topology_converter_v2.py training-level4-v2

    # Dry run to see what would change
    sudo python3 topology_converter_v2.py training-level4-v2 --dry-run

    # Skip automatic rebuild (manual execution needed)
    sudo python3 topology_converter_v2.py training-level4-v2 --skip-startup

    # Resume a failed conversion
    sudo python3 topology_converter_v2.py training-level4-v2 --resume

Note: This script must be run as root (sudo)
        """
    )
    parser.add_argument(
        'target_topology',
        help='Target topology name (e.g., training-level4-v2)'
    )
    parser.add_argument(
        '--source',
        help='Source topology (defaults to current topology in ACCESS_INFO.yaml)'
    )
    parser.add_argument(
        '--skip-startup',
        action='store_true',
        help='Skip running atdStartup.sh (you will need to run it manually)'
    )
    parser.add_argument(
        '--no-monitoring',
        action='store_true',
        help='Skip monitoring CVP updater completion'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Validate only, do not make changes'
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help='Skip confirmation prompt (for automated execution)'
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume a previously failed conversion'
    )
    parser.add_argument(
        '--restore',
        metavar='BACKUP_PATH',
        help='Restore ACCESS_INFO.yaml from a backup'
    )

    args = parser.parse_args()

    # Check if running as root
    if os.geteuid() != 0:
        print("ERROR: This script must be run as root (use sudo)")
        sys.exit(1)

    # Handle restore
    if args.restore:
        converter = TopologyConverter(target_topology=args.target_topology)
        if converter.restore_from_backup(args.restore):
            print("Restore completed. You may need to run atdStartup.sh manually.")
            sys.exit(0)
        else:
            sys.exit(1)

    converter = TopologyConverter(
        source_topology=args.source,
        target_topology=args.target_topology
    )

    # Handle resume
    skip_phases = []
    if args.resume:
        if converter.state.load():
            skip_phases = converter.state.completed_phases
            print(f"Resuming conversion from phase after: {skip_phases[-1] if skip_phases else 'start'}")
        else:
            print("No previous conversion state found - starting fresh")

    if args.dry_run:
        print("\n" + "=" * 60)
        print("DRY RUN MODE - No changes will be made")
        print("=" * 60)

        try:
            if not converter.preflight_checks():
                print("\nPre-flight checks failed!")
                sys.exit(1)

            source_info = converter.get_topology_info(converter.source_topology)
            target_info = converter.get_topology_info(converter.target_topology)

            print(f"\nCurrent Topology: {source_info['name']}")
            print(f"  Nodes: {source_info['node_count']}")
            print(f"  Devices: {', '.join(source_info['nodes'])}")
            print(f"  Configlets: {source_info['configlet_count']}")

            print(f"\nTarget Topology: {target_info['name']}")
            print(f"  Nodes: {target_info['node_count']}")
            print(f"  Devices: {', '.join(target_info['nodes'])}")
            print(f"  Configlets: {target_info['configlet_count']}")

            print("\n" + "=" * 60)
            print("Validation successful - ready to convert")
            print("=" * 60)
        except Exception as e:
            print(f"\nValidation failed: {e}")
            sys.exit(1)

        return

    # Confirm with user (unless --force is used)
    if not args.force and not args.resume:
        print("\n" + "=" * 60)
        print("WARNING: This will destroy the current lab topology!")
        print("=" * 60)
        print(f"Current topology: {converter.source_topology}")
        print(f"Target topology:  {args.target_topology}")
        print("\nThis will:")
        print("  1. Run pre-flight checks")
        print("  2. Backup current state")
        print("  3. Destroy all VMs (except CVP)")
        print("  4. Delete all OVS networks")
        print("  5. Update ACCESS_INFO.yaml")
        print("  6. Run atdStartup.sh (this restarts Docker containers!)")
        print("  7. Wait for kvmbuilder to generate scripts")
        print("  8. Create OVS bridges and VMs")
        print("  9. Reconfigure CVP")
        print("")

        response = input("Proceed with conversion? (yes/no): ")

        if response.lower() != 'yes':
            print("Conversion cancelled")
            return
    else:
        print(f"\n[INFO] {'Resuming' if args.resume else 'Force mode enabled - skipping confirmation'}")
        print(f"[INFO] Converting from {converter.source_topology} to {args.target_topology}")

    print("")

    try:
        converter.convert(
            run_atd_startup=not args.skip_startup,
            monitor_cvp=not args.no_monitoring,
            skip_phases=skip_phases
        )
    except Exception as e:
        sys.exit(1)


if __name__ == '__main__':
    main()

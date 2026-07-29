#!/usr/bin/env python3
"""
ATD Base Image Builder — Host-Level Script

Runs directly on the host (via nsenter from uilanding). Builds a full KVM lab
environment by downloading CVP + EOS disk images from GCS and creating VMs.

Called by uilanding's base_image_builder.py via:
    docker run --rm --privileged --pid=host ... nsenter ... \
        python3 /opt/atd/scripts/base_image_builder_host.py \
        --cvp-version 2026.1.0 --eos-version 4.34.7M \
        --topology training-level-x-cl-veos --force

Phases:
    1. Validate selections and detect what needs rebuilding
    2. Cleanup (destroy VMs, OVS bridges, stale disk images)
    3. Download CVP disk images from GCS (skip if already installed)
    4. Download EOS base image from GCS (skip if already installed)
    5. Update ACCESS_INFO.yaml
    6. Run atdUpdate.sh (kvmbuilder + docker compose restart)
    7. Wait for kvmbuilder to generate KVM scripts
    8. Create OVS bridges
    9. Create and start VMs
   10. Verify VMs running

stdout is captured by uilanding for real-time progress display.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from ruamel.yaml import YAML

import re

ACCESS_INFO_FILE = '/etc/atd/ACCESS_INFO.yaml'
TOPOLOGIES_DIR = '/opt/atd/topologies'
ATD_UPDATE_SCRIPT = '/usr/local/bin/atdUpdate.sh'
KVM_SCRIPTS_DIR = '/home/atdadmin/KVM_scripts'
LIBVIRT_IMAGES_PATH = '/var/lib/libvirt/images'
GCS_BUCKET = 'topology_deploy_files'
STATE_FILE = '/var/log/base_image_builder_state.json'
UILANDING_STATUS_FILE = '/etc/atd/base_image_build_status.json'
LOG_FILE = '/var/log/base_image_build.log'
VERSION_PATTERN = re.compile(r'^[a-zA-Z0-9._-]+$')

# Map EOS version to GCS filename (flat bucket, not subfolders)
EOS_GCS_FILENAMES = {
    '4.34.7M': 'vEOS64-lab-4.34.7M.qcow2',
    '4.32.1F': 'vEOS64-lab-4.32.1F.qcow2',
    '4.35.0.2F': 'vEOS64-lab-4.35.0.2F.qcow2',
    '4.35.1F': 'vEOS64-lab-4.35.1F.qcow2',
    '4.30.1F': 'vEOS-lab-4.30.1F.vmdk',
    '4.31.1F': 'vEOS-lab-4.31.1F.vmdk',
    '4.29.1F': 'vEOS-lab-4.29.1F.vmdk',
}

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger('BaseImageBuilder')


class BuildState:
    """Track build state for resumption."""

    PHASES = [
        'validate', 'cleanup', 'download_cvp', 'download_eos',
        'update_config', 'atd_update', 'wait_kvmbuilder',
        'create_ovs', 'create_vms', 'cvp_reset', 'verify', 'completed'
    ]

    def __init__(self):
        self.cvp_version = None
        self.eos_version = None
        self.topology = None
        self.eos_type = None
        self.current_phase = None
        self.started_at = None
        self.completed_phases = []
        self.errors = []
        self.skip_cvp = False
        self.skip_eos = False

    def save(self):
        state = {
            'cvp_version': self.cvp_version,
            'eos_version': self.eos_version,
            'topology': self.topology,
            'eos_type': self.eos_type,
            'current_phase': self.current_phase,
            'started_at': self.started_at,
            'completed_phases': self.completed_phases,
            'errors': self.errors,
            'skip_cvp': self.skip_cvp,
            'skip_eos': self.skip_eos,
        }
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

    def load(self):
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
            self.cvp_version = state.get('cvp_version')
            self.eos_version = state.get('eos_version')
            self.topology = state.get('topology')
            self.eos_type = state.get('eos_type')
            self.current_phase = state.get('current_phase')
            self.started_at = state.get('started_at')
            self.completed_phases = state.get('completed_phases', [])
            self.errors = state.get('errors', [])
            self.skip_cvp = state.get('skip_cvp', False)
            self.skip_eos = state.get('skip_eos', False)
            return True
        return False

    def clear(self):
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    def mark_phase_complete(self, phase):
        if phase not in self.completed_phases:
            self.completed_phases.append(phase)
        self.current_phase = phase
        self.save()

    def add_error(self, error):
        self.errors.append({
            'time': datetime.now().isoformat(),
            'error': str(error),
        })
        self.save()

    def notify_uilanding(self, success, status_msg):
        """Write completion status to /etc/atd/ so uilanding can read it after restart."""
        try:
            snapshot = {
                'in_progress': False,
                'phase': 'completed',
                'status': status_msg,
                'completed': True,
                'success': success,
                'log': [],
            }
            tmp = UILANDING_STATUS_FILE + '.tmp'
            with open(tmp, 'w') as f:
                json.dump(snapshot, f)
            os.replace(tmp, UILANDING_STATUS_FILE)
        except Exception as e:
            logger.warning(f"Failed to notify uilanding: {e}")


class BaseImageBuilder:
    """Builds ATD base images on the host."""

    def __init__(self, cvp_version, eos_version, topology, eos_type='veos',
                 skip_update=False):
        self.yaml = YAML()
        self.yaml.preserve_quotes = True
        self.skip_update = skip_update

        if not VERSION_PATTERN.match(cvp_version):
            raise ValueError(f"Invalid CVP version format: {cvp_version}")
        if not VERSION_PATTERN.match(eos_version):
            raise ValueError(f"Invalid EOS version format: {eos_version}")

        self.cvp_version = cvp_version
        self.eos_version = eos_version
        self.topology = topology
        self.eos_type = eos_type
        self.state = BuildState()
        self.state.cvp_version = cvp_version
        self.state.eos_version = eos_version
        self.state.topology = topology
        self.state.eos_type = eos_type
        self.state.started_at = datetime.now().isoformat()

    def _run_command(self, cmd, shell=True, check=True, timeout=300, capture=True):
        try:
            logger.info(f"Executing: {cmd}")
            result = subprocess.run(
                cmd, shell=shell, check=check,
                capture_output=capture, text=True, timeout=timeout,
            )
            if result.stdout and result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    logger.info(f"  {line}")
            return result
        except subprocess.CalledProcessError as e:
            logger.error(f"Command failed: {cmd}")
            if e.stderr:
                logger.error(f"Error: {e.stderr}")
            raise
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {timeout}s: {cmd}")
            raise

    def _get_current_config(self):
        """Read current CVP, EOS, topology from ACCESS_INFO."""
        try:
            with open(ACCESS_INFO_FILE, 'r') as f:
                data = self.yaml.load(f)
            return {
                'cvp': str(data.get('cvp', '')),
                'eos': str(data.get('version', '')),
                'topology': str(data.get('topology', '')),
            }
        except Exception as e:
            logger.warning(f"Could not read ACCESS_INFO: {e}")
            return {'cvp': '', 'eos': '', 'topology': ''}

    # =========================================================================
    # Phase 1: Validate
    # =========================================================================

    def phase_validate(self):
        logger.info("Phase 1: Validate")
        logger.info("-" * 60)
        logger.info(f"  CVP Version:  {self.cvp_version}")
        logger.info(f"  EOS Version:  {self.eos_version}")
        logger.info(f"  Topology:     {self.topology}")
        logger.info(f"  EOS Type:     {self.eos_type}")

        topo_path = Path(TOPOLOGIES_DIR) / self.topology
        if not topo_path.exists():
            raise ValueError(f"Topology not found: {topo_path}")

        topo_build = topo_path / 'topo_build.yml'
        if not topo_build.exists():
            raise ValueError(f"topo_build.yml not found: {topo_build}")

        with open(topo_build, 'r') as f:
            data = self.yaml.load(f)
        nodes = []
        if data and 'nodes' in data:
            nodes = [list(n.keys())[0] for n in data['nodes']]
        logger.info(f"  Nodes ({len(nodes)}): {', '.join(nodes)}")

        # Detect what's already installed
        current = self._get_current_config()
        logger.info(f"  Current CVP:      {current['cvp']}")
        logger.info(f"  Current EOS:      {current['eos']}")
        logger.info(f"  Current Topology: {current['topology']}")

        cvp_disks_present = (
            os.path.exists(f'{LIBVIRT_IMAGES_PATH}/cvp1/disk1.qcow2') and
            os.path.exists(f'{LIBVIRT_IMAGES_PATH}/cvp1/disk2.qcow2')
        )

        eos_base_present = os.path.exists(f'{LIBVIRT_IMAGES_PATH}/veos/base/veos.qcow2')

        self.state.skip_cvp = (current['cvp'] == self.cvp_version) and cvp_disks_present
        self.state.skip_eos = (current['eos'] == self.eos_version) and eos_base_present

        if self.state.skip_cvp:
            logger.info(f"  CVP {self.cvp_version} already installed — will SKIP download")
        else:
            logger.info(f"  CVP needs download: {current['cvp']} -> {self.cvp_version}")

        if self.state.skip_eos:
            logger.info(f"  EOS {self.eos_version} already installed — will SKIP download")
        else:
            logger.info(f"  EOS needs download: {current['eos']} -> {self.eos_version}")

        # Check disk space (need ~60GB for full CVP+EOS download)
        min_gb = 10 if (self.state.skip_cvp and self.state.skip_eos) else 70
        result = self._run_command("df -BG / | tail -1 | awk '{print $4}'", check=False)
        if result.returncode == 0:
            available = result.stdout.strip().replace('G', '')
            logger.info(f"  Disk space available: {available}GB (need {min_gb}GB)")
            try:
                if int(available) < min_gb:
                    raise RuntimeError(
                        f"Insufficient disk space: {available}GB available, {min_gb}GB required"
                    )
            except ValueError:
                pass

        # Check libvirtd
        result = self._run_command("which virsh", check=False)
        if result.returncode != 0:
            raise RuntimeError("virsh not found — libvirt not installed")

        # Check OVS
        result = self._run_command("which ovs-vsctl", check=False)
        if result.returncode != 0:
            raise RuntimeError("ovs-vsctl not found — OVS not installed")

        logger.info("  ✓ Validation passed")

    # =========================================================================
    # Phase 2: Cleanup
    # =========================================================================

    def phase_cleanup(self):
        logger.info("Phase 2: Cleanup")
        logger.info("-" * 60)

        # Destroy EOS VMs (keep CVP if version unchanged)
        logger.info("  Destroying EOS VMs...")
        result = self._run_command("virsh list --all --name", check=False)
        vms = [v.strip() for v in result.stdout.split('\n') if v.strip()]

        for vm in vms:
            if 'cvp' in vm.lower() and self.state.skip_cvp:
                logger.info(f"    Skipping CVP VM: {vm} (version unchanged)")
                continue
            self._run_command(f"virsh destroy {vm}", check=False)
            self._run_command(f"virsh undefine {vm}", check=False)
            logger.info(f"    Removed VM: {vm}")

        # Delete OVS bridges (preserve vmgmt)
        logger.info("  Deleting OVS bridges...")
        result = self._run_command("ovs-vsctl list-br", check=False)
        if result.returncode == 0:
            bridges = [b.strip() for b in result.stdout.split('\n') if b.strip()]
            for br in bridges:
                if br == 'vmgmt':
                    continue
                self._run_command(f"ovs-vsctl del-br {br}", check=False)
                logger.info(f"    Deleted bridge: {br}")

        # Clean EOS node disks (uppercase + lowercase)
        logger.info("  Cleaning EOS node disks...")
        self._run_command(
            f"rm -f {LIBVIRT_IMAGES_PATH}/veos/[a-zA-Z]*.qcow2",
            check=False
        )
        self._run_command(f"mkdir -p {LIBVIRT_IMAGES_PATH}/veos/base", check=False)

        if not self.state.skip_cvp:
            logger.info("  Cleaning CVP disks (version change)...")
            self._run_command(f"rm -f {LIBVIRT_IMAGES_PATH}/cvp1/*.qcow2", check=False)
            self._run_command(f"mkdir -p {LIBVIRT_IMAGES_PATH}/cvp1", check=False)

        if not self.state.skip_eos:
            logger.info("  Cleaning EOS base image (version change)...")
            self._run_command(f"rm -f {LIBVIRT_IMAGES_PATH}/veos/base/*.qcow2", check=False)
            self._run_command(f"mkdir -p {LIBVIRT_IMAGES_PATH}/veos/base", check=False)

        logger.info("  ✓ Cleanup complete")

    # =========================================================================
    # Phase 3: Download CVP
    # =========================================================================

    def phase_download_cvp(self):
        logger.info("Phase 3: Download CVP")
        logger.info("-" * 60)

        if self.state.skip_cvp:
            logger.info(f"  SKIPPED — CVP {self.cvp_version} already installed")
            return

        for disk in ['disk1.qcow2', 'disk2.qcow2']:
            gcs_path = f'gs://{GCS_BUCKET}/cvp/{self.cvp_version}/{disk}'
            local_path = f'{LIBVIRT_IMAGES_PATH}/cvp1/{disk}'
            logger.info(f"  Downloading {gcs_path}...")
            self._run_command(
                f'gsutil -m cp {gcs_path} {local_path}',
                timeout=3600,
            )
            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            logger.info(f"  Downloaded {disk}: {size_mb:.0f} MB")

        logger.info("  ✓ CVP download complete")

    # =========================================================================
    # Phase 4: Download EOS
    # =========================================================================

    def phase_download_eos(self):
        logger.info("Phase 4: Download EOS")
        logger.info("-" * 60)

        if self.state.skip_eos:
            logger.info(f"  SKIPPED — EOS {self.eos_version} already installed")
            return

        if self.eos_type == 'veos':
            gcs_filename = EOS_GCS_FILENAMES.get(self.eos_version)
            if not gcs_filename:
                gcs_filename = f'vEOS64-lab-{self.eos_version}.qcow2'
                logger.info(f"  No mapped filename, trying: {gcs_filename}")

            gcs_path = f'gs://{GCS_BUCKET}/veos/{gcs_filename}'
            local_path = f'{LIBVIRT_IMAGES_PATH}/veos/base/veos.qcow2'

            if gcs_filename.endswith('.vmdk'):
                vmdk_tmp = f'{LIBVIRT_IMAGES_PATH}/veos/base/veos-tmp.vmdk'
                logger.info(f"  Downloading VMDK: {gcs_path}...")
                self._run_command(f'gsutil -m cp {gcs_path} {vmdk_tmp}', timeout=1800)
                logger.info("  Converting VMDK to qcow2...")
                self._run_command(
                    f'qemu-img convert -f vmdk -O qcow2 {vmdk_tmp} {local_path}',
                    timeout=600,
                )
                os.remove(vmdk_tmp)
            else:
                logger.info(f"  Downloading {gcs_path}...")
                self._run_command(f'gsutil -m cp {gcs_path} {local_path}', timeout=1800)

            size_mb = os.path.getsize(local_path) / (1024 * 1024)
            logger.info(f"  Downloaded vEOS: {size_mb:.0f} MB")
        else:
            gcs_path = f'gs://{GCS_BUCKET}/ceos/{self.eos_version}/'
            local_path = f'{LIBVIRT_IMAGES_PATH}/ceos/'
            self._run_command(
                f'mkdir -p {local_path} && gsutil -m cp -r {gcs_path} {local_path}',
                timeout=1800,
            )

        logger.info("  ✓ EOS download complete")

    # =========================================================================
    # Phase 5: Update config
    # =========================================================================

    def phase_update_config(self):
        logger.info("Phase 5: Update Configuration")
        logger.info("-" * 60)

        with open(ACCESS_INFO_FILE, 'r') as f:
            data = self.yaml.load(f)

        old_topo = data.get('topology')
        data['topology'] = self.topology
        data['eos_type'] = self.eos_type
        data['cvp'] = self.cvp_version
        data['version'] = self.eos_version

        with open(ACCESS_INFO_FILE, 'w') as f:
            self.yaml.dump(data, f)

        logger.info(f"  topology:  {old_topo} -> {self.topology}")
        logger.info(f"  cvp:       {self.cvp_version}")
        logger.info(f"  version:   {self.eos_version}")
        logger.info(f"  eos_type:  {self.eos_type}")
        logger.info("  ✓ ACCESS_INFO.yaml updated")

    # =========================================================================
    # Phase 6: atdUpdate
    # =========================================================================

    def phase_atd_update(self):
        logger.info("Phase 6: Run atdUpdate.sh")
        logger.info("-" * 60)
        logger.info("  WARNING: Docker containers will be restarted!")

        if not os.path.exists(ATD_UPDATE_SCRIPT):
            raise FileNotFoundError(f"atdUpdate.sh not found at {ATD_UPDATE_SCRIPT}")

        result = self._run_command(
            f'bash {ATD_UPDATE_SCRIPT}',
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            logger.warning(f"  atdUpdate.sh exited with code {result.returncode} (may be expected)")
        logger.info("  ✓ atdUpdate.sh completed")

    # =========================================================================
    # Phase 7: Wait for KVM scripts
    # =========================================================================

    def phase_wait_kvmbuilder(self):
        logger.info("Phase 7: Wait for KVM Builder")
        logger.info("-" * 60)

        # When called with --skip-update (from atdStartup), kvmbuilder already
        # ran with the OLD topology from compose_up. Phase 5 updated ACCESS_INFO
        # with the new topology. Restart kvmbuilder so it re-reads the config
        # and generates scripts for the correct topology.
        if self.skip_update:
            logger.info("  Restarting kvmbuilder for updated topology...")
            self._run_command('docker restart atd-kvmbuilder', check=False, timeout=30)
            time.sleep(3)

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.topology}'
        ovs_script = f'{scripts_dir}/{self.topology}-ovs-create.sh'
        kvm_script = f'{scripts_dir}/{self.topology}-kvm-create.sh'

        timeout = 180
        start = time.time()
        while time.time() - start < timeout:
            if os.path.exists(ovs_script) and os.path.exists(kvm_script):
                elapsed = int(time.time() - start)
                logger.info(f"  ✓ KVM scripts found in {elapsed}s")
                return
            elapsed = int(time.time() - start)
            logger.info(f"  Waiting for KVM scripts... ({elapsed}s/{timeout}s)")
            time.sleep(5)

        raise RuntimeError(f"KVM scripts not found after {timeout}s in {scripts_dir}")

    # =========================================================================
    # Phase 8: Create OVS bridges
    # =========================================================================

    def phase_create_ovs(self):
        logger.info("Phase 8: Create OVS Bridges")
        logger.info("-" * 60)

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.topology}'
        ovs_script = f'{scripts_dir}/{self.topology}-ovs-create.sh'

        if not os.path.exists(ovs_script):
            raise FileNotFoundError(f"OVS script not found: {ovs_script}")

        result = self._run_command(f'bash {ovs_script}', check=False, timeout=120)
        if result.returncode != 0:
            logger.warning(f"  OVS create script returned code {result.returncode}")
        logger.info("  ✓ OVS bridges created")

    # =========================================================================
    # Phase 9: Create and start VMs
    # =========================================================================

    def phase_create_vms(self):
        logger.info("Phase 9: Create and Start VMs")
        logger.info("-" * 60)

        scripts_dir = f'{KVM_SCRIPTS_DIR}/{self.topology}'
        kvm_script = f'{scripts_dir}/{self.topology}-kvm-create.sh'

        if not os.path.exists(kvm_script):
            raise FileNotFoundError(f"KVM script not found: {kvm_script}")

        # Create cvp symlink for KVM script compatibility
        # (KVM script does 'mv cvp cvp1', we download directly to cvp1)
        cvp_link = f'{LIBVIRT_IMAGES_PATH}/cvp'
        cvp_dir = f'{LIBVIRT_IMAGES_PATH}/cvp1'
        if os.path.isdir(cvp_dir) and not os.path.lexists(cvp_link):
            os.symlink(cvp_dir, cvp_link)
            logger.info("  Created cvp -> cvp1 symlink")
        elif os.path.islink(cvp_link) and not os.path.exists(cvp_link):
            os.remove(cvp_link)
            os.symlink(cvp_dir, cvp_link)
            logger.info("  Replaced stale cvp symlink")

        result = self._run_command(
            f'cd {scripts_dir} && bash {self.topology}-kvm-create.sh',
            check=False,
            timeout=600,
        )
        if result.returncode != 0:
            logger.warning(f"  KVM create script returned code {result.returncode}")
        logger.info("  ✓ KVM create script completed")

    # =========================================================================
    # Phase 10: Reset CVP configuration
    # =========================================================================

    def phase_cvp_reset(self):
        logger.info("Phase 10: Reset CVP Configuration")
        logger.info("-" * 60)

        result = self._run_command(
            'docker exec atd-cvpupdater rm -f /home/arista/CVP_DATA/.cvpState.txt',
            check=False,
        )
        if result.returncode == 0:
            logger.info("  ✓ CVP configuration flag removed")
        else:
            logger.warning("  Failed to remove CVP flag (container may not be running yet)")

        time.sleep(2)

        result = self._run_command('docker restart atd-cvpupdater', check=False)
        if result.returncode == 0:
            logger.info("  ✓ CVP updater restarted — will register devices and push configlets")
        else:
            logger.warning("  Failed to restart CVP updater")

    # =========================================================================
    # Phase 11: Verify
    # =========================================================================

    def phase_verify(self):
        logger.info("Phase 11: Verify VMs")
        logger.info("-" * 60)

        result = self._run_command("virsh list --all", check=False)
        if result.returncode == 0:
            running = [l for l in result.stdout.split('\n') if 'running' in l.lower()]
            logger.info(f"  {len(running)} VMs running")
            if len(running) == 0:
                raise RuntimeError("No VMs running after build — KVM create may have failed")
        else:
            logger.warning("  Could not list VMs")

        logger.info("  ✓ Verification complete")

    # =========================================================================
    # Main build flow
    # =========================================================================

    def build(self, skip_phases=None):
        skip_phases = skip_phases or []

        try:
            logger.info("=" * 60)
            logger.info("ATD BASE IMAGE BUILDER")
            logger.info("=" * 60)
            logger.info(f"  CVP:      {self.cvp_version}")
            logger.info(f"  EOS:      {self.eos_version}")
            logger.info(f"  Topology: {self.topology}")
            logger.info(f"  EOS Type: {self.eos_type}")
            logger.info("=" * 60)

            phases = [
                ('validate', self.phase_validate),
                ('cleanup', self.phase_cleanup),
                ('download_cvp', self.phase_download_cvp),
                ('download_eos', self.phase_download_eos),
                ('update_config', self.phase_update_config),
                ('atd_update', self.phase_atd_update),
                ('wait_kvmbuilder', self.phase_wait_kvmbuilder),
                ('create_ovs', self.phase_create_ovs),
                ('create_vms', self.phase_create_vms),
                ('cvp_reset', self.phase_cvp_reset),
                ('verify', self.phase_verify),
            ]

            for phase_name, phase_fn in phases:
                if phase_name in skip_phases:
                    logger.info(f"Phase {phase_name}: SKIPPED (already completed)")
                    continue
                phase_fn()
                self.state.mark_phase_complete(phase_name)
                logger.info("")

            self.state.mark_phase_complete('completed')
            self.state.clear()

            logger.info("=" * 60)
            logger.info("BASE IMAGE BUILD COMPLETED!")
            logger.info("=" * 60)
            logger.info(f"  CVP:      {self.cvp_version}")
            logger.info(f"  EOS:      {self.eos_version}")
            logger.info(f"  Topology: {self.topology}")
            logger.info("=" * 60)

            self.state.notify_uilanding(True, 'Build completed successfully!')
            return True

        except Exception as e:
            self.state.add_error(e)
            logger.error("=" * 60)
            logger.error(f"BASE IMAGE BUILD FAILED: {e}")
            logger.error("=" * 60)
            logger.error(f"  Resume with: python3 {__file__} "
                         f"--cvp-version {self.cvp_version} "
                         f"--eos-version {self.eos_version} "
                         f"--topology {self.topology} --resume --force")

            self.state.notify_uilanding(False, f'Build failed: {e}')
            raise


def main():
    parser = argparse.ArgumentParser(
        description='Build ATD base image (download CVP/EOS, create VMs)',
    )
    parser.add_argument('--cvp-version', required=True, help='CVP version (e.g. 2026.1.0)')
    parser.add_argument('--eos-version', required=True, help='EOS version (e.g. 4.34.7M)')
    parser.add_argument('--topology', required=True, help='Topology name')
    parser.add_argument('--eos-type', default='veos', choices=['veos', 'ceos', 'container-labs'],
                        help='EOS type (default: veos)')
    parser.add_argument('--force', action='store_true', help='Skip confirmation prompt')
    parser.add_argument('--resume', action='store_true', help='Resume failed build')
    parser.add_argument('--skip-update', action='store_true',
                        help='Skip atdUpdate phase (use when called from atdStartup)')

    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: Must run as root (use sudo)")
        sys.exit(1)

    builder = BaseImageBuilder(
        cvp_version=args.cvp_version,
        eos_version=args.eos_version,
        topology=args.topology,
        eos_type=args.eos_type,
        skip_update=args.skip_update,
    )

    skip_phases = []
    if args.skip_update:
        skip_phases.append('atd_update')

    if args.resume:
        if builder.state.load():
            skip_phases.extend(builder.state.completed_phases)
            builder.cvp_version = builder.state.cvp_version or args.cvp_version
            builder.eos_version = builder.state.eos_version or args.eos_version
            builder.topology = builder.state.topology or args.topology
            logger.info(f"Resuming from after: {skip_phases[-1] if skip_phases else 'start'}")
        else:
            logger.info("No previous state — starting fresh")

    if not args.force and not args.resume:
        print(f"\nThis will build a base image with:")
        print(f"  CVP:      {args.cvp_version}")
        print(f"  EOS:      {args.eos_version}")
        print(f"  Topology: {args.topology}")
        print(f"\nThis is DESTRUCTIVE — existing VMs will be destroyed.")
        response = input("Proceed? (yes/no): ")
        if response.lower() != 'yes':
            print("Cancelled.")
            return

    try:
        builder.build(skip_phases=skip_phases)
    except Exception:
        sys.exit(1)


if __name__ == '__main__':
    main()

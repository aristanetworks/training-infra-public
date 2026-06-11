"""
Pytest fixtures and shared test setup for nodebuilder tests.

These tests use mocking to avoid requiring actual libvirt/OVS infrastructure.
"""

import os
import sys
import tempfile
import pytest
from unittest.mock import Mock, patch, MagicMock

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


@pytest.fixture(autouse=True)
def mock_path_validation():
    """Mock path validation to allow temp directories in tests."""
    with patch('persistence._validate_path', return_value=True):
        with patch('orphaned_interfaces._validate_path', return_value=True):
            yield


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def mock_user_nodes_file(temp_dir):
    """Create a temporary user_nodes.yaml file."""
    path = os.path.join(temp_dir, 'user_nodes.yaml')
    with open(path, 'w') as f:
        f.write("nodes: []\n")
    return path


@pytest.fixture
def mock_user_hosts_file(temp_dir):
    """Create a temporary user_hosts.yaml file."""
    path = os.path.join(temp_dir, 'user_hosts.yaml')
    with open(path, 'w') as f:
        f.write("hosts: []\n")
    return path


@pytest.fixture
def mock_user_firewalls_file(temp_dir):
    """Create a temporary user_firewalls.yaml file."""
    path = os.path.join(temp_dir, 'user_firewalls.yaml')
    with open(path, 'w') as f:
        f.write("firewalls: []\n")
    return path


@pytest.fixture
def mock_topo_build_file(temp_dir):
    """Create a temporary topo_build.yml file with sample topology."""
    path = os.path.join(temp_dir, 'topo_build.yml')
    content = """
nodes:
  - spine1:
      ip_addr: 192.168.0.10
      sys_mac: 00:1c:73:00:00:01
      neighbors:
        - neighborDevice: leaf1
          neighborPort: Ethernet1
          port: Ethernet1
        - neighborDevice: leaf2
          neighborPort: Ethernet1
          port: Ethernet2
  - leaf1:
      ip_addr: 192.168.0.11
      sys_mac: 00:1c:73:00:00:02
      neighbors:
        - neighborDevice: spine1
          neighborPort: Ethernet1
          port: Ethernet1
  - leaf2:
      ip_addr: 192.168.0.12
      sys_mac: 00:1c:73:00:00:03
      neighbors:
        - neighborDevice: spine1
          neighborPort: Ethernet2
          port: Ethernet1
"""
    with open(path, 'w') as f:
        f.write(content)
    return path


@pytest.fixture
def mock_dnsmasq_file(temp_dir):
    """Create a temporary dnsmasq.conf file."""
    path = os.path.join(temp_dir, 'veos.conf')
    content = """
dhcp-host=00:1c:73:00:00:01,192.168.0.10,spine1,infinite
dhcp-host=00:1c:73:00:00:02,192.168.0.11,leaf1,infinite
dhcp-host=00:1c:73:00:00:03,192.168.0.12,leaf2,infinite
dhcp-host=00:1c:73:00:00:10,192.168.0.20,available1,infinite
dhcp-host=00:1c:73:00:00:11,192.168.0.21,available2,infinite
dhcp-host=00:1c:73:00:00:12,192.168.0.22,available3,infinite
"""
    with open(path, 'w') as f:
        f.write(content)
    return path


@pytest.fixture
def mock_virsh():
    """Mock virsh command execution."""
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout='',
            stderr=''
        )
        yield mock_run


@pytest.fixture
def mock_ovs_vsctl():
    """Mock ovs-vsctl command execution."""
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = Mock(
            returncode=0,
            stdout='',
            stderr=''
        )
        yield mock_run


@pytest.fixture
def sample_node_data():
    """Sample node data for testing."""
    return {
        'name': 'testleaf1',
        'ip': '192.168.0.20',
        'mac': '00:1c:73:00:00:10',
        'device_type': 'leaf',
        'connections': [
            {
                'target_device': 'spine1'
            }
        ]
    }


@pytest.fixture
def sample_host_data():
    """Sample host data for testing."""
    return {
        'name': 'testhost1',
        'ip': '192.168.0.21',
        'connection': {
            'target_device': 'leaf1',
            'target_port': 'Ethernet5'
        },
        'data_ip': '10.1.1.100/24'
    }


@pytest.fixture
def sample_firewall_data():
    """Sample firewall data for testing."""
    return {
        'name': 'testfw1',
        'mgmt_ip': '192.168.0.22',
        'inside_interface': {
            'ip': '10.1.1.1/24',
            'target_device': 'leaf1',
            'target_port': 'Ethernet6'
        },
        'outside_interface': {
            'ip': '10.2.2.1/24',
            'target_device': 'spine1',
            'target_port': 'Ethernet5'
        }
    }


@pytest.fixture
def mock_user_cloudeos_file(temp_dir):
    """Create empty user_cloudeos.yaml for tests."""
    path = os.path.join(temp_dir, 'user_cloudeos.yaml')
    with open(path, 'w') as f:
        f.write('devices: []\n')
    return path


@pytest.fixture
def mock_user_links_file(temp_dir):
    """Create empty user_links.yaml for tests."""
    path = os.path.join(temp_dir, 'user_links.yaml')
    with open(path, 'w') as f:
        f.write('links: []\n')
    return path


@pytest.fixture
def sample_cloudeos_data():
    """Sample CloudEOS device data for tests."""
    return {
        'name': 'D1',
        'ip': '192.168.0.50',
        'device_type': 'pe',
        'connections': [{
            'target_device': 'PE1',
            'target_port': 'Ethernet7',
            'local_port': 'Ethernet1'
        }]
    }


@pytest.fixture
def sample_link_data():
    """Sample link data for tests."""
    return {
        'source_device': 'spine1',
        'source_port': 'Ethernet5',
        'target_device': 'leaf1',
        'target_port': 'Ethernet5'
    }


@pytest.fixture
def mock_wan_topo_build_file(temp_dir):
    """Create topo_build.yml with PE1/PE2 for WAN topology tests."""
    path = os.path.join(temp_dir, 'wan_topo_build.yml')
    content = """nodes:
  - PE1:
      ip_addr: 192.168.0.41
      sys_mac: 00:1c:73:e1:c6:01
      neighbors:
        - neighborDevice: A1
          neighborPort: Ethernet1
          port: Ethernet1
  - PE2:
      ip_addr: 192.168.0.42
      sys_mac: 00:1c:73:e2:c6:01
      neighbors:
        - neighborDevice: A2
          neighborPort: Ethernet1
          port: Ethernet1
  - A1:
      ip_addr: 192.168.0.21
      sys_mac: 00:1c:73:c1:c6:01
      neighbors:
        - neighborDevice: PE1
          neighborPort: Ethernet1
          port: Ethernet1
  - A2:
      ip_addr: 192.168.0.22
      sys_mac: 00:1c:73:c2:c6:01
      neighbors:
        - neighborDevice: PE2
          neighborPort: Ethernet1
          port: Ethernet1
"""
    with open(path, 'w') as f:
        f.write(content)
    return path

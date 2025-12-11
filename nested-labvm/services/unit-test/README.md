# ATD Unit Test Suite

Comprehensive unit testing suite for Arista Training Labs (ATL) lab environments.

## Quick Start

```bash
# Navigate to unit-test directory
cd nested-labvm/services/unit-test

# Install Python dependencies
pip3 install -r requirements.txt

# Copy UNIT_TEST_CONFIG.yaml to /etc/atd directory
sudo cp UNIT_TEST_CONFIG.yaml /etc/atd/

# Run all tests
cd src
python3 main.py

# View logs
ls -lh /etc/atd/logs/
```

## Structure

```
unit-test/
├── UNIT_TEST_CONFIG.yaml    # Central configuration file (copied to /etc/atd/)
├── requirements.txt          # Python dependencies
├── src/
│   ├── main.py               # Test orchestrator (runs all tests)
│   ├── test_atd_config.py    # ATD configuration validation
│   ├── test_cvp_ssh.py       # CVP SSH connectivity tests
│   ├── test_web.py           # Web service tests
│   ├── test_cvp_inventory.py # CVP inventory and streaming tests
│   └── test_node_ssh.py      # Node SSH connectivity tests
└── README.md
```

## Test Files

### 1. **main.py** - Test Orchestrator
Main entry point that runs all unit tests and provides a consolidated test report.

**Features:**
- Runs all tests sequentially
- Captures exit codes and execution time for each test
- Provides comprehensive test report with:
  - Pass/Fail status for each test
  - Execution time per test
  - Overall success rate
  - Total execution time
  - List of failed tests (if any)

**Usage:**
```bash
cd /path/to/unit-test/src
python3 main.py
```

### 2. **test_atd_config.py** - ATD Configuration Validation
Validates ATD lab configuration and CVP resources.

**Tests:**
- Topology folder exists in `/opt/atd/topologies/`
- Labguides modules are not empty
- ATD branch does not contain "nested"
- CVP disk capacity >= 175 GB (configurable)
- CVP RAM and CPU meet version-specific requirements
- Customer details extraction and display (if available)
  - Exam taker full name
  - Exam taker email
  - External exam ID
  - Exam taker attempt ID

### 3. **test_cvp_ssh.py** - CVP SSH Connectivity
Tests SSH connectivity and CVP system status.

**Tests:**
- Network connectivity to CVP
- SSH login with credentials
- RAM usage check (alerts if > 80%)
- System information retrieval
- CVPI status command execution

### 4. **test_web.py** - Web Service Testing
Tests HTTP/HTTPS connectivity to web services.

**Tests:**
- Labguides URL returns HTTP 200
- Handles redirects correctly
- SSL verification disabled for self-signed certs

### 5. **test_cvp_inventory.py** - CVP Inventory Validation
Tests CVP device inventory and streaming status.

**Tests:**
- CVP login with credentials from ACCESS_INFO.yaml
- Device count matches topology file
- All devices have streaming status "active"

### 6. **test_node_ssh.py** - Node SSH Connectivity
Tests SSH connectivity to all topology nodes.

**Tests:**
- Loads node IPs from topo_build.yml
- Tests SSH login to each node with arista user
- Executes basic commands to verify connectivity
- Password retrieved from ACCESS_INFO.yaml

## Configuration

All test constants are centralized in **UNIT_TEST_CONFIG.yaml**:

```yaml
# File paths
paths:
  access_info: '/etc/atd/ACCESS_INFO.yaml'
  atd_repo: '/etc/atd/ATD_REPO.yaml'
  topologies: '/opt/atd/topologies/'



# Web Service Configuration
web:
  labguides_url: 'https://192.168.0.1/labguides/index.html'
  expected_status_code: 200
  request_timeout: 10

# CVP disk capacity requirements
cvp_disk:
  min_capacity_gb: 175

# CVP hardware requirements by version
cvp_hardware:
  2025.1.0:
    min_ram_gb: 32
    min_cpu_cores: 8
```

## Running Tests

### Run All Tests
```bash
cd /path/to/unit-test/src
python3 main.py
```

### Run Individual Tests
```bash
# ATD Configuration
python3 test_atd_config.py

# CVP SSH
python3 test_cvp_ssh.py

# Web Service
python3 test_web.py

# CVP Inventory
python3 test_cvp_inventory.py

# Node SSH
python3 test_node_ssh.py
```

## Test Report Format

```
================================================================================
  ATD Unit Test Report
================================================================================

--------------------------------------------------------------------------------
Test Results:
--------------------------------------------------------------------------------
✓ PASS     | ATD Configuration Validation             | 2.34s
✓ PASS     | CVP SSH Connectivity Test                | 5.67s
✓ PASS     | Web Service Test                         | 1.23s
✓ PASS     | CVP Inventory Test                       | 8.90s
✓ PASS     | Node SSH Connectivity Test               | 12.45s
--------------------------------------------------------------------------------
Total Tests:    5
Passed:         5
Failed:         0
Success Rate:   100.0%
Total Duration: 30.59s
================================================================================

✓ All tests PASSED!

================================================================================
```

**Note**: Customer Information is displayed only if `customer_details` section exists in ACCESS_INFO.yaml.

## Exit Codes

- **0**: All tests passed
- **1**: One or more tests failed

## Logging

Test results are logged to both console and file:

- **Log directory**: `/etc/atd/logs/`
- **Log filename format**: `atd_unit_test_<epoch_timestamp>.log`
- **Example**: `atd_unit_test_1732147890.log`

The log file contains the complete test execution output including:
- All test results
- Customer information (if available)
- Detailed validation results
- Error messages and stack traces
- Summary statistics

**Note**: The log directory is automatically created if it doesn't exist.

## Dependencies

```
requests==2.31.0
paramiko==3.4.0
pyyaml==6.0.1
```

## Modifying Configuration

To change test parameters, edit `UNIT_TEST_CONFIG.yaml`:

```bash
# Copy config to /etc/atd/ (if not already there)
sudo mkdir -p /etc/atd
sudo cp /path/to/unit-test/UNIT_TEST_CONFIG.yaml /etc/atd/

# Edit the config
sudo vi /etc/atd/UNIT_TEST_CONFIG.yaml
```

Or edit locally and copy:
```bash
vi /path/to/unit-test/UNIT_TEST_CONFIG.yaml
sudo cp /path/to/unit-test/UNIT_TEST_CONFIG.yaml /etc/atd/
```

## Adding New Tests

1. Create a new test file in `src/` directory (e.g., `test_new_feature.py`)
2. Implement a `main()` function that returns 0 for success, 1 for failure
3. Add constants to `UNIT_TEST_CONFIG.yaml`
4. Load config in your test using `load_config()` function
5. Add the test to `main.py` test suite:

```python
test_suite = [
    ("ATD Configuration Validation", test_atd_config.main),
    ("CVP SSH Connectivity Test", test_cvp_ssh.main),
    ("Web Service Test", test_web.main),
    ("CVP Inventory Test", test_cvp_inventory.main),
    ("Node SSH Connectivity Test", test_node_ssh.main),
    ("New Feature Test", test_new_feature.main),  # Add here
]
```

## Troubleshooting

### Configuration Not Found
```
ERROR - Config file not found: /etc/atd/UNIT_TEST_CONFIG.yaml
```
**Solution**: Ensure UNIT_TEST_CONFIG.yaml is in the correct location or update `CONFIG_PATH` in each test file.

### ACCESS_INFO Not Found
```
ERROR - File not found: /etc/atd/ACCESS_INFO.yaml
```
**Solution**: Verify ACCESS_INFO.yaml exists in `/etc/atd/` directory.

### CVP Connection Failed
```
ERROR - CVP login failed with status code: 401
```
**Solution**: Verify CVP credentials in ACCESS_INFO.yaml are correct.

### Topology File Not Found
```
ERROR - Topology file not found: /opt/atd/topologies/{topology}/topo_build.yml
```
**Solution**: Verify topology folder exists and contains topo_build.yml file.

## General Notes

- All tests use centralized configuration from `/etc/atd/UNIT_TEST_CONFIG.yaml`
- **CVP credentials are loaded from `ACCESS_INFO.yaml` at runtime:**
  - CVP SSH (test_cvp_ssh.py): Uses `root` user credentials from `login_info.cvp.shell`
  - CVP Web API (test_cvp_inventory.py): Uses `arista` user credentials from `login_info.cvp.shell`
  - Node SSH (test_node_ssh.py): Uses `arista` user password from `login_info.jump_host.shell`
- SSL verification is disabled for self-signed certificates
- Tests are designed to run on ATD lab host machines
- Each test can be run independently or through the orchestrator
- All Python dependencies are listed in requirements.txt

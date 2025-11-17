#!/bin/bash

#
# ATD Unit Test Runner
# This script runs the ATD unit test suite and logs results
#

# Set the path to the unit test directory
UNIT_TEST_DIR="/opt/atd/nested-labvm/services/unit-test"
UNIT_TEST_SRC="${UNIT_TEST_DIR}/src"

# Check if unit test directory exists
if [ ! -d "$UNIT_TEST_DIR" ]; then
    echo "ERROR: Unit test directory not found: $UNIT_TEST_DIR"
    exit 1
fi

# Check if main.py exists
if [ ! -f "${UNIT_TEST_SRC}/main.py" ]; then
    echo "ERROR: main.py not found in ${UNIT_TEST_SRC}"
    exit 1
fi

# Check if Python3 is installed
if ! command -v python3 &> /dev/null; then
    echo "ERROR: python3 is not installed"
    exit 1
fi

# Check if pip3 is installed
if ! command -v pip3 &> /dev/null; then
    echo "ERROR: pip3 is not installed"
    exit 1
fi

# Install Python requirements
echo "=========================================="
echo "Installing Python Requirements"
echo "=========================================="
if [ -f "${UNIT_TEST_DIR}/requirements.txt" ]; then
    pip3 install -q --ignore-installed -r "${UNIT_TEST_DIR}/requirements.txt"
    if [ $? -ne 0 ]; then
        echo "WARNING: Failed to install some requirements, continuing anyway..."
    fi
else
    echo "WARNING: requirements.txt not found in ${UNIT_TEST_DIR}"
fi
echo ""

# Change to the source directory
cd "$UNIT_TEST_SRC" || exit 1

# Run the unit tests
echo "=========================================="
echo "Running ATD Unit Tests"
echo "=========================================="
python3 main.py

# Capture exit code
EXIT_CODE=$?

# Print completion message
echo ""
echo "=========================================="
if [ $EXIT_CODE -eq 0 ]; then
    echo "Unit tests completed successfully"
else
    echo "Unit tests failed with exit code: $EXIT_CODE"
fi
echo "=========================================="
echo ""
echo "Log files are located in: /etc/atd/logs/"
ls -lh /etc/atd/logs/ 2>/dev/null | tail -5

# Exit with the same code as the Python script
exit $EXIT_CODE

#!/bin/bash
#
# ATD Update Wrapper Script
# This script calls the Python implementation of atdUpdate
#
# Usage: sudo sh /usr/local/bin/atdUpdate.sh
#

echo "Starting ATD Update (Python Edition)"

# Run the Python script
python3 /usr/local/bin/atdUpdate.py

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "ATD Update completed successfully"
else
    echo "ATD Update failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE

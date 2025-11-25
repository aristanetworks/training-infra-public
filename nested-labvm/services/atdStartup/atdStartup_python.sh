#!/bin/bash
#
# ATD Startup Wrapper Script
# This script calls the Python implementation of atdStartup
#
# Usage: sudo sh /usr/local/bin/atdStartup.sh
#

echo "Starting ATD Startup (Python Edition)"

# Run the Python script
python3 /usr/local/bin/atdStartup.py

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "ATD Startup completed successfully"
else
    echo "ATD Startup failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE

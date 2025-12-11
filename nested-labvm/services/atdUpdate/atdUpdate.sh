#!/bin/bash
#
# ATD Update Wrapper Script
# This script is called by external systems and delegates to the Python implementation
#
# The Python script (atdUpdate.py) handles:
#   - Git operations (fetch, checkout, pull)
#   - Syncing scripts to /usr/local/bin/
#   - Running ATD Startup
#
# Usage: sudo sh /usr/local/bin/atdUpdate.sh
#

echo "Starting ATD Update (Python Edition)"

# First time: ensure Python files are in place from the git repo
# If they don't exist in /usr/local/bin/, copy them from the repo
if [ ! -f "/usr/local/bin/atdUpdate.py" ] || [ ! -d "/usr/local/lib/atd-services/utils" ]; then
    echo "Initializing Python files for first run..."
    mkdir -p /usr/local/lib/atd-services/utils
    rsync -av /opt/atd/nested-labvm/services/atdUpdate/atdUpdate.py /usr/local/bin/ 2>/dev/null || true
    rsync -av /opt/atd/nested-labvm/services/atdStartup/atdStartup.py /usr/local/bin/ 2>/dev/null || true
    rsync -av /opt/atd/nested-labvm/services/utils/ /usr/local/lib/atd-services/utils/ 2>/dev/null || true
fi

# Install google-cloud-logging package
echo "Installing google-cloud-logging package..."
pip3 install --upgrade google-cloud-logging cvprac 2>/dev/null || echo "Warning: Failed to install google-cloud-logging package"

# Run the Python script (which handles git, sync, and startup)
python3 /usr/local/bin/atdUpdate.py

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "ATD Update completed successfully"
    # CVP device registration now runs via docker-compose (atd-cvp-device-registration container)
else
    echo "ATD Update failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE

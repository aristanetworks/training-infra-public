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

# Run the Python script (which handles git, sync, and startup)
python3 /usr/local/bin/atdUpdate.py

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
    echo "ATD Update completed successfully"
else
    echo "ATD Update failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE

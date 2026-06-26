#!/bin/bash
#
# ATD Startup Wrapper Script
# This script syncs Python files and delegates to the Python implementation
#
# The Python script (atd_manager.py startup) handles all startup logic:
#   - Download base topology
#   - Setup exam configuration
#   - Network setup (NAT for cloudeos)
#   - Labguide download
#   - Docker container setup
#   - Systemd timer setup
#   - And more...
#
# Note: Cloud Logging is handled by the Python startup (atd_manager.py).
#       No inline Python cloud_log calls needed here.
#
# Usage: sudo sh /usr/local/bin/atdStartup.sh
#

echo "============================================================"
echo "  ATD Startup Script"
echo "============================================================"
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting atdStartup..."

# Sync Python scripts and utilities from the repo
# This ensures Python-based services are available even when called from bash atdUpdate.sh
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing Python scripts and utilities..."
mkdir -p /usr/local/lib/atd-services/utils
mkdir -p /opt/atd/scripts
rsync -av /opt/atd/nested-labvm/services/atdStartup/atdStartup.py /usr/local/bin/ 2>/dev/null || true
rsync -av /opt/atd/nested-labvm/services/utils/ /usr/local/lib/atd-services/utils/ 2>/dev/null || true
rsync -av /opt/atd/nested-labvm/services/topology_converter_v2.py /opt/atd/scripts/ 2>/dev/null || true

# Install required Python packages
# --break-system-packages: needed on rpm-managed systems where pip can't uninstall system packages (e.g. requests)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Installing required Python packages..."
pip3 install --upgrade --break-system-packages google-cloud-logging cvprac ruamel.yaml psutil 2>&1 || {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] First pip install attempt failed, retrying with --ignore-installed..."
    sleep 5
    pip3 install --ignore-installed --break-system-packages google-cloud-logging 2>&1 || echo "Warning: Failed to install google-cloud-logging"
    pip3 install --upgrade --break-system-packages cvprac ruamel.yaml psutil 2>&1 || echo "Warning: Failed to install Python packages"
}

# Run the Python ATD Startup script
echo ""
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Executing ATD Startup (Python Edition)..."
echo "============================================================"
python3 /usr/local/bin/atdStartup.py

# Capture exit code
EXIT_CODE=$?

echo ""
echo "============================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Startup completed successfully!"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Startup failed with exit code: $EXIT_CODE"
fi
echo "============================================================"

exit $EXIT_CODE

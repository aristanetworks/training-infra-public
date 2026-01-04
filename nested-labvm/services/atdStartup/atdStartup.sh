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
# Usage: sudo sh /usr/local/bin/atdStartup.sh
#

# Cloud Logging function - logs to Google Cloud Logging via Python
cloud_log() {
    local message="$1"
    local severity="${2:-INFO}"
    python3 -c "
import sys
import os
sys.stderr = open(os.devnull, 'w')
try:
    from google.cloud import logging
    client = logging.Client()
    logger = client.logger('atd-startup')
    logger.log_text('$message', severity='$severity', labels={'service': 'atd-startup', 'phase': 'bootstrap'})
except:
    pass
" 2>/dev/null || true
}

echo "============================================================"
echo "  ATD Startup Script"
echo "============================================================"
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting atdStartup..."
cloud_log "ATD Startup script initiated"

# Sync Python scripts and utilities from the repo
# This ensures Python-based services are available even when called from bash atdUpdate.sh
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing Python scripts and utilities..."
cloud_log "Syncing Python scripts and utilities"
mkdir -p /usr/local/lib/atd-services/utils
rsync -av /opt/atd/nested-labvm/services/atdStartup/atdStartup.py /usr/local/bin/ 2>/dev/null || true
rsync -av /opt/atd/nested-labvm/services/utils/ /usr/local/lib/atd-services/utils/ 2>/dev/null || true

# Install required Python packages
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Installing required Python packages..."
cloud_log "Installing required Python packages"
pip3 install --upgrade --ignore-installed google-cloud-logging cvprac ruamel.yaml 2>/dev/null || echo "Warning: Failed to install Python packages"

# Run the Python ATD Startup script
echo ""
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Executing ATD Startup (Python Edition)..."
echo "============================================================"
cloud_log "Executing ATD Startup Python script"
python3 /usr/local/bin/atdStartup.py

# Capture exit code
EXIT_CODE=$?

echo ""
echo "============================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Startup completed successfully!"
    cloud_log "ATD Startup completed successfully"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Startup failed with exit code: $EXIT_CODE"
    cloud_log "ATD Startup failed with exit code: $EXIT_CODE" "ERROR"
fi
echo "============================================================"

exit $EXIT_CODE

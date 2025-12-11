#!/bin/bash
#
# ATD Update Script
# This script updates the ATD repository from git and runs atdStartup
#
# Usage: sudo sh /usr/local/bin/atdUpdate.sh
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
    logger = client.logger('atd-update')
    logger.log_text('$message', severity='$severity', labels={'service': 'atd-update', 'phase': 'git-update'})
except:
    pass
" 2>/dev/null || true
}

echo "============================================================"
echo "  ATD Update Script"
echo "============================================================"
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting ATD Update..."
cloud_log "ATD Update script initiated"

# Read configuration
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Reading configuration from /etc/atd/ATD_REPO.yaml..."
BRANCH=$(cat /etc/atd/ATD_REPO.yaml | python3 -m shyaml get-value atd-public-branch)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Target branch: $BRANCH"
cloud_log "Target branch: $BRANCH"

if  [ -z "$(cat /etc/atd/ATD_REPO.yaml | grep repo)" ]
then
    REPO="https://github.com/aristanetworks/atd-public.git"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Using default repo: $REPO"
else
    REPO=$(cat /etc/atd/ATD_REPO.yaml | python3 -m shyaml get-value public-repo)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Using configured repo: $REPO"
fi
cloud_log "Using repo: $REPO"

# Perform git repo check
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Changing to /opt/atd directory..."
cd /opt/atd

# Check the current repo compared to the targeted repo
CURRENT_REPO=$(git remote get-url origin)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Current remote URL: $CURRENT_REPO"

if [[ ! "$CURRENT_REPO" = "$REPO" ]]
then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Repos do not match, updating remote to $REPO"
    cloud_log "Updating remote URL from $CURRENT_REPO to $REPO"
    git remote set-url origin $REPO
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Remote URL matches target"
fi

# Fetch updates from the remote repo
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Fetching updates from remote..."
cloud_log "Fetching updates from remote"
git fetch

# Perform check on the current branch/tag to the targeted
CURRENT_BRANCH=$(git branch --show-current)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Current branch: $CURRENT_BRANCH"

if [[ "$CURRENT_BRANCH" = "$BRANCH" ]]
then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Target branch matches current branch"
    cloud_log "Branch matches, pulling latest changes"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Discarding local changes..."
    git checkout .
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling latest changes..."
    git pull
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Branches do not match, updating to branch $BRANCH"
    cloud_log "Switching branch from $CURRENT_BRANCH to $BRANCH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Discarding local changes..."
    git checkout .
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Checking out branch $BRANCH..."
    git checkout $BRANCH
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling latest changes..."
    git pull
fi

cloud_log "Git update completed successfully"

# Update scripts
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing atdUpdate.sh to /usr/local/bin/..."
rsync -av /opt/atd/nested-labvm/services/atdUpdate/atdUpdate.sh /usr/local/bin/

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing atdStartup.sh to /usr/local/bin/..."
rsync -av /opt/atd/nested-labvm/services/atdStartup/atdStartup.sh /usr/local/bin/

cloud_log "Scripts synced to /usr/local/bin/"

# Execute startup
echo ""
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Executing atdStartup..."
echo "============================================================"
cloud_log "Executing atdStartup.sh"
bash /usr/local/bin/atdStartup.sh

# Capture exit code
EXIT_CODE=$?

echo ""
echo "============================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Update completed successfully!"
    cloud_log "ATD Update completed successfully"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Update failed with exit code: $EXIT_CODE"
    cloud_log "ATD Update failed with exit code: $EXIT_CODE" "ERROR"
fi
echo "============================================================"

exit $EXIT_CODE

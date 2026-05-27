#!/bin/bash
#
# ATD Update Script
# This script updates the ATD repository from git and runs atdStartup
#
# Usage: sudo sh /usr/local/bin/atdUpdate.sh
#
# Note: Cloud Logging is handled by the Python startup (atd_manager.py).
#       No inline Python cloud_log calls needed here.

echo "============================================================"
echo "  ATD Update Script"
echo "============================================================"
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Starting ATD Update..."

# Read configuration
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Reading configuration from /etc/atd/ATD_REPO.yaml..."
BRANCH=$(cat /etc/atd/ATD_REPO.yaml | python3 -m shyaml get-value atd-public-branch)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Target branch: $BRANCH"

if  [ -z "$(cat /etc/atd/ATD_REPO.yaml | grep repo)" ]
then
    REPO="https://github.com/aristanetworks/atd-public.git"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Using default repo: $REPO"
else
    REPO=$(cat /etc/atd/ATD_REPO.yaml | python3 -m shyaml get-value public-repo)
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Using configured repo: $REPO"
fi

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
    git remote set-url origin $REPO
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Remote URL matches target"
fi

# Fetch updates from the remote repo
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Fetching updates from remote..."
git fetch

# Perform check on the current branch/tag to the targeted
CURRENT_BRANCH=$(git branch --show-current)
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Current branch: $CURRENT_BRANCH"

if [[ "$CURRENT_BRANCH" = "$BRANCH" ]]
then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Target branch matches current branch"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Discarding local changes..."
    git checkout .
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling latest changes..."
    git pull
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Branches do not match, updating to branch $BRANCH"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Discarding local changes..."
    git checkout .
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Checking out branch $BRANCH..."
    git checkout $BRANCH
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Pulling latest changes..."
    git pull
fi

# Update scripts
echo ""
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing atdUpdate.sh to /usr/local/bin/..."
rsync -av /opt/atd/nested-labvm/services/atdUpdate/atdUpdate.sh /usr/local/bin/

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Syncing atdStartup.sh to /usr/local/bin/..."
rsync -av /opt/atd/nested-labvm/services/atdStartup/atdStartup.sh /usr/local/bin/

# Execute startup
echo ""
echo "============================================================"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Executing atdStartup..."
echo "============================================================"
bash /usr/local/bin/atdStartup.sh

# Capture exit code
EXIT_CODE=$?

echo ""
echo "============================================================"
if [ $EXIT_CODE -eq 0 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Update completed successfully!"
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ATD Update failed with exit code: $EXIT_CODE"
fi
echo "============================================================"

exit $EXIT_CODE

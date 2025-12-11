#!/bin/bash
#
# CVP Device Registration Container Entrypoint
#
# This script runs the CVP device registration process.
# It waits for CVP to be available, then re-registers any devices with inactive streaming.
#
# Environment Variables:
#   WAIT_FOR_CVP        - If "true", wait for CVP to be available (default: true)
#   CVP_RETRIES         - Max retries waiting for CVP (default: 60)
#   CVP_RETRY_INTERVAL  - Seconds between CVP retries (default: 30)
#   BATCH_SIZE          - Number of devices to process in parallel (default: 10)
#   VERIFY_RETRIES      - Number of verification retries (default: 5)
#   VERIFY_INTERVAL     - Seconds between verification retries (default: 30)
#   SKIP_VERIFY         - If "true", skip verification (default: false)
#

set -e

echo "=============================================="
echo "  CVP Device Registration Container"
echo "=============================================="
echo "Starting at: $(date)"

# Build command arguments
ARGS="-y"  # Always auto-confirm in container

if [ "${WAIT_FOR_CVP:-true}" = "true" ]; then
    ARGS="$ARGS --wait-for-cvp"
    ARGS="$ARGS --cvp-retries ${CVP_RETRIES:-60}"
    ARGS="$ARGS --cvp-retry-interval ${CVP_RETRY_INTERVAL:-30}"
fi

ARGS="$ARGS --batch-size ${BATCH_SIZE:-10}"
ARGS="$ARGS --verify-retries ${VERIFY_RETRIES:-5}"
ARGS="$ARGS --verify-interval ${VERIFY_INTERVAL:-30}"

if [ "${SKIP_VERIFY:-false}" = "true" ]; then
    ARGS="$ARGS --skip-verify"
fi

echo "Running with arguments: $ARGS"
echo "=============================================="

# Run the CVP device registration script
exec python3 /app/cvp_device_registration.py $ARGS

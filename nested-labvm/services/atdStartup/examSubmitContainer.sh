#!/bin/bash

LOG_FILE="/var/log/exam-submission-check.log"
YAML_FILE="/etc/atd/ACCESS_INFO.yaml"

echo "[$(date)] Starting exam-submission-check.service" >> "$LOG_FILE"

# Extract exam_duration from YAML
EXAM_DURATION=$(awk -F ": " "/exam_duration:/ {print \$2}" "$YAML_FILE" | tr -d " ")

# If exam_duration is 0, exit
if [[ "$EXAM_DURATION" -eq 0 ]]; then
    echo "[$(date)] Exam duration is 0. Exiting." >> "$LOG_FILE"
    exit 0
fi

# Get current timestamp
CURRENT_TIME=$(date +%s)

# If current time > exam_duration, execute Docker command
if [[ "$CURRENT_TIME" -gt "$EXAM_DURATION" ]]; then
    echo "[$(date)] Exam duration expired. Executing container command..." >> "$LOG_FILE"

    # Run Docker command and capture status
    if docker exec -d atd-login sudo python3 /usr/local/bin/upload_exam_unattended.py; then
        echo "[$(date)] Successfully executed Docker command inside atd-login container." >> "$LOG_FILE"
    else
        echo "[$(date)] ERROR: Docker command execution failed!" >> "$LOG_FILE"
    fi

    # Disable and stop the service after execution

    if systemctl stop exam-submission-check.timer && systemctl disable exam-submission-check.timer; then
            echo "[$(date)] Successfully stopped and disabled deploy-check timer." >> "$LOG_FILE"
        else
                 echo "[$(date)] ERROR: Failed to stop and disable deploy-check timer!" >> "$LOG_FILE"
        fi
else
    echo "[$(date)] Exam duration has not expired yet. Exiting." >> "$LOG_FILE"
    exit 0
fi
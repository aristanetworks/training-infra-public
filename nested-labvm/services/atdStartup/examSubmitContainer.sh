#!/bin/bash

LOG_FILE="/var/log/exam-submission-check.log"
YAML_FILE="/etc/atd/ACCESS_INFO.yaml"

echo "[$(date)] Starting exam-submission-check.service" >> "$LOG_FILE"

END_EXAM_TIME=$(awk -F ": " "/endExamTime:/ {print \$2}" "$YAML_FILE" | tr -d " ")
if [ -z "$END_EXAM_TIME" ]; then
    END_EXAM_TIME=0
fi

if [[ "$END_EXAM_TIME" -eq  0 ]]; then
    echo "[$(date)]  Exam end time is 0. Exiting." >> "$LOG_FILE"
    exit 0
fi

# Get current timestamp
CURRENT_TIME=$(date +%s)

# If current time > exam_duration, execute Docker command
if [[ "$CURRENT_TIME" -gt "$END_EXAM_TIME" ]]; then
    echo "[$(date)] Exam duration expired. Executing container command..." >> "$LOG_FILE"

    # Run Docker command and capture status
    if docker exec -d atd-login sudo python3 -m exam_upload_v2.main; then
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
    END_EXAM_TIME_READABLE=$(date -d "@$END_EXAM_TIME" "+%Y-%m-%d %H:%M:%S")
    TIME_REMAINING=$(( END_EXAM_TIME - CURRENT_TIME ))
    HOURS_REMAINING=$(( TIME_REMAINING / 3600 ))
    MINUTES_REMAINING=$(( (TIME_REMAINING % 3600) / 60 ))
    echo "[$(date)] Exam duration has not expired yet. Exam ends at $END_EXAM_TIME_READABLE (in $HOURS_REMAINING hours and $MINUTES_REMAINING minutes). Exiting." >> "$LOG_FILE"
    exit 0
fi

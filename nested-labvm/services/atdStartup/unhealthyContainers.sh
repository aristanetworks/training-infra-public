#!/bin/bash

MAX_RETRIES=5
RETRY_DELAY=10
CODER_CONTAINER="atd-coder"

for attempt in $(seq 1 $MAX_RETRIES); do
    echo "Attempt $attempt of $MAX_RETRIES"
    if ! sudo docker ps | grep -q "$CODER_CONTAINER"; then
        echo "Coder container failed to start. Retrying..."
    else
        echo "Coder container is running."
        break
    fi
    sleep $RETRY_DELAY
    
    if [ $attempt -eq $MAX_RETRIES ]; then
        echo "Coder container failed to start after $MAX_RETRIES attempts. Pruning all containers."
        sudo docker container prune -f
        sudo bash /usr/local/bin/atdUpdate.sh
        break
    fi
done
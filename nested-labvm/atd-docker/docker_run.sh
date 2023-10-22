#!/bin/bash

echo "Starting one-time containers"

docker run -d --rm -e PYTHONUNBUFFERED=1 --name atd-vtepinfo -v /etc/atd:/etc/atd:rw us.gcr.io/atd-testdrivetraining-dev/atddocker_vtepinfo:0.1.9
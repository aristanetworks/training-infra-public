#!/bin/bash

#PROJECT=$(cut -d':' -f2 <<<$(grep project /etc/atd/ACCESS_INFO.yaml) | awk '{print $1}')

PROJECT="atd-testdrivetraining-dev"

if [ $PROJECT ]
then
  cp -r /opt/nginx/certs/$PROJECT/* /etc/nginx/certs
else
  cp -r /opt/nginx/certs/alpha-atds/* /etc/nginx/certs
fi

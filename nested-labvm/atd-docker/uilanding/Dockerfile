FROM python:3.9

# Install system dependencies
# Note: tcpdump/openvswitch removed - packet capture runs in dedicated captureservice container
RUN apt-get update && apt-get install -y \
    openssh-client \
    sshpass

RUN pip install --upgrade pip

RUN pip install tornado requests ruamel.yaml docker pyeapi

RUN mkdir -p /opt/topo/html

RUN mkdir -p /root/labguides

WORKDIR /root

RUN apt-get update && \
    apt-get -qy full-upgrade && \
    apt-get install -qy curl && \
    apt-get install -qy curl && \
    curl -sSL https://get.docker.com/ | sh

COPY ./src/html /opt/topo/html/

# Copy Python modules to site-packages
COPY ./src/device_types.py /usr/local/lib/python3.9/site-packages/device_types.py
# Note: capture_manager.py and packet_parser.py are not needed here
# Packet capture runs in the dedicated captureservice container with host network mode

COPY ./src/uilanding.py /usr/local/bin/uilanding

RUN chmod +x /usr/local/bin/uilanding

EXPOSE 80

CMD ["uilanding"]

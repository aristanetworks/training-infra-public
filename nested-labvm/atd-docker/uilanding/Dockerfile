# Stage 1: Build stage - minify JS and CSS
FROM node:20-slim AS builder

WORKDIR /build

# Copy package files and install dependencies
COPY package.json ./
RUN npm install

# Copy source files and build script
COPY src/ ./src/
COPY build.sh ./
RUN chmod +x build.sh

# Run minification (set DOCKER_BUILD to enable in-place minification)
ENV DOCKER_BUILD=1
RUN ./build.sh

# Stage 2: Production image
FROM python:3.9

# Install system dependencies
# Note: tcpdump/openvswitch removed - packet capture runs in dedicated captureservice container
RUN apt-get update && apt-get install -y \
    openssh-client \
    sshpass \
    iputils-ping

RUN pip install --upgrade pip

RUN pip install tornado requests ruamel.yaml docker pyeapi pyyaml google-cloud-logging

RUN mkdir -p /opt/topo/html

RUN mkdir -p /root/labguides

WORKDIR /root

RUN apt-get update && \
    apt-get -qy full-upgrade && \
    apt-get install -qy curl && \
    apt-get install -qy curl && \
    curl -sSL https://get.docker.com/ | sh

# Copy minified HTML/JS/CSS from builder stage
COPY --from=builder /build/src/html /opt/topo/html/

# Copy Python modules to site-packages
COPY ./src/device_types.py /usr/local/bin/device_types.py
COPY ./src/topology_converter.py /usr/local/bin/topology_converter.py
COPY ./src/utils.py /usr/local/bin/utils.py
COPY ./src/handlers/ /usr/local/bin/handlers/
# Note: capture_manager.py and packet_parser.py are not needed here
# Packet capture runs in the dedicated captureservice container with host network mode

COPY ./src/cloud_logging_utils.py /usr/local/lib/python3.9/site-packages/
COPY ./src/uilanding.py /usr/local/bin/uilanding
COPY ./scripts/connectivity-report.py /usr/local/bin/connectivity-report

RUN chmod +x /usr/local/bin/uilanding /usr/local/bin/connectivity-report

EXPOSE 80

CMD ["uilanding"]

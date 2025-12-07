FROM python:3.9-slim

# Install tshark (Wireshark CLI) for rich protocol decoding
# Also keep tcpdump as fallback, and OVS tools for bridge management
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    tshark \
    tcpdump \
    openvswitch-switch \
    iproute2 \
    coreutils \
    && rm -rf /var/lib/apt/lists/* \
    # Allow non-root users to capture (tshark requirement)
    && setcap cap_net_raw,cap_net_admin+eip /usr/bin/dumpcap

# Install Python dependencies
RUN pip install --no-cache-dir tornado

# Create capture directory
RUN mkdir -p /tmp/atl_captures

# Copy source files
COPY src/capture_service.py /usr/local/bin/capture_service

RUN chmod +x /usr/local/bin/capture_service

# Service listens on localhost:8089 (host network mode)
# Only accessible from localhost for security
EXPOSE 8089

# Run as root for NET_ADMIN capability
USER root

CMD ["python3", "/usr/local/bin/capture_service"]

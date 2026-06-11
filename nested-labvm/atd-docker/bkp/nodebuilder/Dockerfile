FROM python:3.11-slim

LABEL maintainer="ATD Team <atd-help@arista.com>"
LABEL description="Nodebuilder Service - Dynamic vEOS node addition for ATD labs"

WORKDIR /app

# Install libvirt client, OVS tools, iproute2, genisoimage for cloud-init ISOs,
# curl for downloading base images, and dependencies for Google Cloud SDK
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt-clients \
    openvswitch-switch \
    iproute2 \
    genisoimage \
    curl \
    gnupg \
    apt-transport-https \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Google Cloud SDK (includes gsutil with proper credential handling)
# The SDK's gsutil uses gcloud credentials from ~/.config/gcloud/
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
    | tee -a /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
    | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Cloud-init templates for Linux hosts and VyOS firewalls
# These are included in the image as defaults, but can be overridden
# by mounting /opt/nodebuilder/images/cloud-init from the host
COPY images/cloud-init/ /opt/nodebuilder/images/cloud-init/

ENV PYTHONUNBUFFERED=1

EXPOSE 8090

CMD ["python", "src/nodebuilder_service.py"]

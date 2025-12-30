FROM python:3.11-slim

LABEL maintainer="ATD Team <atd-help@arista.com>"
LABEL description="Nodebuilder Service - Dynamic vEOS node addition for ATD labs"

WORKDIR /app

# Install libvirt client, OVS tools, iproute2, genisoimage for cloud-init ISOs,
# and curl for downloading base images from GCP bucket
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt-clients \
    openvswitch-switch \
    iproute2 \
    genisoimage \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install gsutil for authenticated GCP bucket access (uses GCE metadata for auth)
RUN pip install --no-cache-dir gsutil

COPY src/ ./src/

# Cloud-init templates for Linux hosts and VyOS firewalls
# These are included in the image as defaults, but can be overridden
# by mounting /opt/nodebuilder/images/cloud-init from the host
COPY images/cloud-init/ /opt/nodebuilder/images/cloud-init/

ENV PYTHONUNBUFFERED=1

EXPOSE 8090

CMD ["python", "src/nodebuilder_service.py"]

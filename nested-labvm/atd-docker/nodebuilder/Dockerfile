FROM python:3.11-slim

LABEL maintainer="ATD Team <atd-help@arista.com>"
LABEL description="Nodebuilder Service - Dynamic vEOS node addition for ATD labs"

WORKDIR /app

# Install libvirt client, OVS tools, and iproute2 for virsh/ovs/ip commands
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvirt-clients \
    openvswitch-switch \
    iproute2 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

ENV PYTHONUNBUFFERED=1

EXPOSE 8090

CMD ["python", "src/nodebuilder_service.py"]

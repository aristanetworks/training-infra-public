FROM python:3.12

LABEL maintainer="Arista Networks"
LABEL description="CVP Device Registration - Re-registers devices with inactive streaming status"

# Install Docker CLI for restarting cvpupdater container
RUN apt-get update && apt-get install -y \
    docker.io \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY src/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/cvp_device_registration.py .
COPY src/entrypoint.sh .

# Make entrypoint executable
RUN chmod +x entrypoint.sh

# Set entrypoint
ENTRYPOINT ["./entrypoint.sh"]

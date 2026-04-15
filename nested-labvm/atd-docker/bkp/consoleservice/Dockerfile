FROM python:3.11-slim

WORKDIR /app

# Install libvirt client for virsh console access
RUN apt-get update && apt-get install -y \
    libvirt-clients \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8095

CMD ["python", "src/console_service.py"]

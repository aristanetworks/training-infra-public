# noVNC + websockify service for Linux host desktop access
#
# This container provides:
# - noVNC HTML5 VNC client (static files)
# - websockify WebSocket-to-TCP proxy with token authentication
#
# Token-based auth: websockify reads tokens from a file to map
# connection tokens to VNC server addresses (IP:port)

FROM python:3.11-slim

# Install dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install websockify
RUN pip install --no-cache-dir websockify

# Clone noVNC (just the static files we need)
RUN git clone --depth 1 https://github.com/novnc/noVNC.git /opt/novnc \
    && rm -rf /opt/novnc/.git

# Create token directory
RUN mkdir -p /tmp/novnc

# Expose websockify port
EXPOSE 6080

# Working directory
WORKDIR /opt/novnc

# Start websockify with noVNC web server and token authentication
# --web: serve noVNC static files
# --token-plugin TokenFile: use token file for authentication
# --token-source: path to token file (written by nodebuilder)
# Token file format: "token: host:port" (one per line)
CMD ["websockify", "--web", "/opt/novnc", "--token-plugin", "TokenFile", "--token-source", "/tmp/novnc/tokens", "6080"]

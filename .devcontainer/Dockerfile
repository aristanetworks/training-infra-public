# Use a modern, secure, and stable base image.
# Debian Bullseye has up-to-date TLS/SSL libraries.
FROM python:3.11-bullseye

# Set ENV to prevent interactive prompts during installation
ENV DEBIAN_FRONTEND=noninteractive

# Consolidate all initial apt installations.
RUN apt-get update && apt-get install -y \
    # Essential build tools & dependencies
    sudo curl gpg apt-transport-https ca-certificates \
    # User-requested tools
    vim git zip cloc lastpass-cli dnsutils zsh zsh-syntax-highlighting zsh-doc less liquidprompt software-properties-common \
    # Clean up apt cache
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- User Creation ---
ARG USERNAME=atddev
ARG USER_UID=1000
ARG USER_GID=$USER_UID
RUN groupadd --gid $USER_GID $USERNAME \
    && useradd --uid $USER_UID --gid $USER_GID -m $USERNAME -s /bin/zsh \
    && echo $USERNAME ALL=\(root\) NOPASSWD:ALL > /etc/sudoers.d/$USERNAME \
    && chmod 0440 /etc/sudoers.d/$USERNAME

# --- Install Google Cloud SDK ---
# This will now work reliably on a modern base image
RUN curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" | tee /etc/apt/sources.list.d/google-cloud-sdk.list > /dev/null \
    && apt-get update && apt-get install -y google-cloud-sdk \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# --- Install GitHub CLI ---
# The original, clean command will also work now
RUN apt-get update && apt-get install -y curl gpg \
    && echo "Attempting to download GitHub CLI key..." \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg  -o /tmp/githubcli-key.gpg \
    && echo "Key downloaded, de-armoring..." \
    && gpg --dearmor -o /usr/share/keyrings/githubcli-archive-keyring.gpg /tmp/githubcli-key.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && echo "Updating apt sources..." \
    && apt-get update \
    && echo "Installing gh..." \
    && apt-get install -y gh \
    && apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/githubcli-key.gpg

# --- Install Python Packages ---
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir dotbot docutils rstcheck sphinx sphinx-bootstrap-theme sphinxcontrib-images sphinx-copybutton sphinx-autobuild rcvpapi ruamel.yaml Pyyaml lxml jsonrpclib ansible cvprac pyeapi pylint tornado apscheduler pymongo

# --- User-specific Configuration ---
RUN echo "export PROMPT_COMMAND='history -a' && export HISTFILE=$CONTAINERWS/.history" >> /home/$USERNAME/.zshrc

# Switch to the non-root user
USER $USERNAME
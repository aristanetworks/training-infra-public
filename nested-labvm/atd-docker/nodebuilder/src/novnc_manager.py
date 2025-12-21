"""
noVNC Manager for Nodebuilder Service

Handles noVNC token management for browser-based VNC access to Linux hosts.

The noVNC proxy (websockify) uses token-based authentication to map
browser WebSocket connections to VNC servers inside VMs.

Architecture:
- Linux hosts run x11vnc inside the VM on port 5900
- x11vnc shares the LXDE desktop session over VNC
- websockify proxies WebSocket connections to VM's management IP:5900

Token flow:
1. Frontend requests token for a host
2. This module generates a secure token and writes to token file
3. Frontend connects to noVNC proxy with token
4. Proxy looks up target (mgmt_ip:5900) from token file and proxies connection
"""

import logging
import os
import secrets
import time
from typing import Dict, Optional

from config import USER_HOSTS_PATH
from persistence import load_user_hosts

logger = logging.getLogger('nodebuilder')

# Token configuration
# Token file is in a directory that's mounted from host for sharing with novnc container
TOKEN_FILE_PATH = os.getenv('NOVNC_TOKEN_FILE', '/tmp/novnc/tokens')
TOKEN_EXPIRY_SECONDS = 3600  # Tokens valid for 1 hour
NOVNC_PROXY_PORT = 6080  # WebSocket proxy port

# x11vnc port inside VMs (standard VNC port)
X11VNC_PORT = 5900

# In-memory token store with expiry times
_token_store: Dict[str, Dict] = {}


def generate_token() -> str:
    """
    Generate a cryptographically secure token.

    Returns:
        32-character hex token
    """
    return secrets.token_hex(16)


def get_host_vnc_target(hostname: str) -> Optional[Dict]:
    """
    Get the VNC connection target for a Linux host.

    Returns the host's management IP and VNC port (5900) for x11vnc.
    This connects to the actual desktop session inside the VM.

    Args:
        hostname: Name of the host

    Returns:
        Dict with 'ip' and 'port', or None if host not found
    """
    try:
        # Load user hosts to get management IP
        hosts_data = load_user_hosts(USER_HOSTS_PATH)

        for host_entry in hosts_data.get('hosts', []):
            for host_name, info in host_entry.items():
                if host_name.lower() == hostname.lower():
                    mgmt_ip = info.get('mgmt_ip')
                    if mgmt_ip:
                        return {
                            'ip': mgmt_ip,
                            'port': X11VNC_PORT
                        }
                    else:
                        logger.warning(f"Host {hostname} has no mgmt_ip configured")
                        return None

        logger.warning(f"Host {hostname} not found in user_hosts.yaml")
        return None

    except Exception as e:
        logger.error(f"Error getting VNC target for {hostname}: {e}")
        return None


def create_vnc_token(hostname: str) -> Optional[Dict]:
    """
    Create a noVNC access token for a host.

    The token is written to the token file used by websockify and
    stored in memory with an expiry time.

    Args:
        hostname: Name of the host to create token for

    Returns:
        Dict with token, vnc_target, and websocket_url, or None on error
    """
    # Get VNC target (management IP and port)
    vnc_target = get_host_vnc_target(hostname)
    if not vnc_target:
        logger.error(f"Cannot create token: VNC target not available for {hostname}")
        return None

    # Generate token
    token = generate_token()
    expiry = time.time() + TOKEN_EXPIRY_SECONDS

    # Store in memory
    _token_store[token] = {
        'hostname': hostname,
        'vnc_ip': vnc_target['ip'],
        'vnc_port': vnc_target['port'],
        'expiry': expiry
    }

    # Write to token file (websockify format: token: host:port)
    try:
        _write_token_file()
    except Exception as e:
        logger.error(f"Failed to write token file: {e}")
        del _token_store[token]
        return None

    logger.info(f"Created noVNC token for {hostname} ({vnc_target['ip']}:{vnc_target['port']})")

    return {
        'token': token,
        'vnc_ip': vnc_target['ip'],
        'vnc_port': vnc_target['port'],
        'websocket_url': f'/novnc/vnc.html?autoconnect=true&path=websockify/?token={token}',
        'expires_in': TOKEN_EXPIRY_SECONDS
    }


def validate_token(token: str) -> Optional[Dict]:
    """
    Validate a noVNC token.

    Args:
        token: Token to validate

    Returns:
        Token info dict if valid, None if invalid or expired
    """
    if token not in _token_store:
        return None

    token_info = _token_store[token]

    # Check expiry
    if time.time() > token_info['expiry']:
        # Clean up expired token
        del _token_store[token]
        _write_token_file()
        return None

    return token_info


def revoke_token(token: str) -> bool:
    """
    Revoke a noVNC token.

    Args:
        token: Token to revoke

    Returns:
        True if token was revoked, False if not found
    """
    if token not in _token_store:
        return False

    del _token_store[token]
    _write_token_file()

    logger.info(f"Revoked noVNC token")
    return True


def revoke_tokens_for_host(hostname: str) -> int:
    """
    Revoke all tokens for a specific host.

    Used when a host is deleted.

    Args:
        hostname: Name of the host

    Returns:
        Number of tokens revoked
    """
    tokens_to_revoke = [
        token for token, info in _token_store.items()
        if info['hostname'] == hostname
    ]

    for token in tokens_to_revoke:
        del _token_store[token]

    if tokens_to_revoke:
        _write_token_file()

    logger.info(f"Revoked {len(tokens_to_revoke)} token(s) for {hostname}")
    return len(tokens_to_revoke)


def cleanup_expired_tokens() -> int:
    """
    Remove all expired tokens.

    Should be called periodically to clean up stale tokens.

    Returns:
        Number of tokens cleaned up
    """
    now = time.time()
    expired = [
        token for token, info in _token_store.items()
        if now > info['expiry']
    ]

    for token in expired:
        del _token_store[token]

    if expired:
        _write_token_file()
        logger.info(f"Cleaned up {len(expired)} expired token(s)")

    return len(expired)


def _write_token_file():
    """
    Write current tokens to the websockify token file.

    File format: token: host:port
    One token per line.
    """
    # Ensure directory exists
    token_dir = os.path.dirname(TOKEN_FILE_PATH)
    if token_dir and not os.path.exists(token_dir):
        os.makedirs(token_dir)

    # Write active tokens
    with open(TOKEN_FILE_PATH, 'w') as f:
        for token, info in _token_store.items():
            # websockify connects to x11vnc inside the VM via management IP
            vnc_ip = info.get('vnc_ip', '127.0.0.1')
            vnc_port = info.get('vnc_port', X11VNC_PORT)
            f.write(f"{token}: {vnc_ip}:{vnc_port}\n")

    logger.debug(f"Wrote {len(_token_store)} token(s) to {TOKEN_FILE_PATH}")


def get_active_tokens() -> Dict:
    """
    Get information about all active tokens.

    For debugging/admin purposes only - doesn't expose actual tokens.

    Returns:
        Dict with token count and host information
    """
    cleanup_expired_tokens()

    tokens_by_host = {}
    for token, info in _token_store.items():
        hostname = info['hostname']
        if hostname not in tokens_by_host:
            tokens_by_host[hostname] = 0
        tokens_by_host[hostname] += 1

    return {
        'total_tokens': len(_token_store),
        'tokens_by_host': tokens_by_host
    }


def get_novnc_url(hostname: str) -> Optional[str]:
    """
    Get a noVNC URL for a host, creating a token if needed.

    Convenience method that combines token creation and URL generation.

    Args:
        hostname: Name of the host

    Returns:
        noVNC URL with token, or None on error
    """
    token_info = create_vnc_token(hostname)
    if not token_info:
        return None

    return token_info['websocket_url']

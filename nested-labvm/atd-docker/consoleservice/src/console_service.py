#!/usr/bin/env python3
"""
Console Service - WebSocket bridge to virsh console for ATD labs

This service provides WebSocket access to VM serial consoles via virsh console.
It runs on port 8095 with host network mode for libvirt access.

Endpoints:
- GET  /health                  - Health check
- GET  /ws/console/{device}     - WebSocket endpoint for console access
- GET  /devices                 - List available VMs for console access
"""

import asyncio
import fcntl
import logging
import os
import pty
import re
import signal
import struct
import subprocess
import termios
from typing import Dict, Optional

from aiohttp import web, WSMsgType

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('consoleservice')

# Configuration
SERVICE_HOST = '0.0.0.0'
SERVICE_PORT = 8095
CONSOLE_TIMEOUT = 3600  # 1 hour max session

# Security: Valid VM name pattern (alphanumeric, dash, underscore)
VALID_VM_PATTERN = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')

routes = web.RouteTableDef()


class ConsoleSession:
    """Manages a virsh console PTY session"""

    def __init__(self, device_name: str):
        self.device = device_name
        self.master_fd: Optional[int] = None
        self.slave_fd: Optional[int] = None
        self.process: Optional[subprocess.Popen] = None
        self.logger = logging.getLogger(f'consoleservice.session.{device_name}')

    async def start(self) -> int:
        """
        Spawn virsh console process with PTY.

        Returns:
            Master file descriptor for reading/writing
        """
        self.logger.info(f"Starting console session for {self.device}")

        # Create pseudo-terminal
        self.master_fd, self.slave_fd = pty.openpty()

        # Set terminal size (80x24 default)
        winsize = struct.pack('HHHH', 24, 80, 0, 0)
        fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)

        # Set up environment for proper terminal handling
        env = os.environ.copy()
        env['TERM'] = 'xterm-256color'
        env['LANG'] = 'en_US.UTF-8'
        env['LC_ALL'] = 'en_US.UTF-8'

        # Spawn virsh console process
        # start_new_session=True creates new session (replaces preexec_fn=os.setsid)
        self.process = subprocess.Popen(
            ['virsh', 'console', self.device, '--force'],
            stdin=self.slave_fd,
            stdout=self.slave_fd,
            stderr=self.slave_fd,
            start_new_session=True,
            env=env
        )

        # Set master to non-blocking
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

        self.logger.info(f"Console session started, PID: {self.process.pid}")
        return self.master_fd

    async def cleanup(self):
        """Clean up PTY and process"""
        self.logger.info(f"Cleaning up console session for {self.device}")

        # Terminate the process
        if self.process:
            try:
                # Send SIGTERM to process group
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
            except Exception as e:
                self.logger.warning(f"Error terminating process: {e}")

        # Close file descriptors
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

        if self.slave_fd is not None:
            try:
                os.close(self.slave_fd)
            except OSError:
                pass

        self.logger.info(f"Console session cleaned up for {self.device}")

    def resize(self, rows: int, cols: int):
        """Resize the PTY"""
        if self.slave_fd:
            winsize = struct.pack('HHHH', rows, cols, 0, 0)
            try:
                fcntl.ioctl(self.slave_fd, termios.TIOCSWINSZ, winsize)
            except OSError as e:
                self.logger.warning(f"Failed to resize terminal: {e}")


def validate_device_name(device: str) -> bool:
    """Validate device name to prevent command injection"""
    if not device or not VALID_VM_PATTERN.match(device):
        return False
    return True


def check_vm_exists(device: str) -> bool:
    """Check if VM exists in libvirt"""
    try:
        result = subprocess.run(
            ['virsh', 'domstate', device],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def get_vm_state(device: str) -> str:
    """Get VM state (running, shut off, etc.)"""
    try:
        result = subprocess.run(
            ['virsh', 'domstate', device],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return 'unknown'
    except Exception:
        return 'unknown'


@routes.get('/health')
async def health(request):
    """Health check endpoint"""
    return web.json_response({
        'status': 'ok',
        'service': 'consoleservice',
        'version': '1.0.0'
    })


@routes.get('/devices')
async def list_devices(request):
    """List VMs available for console access"""
    try:
        result = subprocess.run(
            ['virsh', 'list', '--all', '--name'],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            return web.json_response({'error': 'Failed to list VMs'}, status=500)

        devices = []
        for name in result.stdout.strip().split('\n'):
            name = name.strip()
            if name:
                state = get_vm_state(name)
                devices.append({
                    'name': name,
                    'state': state,
                    'console_available': state == 'running'
                })

        return web.json_response({'devices': devices})

    except Exception as e:
        logger.error(f"Error listing devices: {e}")
        return web.json_response({'error': str(e)}, status=500)


@routes.get('/ws/console/{device}')
async def console_websocket(request):
    """WebSocket endpoint for virsh console"""
    device = request.match_info['device']

    # Security: Validate device name
    if not validate_device_name(device):
        logger.warning(f"Invalid device name requested: {device}")
        return web.Response(status=400, text="Invalid device name")

    # Check VM exists
    if not check_vm_exists(device):
        logger.warning(f"Device not found: {device}")
        return web.Response(status=404, text=f"Device {device} not found")

    # Check VM is running
    state = get_vm_state(device)
    if state != 'running':
        logger.warning(f"Device not running: {device} (state: {state})")
        return web.Response(status=400, text=f"Device {device} is not running (state: {state})")

    # Prepare WebSocket
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)

    logger.info(f"WebSocket connection established for {device}")

    session = ConsoleSession(device)

    try:
        master_fd = await session.start()
        loop = asyncio.get_event_loop()

        # Task to read from PTY and send to WebSocket
        async def read_pty():
            while not ws.closed:
                try:
                    # Use run_in_executor for blocking read
                    data = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: os.read(master_fd, 4096)),
                        timeout=0.1
                    )
                    if data:
                        await ws.send_bytes(data)
                except asyncio.TimeoutError:
                    # No data available, check if process still alive
                    if session.process and session.process.poll() is not None:
                        logger.info(f"Console process exited for {device}")
                        break
                except BlockingIOError:
                    await asyncio.sleep(0.01)
                except OSError as e:
                    if e.errno == 5:  # Input/output error - PTY closed
                        break
                    logger.error(f"PTY read error: {e}")
                    break
                except Exception as e:
                    logger.error(f"Read error: {e}")
                    break

        read_task = asyncio.create_task(read_pty())

        # Read from WebSocket and write to PTY
        try:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    try:
                        os.write(master_fd, msg.data)
                    except OSError as e:
                        logger.error(f"PTY write error: {e}")
                        break
                elif msg.type == WSMsgType.TEXT:
                    # Handle control messages (e.g., resize)
                    try:
                        import json
                        data = json.loads(msg.data)
                        if data.get('type') == 'resize':
                            session.resize(data.get('rows', 24), data.get('cols', 80))
                        elif data.get('type') == 'input':
                            os.write(master_fd, data.get('data', '').encode())
                    except json.JSONDecodeError:
                        # Plain text input
                        os.write(master_fd, msg.data.encode())
                elif msg.type == WSMsgType.ERROR:
                    logger.error(f"WebSocket error: {ws.exception()}")
                    break
        finally:
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass

    except Exception as e:
        logger.error(f"Console session error for {device}: {e}")
    finally:
        await session.cleanup()
        if not ws.closed:
            await ws.close()

    logger.info(f"WebSocket connection closed for {device}")
    return ws


def create_app():
    """Create and configure the application"""
    app = web.Application()
    app.add_routes(routes)
    return app


def main():
    """Main entry point"""
    logger.info(f"Starting Console Service on {SERVICE_HOST}:{SERVICE_PORT}")
    app = create_app()
    web.run_app(app, host=SERVICE_HOST, port=SERVICE_PORT)


if __name__ == '__main__':
    main()

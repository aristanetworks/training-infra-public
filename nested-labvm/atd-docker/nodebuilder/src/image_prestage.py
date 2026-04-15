"""
Background image pre-staging for nodebuilder.

Downloads base images at service startup to reduce wait times
when users create devices. Images are downloaded in priority order:
1. Linux host (most commonly used)
2. VyOS firewall
3. VeloCloud devices (if enabled)

Downloads are staggered to avoid overwhelming bandwidth.
Existing images are skipped (checked by underlying download functions).
"""

import asyncio
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class ImagePrestager:
    """Manages background pre-staging of VM base images."""

    def __init__(self):
        self.downloads_complete = False
        self.download_task = None
        self.status: Dict[str, str] = {}  # Track status per image type

    async def start_prestaging(self):
        """Start background image downloads (non-blocking).

        Creates an async task that runs in the background, allowing the
        service to start accepting requests immediately while images download.
        """
        self.download_task = asyncio.create_task(self._download_all_images())

    async def _download_all_images(self):
        """Download images in priority order: hosts -> firewall -> velo.

        Uses asyncio.to_thread() to wrap synchronous download functions
        so they don't block the event loop.
        """
        from config import (
            get_host_base_image_path,
            get_firewall_base_image_path,
            get_velo_base_image_path,
            get_velo_orchestrator_disk_paths,
            is_velo_enabled
        )

        try:
            # 1. Linux host image (highest priority - most commonly used)
            logger.info("Pre-staging: Starting host base image download...")
            self.status['host'] = 'downloading'
            await asyncio.to_thread(get_host_base_image_path, True)
            self.status['host'] = 'complete'
            logger.info("Pre-staging: Host base image ready")

            # Small delay between downloads to avoid overwhelming bandwidth
            await asyncio.sleep(2)

            # 2. VyOS firewall image
            logger.info("Pre-staging: Starting firewall base image download...")
            self.status['firewall'] = 'downloading'
            await asyncio.to_thread(get_firewall_base_image_path, True)
            self.status['firewall'] = 'complete'
            logger.info("Pre-staging: Firewall base image ready")

            await asyncio.sleep(2)

            # 3. VeloCloud images (only if enabled in ACCESS_INFO.yaml)
            if is_velo_enabled():
                logger.info("Pre-staging: Starting VeloCloud image downloads...")

                # Edge
                self.status['velo_edge'] = 'downloading'
                await asyncio.to_thread(get_velo_base_image_path, 'edge', True)
                self.status['velo_edge'] = 'complete'
                logger.info("Pre-staging: VeloCloud Edge image ready")
                await asyncio.sleep(2)

                # Gateway
                self.status['velo_gateway'] = 'downloading'
                await asyncio.to_thread(get_velo_base_image_path, 'gateway', True)
                self.status['velo_gateway'] = 'complete'
                logger.info("Pre-staging: VeloCloud Gateway image ready")
                await asyncio.sleep(2)

                # Orchestrator (multiple disks - takes longest)
                self.status['velo_orchestrator'] = 'downloading'
                await asyncio.to_thread(get_velo_orchestrator_disk_paths, True)
                self.status['velo_orchestrator'] = 'complete'
                logger.info("Pre-staging: VeloCloud Orchestrator images ready")
            else:
                logger.info("Pre-staging: VeloCloud disabled, skipping VeloCloud images")
                self.status['velo_edge'] = 'skipped'
                self.status['velo_gateway'] = 'skipped'
                self.status['velo_orchestrator'] = 'skipped'

            self.downloads_complete = True
            logger.info("Pre-staging: All base images ready")

        except asyncio.CancelledError:
            logger.info("Pre-staging: Downloads cancelled (service shutdown)")
            raise
        except Exception as e:
            logger.error(f"Pre-staging error: {e}", exc_info=True)
            # Don't crash the service - images will download on-demand as fallback


# Global singleton instance
_prestager = None


def get_prestager() -> ImagePrestager:
    """Get the singleton ImagePrestager instance."""
    global _prestager
    if _prestager is None:
        _prestager = ImagePrestager()
    return _prestager


async def start_background_prestaging():
    """Called from aiohttp on_startup hook.

    Starts the background download task without blocking the server startup.
    """
    prestager = get_prestager()
    await prestager.start_prestaging()
    logger.info("Pre-staging: Background download task started")


async def cancel_prestaging():
    """Called from aiohttp on_cleanup hook.

    Cancels any in-progress downloads when the service is shutting down.
    """
    prestager = get_prestager()
    if prestager.download_task and not prestager.download_task.done():
        prestager.download_task.cancel()
        try:
            await prestager.download_task
        except asyncio.CancelledError:
            logger.info("Pre-staging: Downloads cancelled due to shutdown")

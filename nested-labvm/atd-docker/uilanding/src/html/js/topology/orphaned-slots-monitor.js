/**
 * Orphaned Slots Monitor for ATL Interactive Topology
 *
 * Monitors orphaned interface slots from deleted devices and provides
 * a UI indicator and cleanup functionality.
 *
 * Orphaned slots occur when a device is deleted but its interface slot
 * is preserved on the target device for potential reuse. This helps
 * maintain interface numbering consistency across device additions/deletions.
 */

export class OrphanedSlotsMonitor {
    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.container = topologyManager.container;
        this.pollInterval = null;
        this.pollDelay = 60000; // Check every 60 seconds
        this.lastOrphanedCount = 0;
        this.banner = null;
        this.isPolling = false;
    }

    /**
     * Initialize the monitor - start polling for orphaned slots
     */
    init() {
        console.log('[OrphanedSlotsMonitor] Initializing...');

        // Initial check
        this.checkOrphanedSlots();

        // Start periodic polling
        this.startPolling();
    }

    /**
     * Start polling for orphaned slots
     */
    startPolling() {
        if (this.isPolling) return;

        this.isPolling = true;
        this.pollInterval = setInterval(() => {
            this.checkOrphanedSlots();
        }, this.pollDelay);

        console.log('[OrphanedSlotsMonitor] Started polling every', this.pollDelay / 1000, 'seconds');
    }

    /**
     * Stop polling
     */
    stopPolling() {
        if (this.pollInterval) {
            clearInterval(this.pollInterval);
            this.pollInterval = null;
        }
        this.isPolling = false;
        console.log('[OrphanedSlotsMonitor] Stopped polling');
    }

    /**
     * Check for orphaned slots from the API
     */
    async checkOrphanedSlots() {
        try {
            const response = await fetch('/nb-api/orphaned-slots');

            if (!response.ok) {
                console.warn('[OrphanedSlotsMonitor] Failed to fetch orphaned slots:', response.status);
                return;
            }

            const data = await response.json();
            const orphanedSlots = data.orphaned_slots || {};

            // Count total orphaned slots
            let totalCount = 0;
            const deviceCounts = {};

            for (const [device, slots] of Object.entries(orphanedSlots)) {
                const count = slots.length;
                if (count > 0) {
                    deviceCounts[device] = count;
                    totalCount += count;
                }
            }

            console.log('[OrphanedSlotsMonitor] Orphaned slots:', totalCount, deviceCounts);

            // Update UI based on count
            if (totalCount > 0) {
                this.showOrphanedSlotsBanner(totalCount, deviceCounts);
            } else {
                this.hideOrphanedSlotsBanner();
            }

            this.lastOrphanedCount = totalCount;

        } catch (error) {
            console.error('[OrphanedSlotsMonitor] Error checking orphaned slots:', error);
        }
    }

    /**
     * Show the orphaned slots notification banner
     */
    showOrphanedSlotsBanner(totalCount, deviceCounts) {
        // Remove any existing banner
        this.hideOrphanedSlotsBanner();

        // Create new banner
        this.banner = document.createElement('div');
        this.banner.id = 'orphaned-slots-banner';
        this.banner.className = 'orphaned-slots-banner';

        // Build device summary
        const deviceList = Object.entries(deviceCounts)
            .map(([device, count]) => `${device}: ${count}`)
            .join(', ');

        this.banner.innerHTML = `
            <div class="orphaned-slots-content">
                <span class="orphaned-slots-icon">&#128279;</span>
                <span class="orphaned-slots-text">
                    <strong>${totalCount} orphaned interface slot${totalCount !== 1 ? 's' : ''}</strong>
                    preserved for reuse (${deviceList})
                </span>
                <button class="orphaned-slots-info-btn" id="orphaned-slots-info-btn" title="What are orphaned slots?">
                    ?
                </button>
                <button class="orphaned-slots-cleanup-btn" id="orphaned-slots-cleanup-btn">
                    Cleanup
                </button>
                <button class="orphaned-slots-dismiss" id="orphaned-slots-dismiss" title="Dismiss">
                    &times;
                </button>
            </div>
        `;

        // Add to container (before topology)
        if (this.container.parentElement) {
            this.container.parentElement.insertBefore(this.banner, this.container);
        } else {
            this.container.insertBefore(this.banner, this.container.firstChild);
        }

        // Add event listeners
        const infoBtn = this.banner.querySelector('#orphaned-slots-info-btn');
        const cleanupBtn = this.banner.querySelector('#orphaned-slots-cleanup-btn');
        const dismissBtn = this.banner.querySelector('#orphaned-slots-dismiss');

        infoBtn.addEventListener('click', () => this.showInfoDialog());
        cleanupBtn.addEventListener('click', () => this.showCleanupDialog(deviceCounts));
        dismissBtn.addEventListener('click', () => this.hideOrphanedSlotsBanner());
    }

    /**
     * Hide the orphaned slots notification banner
     */
    hideOrphanedSlotsBanner() {
        if (this.banner) {
            this.banner.remove();
            this.banner = null;
        }

        // Also remove by ID in case it exists
        const existingBanner = document.getElementById('orphaned-slots-banner');
        if (existingBanner) {
            existingBanner.remove();
        }
    }

    /**
     * Show information dialog explaining orphaned slots
     */
    showInfoDialog() {
        // Remove any existing dialog
        const existing = document.querySelector('.orphaned-slots-info-dialog');
        if (existing) existing.remove();

        const dialog = document.createElement('div');
        dialog.className = 'orphaned-slots-dialog orphaned-slots-info-dialog';
        dialog.innerHTML = `
            <div class="orphaned-slots-dialog-content">
                <div class="orphaned-slots-dialog-header">
                    <h3>Orphaned Interface Slots</h3>
                    <button class="orphaned-slots-dialog-close">&times;</button>
                </div>
                <div class="orphaned-slots-dialog-body">
                    <p>When you delete a user-added device, the interface slot on the
                    target switch (e.g., spine1:Ethernet5) is preserved rather than removed.</p>

                    <p><strong>Why?</strong> This prevents interface renumbering issues.
                    If interface 5 were truly removed, a VM reboot would cause interface 6
                    to become interface 5, breaking your topology connections.</p>

                    <p><strong>Automatic Reuse:</strong> When you add a new device to the
                    same target switch, the orphaned slot will be automatically reused,
                    maintaining consistent interface numbering.</p>

                    <p><strong>Cleanup:</strong> Use the cleanup button to manually release
                    orphaned slots. Note that this may cause interface renumbering on the
                    next target VM reboot.</p>
                </div>
                <div class="orphaned-slots-dialog-footer">
                    <button class="orphaned-slots-dialog-btn primary">Got it</button>
                </div>
            </div>
        `;

        document.body.appendChild(dialog);

        // Event listeners
        const closeBtn = dialog.querySelector('.orphaned-slots-dialog-close');
        const gotItBtn = dialog.querySelector('.orphaned-slots-dialog-btn.primary');

        const closeDialog = () => dialog.remove();
        closeBtn.addEventListener('click', closeDialog);
        gotItBtn.addEventListener('click', closeDialog);
        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) closeDialog();
        });
    }

    /**
     * Show cleanup confirmation dialog
     */
    showCleanupDialog(deviceCounts) {
        // Remove any existing dialog
        const existing = document.querySelector('.orphaned-slots-cleanup-dialog');
        if (existing) existing.remove();

        const deviceList = Object.entries(deviceCounts)
            .map(([device, count]) => `<li>${device}: ${count} slot${count !== 1 ? 's' : ''}</li>`)
            .join('');

        const dialog = document.createElement('div');
        dialog.className = 'orphaned-slots-dialog orphaned-slots-cleanup-dialog';
        dialog.innerHTML = `
            <div class="orphaned-slots-dialog-content">
                <div class="orphaned-slots-dialog-header">
                    <h3>Cleanup Orphaned Slots</h3>
                    <button class="orphaned-slots-dialog-close">&times;</button>
                </div>
                <div class="orphaned-slots-dialog-body">
                    <p class="warning-text">
                        <span class="warning-icon">&#9888;</span>
                        This will truly detach the orphaned interfaces from the target VMs.
                    </p>

                    <p>Affected devices:</p>
                    <ul class="device-list">${deviceList}</ul>

                    <p class="note">After cleanup, interface renumbering may occur on the
                    next VM reboot. This action cannot be undone.</p>

                    <div class="cleanup-options">
                        <label class="checkbox-label">
                            <input type="checkbox" id="truly-detach-checkbox" checked>
                            Truly detach interfaces (recommended)
                        </label>
                    </div>
                </div>
                <div class="orphaned-slots-dialog-footer">
                    <button class="orphaned-slots-dialog-btn secondary cancel-btn">Cancel</button>
                    <button class="orphaned-slots-dialog-btn danger cleanup-confirm-btn">Cleanup All</button>
                </div>
            </div>
        `;

        document.body.appendChild(dialog);

        // Event listeners
        const closeBtn = dialog.querySelector('.orphaned-slots-dialog-close');
        const cancelBtn = dialog.querySelector('.cancel-btn');
        const confirmBtn = dialog.querySelector('.cleanup-confirm-btn');
        const trulyDetachCheckbox = dialog.querySelector('#truly-detach-checkbox');

        const closeDialog = () => dialog.remove();
        closeBtn.addEventListener('click', closeDialog);
        cancelBtn.addEventListener('click', closeDialog);
        dialog.addEventListener('click', (e) => {
            if (e.target === dialog) closeDialog();
        });

        confirmBtn.addEventListener('click', async () => {
            const trulyDetach = trulyDetachCheckbox.checked;
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Cleaning up...';

            try {
                await this.performCleanup(trulyDetach);
                closeDialog();
                this.hideOrphanedSlotsBanner();
                // Refresh the check
                this.checkOrphanedSlots();
            } catch (error) {
                alert('Cleanup failed: ' + error.message);
                confirmBtn.disabled = false;
                confirmBtn.textContent = 'Cleanup All';
            }
        });
    }

    /**
     * Perform the cleanup API call
     */
    async performCleanup(trulyDetach = true) {
        const response = await fetch('/nb-api/cleanup-orphaned-slots', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ truly_detach: trulyDetach })
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        const result = await response.json();
        console.log('[OrphanedSlotsMonitor] Cleanup result:', result);
        return result;
    }

    /**
     * Force a refresh of the orphaned slots status
     */
    refresh() {
        this.checkOrphanedSlots();
    }

    /**
     * Destroy the monitor
     */
    destroy() {
        this.stopPolling();
        this.hideOrphanedSlotsBanner();
    }
}

// Export for non-module usage
if (typeof window !== 'undefined') {
    window.OrphanedSlotsMonitor = OrphanedSlotsMonitor;
}

/**
 * Device Reboot Manager
 *
 * Shared component for managing device save config and reboot operations.
 * Used by AddNodeWizard, AddClusterWizard, and other topology components.
 */

class DeviceRebootManager {
    /**
     * Create a new DeviceRebootManager
     * @param {Array} targetDevices - List of devices with name and ip_addr properties
     */
    constructor(targetDevices = []) {
        this.targetDevices = targetDevices;
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Render the reboot section HTML
     * @param {Array} rebootTargets - List of device names that need rebooting
     * @returns {string} HTML string for the reboot section
     */
    renderRebootSection(rebootTargets) {
        if (!rebootTargets || rebootTargets.length === 0) {
            return '';
        }

        const deviceRows = rebootTargets.map(device => {
            const deviceInfo = this.targetDevices.find(d => d.name === device);
            const deviceIp = deviceInfo?.ip_addr || '';
            return `
                <div class="target-device-row" data-device="${this.escapeHtml(device)}" data-ip="${this.escapeHtml(deviceIp)}">
                    <span class="device-name">${this.escapeHtml(device)}</span>
                    <span class="device-ip">${this.escapeHtml(deviceIp)}</span>
                    <button class="save-config-btn" title="Save running config to startup config">
                        Save Config
                    </button>
                    <span class="save-status"></span>
                </div>
            `;
        }).join('');

        return `
            <div class="reboot-section">
                <h4>&#9888; Target Device Reboot Required</h4>
                <p>The following devices need to be rebooted to detect the new interfaces:</p>
                <div class="target-devices-list">
                    ${deviceRows}
                </div>
                <div class="reboot-actions">
                    <p class="reboot-warning">&#9888; Save running configs before rebooting to preserve any configuration changes.</p>
                    <button class="reboot-all-btn">Reboot Target Devices</button>
                    <span class="reboot-status"></span>
                </div>
            </div>
        `;
    }

    /**
     * Attach event handlers to the reboot section
     * @param {HTMLElement} container - Container element containing the reboot section
     * @param {Function} onComplete - Optional callback when operations complete
     */
    attachEventHandlers(container, onComplete = null) {
        if (!container) return;

        // Setup save config button handlers
        container.querySelectorAll('.save-config-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                await this.handleSaveConfig(e.target);
            });
        });

        // Setup reboot button handler
        const rebootBtn = container.querySelector('.reboot-all-btn');
        if (rebootBtn) {
            rebootBtn.addEventListener('click', async () => {
                await this.handleRebootAll(container, onComplete);
            });
        }
    }

    /**
     * Handle save config button click
     * @param {HTMLElement} button - The save config button that was clicked
     */
    async handleSaveConfig(button) {
        const row = button.closest('.target-device-row');
        if (!row) {
            console.error('[DeviceRebootManager] Button not in expected DOM structure');
            return;
        }

        const device = row.dataset.device;
        const ip = row.dataset.ip;
        const statusSpan = row.querySelector('.save-status');

        if (!device) {
            console.error('[DeviceRebootManager] Missing device data attribute');
            return;
        }

        button.disabled = true;
        button.textContent = 'Saving...';
        if (statusSpan) {
            statusSpan.textContent = '';
            statusSpan.className = 'save-status';
        }

        try {
            await NodeBuilderAPI.saveConfig(device, ip);
            button.textContent = 'Saved';
            button.classList.add('saved');
            if (statusSpan) {
                statusSpan.textContent = '';
                statusSpan.className = 'save-status success';
            }
        } catch (error) {
            console.error('[DeviceRebootManager] Error saving config:', error);
            button.textContent = 'Save Config';
            button.disabled = false;
            if (statusSpan) {
                statusSpan.textContent = error.message;
                statusSpan.className = 'save-status error';
            }
        }
    }

    /**
     * Handle reboot all button click
     * @param {HTMLElement} container - Container element
     * @param {Function} onComplete - Optional callback when complete
     */
    async handleRebootAll(container, onComplete = null) {
        if (!container) {
            console.error('[DeviceRebootManager] Container not provided');
            return;
        }

        const rebootBtn = container.querySelector('.reboot-all-btn');
        const rebootStatus = container.querySelector('.reboot-status');

        if (!rebootBtn) {
            console.error('[DeviceRebootManager] Reboot button not found');
            return;
        }

        // Get list of devices to reboot
        const deviceRows = container.querySelectorAll('.target-device-row');
        const devices = Array.from(deviceRows)
            .map(row => row.dataset.device)
            .filter(Boolean);  // Filter out undefined/null values

        if (devices.length === 0) {
            console.warn('[DeviceRebootManager] No devices to reboot');
            return;
        }

        rebootBtn.disabled = true;
        rebootBtn.textContent = 'Rebooting...';
        if (rebootStatus) {
            rebootStatus.textContent = '';
            rebootStatus.className = 'reboot-status';
        }

        try {
            const result = await NodeBuilderAPI.rebootDevices(devices);

            const rebootedCount = result.rebooted?.length || 0;
            const errorCount = result.errors?.length || 0;

            if (errorCount > 0) {
                rebootBtn.textContent = 'Reboot Complete';
                if (rebootStatus) {
                    rebootStatus.textContent = `${rebootedCount} rebooted, ${errorCount} failed`;
                    rebootStatus.className = 'reboot-status warning';
                }
            } else {
                rebootBtn.textContent = 'Rebooted';
                rebootBtn.classList.add('rebooted');
                if (rebootStatus) {
                    rebootStatus.textContent = `${rebootedCount} device${rebootedCount !== 1 ? 's' : ''} rebooting`;
                    rebootStatus.className = 'reboot-status success';
                }
            }

            if (onComplete) {
                onComplete({ success: true, result });
            }
        } catch (error) {
            console.error('[DeviceRebootManager] Error rebooting devices:', error);
            rebootBtn.textContent = 'Reboot Target Devices';
            rebootBtn.disabled = false;
            if (rebootStatus) {
                rebootStatus.textContent = error.message;
                rebootStatus.className = 'reboot-status error';
            }

            if (onComplete) {
                onComplete({ success: false, error });
            }
        }
    }

    /**
     * Create a complete reboot section with handlers attached
     * @param {Array} rebootTargets - List of device names that need rebooting
     * @param {Function} onComplete - Optional callback when operations complete
     * @returns {HTMLElement} DOM element with attached handlers
     */
    createRebootSection(rebootTargets, onComplete = null) {
        const html = this.renderRebootSection(rebootTargets);
        if (!html) return null;

        const wrapper = document.createElement('div');
        wrapper.innerHTML = html;
        const section = wrapper.firstElementChild;

        this.attachEventHandlers(section, onComplete);
        return section;
    }
}

// Export for use in other modules
window.DeviceRebootManager = DeviceRebootManager;

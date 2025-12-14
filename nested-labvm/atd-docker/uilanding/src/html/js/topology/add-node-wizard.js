/**
 * Add Node Wizard for ATD Topology
 *
 * Multi-step wizard for dynamically adding vEOS nodes to running KVM labs.
 * Triggered by right-clicking empty canvas space in the topology diagram.
 *
 * Wizard Flow:
 * 1. Enter device name (validates uniqueness)
 * 2. Select IP address (from available pool, auto-fills MAC)
 * 3. Configure connections (select target devices)
 * 4. Review and confirm
 *
 * Only available for KVM/vEOS labs, disabled for container labs.
 */

class AddNodeWizard {
    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;
        this.currentStep = 1;
        this.totalSteps = 4;

        // Wizard state
        this.nodeConfig = {
            name: '',
            ip: '',
            mac: '',
            connections: []
        };

        // Cached data from API
        this.availableIps = [];
        this.targetDevices = [];
        this.existingNodes = [];

        // Validation state
        this.nameValid = false;
        this.nameError = '';

        this.isSubmitting = false;
    }

    /**
     * Check if add-node feature is available (KVM mode only)
     */
    isAvailable() {
        // Check if this is a cEOS/container lab
        const eventManager = this.topologyManager?.eventManager;
        if (eventManager && eventManager.isCeosLab) {
            return false;
        }
        return true;
    }

    /**
     * Show the wizard overlay
     */
    async show() {
        if (!this.isAvailable()) {
            console.log('Add Node wizard not available for container labs');
            return;
        }

        // Reset state
        this.currentStep = 1;
        this.nodeConfig = {
            name: '',
            ip: '',
            mac: '',
            connections: []
        };
        this.nameValid = false;
        this.nameError = '';
        this.isSubmitting = false;

        // Create overlay
        this.createOverlay();

        // Load data from API
        await this.loadAvailableData();

        // Render first step
        this.renderStep();
    }

    /**
     * Hide and cleanup the wizard
     */
    hide() {
        if (this.overlay) {
            this.overlay.remove();
            this.overlay = null;
        }
    }

    /**
     * Create the wizard overlay and container
     */
    createOverlay() {
        // Remove any existing overlay
        this.hide();

        const overlay = document.createElement('div');
        overlay.className = 'add-node-wizard-overlay';
        overlay.innerHTML = `
            <div class="add-node-wizard">
                <div class="wizard-header">
                    <h2>Add New Node</h2>
                    <button class="wizard-close-btn" title="Close">&times;</button>
                </div>
                <div class="wizard-progress">
                    <div class="progress-steps">
                        <div class="progress-step active" data-step="1">
                            <span class="step-number">1</span>
                            <span class="step-label">Name</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="2">
                            <span class="step-number">2</span>
                            <span class="step-label">IP Address</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="3">
                            <span class="step-number">3</span>
                            <span class="step-label">Connections</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="4">
                            <span class="step-number">4</span>
                            <span class="step-label">Review</span>
                        </div>
                    </div>
                </div>
                <div class="wizard-content">
                    <!-- Step content rendered here -->
                </div>
                <div class="wizard-footer">
                    <button class="wizard-btn wizard-btn-secondary wizard-back-btn" style="display: none;">Back</button>
                    <div class="wizard-footer-spacer"></div>
                    <button class="wizard-btn wizard-btn-secondary wizard-cancel-btn">Cancel</button>
                    <button class="wizard-btn wizard-btn-primary wizard-next-btn">Next</button>
                </div>
            </div>
        `;

        // Event listeners
        overlay.querySelector('.wizard-close-btn').addEventListener('click', () => this.hide());
        overlay.querySelector('.wizard-cancel-btn').addEventListener('click', () => this.hide());
        overlay.querySelector('.wizard-back-btn').addEventListener('click', () => this.previousStep());
        overlay.querySelector('.wizard-next-btn').addEventListener('click', () => this.nextStep());

        // Close on overlay click (but not wizard click)
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hide();
            }
        });

        // Close on escape key
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                this.hide();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);

        document.body.appendChild(overlay);
        this.overlay = overlay;
    }

    /**
     * Load available IPs and target devices from API
     */
    async loadAvailableData() {
        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = '<div class="wizard-loading"><div class="spinner"></div><p>Loading available resources...</p></div>';

        try {
            // Fetch available IPs
            const ipsResponse = await fetch('/td-api/nodes/available-ips');
            if (ipsResponse.ok) {
                const ipsData = await ipsResponse.json();
                this.availableIps = ipsData.available_ips || [];
            } else {
                throw new Error('Failed to fetch available IPs');
            }

            // Fetch target devices
            const devicesResponse = await fetch('/td-api/nodes/target-devices');
            if (devicesResponse.ok) {
                const devicesData = await devicesResponse.json();
                this.targetDevices = devicesData.devices || [];
            } else {
                throw new Error('Failed to fetch target devices');
            }

            // Fetch existing nodes for name validation context
            const nodesResponse = await fetch('/td-api/nodes/existing-nodes');
            if (nodesResponse.ok) {
                const nodesData = await nodesResponse.json();
                this.existingNodes = nodesData.nodes || [];
            }

        } catch (error) {
            console.error('Error loading wizard data:', error);
            content.innerHTML = `
                <div class="wizard-error">
                    <p>Failed to load resources from nodebuilder service.</p>
                    <p class="error-detail">${error.message}</p>
                    <p>Make sure the nodebuilder service is running.</p>
                </div>
            `;
            return;
        }

        // Check if we have any available IPs
        if (this.availableIps.length === 0) {
            content.innerHTML = `
                <div class="wizard-error">
                    <p>No available IP addresses found.</p>
                    <p>All IPs from the DHCP pool are currently in use.</p>
                </div>
            `;
            return;
        }
    }

    /**
     * Render the current step content
     */
    renderStep() {
        const content = this.overlay.querySelector('.wizard-content');
        const backBtn = this.overlay.querySelector('.wizard-back-btn');
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');

        // Update progress indicator
        this.overlay.querySelectorAll('.progress-step').forEach((step, index) => {
            step.classList.remove('active', 'completed');
            if (index + 1 < this.currentStep) {
                step.classList.add('completed');
            } else if (index + 1 === this.currentStep) {
                step.classList.add('active');
            }
        });

        // Update back button visibility
        backBtn.style.display = this.currentStep > 1 ? 'block' : 'none';

        // Update next button text
        if (this.currentStep === this.totalSteps) {
            nextBtn.textContent = 'Create Node';
            nextBtn.classList.add('wizard-btn-create');
        } else {
            nextBtn.textContent = 'Next';
            nextBtn.classList.remove('wizard-btn-create');
        }

        // Render step content
        switch (this.currentStep) {
            case 1:
                this.renderNameStep(content);
                break;
            case 2:
                this.renderIpStep(content);
                break;
            case 3:
                this.renderConnectionsStep(content);
                break;
            case 4:
                this.renderReviewStep(content);
                break;
        }

        this.updateNextButtonState();
    }

    /**
     * Step 1: Device Name Entry
     */
    renderNameStep(content) {
        content.innerHTML = `
            <div class="wizard-step wizard-step-name">
                <h3>Enter Device Name</h3>
                <p class="step-description">Choose a unique name for the new vEOS device. Names must start with a letter and contain only letters, numbers, dashes, and underscores.</p>

                <div class="form-group">
                    <label for="node-name">Device Name</label>
                    <input type="text"
                           id="node-name"
                           class="form-input"
                           placeholder="e.g., leaf5, spine3, borderleaf1"
                           value="${this.escapeHtml(this.nodeConfig.name)}"
                           maxlength="32"
                           autocomplete="off">
                    <div class="validation-message ${this.nameError ? 'error' : this.nameValid ? 'success' : ''}">
                        ${this.nameError || (this.nameValid ? 'Name is available' : '')}
                    </div>
                </div>

                <div class="existing-nodes-hint">
                    <p>Existing nodes: ${this.existingNodes.map(n => n.name || Object.keys(n)[0]).slice(0, 10).join(', ')}${this.existingNodes.length > 10 ? '...' : ''}</p>
                </div>
            </div>
        `;

        const input = content.querySelector('#node-name');
        input.focus();

        // Validate on input with debounce
        let debounceTimer;
        input.addEventListener('input', (e) => {
            this.nodeConfig.name = e.target.value.trim();
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => this.validateName(), 300);
        });

        // Validate on enter key
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && this.nameValid) {
                this.nextStep();
            }
        });
    }

    /**
     * Validate device name via API
     */
    async validateName() {
        const name = this.nodeConfig.name;
        const validationMsg = this.overlay.querySelector('.validation-message');

        if (!name) {
            this.nameValid = false;
            this.nameError = '';
            validationMsg.className = 'validation-message';
            validationMsg.textContent = '';
            this.updateNextButtonState();
            return;
        }

        // Quick client-side validation
        if (!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)) {
            this.nameValid = false;
            this.nameError = 'Name must start with a letter and contain only letters, numbers, dashes, and underscores';
            validationMsg.className = 'validation-message error';
            validationMsg.textContent = this.nameError;
            this.updateNextButtonState();
            return;
        }

        if (name.length > 32) {
            this.nameValid = false;
            this.nameError = 'Name must be 32 characters or less';
            validationMsg.className = 'validation-message error';
            validationMsg.textContent = this.nameError;
            this.updateNextButtonState();
            return;
        }

        // Server-side validation
        try {
            const response = await fetch('/td-api/nodes/validate-node', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: name })
            });

            const result = await response.json();

            if (result.valid) {
                this.nameValid = true;
                this.nameError = '';
                validationMsg.className = 'validation-message success';
                validationMsg.textContent = 'Name is available';
            } else {
                this.nameValid = false;
                this.nameError = result.errors?.[0] || 'Invalid name';
                validationMsg.className = 'validation-message error';
                validationMsg.textContent = this.nameError;
            }
        } catch (error) {
            console.error('Error validating name:', error);
            // Allow proceeding on validation error - server will check again
            this.nameValid = true;
            this.nameError = '';
            validationMsg.className = 'validation-message';
            validationMsg.textContent = '';
        }

        this.updateNextButtonState();
    }

    /**
     * Step 2: IP Address Selection
     */
    renderIpStep(content) {
        const ipOptions = this.availableIps.map(entry => `
            <option value="${this.escapeHtml(entry.ip)}"
                    data-mac="${this.escapeHtml(entry.mac)}"
                    data-hostname="${this.escapeHtml(entry.hostname || '')}">
                ${this.escapeHtml(entry.ip)}${entry.hostname ? ` (${this.escapeHtml(entry.hostname)})` : ''}
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-ip">
                <h3>Select IP Address</h3>
                <p class="step-description">Choose an available IP address from the DHCP pool. The MAC address will be automatically assigned.</p>

                <div class="form-group">
                    <label for="node-ip">IP Address</label>
                    <select id="node-ip" class="form-select">
                        <option value="">Select an IP address...</option>
                        ${ipOptions}
                    </select>
                </div>

                <div class="mac-display ${this.nodeConfig.mac ? 'visible' : ''}">
                    <label>MAC Address (auto-assigned)</label>
                    <div class="mac-value">${this.escapeHtml(this.nodeConfig.mac) || '--'}</div>
                </div>

                <div class="ip-info">
                    <p><strong>${this.availableIps.length}</strong> IP addresses available in the DHCP pool</p>
                </div>
            </div>
        `;

        const select = content.querySelector('#node-ip');

        // Set current value if already selected
        if (this.nodeConfig.ip) {
            select.value = this.nodeConfig.ip;
        }

        select.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            this.nodeConfig.ip = e.target.value;
            this.nodeConfig.mac = selectedOption?.dataset.mac || '';

            const macDisplay = content.querySelector('.mac-display');
            const macValue = content.querySelector('.mac-value');

            if (this.nodeConfig.mac) {
                macValue.textContent = this.nodeConfig.mac;
                macDisplay.classList.add('visible');
            } else {
                macDisplay.classList.remove('visible');
            }

            this.updateNextButtonState();
        });
    }

    /**
     * Step 3: Connection Configuration
     */
    renderConnectionsStep(content) {
        const deviceCards = this.targetDevices.map(device => {
            const isSelected = this.nodeConfig.connections.some(c => c.target_device === device.name);
            const usedPortsDisplay = device.used_ports?.length > 0
                ? `Used: ${device.used_ports.slice(0, 3).join(', ')}${device.used_ports.length > 3 ? '...' : ''}`
                : 'No ports in use';

            return `
                <div class="device-card ${isSelected ? 'selected' : ''}"
                     data-device="${this.escapeHtml(device.name)}">
                    <div class="device-card-header">
                        <input type="checkbox"
                               class="device-checkbox"
                               id="conn-${this.escapeHtml(device.name)}"
                               ${isSelected ? 'checked' : ''}>
                        <label for="conn-${this.escapeHtml(device.name)}">${this.escapeHtml(device.name)}</label>
                    </div>
                    <div class="device-card-info">
                        <span class="next-port">Next: ${this.escapeHtml(device.next_available_port)}</span>
                        <span class="used-ports">${usedPortsDisplay}</span>
                    </div>
                </div>
            `;
        }).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-connections">
                <h3>Configure Connections</h3>
                <p class="step-description">Select which existing devices to connect to. Each connection will use the next available port on both devices.</p>

                <div class="connection-hint">
                    <strong>Tip:</strong> Interfaces are automatically assigned contiguously starting from Ethernet1 on the new node.
                </div>

                <div class="device-grid">
                    ${deviceCards || '<p class="no-devices">No devices available for connection</p>'}
                </div>

                <div class="connection-summary">
                    <h4>Selected Connections</h4>
                    <div class="connection-list">
                        ${this.renderConnectionSummary()}
                    </div>
                </div>
            </div>
        `;

        // Add click handlers to device cards
        content.querySelectorAll('.device-card').forEach(card => {
            card.addEventListener('click', (e) => {
                // Don't trigger if clicking the checkbox directly
                if (e.target.type === 'checkbox') return;

                const checkbox = card.querySelector('.device-checkbox');
                checkbox.checked = !checkbox.checked;
                this.handleConnectionToggle(card, checkbox.checked);
            });

            card.querySelector('.device-checkbox').addEventListener('change', (e) => {
                this.handleConnectionToggle(card, e.target.checked);
            });
        });
    }

    /**
     * Handle connection checkbox toggle
     */
    handleConnectionToggle(card, isSelected) {
        const deviceName = card.dataset.device;
        const device = this.targetDevices.find(d => d.name === deviceName);

        if (isSelected) {
            card.classList.add('selected');
            // Add connection if not already present
            if (!this.nodeConfig.connections.some(c => c.target_device === deviceName)) {
                this.nodeConfig.connections.push({
                    target_device: deviceName,
                    target_port: device?.next_available_port || 'Ethernet1'
                });
            }
        } else {
            card.classList.remove('selected');
            // Remove connection
            this.nodeConfig.connections = this.nodeConfig.connections.filter(
                c => c.target_device !== deviceName
            );
        }

        // Update connection summary
        const summaryList = this.overlay.querySelector('.connection-list');
        summaryList.innerHTML = this.renderConnectionSummary();

        this.updateNextButtonState();
    }

    /**
     * Render connection summary list
     */
    renderConnectionSummary() {
        if (this.nodeConfig.connections.length === 0) {
            return '<p class="no-connections">No connections selected</p>';
        }

        return this.nodeConfig.connections.map((conn, index) => `
            <div class="connection-item">
                <span class="local-port">Ethernet${index + 1}</span>
                <span class="connection-arrow">&harr;</span>
                <span class="remote-info">${this.escapeHtml(conn.target_device)} (${this.escapeHtml(conn.target_port)})</span>
            </div>
        `).join('');
    }

    /**
     * Step 4: Review and Confirm
     */
    renderReviewStep(content) {
        const connectionsHtml = this.nodeConfig.connections.length > 0
            ? this.nodeConfig.connections.map((conn, index) => `
                <tr>
                    <td>Ethernet${index + 1}</td>
                    <td>${this.escapeHtml(conn.target_device)}</td>
                    <td>${this.escapeHtml(conn.target_port)}</td>
                </tr>
            `).join('')
            : '<tr><td colspan="3" class="no-data">No connections configured</td></tr>';

        content.innerHTML = `
            <div class="wizard-step wizard-step-review">
                <h3>Review Configuration</h3>
                <p class="step-description">Please review the configuration before creating the node.</p>

                <div class="review-section">
                    <h4>Device Information</h4>
                    <table class="review-table">
                        <tr>
                            <th>Device Name</th>
                            <td>${this.escapeHtml(this.nodeConfig.name)}</td>
                        </tr>
                        <tr>
                            <th>IP Address</th>
                            <td>${this.escapeHtml(this.nodeConfig.ip)}</td>
                        </tr>
                        <tr>
                            <th>MAC Address</th>
                            <td>${this.escapeHtml(this.nodeConfig.mac)}</td>
                        </tr>
                        <tr>
                            <th>Platform</th>
                            <td>vEOS</td>
                        </tr>
                    </table>
                </div>

                <div class="review-section">
                    <h4>Connections (${this.nodeConfig.connections.length})</h4>
                    <table class="review-table connections-table">
                        <thead>
                            <tr>
                                <th>Local Port</th>
                                <th>Target Device</th>
                                <th>Target Port</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${connectionsHtml}
                        </tbody>
                    </table>
                </div>

                <div class="review-notes">
                    <h4>What happens next:</h4>
                    <ul>
                        <li>A new vEOS VM will be created with the specified configuration</li>
                        <li>OVS bridges will be created for each connection</li>
                        <li>Interfaces will be attached to the target devices</li>
                        <li>The device will boot and register with CVP via ZTP</li>
                        <li>The topology diagram will update automatically</li>
                    </ul>
                </div>
            </div>
        `;
    }

    /**
     * Update next button enabled/disabled state
     */
    updateNextButtonState() {
        const nextBtn = this.overlay?.querySelector('.wizard-next-btn');
        if (!nextBtn) return;

        let canProceed = false;

        switch (this.currentStep) {
            case 1:
                canProceed = this.nameValid && this.nodeConfig.name.length > 0;
                break;
            case 2:
                canProceed = this.nodeConfig.ip && this.nodeConfig.mac;
                break;
            case 3:
                // Connections are optional but recommended
                canProceed = true;
                break;
            case 4:
                canProceed = !this.isSubmitting;
                break;
        }

        nextBtn.disabled = !canProceed || this.isSubmitting;
    }

    /**
     * Go to next step
     */
    async nextStep() {
        if (this.currentStep === this.totalSteps) {
            await this.submitNode();
            return;
        }

        this.currentStep++;
        this.renderStep();
    }

    /**
     * Go to previous step
     */
    previousStep() {
        if (this.currentStep > 1) {
            this.currentStep--;
            this.renderStep();
        }
    }

    /**
     * Submit the node creation request
     */
    async submitNode() {
        if (this.isSubmitting) return;

        this.isSubmitting = true;
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');
        const originalText = nextBtn.textContent;
        nextBtn.textContent = 'Creating...';
        nextBtn.disabled = true;

        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = `
            <div class="wizard-creating">
                <div class="spinner large"></div>
                <h3>Creating Node...</h3>
                <p>This may take a minute. Please wait.</p>
                <div class="creation-log"></div>
            </div>
        `;

        const log = content.querySelector('.creation-log');

        try {
            this.logMessage(log, 'Sending request to nodebuilder service...');

            const response = await fetch('/td-api/nodes/add-node', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: this.nodeConfig.name,
                    ip: this.nodeConfig.ip,
                    connections: this.nodeConfig.connections
                })
            });

            const result = await response.json();

            if (!response.ok) {
                throw new Error(result.error || 'Failed to create node');
            }

            this.logMessage(log, 'Node created successfully!', 'success');
            this.logMessage(log, `VM: ${result.node?.name || this.nodeConfig.name}`);
            this.logMessage(log, `IP: ${result.node?.ip || this.nodeConfig.ip}`);

            // Show success state
            content.innerHTML = `
                <div class="wizard-success">
                    <div class="success-icon">&#10004;</div>
                    <h3>Node Created Successfully!</h3>
                    <p>The new vEOS node <strong>${this.escapeHtml(this.nodeConfig.name)}</strong> has been created.</p>
                    <p>It will boot and register with CVP automatically via ZTP.</p>
                    <div class="success-details">
                        <p>IP Address: <code>${this.escapeHtml(this.nodeConfig.ip)}</code></p>
                        <p>MAC Address: <code>${this.escapeHtml(this.nodeConfig.mac)}</code></p>
                    </div>
                </div>
            `;

            // Update button to close
            nextBtn.textContent = 'Close';
            nextBtn.disabled = false;
            nextBtn.onclick = () => {
                this.hide();
                // Refresh the topology diagram
                if (this.topologyManager) {
                    this.topologyManager.refreshTopology();
                }
            };

            // Hide back and cancel buttons
            this.overlay.querySelector('.wizard-back-btn').style.display = 'none';
            this.overlay.querySelector('.wizard-cancel-btn').style.display = 'none';

        } catch (error) {
            console.error('Error creating node:', error);

            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Create Node</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Check the nodebuilder service logs for more details.</p>
                </div>
            `;

            nextBtn.textContent = 'Retry';
            nextBtn.disabled = false;
            nextBtn.onclick = () => this.submitNode();

            this.isSubmitting = false;
        }
    }

    /**
     * Log a message to the creation log
     */
    logMessage(logElement, message, type = 'info') {
        const entry = document.createElement('div');
        entry.className = `log-entry log-${type}`;
        entry.textContent = `[${new Date().toLocaleTimeString()}] ${message}`;
        logElement.appendChild(entry);
        logElement.scrollTop = logElement.scrollHeight;
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

    // =========================================
    // User Node Restore Functionality
    // =========================================

    /**
     * Check if there are user nodes that need restoration
     * Called on topology load to show restore notification if needed
     */
    async checkUserNodesStatus() {
        if (!this.isAvailable()) {
            return { has_user_nodes: false, needs_restore: false };
        }

        try {
            const response = await fetch('/td-api/nodes/user-nodes-status');
            if (!response.ok) {
                console.warn('[AddNodeWizard] Failed to check user nodes status');
                return { has_user_nodes: false, needs_restore: false };
            }
            return await response.json();
        } catch (error) {
            console.error('[AddNodeWizard] Error checking user nodes:', error);
            return { has_user_nodes: false, needs_restore: false };
        }
    }

    /**
     * Show restore notification banner if user nodes need restoration
     */
    async showRestoreNotificationIfNeeded() {
        const status = await this.checkUserNodesStatus();

        if (!status.has_user_nodes) {
            this.hideRestoreNotification();
            return;
        }

        // Remove any existing notification
        this.hideRestoreNotification();

        // Create notification banner
        const banner = document.createElement('div');
        banner.id = 'user-nodes-restore-banner';
        banner.className = 'user-nodes-restore-banner';

        if (status.needs_restore) {
            // Nodes need restoration
            const stoppedNodes = status.nodes.filter(n => !n.running);
            banner.innerHTML = `
                <div class="restore-banner-content">
                    <span class="restore-banner-icon">&#9888;</span>
                    <span class="restore-banner-text">
                        <strong>${stoppedNodes.length} user-added node${stoppedNodes.length !== 1 ? 's' : ''}</strong>
                        ${stoppedNodes.length !== 1 ? 'are' : 'is'} not running.
                    </span>
                    <button class="restore-banner-btn" id="restore-user-nodes-btn">
                        Restore User Nodes
                    </button>
                    <button class="restore-banner-dismiss" title="Dismiss">&#10005;</button>
                </div>
            `;
            banner.classList.add('needs-restore');
        } else {
            // All user nodes are running - show success briefly
            banner.innerHTML = `
                <div class="restore-banner-content">
                    <span class="restore-banner-icon success">&#10003;</span>
                    <span class="restore-banner-text">
                        ${status.nodes.length} user-added node${status.nodes.length !== 1 ? 's' : ''} running.
                    </span>
                    <button class="restore-banner-dismiss" title="Dismiss">&#10005;</button>
                </div>
            `;
            banner.classList.add('all-running');
            // Auto-dismiss after 5 seconds
            setTimeout(() => this.hideRestoreNotification(), 5000);
        }

        // Add to topology container
        const topoContainer = document.getElementById('interactive-topology');
        if (topoContainer) {
            topoContainer.parentElement.insertBefore(banner, topoContainer);
        }

        // Set up event handlers
        const restoreBtn = banner.querySelector('#restore-user-nodes-btn');
        if (restoreBtn) {
            restoreBtn.addEventListener('click', () => this.showRestoreDialog());
        }

        const dismissBtn = banner.querySelector('.restore-banner-dismiss');
        if (dismissBtn) {
            dismissBtn.addEventListener('click', () => this.hideRestoreNotification());
        }
    }

    /**
     * Hide the restore notification banner
     */
    hideRestoreNotification() {
        const existing = document.getElementById('user-nodes-restore-banner');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Show the restore dialog with node list and restore button
     */
    async showRestoreDialog() {
        // Get current status
        const status = await this.checkUserNodesStatus();

        if (!status.has_user_nodes) {
            alert('No user-added nodes found.');
            return;
        }

        // Create overlay
        const overlay = document.createElement('div');
        overlay.id = 'restore-nodes-overlay';
        overlay.className = 'add-node-overlay';
        overlay.innerHTML = `
            <div class="add-node-wizard restore-nodes-dialog">
                <div class="wizard-header">
                    <h2>Restore User Nodes</h2>
                    <button class="wizard-close-btn" title="Close">&times;</button>
                </div>
                <div class="wizard-content">
                    <p class="restore-description">
                        The following user-added nodes will be started:
                    </p>
                    <div class="restore-nodes-list">
                        ${status.nodes.map(node => `
                            <div class="restore-node-item ${node.running ? 'running' : 'stopped'}">
                                <span class="node-status-icon">${node.running ? '&#10003;' : '&#9679;'}</span>
                                <span class="node-name">${this.escapeHtml(node.name)}</span>
                                <span class="node-ip">${this.escapeHtml(node.ip)}</span>
                                <span class="node-state">${node.running ? 'Running' : 'Stopped'}</span>
                            </div>
                        `).join('')}
                    </div>
                </div>
                <div class="wizard-footer">
                    <button class="wizard-cancel-btn">Cancel</button>
                    <button class="wizard-next-btn restore-btn" ${!status.needs_restore ? 'disabled' : ''}>
                        ${status.needs_restore ? 'Restore All Nodes' : 'All Nodes Running'}
                    </button>
                </div>
            </div>
        `;

        document.body.appendChild(overlay);

        // Event handlers
        const closeBtn = overlay.querySelector('.wizard-close-btn');
        const cancelBtn = overlay.querySelector('.wizard-cancel-btn');
        const restoreBtn = overlay.querySelector('.restore-btn');

        const closeDialog = () => overlay.remove();

        closeBtn.addEventListener('click', closeDialog);
        cancelBtn.addEventListener('click', closeDialog);
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) closeDialog();
        });

        if (status.needs_restore) {
            restoreBtn.addEventListener('click', () => this.executeRestore(overlay));
        }
    }

    /**
     * Execute the restore operation
     */
    async executeRestore(overlay) {
        const content = overlay.querySelector('.wizard-content');
        const restoreBtn = overlay.querySelector('.restore-btn');
        const cancelBtn = overlay.querySelector('.wizard-cancel-btn');

        restoreBtn.disabled = true;
        restoreBtn.textContent = 'Restoring...';
        cancelBtn.style.display = 'none';

        content.innerHTML = `
            <div class="restore-progress">
                <div class="progress-spinner"></div>
                <p>Starting user-added nodes...</p>
                <div class="restore-log"></div>
            </div>
        `;

        const logElement = content.querySelector('.restore-log');

        try {
            this.logMessage(logElement, 'Sending restore request...', 'info');

            const response = await fetch('/td-api/nodes/restore-user-nodes', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            });

            const result = await response.json();

            if (result.error) {
                throw new Error(result.error);
            }

            // Log results
            if (result.restored && result.restored.length > 0) {
                result.restored.forEach(node => {
                    const status = node.status === 'already_running' ? 'already running' : 'started';
                    this.logMessage(logElement, `${node.name}: ${status}`, 'success');
                });
            }

            if (result.errors && result.errors.length > 0) {
                result.errors.forEach(node => {
                    this.logMessage(logElement, `${node.name}: ${node.error}`, 'error');
                });
            }

            // Update UI to show success
            content.innerHTML = `
                <div class="restore-success">
                    <div class="success-icon">&#10003;</div>
                    <h3>Restore Complete</h3>
                    <p>
                        ${result.restored?.length || 0} node(s) restored successfully.
                        ${result.errors?.length > 0 ? `${result.errors.length} error(s).` : ''}
                    </p>
                </div>
            `;

            restoreBtn.textContent = 'Close';
            restoreBtn.disabled = false;
            restoreBtn.onclick = () => {
                overlay.remove();
                this.hideRestoreNotification();
                // Refresh topology
                if (this.topologyManager) {
                    this.topologyManager.refreshTopology();
                }
            };

        } catch (error) {
            console.error('[AddNodeWizard] Restore failed:', error);

            content.innerHTML = `
                <div class="restore-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Restore Failed</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                </div>
            `;

            restoreBtn.textContent = 'Close';
            restoreBtn.disabled = false;
            restoreBtn.onclick = () => overlay.remove();
        }
    }
}

// Export for use in other modules
window.AddNodeWizard = AddNodeWizard;

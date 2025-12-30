/**
 * Add Host Wizard for ATL Topology
 *
 * Multi-step wizard for dynamically adding Linux desktop VMs to running KVM labs.
 * Linux hosts provide a lightweight Debian LXDE desktop accessible via noVNC.
 *
 * Wizard Flow:
 * 1. Enter hostname (validates uniqueness, shows limit)
 * 2. Select management IP address (from available pool)
 * 3. Configure network connection (optional - select target switch/port)
 * 4. Review and confirm
 *
 * Only available for KVM labs, disabled for container labs.
 *
 * Dependencies:
 * - NodeBuilderAPI (shared API service)
 * - DeviceRebootManager (shared reboot component)
 */

class AddHostWizard {
    // Configuration constants
    static MAX_NAME_LENGTH = 32;
    static NAME_VALIDATION_DEBOUNCE_MS = 300;
    static MAX_HOSTS = 2;

    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;
        this.currentStep = 1;
        this.totalSteps = 4;

        // Wizard state
        this.hostConfig = {
            name: '',
            ip: '',
            data_ip: '',
            connection: null
        };

        // Cached data from API
        this.hostStatus = null;
        this.availableIps = [];
        this.targetDevices = [];

        // Validation state
        this.nameValid = false;
        this.nameError = '';

        this.isSubmitting = false;

        // Event handler references for cleanup
        this.escapeHandler = null;

        // Track validation request for race condition prevention
        this.pendingValidationRequestId = 0;
    }

    /**
     * Check if add-host feature is available (KVM mode only)
     */
    isAvailable() {
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
            console.log('Add Host wizard not available for container labs');
            return;
        }

        // Reset state
        this.currentStep = 1;
        this.hostConfig = {
            name: '',
            ip: '',
            data_ip: '',
            connection: null
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
        if (this.escapeHandler) {
            document.removeEventListener('keydown', this.escapeHandler);
            this.escapeHandler = null;
        }

        if (this.overlay) {
            this.overlay.remove();
            this.overlay = null;
        }
    }

    /**
     * Create the wizard overlay and container
     */
    createOverlay() {
        this.hide();

        const overlay = document.createElement('div');
        overlay.className = 'add-node-wizard-overlay';
        overlay.innerHTML = `
            <div class="add-node-wizard add-host-wizard">
                <div class="wizard-header">
                    <h2>Add Linux Host</h2>
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
                            <span class="step-label">Connection</span>
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

        // Close on overlay click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hide();
            }
        });

        // Close on escape key
        this.escapeHandler = (e) => {
            if (e.key === 'Escape') {
                this.hide();
            }
        };
        document.addEventListener('keydown', this.escapeHandler);

        document.body.appendChild(overlay);
        this.overlay = overlay;
    }

    /**
     * Load available data from API
     */
    async loadAvailableData() {
        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = '<div class="wizard-loading"><div class="spinner"></div><p>Loading available resources...</p></div>';

        try {
            const data = await NodeBuilderAPI.loadHostWizardData();
            this.hostStatus = data.hostStatus;
            this.availableIps = data.availableIps;
            this.targetDevices = data.targetDevices;

        } catch (error) {
            console.error('[AddHostWizard] Error loading wizard data:', error);
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Load Resources</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Make sure the nodebuilder service is running.</p>
                </div>
            `;
            return;
        }

        // Check if we can add more hosts
        if (!this.hostStatus.can_add_more) {
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#9888;</div>
                    <h3>Host Limit Reached</h3>
                    <p>Maximum of ${this.hostStatus.max_allowed} Linux hosts per topology.</p>
                    <p>Delete an existing host to add a new one.</p>
                </div>
            `;
            return;
        }

        // Check if we have any available IPs
        if (this.availableIps.length === 0) {
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#9888;</div>
                    <h3>No Available IP Addresses</h3>
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
            nextBtn.textContent = 'Create Host';
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
                this.renderConnectionStep(content);
                break;
            case 4:
                this.renderReviewStep(content);
                break;
        }

        this.updateNextButtonState();
    }

    /**
     * Step 1: Hostname Entry
     */
    renderNameStep(content) {
        const currentCount = this.hostStatus?.current_count || 0;
        const maxAllowed = this.hostStatus?.max_allowed || AddHostWizard.MAX_HOSTS;

        content.innerHTML = `
            <div class="wizard-step wizard-step-name">
                <h3>Enter Hostname</h3>
                <p class="step-description">Choose a unique name for the Linux desktop host. Names must start with a letter and contain only letters, numbers, dashes, and underscores.</p>

                <div class="host-limit-badge">
                    <span class="limit-count">${currentCount}/${maxAllowed}</span>
                    <span class="limit-label">hosts used</span>
                </div>

                <div class="form-group">
                    <label for="host-name">Hostname</label>
                    <input type="text"
                           id="host-name"
                           class="form-input"
                           placeholder="e.g., desktop1, workstation, client1"
                           value="${this.escapeHtml(this.hostConfig.name)}"
                           maxlength="${AddHostWizard.MAX_NAME_LENGTH}"
                           autocomplete="off"
                           aria-describedby="host-name-validation"
                           aria-invalid="${this.nameError ? 'true' : 'false'}">
                    <div id="host-name-validation"
                         class="validation-message ${this.nameError ? 'error' : this.nameValid ? 'success' : ''}"
                         role="alert"
                         aria-live="polite">
                        ${this.nameError || (this.nameValid ? 'Name is available' : '')}
                    </div>
                </div>

                <div class="host-info-box">
                    <h4>Linux Host Details</h4>
                    <ul>
                        <li>Debian 12 with LXDE desktop</li>
                        <li>Access via noVNC (browser-based desktop)</li>
                        <li>Pre-installed: Firefox, iperf3, tcpdump, mtr</li>
                        <li>Default credentials: arista / arista</li>
                    </ul>
                </div>
            </div>
        `;

        const input = content.querySelector('#host-name');
        input.focus();

        // Validate on input with debounce
        let debounceTimer;
        input.addEventListener('input', (e) => {
            this.hostConfig.name = e.target.value.trim();
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => this.validateName(), AddHostWizard.NAME_VALIDATION_DEBOUNCE_MS);
        });

        // Validate on enter key
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && this.nameValid) {
                this.nextStep();
            }
        });
    }

    /**
     * Validate hostname via API
     */
    async validateName() {
        const name = this.hostConfig.name;
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

        if (name.length > AddHostWizard.MAX_NAME_LENGTH) {
            this.nameValid = false;
            this.nameError = `Name must be ${AddHostWizard.MAX_NAME_LENGTH} characters or less`;
            validationMsg.className = 'validation-message error';
            validationMsg.textContent = this.nameError;
            this.updateNextButtonState();
            return;
        }

        // Server-side validation using shared API
        const expectedRequestId = NodeBuilderAPI.getValidationRequestId() + 1;
        this.pendingValidationRequestId = expectedRequestId;
        this.pendingValidationName = name;

        try {
            const result = await NodeBuilderAPI.validateNode(name);

            // Check for race conditions
            if (result.requestId !== this.pendingValidationRequestId ||
                result.validatedName !== this.pendingValidationName) {
                return;
            }

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
            console.error('[AddHostWizard] Error validating name:', error);
            // Allow proceeding on validation error
            this.nameValid = true;
            this.nameError = '';
            validationMsg.className = 'validation-message';
            validationMsg.textContent = '';
        }

        this.updateNextButtonState();
    }

    /**
     * Step 2: Management IP Address Selection
     */
    renderIpStep(content) {
        const ipOptions = this.availableIps.map(entry => `
            <option value="${this.escapeHtml(entry.ip)}"
                    data-hostname="${this.escapeHtml(entry.hostname || '')}">
                ${this.escapeHtml(entry.ip)}${entry.hostname ? ` (${this.escapeHtml(entry.hostname)})` : ''}
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-ip">
                <h3>Select Management IP</h3>
                <p class="step-description">Choose an available IP address for SSH and noVNC access (eth0 - management interface).</p>

                <div class="form-group">
                    <label for="host-ip">Management IP Address</label>
                    <select id="host-ip" class="form-select">
                        <option value="">Select an IP address...</option>
                        ${ipOptions}
                    </select>
                </div>

                <div class="ip-info">
                    <p><strong>${this.availableIps.length}</strong> IP addresses available</p>
                </div>

                <div class="interface-info-box">
                    <h4>Network Interfaces</h4>
                    <p>The Linux host will have two network interfaces:</p>
                    <ul>
                        <li><strong>eth0 (Management)</strong> - For SSH/noVNC access, uses the IP selected above</li>
                        <li><strong>eth1 (Data)</strong> - Connected to the network topology (optional, configured in next step)</li>
                    </ul>
                </div>
            </div>
        `;

        const select = content.querySelector('#host-ip');

        // Set current value if already selected
        if (this.hostConfig.ip) {
            select.value = this.hostConfig.ip;
        }

        select.addEventListener('change', (e) => {
            this.hostConfig.ip = e.target.value;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 3: Network Connection Configuration
     */
    renderConnectionStep(content) {
        const deviceOptions = this.targetDevices.map(device => `
            <option value="${this.escapeHtml(device.name)}"
                    data-next-port="${this.escapeHtml(device.next_available_port)}">
                ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
            </option>
        `).join('');

        const hasConnection = this.hostConfig.connection !== null;

        content.innerHTML = `
            <div class="wizard-step wizard-step-connection">
                <h3>Configure Network Connection</h3>
                <p class="step-description">Optionally connect the host's data interface (eth1) to a network switch. You can also configure a static IP for this interface.</p>

                <div class="form-group">
                    <label class="checkbox-label">
                        <input type="checkbox" id="enable-connection" ${hasConnection ? 'checked' : ''}>
                        <span>Connect to network topology</span>
                    </label>
                </div>

                <div id="connection-config" class="${hasConnection ? '' : 'hidden'}">
                    <div class="form-group">
                        <label for="target-device">Target Switch</label>
                        <select id="target-device" class="form-select">
                            <option value="">Select a switch...</option>
                            ${deviceOptions}
                        </select>
                    </div>

                    <div class="form-group">
                        <label for="target-port">Target Port</label>
                        <input type="text" id="target-port" class="form-input"
                               placeholder="Auto-selected" readonly>
                    </div>

                    <div class="form-group">
                        <label for="data-ip">Data Interface IP (optional)</label>
                        <input type="text" id="data-ip" class="form-input"
                               placeholder="e.g., 10.1.1.100/24"
                               value="${this.escapeHtml(this.hostConfig.data_ip || '')}">
                        <p class="field-hint">CIDR notation. Leave blank for DHCP or manual config later.</p>
                    </div>
                </div>

                <div class="connection-hint">
                    <strong>Tip:</strong> The data interface (eth1) can be used to connect the host to your network topology for testing traffic flows.
                </div>
            </div>
        `;

        const enableCheckbox = content.querySelector('#enable-connection');
        const configDiv = content.querySelector('#connection-config');
        const deviceSelect = content.querySelector('#target-device');
        const portInput = content.querySelector('#target-port');
        const dataIpInput = content.querySelector('#data-ip');

        // Set initial values if editing
        if (this.hostConfig.connection) {
            deviceSelect.value = this.hostConfig.connection.target_device;
            portInput.value = this.hostConfig.connection.target_port;
        }

        enableCheckbox.addEventListener('change', (e) => {
            if (e.target.checked) {
                configDiv.classList.remove('hidden');
            } else {
                configDiv.classList.add('hidden');
                this.hostConfig.connection = null;
            }
            this.updateNextButtonState();
        });

        deviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';
            portInput.value = nextPort;

            if (e.target.value) {
                this.hostConfig.connection = {
                    target_device: e.target.value,
                    target_port: nextPort
                };
            } else {
                this.hostConfig.connection = null;
            }
            this.updateNextButtonState();
        });

        dataIpInput.addEventListener('input', (e) => {
            this.hostConfig.data_ip = e.target.value.trim();
        });
    }

    /**
     * Step 4: Review and Confirm
     */
    renderReviewStep(content) {
        const connectionHtml = this.hostConfig.connection
            ? `<tr><th>Data Connection</th><td>${this.escapeHtml(this.hostConfig.connection.target_device)} (${this.escapeHtml(this.hostConfig.connection.target_port)})</td></tr>`
            : '<tr><th>Data Connection</th><td>Not connected</td></tr>';

        const dataIpHtml = this.hostConfig.data_ip
            ? `<tr><th>Data Interface IP</th><td>${this.escapeHtml(this.hostConfig.data_ip)}</td></tr>`
            : '';

        content.innerHTML = `
            <div class="wizard-step wizard-step-review">
                <h3>Review Configuration</h3>
                <p class="step-description">Please review the configuration before creating the Linux host.</p>

                <div class="review-section">
                    <h4>Host Information</h4>
                    <table class="review-table">
                        <tr>
                            <th>Hostname</th>
                            <td>${this.escapeHtml(this.hostConfig.name)}</td>
                        </tr>
                        <tr>
                            <th>Management IP</th>
                            <td>${this.escapeHtml(this.hostConfig.ip)}</td>
                        </tr>
                        ${connectionHtml}
                        ${dataIpHtml}
                        <tr>
                            <th>Operating System</th>
                            <td>Debian 12 + LXDE</td>
                        </tr>
                        <tr>
                            <th>Access Method</th>
                            <td>noVNC (browser desktop), SSH</td>
                        </tr>
                    </table>
                </div>

                <div class="review-notes">
                    <h4>What happens next:</h4>
                    <ul>
                        <li>A new Linux VM will be created with Debian 12 LXDE desktop</li>
                        <li>The VM will boot and be accessible via noVNC within ~60 seconds</li>
                        <li>You can access the desktop from the Terminals menu</li>
                        <li>Default login: arista / arista</li>
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
                canProceed = this.nameValid && this.hostConfig.name.length > 0;
                break;
            case 2:
                canProceed = this.hostConfig.ip !== '';
                break;
            case 3:
                // Connection is optional
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
            await this.submitHost();
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
     * Submit the host creation request
     */
    async submitHost() {
        if (this.isSubmitting) return;

        this.isSubmitting = true;
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');
        nextBtn.textContent = 'Creating...';
        nextBtn.disabled = true;

        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = `
            <div class="wizard-creating">
                <div class="spinner large"></div>
                <h3>Creating Linux Host...</h3>
                <p>This may take a minute. Please wait.</p>
                <div class="creation-log"></div>
            </div>
        `;

        const log = content.querySelector('.creation-log');

        try {
            this.logMessage(log, 'Sending request to nodebuilder service...');

            const result = await NodeBuilderAPI.addHost({
                name: this.hostConfig.name,
                ip: this.hostConfig.ip,
                data_ip: this.hostConfig.data_ip || null,
                connection: this.hostConfig.connection
            });

            this.logMessage(log, 'Host created successfully!', 'success');
            this.logMessage(log, `VM: ${result.host?.name || this.hostConfig.name}`);
            this.logMessage(log, `IP: ${result.host?.mgmt_ip || this.hostConfig.ip}`);

            // Use API-provided reboot info (accounts for orphaned slot reuse)
            // targets_need_reboot: devices that need reboot (new interface attached)
            // targets_reused_slots: devices that reused orphaned slots (no reboot needed)
            const rebootTargets = result.targets_need_reboot || [];
            const reusedSlots = result.targets_reused_slots || [];

            // Log slot reuse optimization if applicable
            if (reusedSlots.length > 0) {
                this.logMessage(log, `Optimized: ${reusedSlots.join(', ')} reused existing interface slots (no reboot needed)`, 'success');
            }

            // Store result for later use
            this.createdHost = result.host || { name: this.hostConfig.name, ip: this.hostConfig.ip };

            // Use shared DeviceRebootManager for reboot section
            const rebootManager = new DeviceRebootManager(this.targetDevices);

            // Show success state with reboot options
            content.innerHTML = `
                <div class="wizard-success">
                    <div class="success-icon">&#10004;</div>
                    <h3>Linux Host Created!</h3>
                    <p>The Linux host <strong>${this.escapeHtml(this.hostConfig.name)}</strong> has been created.</p>
                    <p>It will be ready for access via noVNC within ~60 seconds.</p>
                    <div class="success-details">
                        <p>Management IP: <code>${this.escapeHtml(this.hostConfig.ip)}</code></p>
                        <p>Access: Terminals menu > ${this.escapeHtml(this.hostConfig.name)}</p>
                    </div>

                    ${rebootTargets.length > 0 ? rebootManager.renderRebootSection(rebootTargets) : ''}
                </div>
            `;

            // Attach reboot handlers if there are targets
            if (rebootTargets.length > 0) {
                rebootManager.attachEventHandlers(content);
            }

            // Update button to close
            nextBtn.textContent = 'Close';
            nextBtn.disabled = false;
            nextBtn.onclick = () => {
                this.hide();
                if (this.topologyManager) {
                    this.topologyManager.refreshTopology();
                }
            };

            // Hide back and cancel buttons
            this.overlay.querySelector('.wizard-back-btn').style.display = 'none';
            this.overlay.querySelector('.wizard-cancel-btn').style.display = 'none';

        } catch (error) {
            console.error('[AddHostWizard] Error creating host:', error);

            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Create Host</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Check the nodebuilder service logs for more details.</p>
                </div>
            `;

            nextBtn.textContent = 'Retry';
            nextBtn.disabled = false;
            nextBtn.onclick = () => this.submitHost();

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
}

// Export for use in other modules
window.AddHostWizard = AddHostWizard;

/**
 * Add Firewall Wizard for ATL Topology
 *
 * Multi-step wizard for dynamically adding VyOS firewall VMs to running KVM labs.
 * VyOS firewalls provide routing, NAT, and firewall capabilities.
 *
 * Wizard Flow:
 * 1. Enter hostname (validates uniqueness, shows limit)
 * 2. Select management IP address (from available pool)
 * 3. Configure inside interface (select target switch/port)
 * 4. Configure outside interface (select target switch/port)
 * 5. Review and confirm
 *
 * Note: Interface IPs are configured manually in VyOS after boot.
 * Only available for KVM labs, disabled for container labs.
 *
 * Dependencies:
 * - NodeBuilderAPI (shared API service)
 * - DeviceRebootManager (shared reboot component)
 */

class AddFirewallWizard {
    // Configuration constants
    static MAX_NAME_LENGTH = 32;
    static NAME_VALIDATION_DEBOUNCE_MS = 300;
    static MAX_FIREWALLS = 1;

    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;
        this.currentStep = 1;
        this.totalSteps = 4;  // Reduced: no name entry step

        // Fixed firewall name (only 1 firewall allowed per topology)
        this.FIXED_NAME = 'fw1';

        // Wizard state
        this.firewallConfig = {
            name: 'fw1',  // Fixed name
            mgmt_ip: '',
            inside_interface: {
                target_device: '',
                target_port: ''
            },
            outside_interface: {
                target_device: '',
                target_port: ''
            }
        };

        // Cached data from API
        this.firewallStatus = null;
        this.availableIps = [];
        this.targetDevices = [];

        this.isSubmitting = false;

        // Event handler references for cleanup
        this.escapeHandler = null;
    }

    /**
     * Check if add-firewall feature is available (KVM mode only)
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
            console.log('Add Firewall wizard not available for container labs');
            return;
        }

        // Pre-check: verify slots are available BEFORE showing wizard
        // This gives immediate feedback without wasting user time
        try {
            const firewallStatus = await NodeBuilderAPI.fetchWithRetry('/td-api/firewalls/status');
            if (!firewallStatus.can_add_more) {
                this.showSlotError('Firewall Limit Reached',
                    `Maximum of ${firewallStatus.max_allowed} firewall per topology.`,
                    'Delete the existing firewall to add a new one.');
                return;
            }
        } catch (error) {
            console.error('[AddFirewallWizard] Error checking firewall slots:', error);
            this.showSlotError('Service Unavailable',
                'Unable to check firewall availability.',
                'Make sure the nodebuilder service is running.');
            return;
        }

        // Reset state
        this.currentStep = 1;
        this.firewallConfig = {
            name: this.FIXED_NAME,  // Fixed name
            mgmt_ip: '',
            inside_interface: {
                target_device: '',
                target_port: ''
            },
            outside_interface: {
                target_device: '',
                target_port: ''
            }
        };
        this.isSubmitting = false;

        // Create overlay
        this.createOverlay();

        // Load data from API
        await this.loadAvailableData();

        // Render first step
        this.renderStep();
    }

    /**
     * Show a slot/limit error without opening the full wizard
     */
    showSlotError(title, message, hint) {
        // Create a simple error modal
        const overlay = document.createElement('div');
        overlay.className = 'add-node-wizard-overlay';
        overlay.innerHTML = `
            <div class="add-node-wizard" style="max-width: 450px;">
                <div class="wizard-header">
                    <h2>${this.escapeHtml(title)}</h2>
                    <button class="wizard-close-btn" title="Close">&times;</button>
                </div>
                <div class="wizard-content">
                    <div class="wizard-error">
                        <div class="error-icon">&#9888;</div>
                        <h3>${this.escapeHtml(title)}</h3>
                        <p>${this.escapeHtml(message)}</p>
                        <p class="error-hint">${this.escapeHtml(hint)}</p>
                    </div>
                </div>
                <div class="wizard-footer">
                    <div class="wizard-footer-spacer"></div>
                    <button class="wizard-btn wizard-btn-primary wizard-close-error-btn">OK</button>
                </div>
            </div>
        `;

        overlay.querySelector('.wizard-close-btn').addEventListener('click', () => overlay.remove());
        overlay.querySelector('.wizard-close-error-btn').addEventListener('click', () => overlay.remove());
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) overlay.remove();
        });

        document.body.appendChild(overlay);
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
            <div class="add-node-wizard add-firewall-wizard">
                <div class="wizard-header">
                    <h2>Add VyOS Firewall</h2>
                    <button class="wizard-close-btn" title="Close">&times;</button>
                </div>
                <div class="wizard-progress">
                    <div class="progress-steps">
                        <div class="progress-step active" data-step="1">
                            <span class="step-number">1</span>
                            <span class="step-label">Mgmt IP</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="2">
                            <span class="step-number">2</span>
                            <span class="step-label">Inside</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="3">
                            <span class="step-number">3</span>
                            <span class="step-label">Outside</span>
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
            // Clear cache before loading to ensure fresh data
            NodeBuilderAPI.invalidateCache('available-ips');
            NodeBuilderAPI.invalidateCache('target-devices');

            const data = await NodeBuilderAPI.loadFirewallWizardData();

            // Validate data structure
            if (!data || typeof data !== 'object') {
                throw new Error('Invalid response from server: expected data object');
            }

            this.firewallStatus = data.firewallStatus || { current_count: 0, max_allowed: 1, can_add_more: true };
            this.availableIps = Array.isArray(data.availableIps) ? data.availableIps : [];
            this.targetDevices = Array.isArray(data.targetDevices) ? data.targetDevices : [];

        } catch (error) {
            console.error('[AddFirewallWizard] Error loading wizard data:', error);
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Load Resources</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Make sure the nodebuilder service is running.</p>
                    <button class="wizard-btn wizard-btn-primary wizard-retry-btn">Retry</button>
                </div>
            `;
            // Add retry handler
            const retryBtn = content.querySelector('.wizard-retry-btn');
            if (retryBtn) {
                retryBtn.addEventListener('click', () => this.loadAvailableData());
            }
            return;
        }

        // Check if we can add more firewalls
        if (!this.firewallStatus.can_add_more) {
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#9888;</div>
                    <h3>Firewall Limit Reached</h3>
                    <p>Maximum of ${this.firewallStatus.max_allowed} VyOS firewall per topology.</p>
                    <p>Delete the existing firewall to add a new one.</p>
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
            nextBtn.textContent = 'Create Firewall';
            nextBtn.classList.add('wizard-btn-create');
        } else {
            nextBtn.textContent = 'Next';
            nextBtn.classList.remove('wizard-btn-create');
        }

        // Render step content (name is fixed, so step 1 is now Mgmt IP)
        switch (this.currentStep) {
            case 1:
                this.renderMgmtIpStep(content);
                break;
            case 2:
                this.renderInsideInterfaceStep(content);
                break;
            case 3:
                this.renderOutsideInterfaceStep(content);
                break;
            case 4:
                this.renderReviewStep(content);
                break;
        }

        this.updateNextButtonState();
    }

    /**
     * Step 1: Management IP Address Selection
     */
    renderMgmtIpStep(content) {
        const ipOptions = this.availableIps.map(entry => `
            <option value="${this.escapeHtml(entry.ip)}"
                    data-hostname="${this.escapeHtml(entry.hostname || '')}">
                ${this.escapeHtml(entry.ip)}${entry.hostname ? ` (${this.escapeHtml(entry.hostname)})` : ''}
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-ip">
                <h3>Select Management IP</h3>
                <p class="step-description">Choose an available IP address for SSH management access (eth0 - management interface).</p>

                <div class="form-group">
                    <label for="firewall-mgmt-ip">Management IP Address</label>
                    <select id="firewall-mgmt-ip" class="form-select">
                        <option value="">Select an IP address...</option>
                        ${ipOptions}
                    </select>
                </div>

                <div class="ip-info">
                    <p><strong>${this.availableIps.length}</strong> IP addresses available</p>
                </div>

                <div class="interface-info-box">
                    <h4>VyOS Network Interfaces</h4>
                    <p>The firewall will have three network interfaces:</p>
                    <ul>
                        <li><strong>eth0 (Management)</strong> - For SSH access, uses the IP selected above</li>
                        <li><strong>eth1 (Inside)</strong> - Connected to internal/trusted network</li>
                        <li><strong>eth2 (Outside)</strong> - Connected to external/untrusted network</li>
                    </ul>
                </div>
            </div>
        `;

        const select = content.querySelector('#firewall-mgmt-ip');

        // Set current value if already selected
        if (this.firewallConfig.mgmt_ip) {
            select.value = this.firewallConfig.mgmt_ip;
        }

        select.addEventListener('change', (e) => {
            this.firewallConfig.mgmt_ip = e.target.value;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 3: Inside Interface Configuration
     */
    renderInsideInterfaceStep(content) {
        const deviceOptions = this.targetDevices.map(device => `
            <option value="${this.escapeHtml(device.name)}"
                    data-next-port="${this.escapeHtml(device.next_available_port)}">
                ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-interface">
                <h3>Configure Inside Interface</h3>
                <p class="step-description">Select which switch to connect eth1 (inside/trusted interface) to.</p>

                <div class="form-group">
                    <label for="inside-device">Target Switch</label>
                    <select id="inside-device" class="form-select">
                        <option value="">Select a switch...</option>
                        ${deviceOptions}
                    </select>
                </div>

                <div class="form-group">
                    <label for="inside-port">Target Port</label>
                    <input type="text" id="inside-port" class="form-input"
                           placeholder="Auto-selected" readonly
                           value="${this.escapeHtml(this.firewallConfig.inside_interface.target_port)}">
                </div>

                <div class="interface-diagram">
                    <div class="diagram-box inside">
                        <span class="diagram-label">Inside Network</span>
                    </div>
                    <div class="diagram-arrow">&rarr;</div>
                    <div class="diagram-box firewall">
                        <span class="diagram-label">VyOS eth1</span>
                    </div>
                </div>

                <div class="config-note">
                    <strong>Note:</strong> After boot, configure the interface IP in VyOS:
                    <code>set interfaces ethernet eth1 address 10.x.x.x/24</code>
                </div>
            </div>
        `;

        const deviceSelect = content.querySelector('#inside-device');
        const portInput = content.querySelector('#inside-port');

        // Set initial values
        if (this.firewallConfig.inside_interface.target_device) {
            deviceSelect.value = this.firewallConfig.inside_interface.target_device;
        }

        deviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';
            portInput.value = nextPort;

            this.firewallConfig.inside_interface.target_device = e.target.value;
            this.firewallConfig.inside_interface.target_port = nextPort;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 4: Outside Interface Configuration
     */
    renderOutsideInterfaceStep(content) {
        // Allow same device for inside and outside (different ports)
        const insideDevice = this.firewallConfig.inside_interface.target_device;
        const insidePort = this.firewallConfig.inside_interface.target_port;

        // Calculate the next available port for each device
        // If device is already used for inside interface, calculate port after that
        const deviceOptions = this.targetDevices.map(device => {
            let nextPort = device.next_available_port;
            let portNote = `next: ${device.next_available_port}`;

            // If this device is used for inside interface, calculate the NEXT port after inside
            if (device.name === insideDevice && insidePort) {
                const insidePortNum = parseInt(insidePort.replace(/\D/g, ''), 10);
                if (!isNaN(insidePortNum)) {
                    nextPort = `Ethernet${insidePortNum + 1}`;
                    portNote = `next: ${nextPort} (after inside)`;
                }
            }

            return `
                <option value="${this.escapeHtml(device.name)}"
                        data-next-port="${this.escapeHtml(nextPort)}"
                        data-is-inside-device="${device.name === insideDevice}">
                    ${this.escapeHtml(device.name)} (${portNote})
                </option>
            `;
        }).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-interface">
                <h3>Configure Outside Interface</h3>
                <p class="step-description">Select which switch to connect eth2 (outside/untrusted interface) to.</p>

                <div class="form-group">
                    <label for="outside-device">Target Switch</label>
                    <select id="outside-device" class="form-select">
                        <option value="">Select a switch...</option>
                        ${deviceOptions}
                    </select>
                    <p class="field-hint" id="outside-device-hint">You can use the same device as inside interface (different ports will be used)</p>
                </div>

                <div class="form-group">
                    <label for="outside-port">Target Port</label>
                    <input type="text" id="outside-port" class="form-input"
                           placeholder="Auto-selected" readonly
                           value="${this.escapeHtml(this.firewallConfig.outside_interface.target_port)}">
                </div>

                <div class="interface-diagram">
                    <div class="diagram-box firewall">
                        <span class="diagram-label">VyOS eth2</span>
                    </div>
                    <div class="diagram-arrow">&rarr;</div>
                    <div class="diagram-box outside">
                        <span class="diagram-label">Outside Network</span>
                    </div>
                </div>

                <div class="config-note">
                    <strong>Note:</strong> After boot, configure the interface IP in VyOS:
                    <code>set interfaces ethernet eth2 address 10.x.x.x/24</code>
                </div>
            </div>
        `;

        const deviceSelect = content.querySelector('#outside-device');
        const portInput = content.querySelector('#outside-port');

        // Set initial values
        if (this.firewallConfig.outside_interface.target_device) {
            deviceSelect.value = this.firewallConfig.outside_interface.target_device;
        }

        deviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';
            portInput.value = nextPort;

            this.firewallConfig.outside_interface.target_device = e.target.value;
            this.firewallConfig.outside_interface.target_port = nextPort;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 5: Review and Confirm
     */
    renderReviewStep(content) {
        content.innerHTML = `
            <div class="wizard-step wizard-step-review">
                <h3>Review Configuration</h3>
                <p class="step-description">Please review the configuration before creating the VyOS firewall.</p>

                <div class="review-section">
                    <h4>Firewall Information</h4>
                    <table class="review-table">
                        <tr>
                            <th>Firewall Name</th>
                            <td>${this.escapeHtml(this.firewallConfig.name)}</td>
                        </tr>
                        <tr>
                            <th>Management IP</th>
                            <td>${this.escapeHtml(this.firewallConfig.mgmt_ip)}</td>
                        </tr>
                        <tr>
                            <th>Operating System</th>
                            <td>VyOS 1.4 Community</td>
                        </tr>
                    </table>
                </div>

                <div class="review-section">
                    <h4>Inside Interface (eth1)</h4>
                    <table class="review-table">
                        <tr>
                            <th>Connected To</th>
                            <td>${this.escapeHtml(this.firewallConfig.inside_interface.target_device)} (${this.escapeHtml(this.firewallConfig.inside_interface.target_port)})</td>
                        </tr>
                        <tr>
                            <th>IP Address</th>
                            <td><em>Configure in VyOS after boot</em></td>
                        </tr>
                    </table>
                </div>

                <div class="review-section">
                    <h4>Outside Interface (eth2)</h4>
                    <table class="review-table">
                        <tr>
                            <th>Connected To</th>
                            <td>${this.escapeHtml(this.firewallConfig.outside_interface.target_device)} (${this.escapeHtml(this.firewallConfig.outside_interface.target_port)})</td>
                        </tr>
                        <tr>
                            <th>IP Address</th>
                            <td><em>Configure in VyOS after boot</em></td>
                        </tr>
                    </table>
                </div>

                <div class="review-notes">
                    <h4>What happens next:</h4>
                    <ul>
                        <li>A new VyOS VM will be created and connected to the selected switches</li>
                        <li>The firewall will boot and be accessible via SSH within ~90 seconds</li>
                        <li>Configure interface IPs and firewall rules after login</li>
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

        // Step mapping (name is fixed): 1=MgmtIP, 2=Inside, 3=Outside, 4=Review
        switch (this.currentStep) {
            case 1:
                canProceed = this.firewallConfig.mgmt_ip !== '';
                break;
            case 2:
                // Only require target device selection (IP configured manually after boot)
                canProceed = this.firewallConfig.inside_interface.target_device !== '';
                break;
            case 3:
                // Require target device selection and validate not same port as inside
                const outsideDevice = this.firewallConfig.outside_interface.target_device;
                const outsidePort = this.firewallConfig.outside_interface.target_port;
                const insideDevice = this.firewallConfig.inside_interface.target_device;
                const insidePort = this.firewallConfig.inside_interface.target_port;

                canProceed = outsideDevice !== '';

                // Validate that same device + same port isn't used for both interfaces
                if (outsideDevice === insideDevice && outsidePort === insidePort && outsidePort !== '') {
                    canProceed = false;
                    // Show error hint if available
                    const hintEl = this.overlay?.querySelector('#outside-device-hint');
                    if (hintEl) {
                        hintEl.textContent = 'Error: Inside and outside interfaces cannot use the same port on the same device';
                        hintEl.classList.add('error-hint');
                    }
                } else {
                    // Clear error hint
                    const hintEl = this.overlay?.querySelector('#outside-device-hint');
                    if (hintEl) {
                        hintEl.textContent = 'You can use the same device as inside interface (different ports will be used)';
                        hintEl.classList.remove('error-hint');
                    }
                }
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
            await this.submitFirewall();
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
     * Submit the firewall creation request
     */
    async submitFirewall() {
        if (this.isSubmitting) return;

        this.isSubmitting = true;
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');
        nextBtn.textContent = 'Creating...';
        nextBtn.disabled = true;

        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = `
            <div class="wizard-creating">
                <div class="spinner large"></div>
                <h3>Creating VyOS Firewall...</h3>
                <p>This may take up to 90 seconds. Please wait.</p>
                <div class="creation-log"></div>
            </div>
        `;

        const log = content.querySelector('.creation-log');

        try {
            this.logMessage(log, 'Sending request to nodebuilder service...');

            const result = await NodeBuilderAPI.addFirewall({
                name: this.firewallConfig.name,
                mgmt_ip: this.firewallConfig.mgmt_ip,
                inside_interface: this.firewallConfig.inside_interface,
                outside_interface: this.firewallConfig.outside_interface
            });

            this.logMessage(log, 'Firewall created successfully!', 'success');
            this.logMessage(log, `VM: ${result.firewall?.name || this.firewallConfig.name}`);
            this.logMessage(log, `Mgmt IP: ${result.firewall?.mgmt_ip || this.firewallConfig.mgmt_ip}`);

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
            this.createdFirewall = result.firewall || { name: this.firewallConfig.name, mgmt_ip: this.firewallConfig.mgmt_ip };

            // Use shared DeviceRebootManager for reboot section
            const rebootManager = new DeviceRebootManager(this.targetDevices);

            // Show success state with reboot options
            content.innerHTML = `
                <div class="wizard-success">
                    <div class="success-icon">&#10004;</div>
                    <h3>VyOS Firewall Created!</h3>
                    <p>The firewall <strong>${this.escapeHtml(this.firewallConfig.name)}</strong> has been created.</p>
                    <p>It will be ready for SSH access within ~90 seconds.</p>
                    <div class="success-details">
                        <p>Management IP: <code>${this.escapeHtml(this.firewallConfig.mgmt_ip)}</code></p>
                        <p>eth1 (inside): connected to ${this.escapeHtml(this.firewallConfig.inside_interface.target_device)}</p>
                        <p>eth2 (outside): connected to ${this.escapeHtml(this.firewallConfig.outside_interface.target_device)}</p>
                    </div>
                    <div class="config-note" style="margin-top: 1rem;">
                        <strong>Next step:</strong> Configure interface IPs in VyOS after boot.
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
            console.error('[AddFirewallWizard] Error creating firewall:', error);

            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Create Firewall</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Check the nodebuilder service logs for more details.</p>
                </div>
            `;

            nextBtn.textContent = 'Retry';
            nextBtn.disabled = false;
            nextBtn.onclick = () => this.submitFirewall();

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
window.AddFirewallWizard = AddFirewallWizard;

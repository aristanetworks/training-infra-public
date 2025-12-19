/**
 * Add Firewall Wizard for ATL Topology
 *
 * Multi-step wizard for dynamically adding VyOS firewall VMs to running KVM labs.
 * VyOS firewalls provide routing, NAT, and firewall capabilities.
 *
 * Wizard Flow:
 * 1. Enter hostname (validates uniqueness, shows limit)
 * 2. Select management IP address (from available pool)
 * 3. Configure inside interface (IP in CIDR, target switch/port)
 * 4. Configure outside interface (IP in CIDR, target switch/port)
 * 5. Review and confirm
 *
 * Only available for KVM labs, disabled for container labs.
 *
 * Dependencies:
 * - NodeBuilderAPI (shared API service)
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
        this.totalSteps = 5;

        // Wizard state
        this.firewallConfig = {
            name: '',
            mgmt_ip: '',
            inside_interface: {
                ip: '',
                target_device: '',
                target_port: ''
            },
            outside_interface: {
                ip: '',
                target_device: '',
                target_port: ''
            }
        };

        // Cached data from API
        this.firewallStatus = null;
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

        // Reset state
        this.currentStep = 1;
        this.firewallConfig = {
            name: '',
            mgmt_ip: '',
            inside_interface: {
                ip: '',
                target_device: '',
                target_port: ''
            },
            outside_interface: {
                ip: '',
                target_device: '',
                target_port: ''
            }
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
            <div class="add-node-wizard add-firewall-wizard">
                <div class="wizard-header">
                    <h2>Add VyOS Firewall</h2>
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
                            <span class="step-label">Mgmt IP</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="3">
                            <span class="step-number">3</span>
                            <span class="step-label">Inside</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="4">
                            <span class="step-number">4</span>
                            <span class="step-label">Outside</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="5">
                            <span class="step-number">5</span>
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
            const data = await NodeBuilderAPI.loadFirewallWizardData();
            this.firewallStatus = data.firewallStatus;
            this.availableIps = data.availableIps;
            this.targetDevices = data.targetDevices;

        } catch (error) {
            console.error('[AddFirewallWizard] Error loading wizard data:', error);
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

        // Render step content
        switch (this.currentStep) {
            case 1:
                this.renderNameStep(content);
                break;
            case 2:
                this.renderMgmtIpStep(content);
                break;
            case 3:
                this.renderInsideInterfaceStep(content);
                break;
            case 4:
                this.renderOutsideInterfaceStep(content);
                break;
            case 5:
                this.renderReviewStep(content);
                break;
        }

        this.updateNextButtonState();
    }

    /**
     * Step 1: Firewall Name Entry
     */
    renderNameStep(content) {
        const currentCount = this.firewallStatus?.current_count || 0;
        const maxAllowed = this.firewallStatus?.max_allowed || AddFirewallWizard.MAX_FIREWALLS;

        content.innerHTML = `
            <div class="wizard-step wizard-step-name">
                <h3>Enter Firewall Name</h3>
                <p class="step-description">Choose a unique name for the VyOS firewall. Names must start with a letter and contain only letters, numbers, dashes, and underscores.</p>

                <div class="host-limit-badge firewall-limit-badge">
                    <span class="limit-count">${currentCount}/${maxAllowed}</span>
                    <span class="limit-label">firewall used</span>
                </div>

                <div class="form-group">
                    <label for="firewall-name">Firewall Name</label>
                    <input type="text"
                           id="firewall-name"
                           class="form-input"
                           placeholder="e.g., fw1, firewall, edge-fw"
                           value="${this.escapeHtml(this.firewallConfig.name)}"
                           maxlength="${AddFirewallWizard.MAX_NAME_LENGTH}"
                           autocomplete="off"
                           aria-describedby="firewall-name-validation"
                           aria-invalid="${this.nameError ? 'true' : 'false'}">
                    <div id="firewall-name-validation"
                         class="validation-message ${this.nameError ? 'error' : this.nameValid ? 'success' : ''}"
                         role="alert"
                         aria-live="polite">
                        ${this.nameError || (this.nameValid ? 'Name is available' : '')}
                    </div>
                </div>

                <div class="host-info-box firewall-info-box">
                    <h4>VyOS Firewall Details</h4>
                    <ul>
                        <li>VyOS 1.4 Community Edition</li>
                        <li>3 interfaces: Management, Inside, Outside</li>
                        <li>Full routing, NAT, and firewall capabilities</li>
                        <li>Access via SSH or serial console</li>
                        <li>Default credentials: arista / arista</li>
                    </ul>
                </div>
            </div>
        `;

        const input = content.querySelector('#firewall-name');
        input.focus();

        // Validate on input with debounce
        let debounceTimer;
        input.addEventListener('input', (e) => {
            this.firewallConfig.name = e.target.value.trim();
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => this.validateName(), AddFirewallWizard.NAME_VALIDATION_DEBOUNCE_MS);
        });

        // Validate on enter key
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && this.nameValid) {
                this.nextStep();
            }
        });
    }

    /**
     * Validate firewall name via API
     */
    async validateName() {
        const name = this.firewallConfig.name;
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

        if (name.length > AddFirewallWizard.MAX_NAME_LENGTH) {
            this.nameValid = false;
            this.nameError = `Name must be ${AddFirewallWizard.MAX_NAME_LENGTH} characters or less`;
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
            console.error('[AddFirewallWizard] Error validating name:', error);
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
                <p class="step-description">Configure eth1, the inside/trusted interface. This typically connects to your internal network.</p>

                <div class="form-group">
                    <label for="inside-ip">Inside Interface IP (CIDR)</label>
                    <input type="text" id="inside-ip" class="form-input"
                           placeholder="e.g., 10.1.1.1/24"
                           value="${this.escapeHtml(this.firewallConfig.inside_interface.ip)}">
                    <p class="field-hint">Use CIDR notation (e.g., 10.1.1.1/24)</p>
                    <div id="inside-ip-validation" class="validation-message"></div>
                </div>

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
                        <span class="diagram-ip">${this.escapeHtml(this.firewallConfig.inside_interface.ip) || 'Configure IP'}</span>
                    </div>
                    <div class="diagram-arrow">&rarr;</div>
                    <div class="diagram-box firewall">
                        <span class="diagram-label">VyOS eth1</span>
                    </div>
                </div>
            </div>
        `;

        const ipInput = content.querySelector('#inside-ip');
        const deviceSelect = content.querySelector('#inside-device');
        const portInput = content.querySelector('#inside-port');
        const validationMsg = content.querySelector('#inside-ip-validation');

        // Set initial values
        if (this.firewallConfig.inside_interface.target_device) {
            deviceSelect.value = this.firewallConfig.inside_interface.target_device;
        }

        ipInput.addEventListener('input', (e) => {
            this.firewallConfig.inside_interface.ip = e.target.value.trim();
            this.validateCidrIp(e.target.value.trim(), validationMsg);
            this.updateNextButtonState();
        });

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
        // Filter out device already used for inside interface
        const insideDevice = this.firewallConfig.inside_interface.target_device;
        const availableDevices = this.targetDevices.filter(d => d.name !== insideDevice);

        const deviceOptions = availableDevices.map(device => `
            <option value="${this.escapeHtml(device.name)}"
                    data-next-port="${this.escapeHtml(device.next_available_port)}">
                ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step wizard-step-interface">
                <h3>Configure Outside Interface</h3>
                <p class="step-description">Configure eth2, the outside/untrusted interface. This typically connects to your external network or internet simulation.</p>

                <div class="form-group">
                    <label for="outside-ip">Outside Interface IP (CIDR)</label>
                    <input type="text" id="outside-ip" class="form-input"
                           placeholder="e.g., 10.2.2.1/24"
                           value="${this.escapeHtml(this.firewallConfig.outside_interface.ip)}">
                    <p class="field-hint">Use CIDR notation (e.g., 10.2.2.1/24)</p>
                    <div id="outside-ip-validation" class="validation-message"></div>
                </div>

                <div class="form-group">
                    <label for="outside-device">Target Switch</label>
                    <select id="outside-device" class="form-select">
                        <option value="">Select a switch...</option>
                        ${deviceOptions}
                    </select>
                    ${insideDevice ? `<p class="field-hint">${this.escapeHtml(insideDevice)} is already used for inside interface</p>` : ''}
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
                        <span class="diagram-ip">${this.escapeHtml(this.firewallConfig.outside_interface.ip) || 'Configure IP'}</span>
                    </div>
                </div>
            </div>
        `;

        const ipInput = content.querySelector('#outside-ip');
        const deviceSelect = content.querySelector('#outside-device');
        const portInput = content.querySelector('#outside-port');
        const validationMsg = content.querySelector('#outside-ip-validation');

        // Set initial values
        if (this.firewallConfig.outside_interface.target_device) {
            deviceSelect.value = this.firewallConfig.outside_interface.target_device;
        }

        ipInput.addEventListener('input', (e) => {
            this.firewallConfig.outside_interface.ip = e.target.value.trim();
            this.validateCidrIp(e.target.value.trim(), validationMsg);
            this.updateNextButtonState();
        });

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
     * Validate CIDR IP format
     */
    validateCidrIp(value, validationEl) {
        if (!value) {
            validationEl.className = 'validation-message';
            validationEl.textContent = '';
            return false;
        }

        // Basic CIDR validation regex
        const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;
        if (!cidrRegex.test(value)) {
            validationEl.className = 'validation-message error';
            validationEl.textContent = 'Invalid format. Use CIDR notation (e.g., 10.1.1.1/24)';
            return false;
        }

        // Validate IP octets and prefix length
        const [ip, prefix] = value.split('/');
        const octets = ip.split('.').map(Number);
        const prefixLen = parseInt(prefix, 10);

        if (octets.some(o => o < 0 || o > 255)) {
            validationEl.className = 'validation-message error';
            validationEl.textContent = 'Invalid IP address octets';
            return false;
        }

        if (prefixLen < 1 || prefixLen > 32) {
            validationEl.className = 'validation-message error';
            validationEl.textContent = 'Prefix length must be between 1 and 32';
            return false;
        }

        validationEl.className = 'validation-message success';
        validationEl.textContent = 'Valid';
        return true;
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
                            <th>IP Address</th>
                            <td>${this.escapeHtml(this.firewallConfig.inside_interface.ip)}</td>
                        </tr>
                        <tr>
                            <th>Connected To</th>
                            <td>${this.escapeHtml(this.firewallConfig.inside_interface.target_device)} (${this.escapeHtml(this.firewallConfig.inside_interface.target_port)})</td>
                        </tr>
                    </table>
                </div>

                <div class="review-section">
                    <h4>Outside Interface (eth2)</h4>
                    <table class="review-table">
                        <tr>
                            <th>IP Address</th>
                            <td>${this.escapeHtml(this.firewallConfig.outside_interface.ip)}</td>
                        </tr>
                        <tr>
                            <th>Connected To</th>
                            <td>${this.escapeHtml(this.firewallConfig.outside_interface.target_device)} (${this.escapeHtml(this.firewallConfig.outside_interface.target_port)})</td>
                        </tr>
                    </table>
                </div>

                <div class="review-notes">
                    <h4>What happens next:</h4>
                    <ul>
                        <li>A new VyOS VM will be created with the specified configuration</li>
                        <li>The firewall will boot and be accessible via SSH within ~90 seconds</li>
                        <li>No firewall rules are configured by default - you configure policies</li>
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
                canProceed = this.nameValid && this.firewallConfig.name.length > 0;
                break;
            case 2:
                canProceed = this.firewallConfig.mgmt_ip !== '';
                break;
            case 3:
                canProceed = this.firewallConfig.inside_interface.ip !== '' &&
                            this.firewallConfig.inside_interface.target_device !== '' &&
                            this.isValidCidr(this.firewallConfig.inside_interface.ip);
                break;
            case 4:
                canProceed = this.firewallConfig.outside_interface.ip !== '' &&
                            this.firewallConfig.outside_interface.target_device !== '' &&
                            this.isValidCidr(this.firewallConfig.outside_interface.ip);
                break;
            case 5:
                canProceed = !this.isSubmitting;
                break;
        }

        nextBtn.disabled = !canProceed || this.isSubmitting;
    }

    /**
     * Check if a string is valid CIDR notation
     */
    isValidCidr(value) {
        if (!value) return false;
        const cidrRegex = /^(\d{1,3}\.){3}\d{1,3}\/\d{1,2}$/;
        if (!cidrRegex.test(value)) return false;

        const [ip, prefix] = value.split('/');
        const octets = ip.split('.').map(Number);
        const prefixLen = parseInt(prefix, 10);

        return !octets.some(o => o < 0 || o > 255) && prefixLen >= 1 && prefixLen <= 32;
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

            // Show success state
            content.innerHTML = `
                <div class="wizard-success">
                    <div class="success-icon">&#10004;</div>
                    <h3>VyOS Firewall Created!</h3>
                    <p>The firewall <strong>${this.escapeHtml(this.firewallConfig.name)}</strong> has been created.</p>
                    <p>It will be ready for SSH access within ~90 seconds.</p>
                    <div class="success-details">
                        <p>Management IP: <code>${this.escapeHtml(this.firewallConfig.mgmt_ip)}</code></p>
                        <p>Inside: <code>${this.escapeHtml(this.firewallConfig.inside_interface.ip)}</code></p>
                        <p>Outside: <code>${this.escapeHtml(this.firewallConfig.outside_interface.ip)}</code></p>
                    </div>
                </div>
            `;

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

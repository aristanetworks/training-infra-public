/**
 * Add VeloCloud Wizard for ATL Topology
 *
 * Multi-step wizard for dynamically adding VeloCloud SD-WAN devices to running KVM labs.
 * Supports three device types:
 * - Edge: SD-WAN appliance with WAN1-3 and LAN interfaces
 * - Gateway: Data plane hub with Transport1-2 interfaces
 * - Orchestrator: Management plane with single data interface
 *
 * Wizard Flow:
 * 1. Select device type (Edge, Gateway, Orchestrator)
 * 2. Enter hostname (validates uniqueness, shows limits)
 * 3. Select management IP address
 * 4. Configure interfaces (varies by device type)
 * 5. Review and confirm
 *
 * Only available for KVM labs with VeloCloud feature enabled.
 *
 * Dependencies:
 * - NodeBuilderAPI (shared API service)
 * - DeviceRebootManager (shared reboot component)
 */

class AddVelocloudWizard {
    // Configuration constants
    static MAX_NAME_LENGTH = 32;
    static NAME_VALIDATION_DEBOUNCE_MS = 300;

    // Interface name mappings (frontend -> backend)
    // Gateway: eth0/eth1 in UI map to transport1/transport2 in VM
    static GATEWAY_INTERFACE_MAP = {
        'eth0': 'transport1',
        'eth1': 'transport2'
    };

    // Edge: GE1-GE8 in UI map to wan1-3/lan in VM
    // GE1-GE2 are LAN ports, GE3-GE5 are WAN ports (wan1-3), GE6-GE8 fall back to lan
    static EDGE_INTERFACE_MAP = {
        'GE1': 'lan',
        'GE2': 'lan',
        'GE3': 'wan1',
        'GE4': 'wan2',
        'GE5': 'wan3',
        'GE6': 'lan',
        'GE7': 'lan',
        'GE8': 'lan'
    };

    // Device type configurations
    static DEVICE_TYPES = {
        edge: {
            label: 'VeloCloud Edge',
            description: 'SD-WAN appliance for branch and edge locations',
            icon: '🌐',
            interfaces: [
                { key: 'GE1', label: 'GE1 (LAN)', description: 'LAN port - Internal network', required: false },
                { key: 'GE2', label: 'GE2 (LAN)', description: 'LAN port - Internal network', required: false },
                { key: 'GE3', label: 'GE3 (WAN)', description: 'WAN port - Primary uplink', required: false },
                { key: 'GE4', label: 'GE4 (WAN)', description: 'WAN port - Secondary uplink', required: false },
                { key: 'GE5', label: 'GE5 (WAN)', description: 'WAN port - Tertiary uplink', required: false },
                { key: 'GE6', label: 'GE6 (WAN)', description: 'WAN port - Additional uplink', required: false },
                { key: 'GE7', label: 'GE7 (WAN)', description: 'WAN port - Additional uplink', required: false },
                { key: 'GE8', label: 'GE8 (WAN)', description: 'WAN port - Additional uplink', required: false }
            ],
            deviceType: 'velo_edge',
            requiresEdgeConfig: true  // Flag for Edge-specific VCO configuration
        },
        gateway: {
            label: 'VeloCloud Gateway',
            description: 'Data plane hub for Edge traffic aggregation',
            icon: '🔀',
            interfaces: [
                { key: 'eth0', label: 'Public (eth0)', description: 'Internet-facing interface for VCO and VCMP', required: true },
                { key: 'eth1', label: 'Handoff (eth1)', description: 'PE router handoff for customer traffic', required: false }
            ],
            deviceType: 'velo_gateway',
            requiresGatewayConfig: true  // Flag for Gateway-specific configuration
        },
        orchestrator: {
            label: 'VeloCloud Orchestrator',
            description: 'Management and control plane (VCO)',
            icon: '🎛️',
            interfaces: [
                // eth0 is management - uses mgmt_ip from step 2, no switch connection needed
                { key: 'eth1', label: 'eth1 (Data)', description: 'Data interface - Edge/Gateway connectivity', required: false }
            ],
            deviceType: 'velo_orchestrator',
            requiresOrchestratorConfig: true  // Flag for Orchestrator-specific configuration
        }
    };

    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;
        this.currentStep = 1;
        this.totalSteps = 4;  // Reduced: no name entry step (auto-generated)

        // Wizard state
        this.veloConfig = {
            device_type: '',
            name: '',
            mgmt_ip: '',
            interfaces: {},  // Will be populated based on device type
            gateway_config: {  // Gateway-specific configuration
                vco: '',
                activation_code: '',
                eth0_ip: '',
                eth0_gateway: '',
                eth1_ip: '',
                eth1_gateway: '',
                // Connection fields for topology integration
                eth0_target_device: '',
                eth0_target_port: '',
                eth1_target_device: '',
                eth1_target_port: ''
            },
            edge_config: {  // Edge-specific configuration
                vco: '',
                activation_code: '',
                interfaces: {}  // GE1-GE8 interface config (type, ip, netmask, gateway, target_device, target_port)
            }
        };

        // Cached data from API
        this.veloStatus = null;
        this.availableIps = [];
        this.targetDevices = [];

        this.isSubmitting = false;

        // Event handler references for cleanup
        this.escapeHandler = null;
    }

    /**
     * Generate the next available name for a device type
     * Names follow pattern: edge1, edge2, gateway1, orchestrator1, etc.
     */
    generateNextName(deviceType) {
        // Map device type to prefix
        const prefixMap = {
            'edge': 'edge',
            'gateway': 'gateway',
            'orchestrator': 'orchestrator'
        };

        const prefix = prefixMap[deviceType] || deviceType;
        // API returns { devices: { edge: { count, max }, ... } }
        const devices = this.veloStatus?.devices || {};
        const existingCount = devices[deviceType]?.count || 0;

        // Start from existingCount + 1 and find first available
        // In practice, with low limits (1-2 per type), this is usually just existingCount + 1
        return `${prefix}${existingCount + 1}`;
    }

    /**
     * Check if VeloCloud feature is available (KVM mode + feature enabled)
     */
    isAvailable() {
        const eventManager = this.topologyManager?.eventManager;
        if (eventManager && eventManager.isCeosLab) {
            return false;
        }
        // Feature flag check will be done during loadAvailableData
        return true;
    }

    /**
     * Show the wizard overlay
     */
    async show() {
        if (!this.isAvailable()) {
            console.log('VeloCloud wizard not available for container labs');
            return;
        }

        // Reset state
        this.currentStep = 1;
        this.veloConfig = {
            device_type: '',
            name: '',
            mgmt_ip: '',
            interfaces: {},
            gateway_config: {
                vco: '',
                activation_code: '',
                eth0_ip: '',
                eth0_gateway: '',
                eth1_ip: '',
                eth1_gateway: '',
                // Connection fields for topology integration
                eth0_target_device: '',
                eth0_target_port: '',
                eth1_target_device: '',
                eth1_target_port: ''
            },
            edge_config: {
                vco: '',
                activation_code: '',
                interfaces: {}
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
            <div class="add-node-wizard add-velocloud-wizard">
                <div class="wizard-header">
                    <h2>Add VeloCloud Device</h2>
                    <button class="wizard-close-btn" title="Close">&times;</button>
                </div>
                <div class="wizard-progress">
                    <div class="progress-steps">
                        <div class="progress-step active" data-step="1">
                            <span class="step-number">1</span>
                            <span class="step-label">Type</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="2">
                            <span class="step-number">2</span>
                            <span class="step-label">Mgmt IP</span>
                        </div>
                        <div class="progress-connector"></div>
                        <div class="progress-step" data-step="3">
                            <span class="step-number">3</span>
                            <span class="step-label">Interfaces</span>
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

            const data = await NodeBuilderAPI.loadVeloWizardData();

            // Validate data structure
            if (!data || typeof data !== 'object') {
                throw new Error('Invalid response from server: expected data object');
            }

            this.veloStatus = data.veloStatus || { enabled: false, orchestrator_count: 0, edge_count: 0 };
            this.availableIps = Array.isArray(data.availableIps) ? data.availableIps : [];
            this.targetDevices = Array.isArray(data.targetDevices) ? data.targetDevices : [];

        } catch (error) {
            console.error('[AddVelocloudWizard] Error loading wizard data:', error);
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

        // Check if VeloCloud feature is enabled
        if (!this.veloStatus.enabled) {
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#9888;</div>
                    <h3>VeloCloud Feature Not Enabled</h3>
                    <p>VeloCloud SD-WAN devices are not available in this topology.</p>
                    <p class="error-hint">Contact your administrator to enable VeloCloud support.</p>
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
            nextBtn.textContent = 'Create Device';
            nextBtn.classList.add('wizard-btn-create');
        } else {
            nextBtn.textContent = 'Next';
            nextBtn.classList.remove('wizard-btn-create');
        }

        // Render step content (name is auto-generated, so step 1 is Type, step 2 is Mgmt IP)
        switch (this.currentStep) {
            case 1:
                this.renderDeviceTypeStep(content);
                break;
            case 2:
                this.renderMgmtIpStep(content);
                break;
            case 3:
                this.renderInterfacesStep(content);
                break;
            case 4:
                this.renderReviewStep(content);
                break;
        }

        this.updateNextButtonState();
    }

    /**
     * Step 1: Device Type Selection
     */
    renderDeviceTypeStep(content) {
        // API returns { devices: { edge: { count, max }, gateway: {...}, orchestrator: {...} } }
        const devices = this.veloStatus.devices || {};
        const counts = {
            edge: devices.edge?.count || 0,
            gateway: devices.gateway?.count || 0,
            orchestrator: devices.orchestrator?.count || 0
        };
        const limits = {
            edge: devices.edge?.max || 1,
            gateway: devices.gateway?.max || 1,
            orchestrator: devices.orchestrator?.max || 1
        };

        content.innerHTML = `
            <div class="wizard-step wizard-step-device-type">
                <h3>Select VeloCloud Device Type</h3>
                <p class="step-description">Choose the type of VeloCloud SD-WAN device to add to your topology.</p>

                <div class="device-type-grid">
                    ${Object.entries(AddVelocloudWizard.DEVICE_TYPES).map(([key, config]) => {
                        const count = counts[key] || 0;
                        const limit = limits[key] || 1;
                        const atLimit = count >= limit;

                        return `
                            <div class="device-type-card ${this.veloConfig.device_type === key ? 'selected' : ''} ${atLimit ? 'disabled' : ''}"
                                 data-type="${key}"
                                 ${atLimit ? 'title="Limit reached"' : ''}>
                                <div class="device-type-icon">${config.icon}</div>
                                <div class="device-type-label">${config.label}</div>
                                <div class="device-type-desc">${config.description}</div>
                                <div class="device-type-limit ${atLimit ? 'at-limit' : ''}">
                                    ${count}/${limit} used
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>

                <div class="velocloud-info-box">
                    <h4>VeloCloud SD-WAN Overview</h4>
                    <ul>
                        <li><strong>Edge</strong> - Branch appliance with WAN and LAN connectivity</li>
                        <li><strong>Gateway</strong> - Cloud-based data plane for Edge aggregation</li>
                        <li><strong>Orchestrator</strong> - Central management and control (VCO)</li>
                        <li>All devices configured for standalone training mode</li>
                    </ul>
                </div>
            </div>
        `;

        // Add click handlers for device type cards
        content.querySelectorAll('.device-type-card:not(.disabled)').forEach(card => {
            card.addEventListener('click', () => {
                content.querySelectorAll('.device-type-card').forEach(c => c.classList.remove('selected'));
                card.classList.add('selected');
                this.veloConfig.device_type = card.dataset.type;

                // Auto-generate name based on device type and existing count
                this.veloConfig.name = this.generateNextName(card.dataset.type);

                // Initialize interfaces for the selected type
                const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];
                this.veloConfig.interfaces = {};
                typeConfig.interfaces.forEach(iface => {
                    this.veloConfig.interfaces[iface.key] = {
                        ip: '',
                        target_device: '',
                        target_port: ''
                    };
                });

                this.updateNextButtonState();
            });
        });
    }

    // Fixed management IP for VeloCloud Orchestrator
    // This enables predictable nginx proxy configuration (like CVP at 192.168.0.5)
    static VCO_FIXED_MGMT_IP = '192.168.0.6';

    /**
     * Step 2: Management IP Selection
     */
    renderMgmtIpStep(content) {
        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];

        // Orchestrator uses a fixed management IP for nginx proxy routing
        // This enables the VCO web UI to be accessible at /vco/ with proper asset routing
        if (this.veloConfig.device_type === 'orchestrator') {
            this.veloConfig.mgmt_ip = AddVelocloudWizard.VCO_FIXED_MGMT_IP;

            content.innerHTML = `
                <div class="wizard-step wizard-step-ip">
                    <h3>Management IP</h3>
                    <p class="step-description">The VeloCloud Orchestrator uses a reserved management IP address.</p>

                    <div class="form-group">
                        <label>Management IP Address</label>
                        <div class="fixed-ip-display">
                            <code>${this.escapeHtml(AddVelocloudWizard.VCO_FIXED_MGMT_IP)}</code>
                            <span class="fixed-ip-badge">Reserved</span>
                        </div>
                    </div>

                    <div class="ip-info orchestrator-ip-info">
                        <p>This IP is reserved for the Orchestrator to enable:</p>
                        <ul>
                            <li>Web UI access at <code>/vco/</code> through the lab portal</li>
                            <li>Consistent proxy routing for all VCO paths</li>
                            <li>Only one Orchestrator per topology is supported</li>
                        </ul>
                    </div>

                    <div class="interface-info-box velocloud-interface-info">
                        <h4>${typeConfig.label} Network Interfaces</h4>
                        <p>The device will have the following network interfaces:</p>
                        <ul>
                            <li><strong>eth0 (Management)</strong> - For SSH and web UI access (${AddVelocloudWizard.VCO_FIXED_MGMT_IP})</li>
                            ${typeConfig.interfaces.map((iface, idx) => `
                                <li><strong>eth${idx + 1} (${iface.label})</strong> - ${iface.description}</li>
                            `).join('')}
                        </ul>
                    </div>
                </div>
            `;
            return;
        }

        // Edge and Gateway use selectable IPs from the pool
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
                    <label for="velocloud-mgmt-ip">Management IP Address</label>
                    <select id="velocloud-mgmt-ip" class="form-select">
                        <option value="">Select an IP address...</option>
                        ${ipOptions}
                    </select>
                </div>

                <div class="ip-info">
                    <p><strong>${this.availableIps.length}</strong> IP addresses available</p>
                </div>

                <div class="interface-info-box velocloud-interface-info">
                    <h4>${typeConfig.label} Network Interfaces</h4>
                    <p>The device will have the following network interfaces:</p>
                    <ul>
                        <li><strong>eth0 (Management)</strong> - For SSH access, uses the IP selected above</li>
                        ${typeConfig.interfaces.map((iface, idx) => `
                            <li><strong>eth${idx + 1} (${iface.label})</strong> - ${iface.description}</li>
                        `).join('')}
                    </ul>
                </div>
            </div>
        `;

        const select = content.querySelector('#velocloud-mgmt-ip');

        // Set current value if already selected
        if (this.veloConfig.mgmt_ip) {
            select.value = this.veloConfig.mgmt_ip;
        }

        select.addEventListener('change', (e) => {
            this.veloConfig.mgmt_ip = e.target.value;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 4: Interface Configuration
     */
    renderInterfacesStep(content) {
        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];

        // Gateway has special configuration requirements
        if (typeConfig.requiresGatewayConfig) {
            this.renderGatewayConfigStep(content);
            return;
        }

        // Edge has special configuration requirements (VCO + GE interface config)
        if (typeConfig.requiresEdgeConfig) {
            this.renderEdgeConfigStep(content);
            return;
        }

        // Orchestrator has simplified configuration (eth0 uses mgmt_ip from step 2)
        if (typeConfig.requiresOrchestratorConfig) {
            this.renderOrchestratorConfigStep(content);
            return;
        }

        content.innerHTML = `
            <div class="wizard-step wizard-step-interfaces">
                <h3>Configure Interfaces</h3>
                <p class="step-description">Configure the data interfaces for the ${typeConfig.label}. All interfaces are optional - configure only what you need.</p>

                <div class="interfaces-container">
                    ${typeConfig.interfaces.map((iface, idx) => {
                        const ifaceConfig = this.veloConfig.interfaces[iface.key] || { ip: '', target_device: '', target_port: '' };

                        return `
                            <div class="interface-config-card" data-interface="${iface.key}">
                                <div class="interface-header">
                                    <span class="interface-name">eth${idx + 1} - ${iface.label}</span>
                                    <span class="interface-desc">${iface.description}</span>
                                </div>
                                <div class="interface-fields">
                                    <div class="form-group">
                                        <label for="iface-${iface.key}-ip">IP Address (CIDR)</label>
                                        <input type="text" id="iface-${iface.key}-ip" class="form-input interface-ip"
                                               placeholder="e.g., 10.${idx + 1}.1.1/24 (optional)"
                                               value="${this.escapeHtml(ifaceConfig.ip)}"
                                               data-interface="${iface.key}">
                                        <div id="iface-${iface.key}-validation" class="validation-message"></div>
                                    </div>
                                    <div class="form-group">
                                        <label for="iface-${iface.key}-device">Target Switch</label>
                                        <select id="iface-${iface.key}-device" class="form-select interface-device" data-interface="${iface.key}">
                                            <option value="">Not connected</option>
                                            ${this.targetDevices.map(device => `
                                                <option value="${this.escapeHtml(device.name)}"
                                                        data-next-port="${this.escapeHtml(device.next_available_port)}">
                                                    ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
                                                </option>
                                            `).join('')}
                                        </select>
                                    </div>
                                    <div class="form-group">
                                        <label for="iface-${iface.key}-port">Target Port</label>
                                        <input type="text" id="iface-${iface.key}-port" class="form-input interface-port"
                                               placeholder="Auto-selected" readonly
                                               value="${this.escapeHtml(ifaceConfig.target_port)}"
                                               data-interface="${iface.key}">
                                    </div>
                                </div>
                            </div>
                        `;
                    }).join('')}
                </div>

                <div class="velocloud-interface-note">
                    <p><strong>Note:</strong> Interfaces without IP addresses or target switches will be created but left unconfigured.</p>
                </div>
            </div>
        `;

        // Set initial values and add event listeners
        typeConfig.interfaces.forEach(iface => {
            const ifaceConfig = this.veloConfig.interfaces[iface.key] || { ip: '', target_device: '', target_port: '' };

            const ipInput = content.querySelector(`#iface-${iface.key}-ip`);
            const deviceSelect = content.querySelector(`#iface-${iface.key}-device`);
            const portInput = content.querySelector(`#iface-${iface.key}-port`);
            const validationMsg = content.querySelector(`#iface-${iface.key}-validation`);

            // Set initial values
            if (ifaceConfig.target_device) {
                deviceSelect.value = ifaceConfig.target_device;
            }

            ipInput.addEventListener('input', (e) => {
                this.veloConfig.interfaces[iface.key].ip = e.target.value.trim();
                if (e.target.value.trim()) {
                    this.validateCidrIp(e.target.value.trim(), validationMsg);
                } else {
                    validationMsg.className = 'validation-message';
                    validationMsg.textContent = '';
                }
                this.updateNextButtonState();
            });

            deviceSelect.addEventListener('change', (e) => {
                const selectedOption = e.target.selectedOptions[0];
                const nextPort = selectedOption?.dataset.nextPort || '';

                // Calculate the actual next port considering other interfaces using the same device
                let actualPort = nextPort;
                if (e.target.value) {
                    const sameDeviceCount = this.countSameDeviceInterfaces(e.target.value, iface.key);
                    if (sameDeviceCount > 0 && nextPort) {
                        const portNum = parseInt(nextPort.replace(/\D/g, ''), 10);
                        if (!isNaN(portNum)) {
                            actualPort = `Ethernet${portNum + sameDeviceCount}`;
                        }
                    }
                }

                portInput.value = actualPort;
                this.veloConfig.interfaces[iface.key].target_device = e.target.value;
                this.veloConfig.interfaces[iface.key].target_port = actualPort;
                this.updateNextButtonState();
            });
        });
    }

    /**
     * Step 4 (Gateway): VeloCloud Gateway-specific Configuration
     * Collects VCO registration and network interface details
     */
    renderGatewayConfigStep(content) {
        const gc = this.veloConfig.gateway_config;

        content.innerHTML = `
            <div class="wizard-step wizard-step-gateway-config">
                <h3>Gateway Configuration</h3>
                <p class="step-description">Configure the VeloCloud Gateway network and orchestrator settings.</p>

                <!-- VCO Registration Section -->
                <div class="config-section">
                    <h4>Orchestrator Registration</h4>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="gateway-vco">VCO Address</label>
                            <input type="text" id="gateway-vco" class="form-input"
                                   placeholder="orchestrator.example.com"
                                   value="${this.escapeHtml(gc.vco)}">
                            <small class="form-hint">VeloCloud Orchestrator hostname or IP</small>
                        </div>
                        <div class="form-group">
                            <label for="gateway-activation">Activation Code</label>
                            <input type="text" id="gateway-activation" class="form-input"
                                   placeholder="XXXX-XXXX-XXXX-XXXX"
                                   value="${this.escapeHtml(gc.activation_code)}">
                            <small class="form-hint">Gateway activation key from VCO</small>
                        </div>
                    </div>
                </div>

                <!-- eth0 - Public Interface -->
                <div class="config-section">
                    <h4>eth0 - Public Interface (Internet-facing)</h4>
                    <p class="section-desc">Primary interface for VCO communication and Edge VCMP tunnels.</p>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="gateway-eth0-ip">IP Address (CIDR)</label>
                            <input type="text" id="gateway-eth0-ip" class="form-input"
                                   placeholder="192.168.0.50/24"
                                   value="${this.escapeHtml(gc.eth0_ip)}">
                            <div id="gateway-eth0-validation" class="validation-message"></div>
                        </div>
                        <div class="form-group">
                            <label for="gateway-eth0-gw">Gateway</label>
                            <input type="text" id="gateway-eth0-gw" class="form-input"
                                   placeholder="192.168.0.1"
                                   value="${this.escapeHtml(gc.eth0_gateway)}">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="gateway-eth0-device">Connect to Switch</label>
                            <select id="gateway-eth0-device" class="form-select">
                                <option value="">Not connected</option>
                                ${this.targetDevices.map(device => `
                                    <option value="${this.escapeHtml(device.name)}"
                                            data-next-port="${this.escapeHtml(device.next_available_port)}"
                                            ${gc.eth0_target_device === device.name ? 'selected' : ''}>
                                        ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
                                    </option>
                                `).join('')}
                            </select>
                            <small class="form-hint">Connect to a switch for internet simulation</small>
                        </div>
                        <div class="form-group">
                            <label for="gateway-eth0-port">Target Port</label>
                            <input type="text" id="gateway-eth0-port" class="form-input"
                                   placeholder="Auto-selected" readonly
                                   value="${this.escapeHtml(gc.eth0_target_port)}">
                        </div>
                    </div>
                </div>

                <!-- eth1 - Handoff Interface -->
                <div class="config-section">
                    <h4>eth1 - Handoff Interface (Optional)</h4>
                    <p class="section-desc">PE router connection for customer traffic handoff. Supports VLAN tagging.</p>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="gateway-eth1-ip">IP Address (CIDR)</label>
                            <input type="text" id="gateway-eth1-ip" class="form-input"
                                   placeholder="10.0.0.1/24 (optional)"
                                   value="${this.escapeHtml(gc.eth1_ip)}">
                            <div id="gateway-eth1-validation" class="validation-message"></div>
                        </div>
                        <div class="form-group">
                            <label for="gateway-eth1-gw">Gateway</label>
                            <input type="text" id="gateway-eth1-gw" class="form-input"
                                   placeholder="10.0.0.254 (optional)"
                                   value="${this.escapeHtml(gc.eth1_gateway)}">
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="gateway-eth1-device">Connect to Switch</label>
                            <select id="gateway-eth1-device" class="form-select">
                                <option value="">Not connected</option>
                                ${this.targetDevices.map(device => `
                                    <option value="${this.escapeHtml(device.name)}"
                                            data-next-port="${this.escapeHtml(device.next_available_port)}"
                                            ${gc.eth1_target_device === device.name ? 'selected' : ''}>
                                        ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
                                    </option>
                                `).join('')}
                            </select>
                            <small class="form-hint">Connect to a PE router for handoff</small>
                        </div>
                        <div class="form-group">
                            <label for="gateway-eth1-port">Target Port</label>
                            <input type="text" id="gateway-eth1-port" class="form-input"
                                   placeholder="Auto-selected" readonly
                                   value="${this.escapeHtml(gc.eth1_target_port)}">
                        </div>
                    </div>
                </div>

                <div class="velocloud-interface-note">
                    <p><strong>Note:</strong> Gateway uses default credentials: <code>vcadmin</code> / <code>[lab password]</code></p>
                </div>
            </div>
        `;

        // Add event listeners for all Gateway config fields
        const vcoInput = content.querySelector('#gateway-vco');
        const activationInput = content.querySelector('#gateway-activation');
        const eth0IpInput = content.querySelector('#gateway-eth0-ip');
        const eth0GwInput = content.querySelector('#gateway-eth0-gw');
        const eth1IpInput = content.querySelector('#gateway-eth1-ip');
        const eth1GwInput = content.querySelector('#gateway-eth1-gw');
        const eth0Validation = content.querySelector('#gateway-eth0-validation');
        const eth1Validation = content.querySelector('#gateway-eth1-validation');

        vcoInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.vco = e.target.value.trim();
        });

        activationInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.activation_code = e.target.value.trim();
        });

        eth0IpInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.eth0_ip = e.target.value.trim();
            if (e.target.value.trim()) {
                this.validateCidrIp(e.target.value.trim(), eth0Validation);
            } else {
                eth0Validation.className = 'validation-message';
                eth0Validation.textContent = '';
            }
            this.updateNextButtonState();
        });

        eth0GwInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.eth0_gateway = e.target.value.trim();
        });

        eth1IpInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.eth1_ip = e.target.value.trim();
            if (e.target.value.trim()) {
                this.validateCidrIp(e.target.value.trim(), eth1Validation);
            } else {
                eth1Validation.className = 'validation-message';
                eth1Validation.textContent = '';
            }
            this.updateNextButtonState();
        });

        eth1GwInput.addEventListener('input', (e) => {
            this.veloConfig.gateway_config.eth1_gateway = e.target.value.trim();
        });

        // Connection event listeners for eth0
        const eth0DeviceSelect = content.querySelector('#gateway-eth0-device');
        const eth0PortInput = content.querySelector('#gateway-eth0-port');

        eth0DeviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';

            // Calculate actual port considering other interfaces using same device
            let actualPort = nextPort;
            if (e.target.value) {
                const sameDeviceCount = this.countSameDeviceGatewayInterfaces(e.target.value, 'eth0');
                if (sameDeviceCount > 0 && nextPort) {
                    const portNum = parseInt(nextPort.replace(/\D/g, ''), 10);
                    if (!isNaN(portNum)) {
                        actualPort = `Ethernet${portNum + sameDeviceCount}`;
                    }
                }
            }

            eth0PortInput.value = actualPort;
            this.veloConfig.gateway_config.eth0_target_device = e.target.value;
            this.veloConfig.gateway_config.eth0_target_port = actualPort;
        });

        // Connection event listeners for eth1
        const eth1DeviceSelect = content.querySelector('#gateway-eth1-device');
        const eth1PortInput = content.querySelector('#gateway-eth1-port');

        eth1DeviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';

            // Calculate actual port considering other interfaces using same device
            let actualPort = nextPort;
            if (e.target.value) {
                const sameDeviceCount = this.countSameDeviceGatewayInterfaces(e.target.value, 'eth1');
                if (sameDeviceCount > 0 && nextPort) {
                    const portNum = parseInt(nextPort.replace(/\D/g, ''), 10);
                    if (!isNaN(portNum)) {
                        actualPort = `Ethernet${portNum + sameDeviceCount}`;
                    }
                }
            }

            eth1PortInput.value = actualPort;
            this.veloConfig.gateway_config.eth1_target_device = e.target.value;
            this.veloConfig.gateway_config.eth1_target_port = actualPort;
        });
    }

    /**
     * Step 4 (Edge): VeloCloud Edge-specific Configuration
     * Collects VCO registration and GE interface network details
     */
    renderEdgeConfigStep(content) {
        const ec = this.veloConfig.edge_config;
        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];

        // Build interface rows for GE3-GE8 (WAN ports - most commonly configured)
        // GE1-GE2 are LAN ports, typically used for downstream connections
        const wanInterfaces = typeConfig.interfaces.filter(i => i.key.match(/GE[3-8]/));
        const lanInterfaces = typeConfig.interfaces.filter(i => i.key.match(/GE[12]/));

        content.innerHTML = `
            <div class="wizard-step wizard-step-edge-config">
                <h3>Edge Configuration</h3>
                <p class="step-description">Configure the VeloCloud Edge orchestrator and network interface settings.</p>

                <!-- VCO Registration Section -->
                <div class="config-section">
                    <h4>Orchestrator Registration</h4>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="edge-vco">VCO Address</label>
                            <input type="text" id="edge-vco" class="form-input"
                                   placeholder="orchestrator.example.com"
                                   value="${this.escapeHtml(ec.vco)}">
                            <small class="form-hint">VeloCloud Orchestrator hostname or IP</small>
                        </div>
                        <div class="form-group">
                            <label for="edge-activation">Activation Code</label>
                            <input type="text" id="edge-activation" class="form-input"
                                   placeholder="XXXX-XXXX-XXXX-XXXX"
                                   value="${this.escapeHtml(ec.activation_code)}">
                            <small class="form-hint">Edge activation key from VCO</small>
                        </div>
                    </div>
                </div>

                <!-- WAN Interfaces (GE3-GE8) -->
                <div class="config-section">
                    <h4>WAN Interfaces (GE3-GE8)</h4>
                    <p class="section-desc">Configure WAN ports for internet/MPLS uplinks. Connect to topology switches.</p>
                    <div class="edge-interfaces-grid edge-interfaces-grid--with-connections">
                        <div class="edge-interface-header">
                            <span>Port</span>
                            <span>Type</span>
                            <span>IP</span>
                            <span>Netmask</span>
                            <span>Gateway</span>
                            <span>Connect To</span>
                            <span>Port</span>
                        </div>
                        ${wanInterfaces.map(iface => {
                            const ifConfig = ec.interfaces[iface.key] || {};
                            return `
                                <div class="edge-interface-row" data-interface="${iface.key}">
                                    <div class="interface-label">${iface.key}</div>
                                    <select class="form-select interface-type" data-interface="${iface.key}">
                                        <option value="dhcp" ${ifConfig.type !== 'static' ? 'selected' : ''}>DHCP</option>
                                        <option value="static" ${ifConfig.type === 'static' ? 'selected' : ''}>Static</option>
                                    </select>
                                    <input type="text" class="form-input interface-ip" data-interface="${iface.key}"
                                           placeholder="IP"
                                           value="${this.escapeHtml(ifConfig.ip || '')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <input type="text" class="form-input interface-netmask" data-interface="${iface.key}"
                                           placeholder="Netmask"
                                           value="${this.escapeHtml(ifConfig.netmask || '255.255.255.0')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <input type="text" class="form-input interface-gateway" data-interface="${iface.key}"
                                           placeholder="Gateway"
                                           value="${this.escapeHtml(ifConfig.gateway || '')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <select class="form-select interface-target-device" data-interface="${iface.key}">
                                        <option value="">None</option>
                                        ${this.targetDevices.map(device => `
                                            <option value="${this.escapeHtml(device.name)}"
                                                    data-next-port="${this.escapeHtml(device.next_available_port)}"
                                                    ${ifConfig.target_device === device.name ? 'selected' : ''}>
                                                ${this.escapeHtml(device.name)}
                                            </option>
                                        `).join('')}
                                    </select>
                                    <input type="text" class="form-input interface-target-port" data-interface="${iface.key}"
                                           placeholder="Auto"
                                           value="${this.escapeHtml(ifConfig.target_port || '')}"
                                           readonly>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>

                <!-- LAN Interfaces (GE1-GE2) - Simplified -->
                <div class="config-section">
                    <h4>LAN Interfaces (GE1-GE2) - Optional</h4>
                    <p class="section-desc">Configure LAN ports for internal network connections.</p>
                    <div class="edge-interfaces-grid edge-interfaces-grid--with-connections">
                        <div class="edge-interface-header">
                            <span>Port</span>
                            <span>Type</span>
                            <span>IP</span>
                            <span>Netmask</span>
                            <span>Gateway</span>
                            <span>Connect To</span>
                            <span>Port</span>
                        </div>
                        ${lanInterfaces.map(iface => {
                            const ifConfig = ec.interfaces[iface.key] || {};
                            return `
                                <div class="edge-interface-row" data-interface="${iface.key}">
                                    <div class="interface-label">${iface.key}</div>
                                    <select class="form-select interface-type" data-interface="${iface.key}">
                                        <option value="dhcp" ${ifConfig.type !== 'static' ? 'selected' : ''}>DHCP</option>
                                        <option value="static" ${ifConfig.type === 'static' ? 'selected' : ''}>Static</option>
                                    </select>
                                    <input type="text" class="form-input interface-ip" data-interface="${iface.key}"
                                           placeholder="IP"
                                           value="${this.escapeHtml(ifConfig.ip || '')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <input type="text" class="form-input interface-netmask" data-interface="${iface.key}"
                                           placeholder="Netmask"
                                           value="${this.escapeHtml(ifConfig.netmask || '255.255.255.0')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <input type="text" class="form-input interface-gateway" data-interface="${iface.key}"
                                           placeholder="Gateway"
                                           value="${this.escapeHtml(ifConfig.gateway || '')}"
                                           ${ifConfig.type !== 'static' ? 'disabled' : ''}>
                                    <select class="form-select interface-target-device" data-interface="${iface.key}">
                                        <option value="">None</option>
                                        ${this.targetDevices.map(device => `
                                            <option value="${this.escapeHtml(device.name)}"
                                                    data-next-port="${this.escapeHtml(device.next_available_port)}"
                                                    ${ifConfig.target_device === device.name ? 'selected' : ''}>
                                                ${this.escapeHtml(device.name)}
                                            </option>
                                        `).join('')}
                                    </select>
                                    <input type="text" class="form-input interface-target-port" data-interface="${iface.key}"
                                           placeholder="Auto"
                                           value="${this.escapeHtml(ifConfig.target_port || '')}"
                                           readonly>
                                </div>
                            `;
                        }).join('')}
                    </div>
                </div>

                <div class="velocloud-interface-note">
                    <p><strong>Note:</strong> Edge uses default credentials: <code>root</code> / <code>[lab password]</code></p>
                </div>
            </div>
        `;

        // Add event listeners for VCO fields
        const vcoInput = content.querySelector('#edge-vco');
        const activationInput = content.querySelector('#edge-activation');

        vcoInput.addEventListener('input', (e) => {
            this.veloConfig.edge_config.vco = e.target.value.trim();
        });

        activationInput.addEventListener('input', (e) => {
            this.veloConfig.edge_config.activation_code = e.target.value.trim();
        });

        // Add event listeners for each interface row
        content.querySelectorAll('.edge-interface-row').forEach(row => {
            const ifaceKey = row.dataset.interface;
            const typeSelect = row.querySelector('.interface-type');
            const ipInput = row.querySelector('.interface-ip');
            const netmaskInput = row.querySelector('.interface-netmask');
            const gatewayInput = row.querySelector('.interface-gateway');
            const targetDeviceSelect = row.querySelector('.interface-target-device');
            const targetPortInput = row.querySelector('.interface-target-port');

            // Initialize interface config if not exists
            if (!this.veloConfig.edge_config.interfaces[ifaceKey]) {
                this.veloConfig.edge_config.interfaces[ifaceKey] = {
                    type: 'dhcp',
                    target_device: '',
                    target_port: ''
                };
            }

            typeSelect.addEventListener('change', (e) => {
                const isStatic = e.target.value === 'static';
                ipInput.disabled = !isStatic;
                netmaskInput.disabled = !isStatic;
                gatewayInput.disabled = !isStatic;

                this.veloConfig.edge_config.interfaces[ifaceKey].type = e.target.value;

                if (!isStatic) {
                    // Clear static config when switching to DHCP
                    this.veloConfig.edge_config.interfaces[ifaceKey].ip = '';
                    this.veloConfig.edge_config.interfaces[ifaceKey].netmask = '';
                    this.veloConfig.edge_config.interfaces[ifaceKey].gateway = '';
                    ipInput.value = '';
                    netmaskInput.value = '255.255.255.0';
                    gatewayInput.value = '';
                }
            });

            ipInput.addEventListener('input', (e) => {
                this.veloConfig.edge_config.interfaces[ifaceKey].ip = e.target.value.trim();
            });

            netmaskInput.addEventListener('input', (e) => {
                this.veloConfig.edge_config.interfaces[ifaceKey].netmask = e.target.value.trim();
            });

            gatewayInput.addEventListener('input', (e) => {
                this.veloConfig.edge_config.interfaces[ifaceKey].gateway = e.target.value.trim();
            });

            // Connection event listener for target device
            targetDeviceSelect.addEventListener('change', (e) => {
                const selectedOption = e.target.selectedOptions[0];
                const nextPort = selectedOption?.dataset.nextPort || '';

                // Calculate actual port considering other interfaces using same device
                let actualPort = nextPort;
                if (e.target.value) {
                    const sameDeviceCount = this.countSameDeviceEdgeInterfaces(e.target.value, ifaceKey);
                    if (sameDeviceCount > 0 && nextPort) {
                        const portNum = parseInt(nextPort.replace(/\D/g, ''), 10);
                        if (!isNaN(portNum)) {
                            actualPort = `Ethernet${portNum + sameDeviceCount}`;
                        }
                    }
                }

                targetPortInput.value = actualPort;
                this.veloConfig.edge_config.interfaces[ifaceKey].target_device = e.target.value;
                this.veloConfig.edge_config.interfaces[ifaceKey].target_port = actualPort;
            });
        });
    }

    /**
     * Step 4 (Orchestrator): VeloCloud Orchestrator-specific Configuration
     * Simplified config: eth0 uses mgmt_ip from step 2, only eth1 needs switch connection
     */
    renderOrchestratorConfigStep(content) {
        const eth1Config = this.veloConfig.interfaces['eth1'] || { ip: '', target_device: '', target_port: '' };

        content.innerHTML = `
            <div class="wizard-step wizard-step-orchestrator-config">
                <h3>Orchestrator Network Configuration</h3>
                <p class="step-description">Configure the VeloCloud Orchestrator network interfaces.</p>

                <!-- eth0 - Management Interface (auto-configured) -->
                <div class="config-section">
                    <h4>eth0 - Management Interface</h4>
                    <p class="section-desc">This interface is automatically configured using the management IP selected in Step 2.</p>
                    <div class="orchestrator-mgmt-summary">
                        <table class="review-table">
                            <tr>
                                <th>Interface</th>
                                <td>eth0 (Management)</td>
                            </tr>
                            <tr>
                                <th>IP Address</th>
                                <td><code>${this.escapeHtml(this.veloConfig.mgmt_ip)}</code></td>
                            </tr>
                            <tr>
                                <th>Purpose</th>
                                <td>SSH access, Web UI access</td>
                            </tr>
                            <tr>
                                <th>Network Connection</th>
                                <td>Connected to management network (no switch required)</td>
                            </tr>
                        </table>
                    </div>
                </div>

                <!-- eth1 - Data Interface -->
                <div class="config-section">
                    <h4>eth1 - Data Interface (Optional)</h4>
                    <p class="section-desc">Connect to a topology switch for Edge/Gateway connectivity.</p>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="orch-eth1-ip">IP Address (CIDR)</label>
                            <input type="text" id="orch-eth1-ip" class="form-input"
                                   placeholder="e.g., 10.1.1.1/24 (optional)"
                                   value="${this.escapeHtml(eth1Config.ip)}">
                            <div id="orch-eth1-validation" class="validation-message"></div>
                        </div>
                    </div>
                    <div class="form-row">
                        <div class="form-group">
                            <label for="orch-eth1-device">Connect to Switch</label>
                            <select id="orch-eth1-device" class="form-select">
                                <option value="">Not connected</option>
                                ${this.targetDevices.map(device => `
                                    <option value="${this.escapeHtml(device.name)}"
                                            data-next-port="${this.escapeHtml(device.next_available_port)}"
                                            ${eth1Config.target_device === device.name ? 'selected' : ''}>
                                        ${this.escapeHtml(device.name)} (next: ${this.escapeHtml(device.next_available_port)})
                                    </option>
                                `).join('')}
                            </select>
                            <small class="form-hint">Connect to a switch for Edge/Gateway communication</small>
                        </div>
                        <div class="form-group">
                            <label for="orch-eth1-port">Target Port</label>
                            <input type="text" id="orch-eth1-port" class="form-input"
                                   placeholder="Auto-selected" readonly
                                   value="${this.escapeHtml(eth1Config.target_port)}">
                        </div>
                    </div>
                </div>

                <div class="velocloud-interface-note">
                    <p><strong>Note:</strong> Orchestrator uses default credentials: <code>vcadmin</code> / <code>[lab password]</code></p>
                    <p>Web UI will be available at <code>https://${this.escapeHtml(this.veloConfig.mgmt_ip)}/</code> once the device boots.</p>
                </div>
            </div>
        `;

        // Add event listeners
        const eth1IpInput = content.querySelector('#orch-eth1-ip');
        const eth1Validation = content.querySelector('#orch-eth1-validation');
        const eth1DeviceSelect = content.querySelector('#orch-eth1-device');
        const eth1PortInput = content.querySelector('#orch-eth1-port');

        eth1IpInput.addEventListener('input', (e) => {
            this.veloConfig.interfaces['eth1'].ip = e.target.value.trim();
            if (e.target.value.trim()) {
                this.validateCidrIp(e.target.value.trim(), eth1Validation);
            } else {
                eth1Validation.className = 'validation-message';
                eth1Validation.textContent = '';
            }
            this.updateNextButtonState();
        });

        eth1DeviceSelect.addEventListener('change', (e) => {
            const selectedOption = e.target.selectedOptions[0];
            const nextPort = selectedOption?.dataset.nextPort || '';

            eth1PortInput.value = nextPort;
            this.veloConfig.interfaces['eth1'].target_device = e.target.value;
            this.veloConfig.interfaces['eth1'].target_port = nextPort;
        });
    }

    /**
     * Count how many interfaces are already configured to use the same device
     * Used by Orchestrator interface configuration
     */
    countSameDeviceInterfaces(deviceName, excludeInterface) {
        let count = 0;
        Object.entries(this.veloConfig.interfaces).forEach(([key, config]) => {
            if (key !== excludeInterface && config.target_device === deviceName) {
                count++;
            }
        });
        return count;
    }

    /**
     * Count how many Gateway interfaces are already configured to use the same device
     * Used by Gateway interface configuration for port auto-calculation
     */
    countSameDeviceGatewayInterfaces(deviceName, excludeInterface) {
        const gc = this.veloConfig.gateway_config;
        let count = 0;
        if (excludeInterface !== 'eth0' && gc.eth0_target_device === deviceName) {
            count++;
        }
        if (excludeInterface !== 'eth1' && gc.eth1_target_device === deviceName) {
            count++;
        }
        return count;
    }

    /**
     * Count how many Edge interfaces are already configured to use the same device
     * Used by Edge interface configuration for port auto-calculation
     */
    countSameDeviceEdgeInterfaces(deviceName, excludeInterface) {
        const interfaces = this.veloConfig.edge_config.interfaces || {};
        let count = 0;
        for (const [key, config] of Object.entries(interfaces)) {
            if (key !== excludeInterface && config.target_device === deviceName) {
                count++;
            }
        }
        return count;
    }

    /**
     * Validate CIDR IP format
     */
    validateCidrIp(value, validationEl) {
        if (!value) {
            validationEl.className = 'validation-message';
            validationEl.textContent = '';
            return true; // Empty is valid (optional)
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
        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];

        // Build interfaces summary
        const configuredInterfaces = Object.entries(this.veloConfig.interfaces)
            .filter(([key, config]) => config.ip || config.target_device)
            .map(([key, config]) => {
                const ifaceInfo = typeConfig.interfaces.find(i => i.key === key);
                return { key, label: ifaceInfo?.label || key, ...config };
            });

        content.innerHTML = `
            <div class="wizard-step wizard-step-review">
                <h3>Review Configuration</h3>
                <p class="step-description">Please review the configuration before creating the ${typeConfig.label}.</p>

                <div class="review-section">
                    <h4>Device Information</h4>
                    <table class="review-table">
                        <tr>
                            <th>Device Type</th>
                            <td>${typeConfig.label}</td>
                        </tr>
                        <tr>
                            <th>Device Name</th>
                            <td>${this.escapeHtml(this.veloConfig.name)}</td>
                        </tr>
                        <tr>
                            <th>Management IP</th>
                            <td>${this.escapeHtml(this.veloConfig.mgmt_ip)}</td>
                        </tr>
                    </table>
                </div>

                ${typeConfig.requiresGatewayConfig ? this.renderGatewayReviewSection() :
                  typeConfig.requiresEdgeConfig ? this.renderEdgeReviewSection() :
                  typeConfig.requiresOrchestratorConfig ? this.renderOrchestratorReviewSection() : `
                <div class="review-section">
                    <h4>Data Interfaces</h4>
                    ${configuredInterfaces.length > 0 ? `
                        <table class="review-table">
                            <tr>
                                <th>Interface</th>
                                <th>IP Address</th>
                                <th>Connected To</th>
                            </tr>
                            ${configuredInterfaces.map(iface => `
                                <tr>
                                    <td>${this.escapeHtml(iface.label)}</td>
                                    <td>${iface.ip ? this.escapeHtml(iface.ip) : '<em>Not configured</em>'}</td>
                                    <td>${iface.target_device ? `${this.escapeHtml(iface.target_device)} (${this.escapeHtml(iface.target_port)})` : '<em>Not connected</em>'}</td>
                                </tr>
                            `).join('')}
                        </table>
                    ` : `
                        <p class="no-interfaces-configured">No data interfaces configured. Interfaces will be created but left unconfigured.</p>
                    `}
                </div>
                `}

                <div class="review-notes">
                    <h4>What happens next:</h4>
                    <ul>
                        <li>A new ${typeConfig.label} VM will be created with the specified configuration</li>
                        <li>The device will boot and be accessible via SSH within ~90 seconds</li>
                        ${typeConfig.requiresGatewayConfig ?
                            '<li>Gateway will attempt to register with the configured VCO</li><li>Default login: vcadmin / [lab password]</li>' :
                          typeConfig.requiresEdgeConfig ?
                            '<li>Edge will attempt to register with the configured VCO</li><li>Default login: root / [lab password]</li>' :
                          this.veloConfig.device_type === 'orchestrator' ?
                            '<li>Web UI available at https://&lt;mgmt_ip&gt;/</li><li>Default login: vcadmin / [lab password]</li>' :
                            '<li>Device is configured for standalone training mode</li><li>Default login: arista / arista</li>'}
                    </ul>
                </div>
            </div>
        `;
    }

    /**
     * Render Gateway-specific review section
     */
    renderGatewayReviewSection() {
        const gc = this.veloConfig.gateway_config;

        return `
            <div class="review-section">
                <h4>Orchestrator Registration</h4>
                <table class="review-table">
                    <tr>
                        <th>VCO Address</th>
                        <td>${gc.vco ? this.escapeHtml(gc.vco) : '<em>Not configured</em>'}</td>
                    </tr>
                    <tr>
                        <th>Activation Code</th>
                        <td>${gc.activation_code ? this.escapeHtml(gc.activation_code) : '<em>Not configured</em>'}</td>
                    </tr>
                </table>
            </div>

            <div class="review-section">
                <h4>Network Interfaces</h4>
                <table class="review-table">
                    <tr>
                        <th>Interface</th>
                        <th>IP Address</th>
                        <th>Gateway</th>
                        <th>Connected To</th>
                    </tr>
                    <tr>
                        <td>eth0 (Public)</td>
                        <td>${gc.eth0_ip ? this.escapeHtml(gc.eth0_ip) : '<em>Not configured</em>'}</td>
                        <td>${gc.eth0_gateway ? this.escapeHtml(gc.eth0_gateway) : '<em>Not configured</em>'}</td>
                        <td>${gc.eth0_target_device ?
                            `${this.escapeHtml(gc.eth0_target_device)} (${this.escapeHtml(gc.eth0_target_port)})` :
                            '<em>Not connected</em>'}</td>
                    </tr>
                    <tr>
                        <td>eth1 (Handoff)</td>
                        <td>${gc.eth1_ip ? this.escapeHtml(gc.eth1_ip) : '<em>Not configured</em>'}</td>
                        <td>${gc.eth1_gateway ? this.escapeHtml(gc.eth1_gateway) : '<em>Not configured</em>'}</td>
                        <td>${gc.eth1_target_device ?
                            `${this.escapeHtml(gc.eth1_target_device)} (${this.escapeHtml(gc.eth1_target_port)})` :
                            '<em>Not connected</em>'}</td>
                    </tr>
                </table>
            </div>
        `;
    }

    /**
     * Render Edge-specific review section
     */
    renderEdgeReviewSection() {
        const ec = this.veloConfig.edge_config;

        // Build configured interfaces list - include interfaces with static IP OR connections
        const configuredInterfaces = Object.entries(ec.interfaces || {})
            .filter(([key, config]) => (config.type === 'static' && config.ip) || config.target_device)
            .map(([key, config]) => ({ key, ...config }));

        return `
            <div class="review-section">
                <h4>Orchestrator Registration</h4>
                <table class="review-table">
                    <tr>
                        <th>VCO Address</th>
                        <td>${ec.vco ? this.escapeHtml(ec.vco) : '<em>Not configured</em>'}</td>
                    </tr>
                    <tr>
                        <th>Activation Code</th>
                        <td>${ec.activation_code ? this.escapeHtml(ec.activation_code) : '<em>Not configured</em>'}</td>
                    </tr>
                </table>
            </div>

            <div class="review-section">
                <h4>Network Interfaces (GE1-GE8)</h4>
                ${configuredInterfaces.length > 0 ? `
                    <table class="review-table">
                        <tr>
                            <th>Interface</th>
                            <th>Type</th>
                            <th>IP Address</th>
                            <th>Netmask</th>
                            <th>Gateway</th>
                            <th>Connected To</th>
                        </tr>
                        ${configuredInterfaces.map(iface => `
                            <tr>
                                <td>${this.escapeHtml(iface.key)}</td>
                                <td>${iface.type === 'static' ? 'Static' : 'DHCP'}</td>
                                <td>${iface.ip ? this.escapeHtml(iface.ip) : '<em>DHCP</em>'}</td>
                                <td>${iface.netmask ? this.escapeHtml(iface.netmask) : '255.255.255.0'}</td>
                                <td>${iface.gateway ? this.escapeHtml(iface.gateway) : '<em>None</em>'}</td>
                                <td>${iface.target_device ?
                                    `${this.escapeHtml(iface.target_device)} (${this.escapeHtml(iface.target_port)})` :
                                    '<em>Not connected</em>'}</td>
                            </tr>
                        `).join('')}
                    </table>
                ` : `
                    <p class="no-interfaces-configured">All interfaces configured for DHCP with no topology connections.</p>
                `}
            </div>
        `;
    }

    /**
     * Render Orchestrator-specific review section
     */
    renderOrchestratorReviewSection() {
        const eth1Config = this.veloConfig.interfaces['eth1'] || {};

        return `
            <div class="review-section">
                <h4>Network Interfaces</h4>
                <table class="review-table">
                    <tr>
                        <th>Interface</th>
                        <th>IP Address</th>
                        <th>Purpose</th>
                        <th>Connected To</th>
                    </tr>
                    <tr>
                        <td>eth0 (Management)</td>
                        <td>${this.escapeHtml(this.veloConfig.mgmt_ip)}</td>
                        <td>SSH / Web UI access</td>
                        <td>Management network</td>
                    </tr>
                    <tr>
                        <td>eth1 (Data)</td>
                        <td>${eth1Config.ip ? this.escapeHtml(eth1Config.ip) : '<em>Not configured</em>'}</td>
                        <td>Edge/Gateway connectivity</td>
                        <td>${eth1Config.target_device ?
                            `${this.escapeHtml(eth1Config.target_device)} (${this.escapeHtml(eth1Config.target_port)})` :
                            '<em>Not connected</em>'}</td>
                    </tr>
                </table>
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

        // Step mapping (name is auto-generated): 1=Type, 2=MgmtIP, 3=Interfaces, 4=Review
        switch (this.currentStep) {
            case 1:
                canProceed = this.veloConfig.device_type !== '';
                break;
            case 2:
                canProceed = this.veloConfig.mgmt_ip !== '';
                break;
            case 3:
                // All interfaces are optional, so always allow proceeding
                // Just validate any filled-in IPs
                canProceed = this.validateAllInterfaceIps();
                break;
            case 4:
                canProceed = !this.isSubmitting;
                break;
        }

        nextBtn.disabled = !canProceed || this.isSubmitting;
    }

    /**
     * Validate all interface IPs that have values
     */
    validateAllInterfaceIps() {
        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];

        // For Gateway, validate gateway_config IPs (CIDR format)
        if (typeConfig.requiresGatewayConfig) {
            const gc = this.veloConfig.gateway_config;
            if (gc.eth0_ip && !this.isValidCidr(gc.eth0_ip)) return false;
            if (gc.eth1_ip && !this.isValidCidr(gc.eth1_ip)) return false;
            return true;
        }

        // For Edge, validate edge_config interface IPs (plain IP format)
        if (typeConfig.requiresEdgeConfig) {
            const ec = this.veloConfig.edge_config;
            for (const [key, config] of Object.entries(ec.interfaces || {})) {
                if (config.type === 'static' && config.ip && !this.isValidIp(config.ip)) {
                    return false;
                }
            }
            return true;
        }

        // Standard interface validation for Orchestrator
        for (const iface of typeConfig.interfaces) {
            const ip = this.veloConfig.interfaces[iface.key]?.ip;
            if (ip && !this.isValidCidr(ip)) {
                return false;
            }
        }
        return true;
    }

    /**
     * Check if a string is a valid IP address (without CIDR)
     */
    isValidIp(value) {
        if (!value) return true; // Empty is valid (optional)
        const ipRegex = /^(\d{1,3}\.){3}\d{1,3}$/;
        if (!ipRegex.test(value)) return false;

        const octets = value.split('.').map(Number);
        return !octets.some(o => o < 0 || o > 255);
    }

    /**
     * Check if a string is valid CIDR notation
     */
    isValidCidr(value) {
        if (!value) return true; // Empty is valid (optional)
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
            await this.submitDevice();
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
     * Submit the device creation request
     */
    async submitDevice() {
        if (this.isSubmitting) return;

        this.isSubmitting = true;
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');
        nextBtn.textContent = 'Creating...';
        nextBtn.disabled = true;

        const typeConfig = AddVelocloudWizard.DEVICE_TYPES[this.veloConfig.device_type];
        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = `
            <div class="wizard-creating">
                <div class="spinner large"></div>
                <h3>Creating ${typeConfig.label}...</h3>
                <p>This may take up to 90 seconds. Please wait.</p>
                <div class="creation-log"></div>
            </div>
        `;

        const log = content.querySelector('.creation-log');

        try {
            this.logMessage(log, 'Sending request to nodebuilder service...');

            // Build interface configuration for API
            const interfaceConfig = {};
            Object.entries(this.veloConfig.interfaces).forEach(([key, config]) => {
                if (config.ip || config.target_device) {
                    interfaceConfig[key] = {
                        ip: config.ip || null,
                        target_device: config.target_device || null,
                        target_port: config.target_port || null
                    };
                }
            });

            // Build request payload
            const requestPayload = {
                name: this.veloConfig.name,
                device_type: this.veloConfig.device_type,
                mgmt_ip: this.veloConfig.mgmt_ip,
                interfaces: interfaceConfig
            };

            // Add gateway_config and connections for Gateway devices
            if (this.veloConfig.device_type === 'gateway') {
                requestPayload.gateway_config = this.veloConfig.gateway_config;

                // Build connections array for Gateway
                const connections = [];
                const gc = this.veloConfig.gateway_config;

                if (gc.eth0_target_device) {
                    connections.push({
                        target_device: gc.eth0_target_device,
                        local_port: AddVelocloudWizard.GATEWAY_INTERFACE_MAP['eth0'],  // 'transport1'
                        target_port: gc.eth0_target_port
                    });
                }
                if (gc.eth1_target_device) {
                    connections.push({
                        target_device: gc.eth1_target_device,
                        local_port: AddVelocloudWizard.GATEWAY_INTERFACE_MAP['eth1'],  // 'transport2'
                        target_port: gc.eth1_target_port
                    });
                }

                if (connections.length > 0) {
                    requestPayload.connections = connections;
                }
            }

            // Add edge_config and connections for Edge devices
            if (this.veloConfig.device_type === 'edge') {
                requestPayload.edge_config = this.veloConfig.edge_config;

                // Build connections array for Edge
                const connections = [];
                const ec = this.veloConfig.edge_config;

                for (const [ifaceKey, config] of Object.entries(ec.interfaces || {})) {
                    if (config.target_device) {
                        connections.push({
                            target_device: config.target_device,
                            local_port: AddVelocloudWizard.EDGE_INTERFACE_MAP[ifaceKey] || 'lan',
                            target_port: config.target_port
                        });
                    }
                }

                if (connections.length > 0) {
                    requestPayload.connections = connections;
                }
            }

            const result = await NodeBuilderAPI.addVeloDevice(requestPayload);

            this.logMessage(log, 'Device created successfully!', 'success');
            this.logMessage(log, `VM: ${result.device?.name || this.veloConfig.name}`);
            this.logMessage(log, `Type: ${typeConfig.label}`);
            this.logMessage(log, `Mgmt IP: ${result.device?.mgmt_ip || this.veloConfig.mgmt_ip}`);

            // Handle reboot targets
            const rebootTargets = result.targets_need_reboot || [];
            const reusedSlots = result.targets_reused_slots || [];

            if (reusedSlots.length > 0) {
                this.logMessage(log, `Optimized: ${reusedSlots.join(', ')} reused existing interface slots`, 'success');
            }

            // Store result for later use
            this.createdDevice = result.device || { name: this.veloConfig.name, mgmt_ip: this.veloConfig.mgmt_ip };

            // Use shared DeviceRebootManager for reboot section
            const rebootManager = new DeviceRebootManager(this.targetDevices);

            // Show success state with reboot options
            content.innerHTML = `
                <div class="wizard-success">
                    <div class="success-icon velocloud-success">&#10004;</div>
                    <h3>${typeConfig.label} Created!</h3>
                    <p>The device <strong>${this.escapeHtml(this.veloConfig.name)}</strong> has been created.</p>
                    <p>It will be ready for SSH access within ~90 seconds.</p>
                    <div class="success-details">
                        <p>Device Type: <code>${typeConfig.label}</code></p>
                        <p>Management IP: <code>${this.escapeHtml(this.veloConfig.mgmt_ip)}</code></p>
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
            console.error('[AddVelocloudWizard] Error creating device:', error);

            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Create Device</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Check the nodebuilder service logs for more details.</p>
                </div>
            `;

            nextBtn.textContent = 'Retry';
            nextBtn.disabled = false;
            nextBtn.onclick = () => this.submitDevice();

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
window.AddVelocloudWizard = AddVelocloudWizard;

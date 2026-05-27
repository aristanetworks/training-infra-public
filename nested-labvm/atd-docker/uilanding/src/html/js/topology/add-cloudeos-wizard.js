/**
 * Add CloudEOS Wizard for ATL Topology
 *
 * Multi-step wizard for dynamically adding CloudEOS container nodes to running KVM labs.
 * CloudEOS nodes run as lightweight containers and share the same management network.
 *
 * Wizard Flow:
 * 1. Select device type (leaf, spine, router, pe, ce, etc.) and enter a name
 * 2. Select management IP address (from available pool)
 * 3. Configure connections (select target devices and ports)
 * 4. Review and confirm
 *
 * Note: Only available for KVM labs, disabled for container labs.
 *
 * Dependencies:
 * - NodeBuilderAPI (shared API service)
 * - DeviceRebootManager (shared reboot component)
 */

class AddCloudeosWizard {
    // Configuration constants
    static MAX_NAME_LENGTH = 32;
    static NAME_VALIDATION_DEBOUNCE_MS = 300;

    // Valid device types matching server-side VALID_DEVICE_TYPES
    static VALID_DEVICE_TYPES = [
        { value: 'leaf',       label: 'Leaf',            tier: 6 },
        { value: 'spine',      label: 'Spine',           tier: 5 },
        { value: 'router',     label: 'Router',          tier: 4 },
        { value: 'borderleaf', label: 'Borderleaf',      tier: 3 },
        { value: 'pe',         label: 'PE Router',       tier: 2 },
        { value: 'ce',         label: 'CE Router',       tier: 7 },
        { value: 'p',          label: 'P Router',        tier: 2 },
        { value: 'core',       label: 'Core',            tier: 1 },
        { value: 'dci',        label: 'DCI',             tier: 2 },
        { value: 'gw',         label: 'Gateway',         tier: 3 },
        { value: 'rr',         label: 'Route Reflector', tier: 2 },
        { value: 'host',       label: 'Host',            tier: 8 },
        { value: 'other',      label: 'Other',           tier: 9 }
    ];

    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;
        this.currentStep = 1;
        this.totalSteps = 4;

        // Wizard state
        this.cloudeosConfig = {
            name: '',
            ip: '',
            device_type: 'leaf',
            connections: []
        };

        // Cached data from API
        this.cloudeosStatus = null;
        this.availableIps = [];
        this.targetDevices = [];
        this.existingNodes = [];

        // Validation state
        this.nameValid = false;
        this.nameError = '';

        this.isSubmitting = false;
        this.isProgressing = false;

        // Event handler references for cleanup
        this.escapeHandler = null;

        // Track validation request for race condition prevention
        this.pendingValidationRequestId = 0;
        this.pendingValidationName = '';
    }

    /**
     * Check if add-cloudeos feature is available (KVM mode only)
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
            console.log('Add CloudEOS wizard not available for container labs');
            return;
        }

        // Pre-check: verify slots are available BEFORE showing wizard
        try {
            const response = await NodeBuilderAPI.fetchWithRetry('/td-api/nodes/cloudeos-status');
            if (!response.ok) {
                throw new Error('HTTP ' + response.status + ': ' + response.statusText);
            }
            const cloudeosStatus = await response.json();
            if (!cloudeosStatus.can_add_more) {
                this.showSlotError('CloudEOS Limit Reached',
                    'Maximum of ' + cloudeosStatus.max_allowed + ' CloudEOS nodes per topology.',
                    'Delete an existing CloudEOS node to add a new one.');
                return;
            }
        } catch (error) {
            console.error('[AddCloudeosWizard] Error checking CloudEOS slots:', error);
            this.showSlotError('Service Unavailable',
                'Unable to check CloudEOS availability.',
                'Make sure the nodebuilder service is running.');
            return;
        }

        // Reset state
        this.currentStep = 1;
        this.cloudeosConfig = {
            name: '',
            ip: '',
            device_type: 'leaf',
            connections: []
        };
        this.nameValid = false;
        this.nameError = '';
        this.isSubmitting = false;

        // Create overlay
        this.createOverlay();

        // Load data from API
        await this.loadAvailableData();

        // Auto-generate initial name based on default device type
        if (!this.cloudeosConfig.name) {
            this.cloudeosConfig.name = this.getNextAvailableName(this.cloudeosConfig.device_type);
        }

        // Render first step
        this.renderStep();
    }

    /**
     * Show a slot/limit error without opening the full wizard
     */
    showSlotError(title, message, hint) {
        const overlay = document.createElement('div');
        overlay.className = 'add-node-wizard-overlay';

        const modal = document.createElement('div');
        modal.className = 'add-node-wizard';
        modal.style.maxWidth = '450px';

        const header = document.createElement('div');
        header.className = 'wizard-header';
        const h2 = document.createElement('h2');
        h2.textContent = title;
        const closeBtn = document.createElement('button');
        closeBtn.className = 'wizard-close-btn';
        closeBtn.title = 'Close';
        closeBtn.textContent = '\u00d7';
        header.appendChild(h2);
        header.appendChild(closeBtn);

        const contentDiv = document.createElement('div');
        contentDiv.className = 'wizard-content';
        const errorDiv = document.createElement('div');
        errorDiv.className = 'wizard-error';
        const icon = document.createElement('div');
        icon.className = 'error-icon';
        icon.textContent = '\u26a0';
        const h3 = document.createElement('h3');
        h3.textContent = title;
        const p1 = document.createElement('p');
        p1.textContent = message;
        const p2 = document.createElement('p');
        p2.className = 'error-hint';
        p2.textContent = hint;
        errorDiv.appendChild(icon);
        errorDiv.appendChild(h3);
        errorDiv.appendChild(p1);
        errorDiv.appendChild(p2);
        contentDiv.appendChild(errorDiv);

        const footer = document.createElement('div');
        footer.className = 'wizard-footer';
        const spacer = document.createElement('div');
        spacer.className = 'wizard-footer-spacer';
        const okBtn = document.createElement('button');
        okBtn.className = 'wizard-btn wizard-btn-primary wizard-close-error-btn';
        okBtn.textContent = 'OK';
        footer.appendChild(spacer);
        footer.appendChild(okBtn);

        modal.appendChild(header);
        modal.appendChild(contentDiv);
        modal.appendChild(footer);
        overlay.appendChild(modal);

        closeBtn.addEventListener('click', () => overlay.remove());
        okBtn.addEventListener('click', () => overlay.remove());
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

        // Build progress steps
        const steps = [
            { num: 1, label: 'Type \u0026 Name' },
            { num: 2, label: 'IP Address' },
            { num: 3, label: 'Connections' },
            { num: 4, label: 'Review' }
        ];

        let stepsHtml = '';
        steps.forEach((step, index) => {
            if (index > 0) {
                stepsHtml += '<div class="progress-connector"></div>';
            }
            const activeClass = step.num === 1 ? ' active' : '';
            stepsHtml += '<div class="progress-step' + activeClass + '" data-step="' + step.num + '">' +
                '<span class="step-number">' + step.num + '</span>' +
                '<span class="step-label">' + step.label + '</span>' +
                '</div>';
        });

        overlay.innerHTML = '<div class="add-node-wizard add-cloudeos-wizard">' +
            '<div class="wizard-header">' +
            '<h2>Add CloudEOS Node</h2>' +
            '<button class="wizard-close-btn" title="Close">\u00d7</button>' +
            '</div>' +
            '<div class="wizard-progress"><div class="progress-steps">' + stepsHtml + '</div></div>' +
            '<div class="wizard-content"></div>' +
            '<div class="wizard-footer">' +
            '<button class="wizard-btn wizard-btn-secondary wizard-back-btn" style="display: none;">Back</button>' +
            '<div class="wizard-footer-spacer"></div>' +
            '<button class="wizard-btn wizard-btn-secondary wizard-cancel-btn">Cancel</button>' +
            '<button class="wizard-btn wizard-btn-primary wizard-next-btn">Next</button>' +
            '</div>' +
            '</div>';

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

        const loadingDiv = document.createElement('div');
        loadingDiv.className = 'wizard-loading';
        loadingDiv.innerHTML = '<div class="spinner"></div><p>Loading available resources...</p>';
        content.innerHTML = '';
        content.appendChild(loadingDiv);

        try {
            // Clear cache before loading to ensure fresh data
            NodeBuilderAPI.invalidateCache('available-ips');
            NodeBuilderAPI.invalidateCache('target-devices');
            NodeBuilderAPI.invalidateCache('existing-nodes');

            const [availableIps, targetDevices, existingNodes] = await Promise.all([
                NodeBuilderAPI.getAvailableIps(),
                NodeBuilderAPI.getTargetDevices(),
                NodeBuilderAPI.getExistingNodes()
            ]);

            this.availableIps = Array.isArray(availableIps) ? availableIps : [];
            this.targetDevices = Array.isArray(targetDevices) ? targetDevices : [];
            this.existingNodes = Array.isArray(existingNodes) ? existingNodes : [];

        } catch (error) {
            console.error('[AddCloudeosWizard] Error loading wizard data:', error);

            content.innerHTML = '';
            const errDiv = document.createElement('div');
            errDiv.className = 'wizard-error';

            const icon = document.createElement('div');
            icon.className = 'error-icon';
            icon.textContent = '\u2718';

            const h3 = document.createElement('h3');
            h3.textContent = 'Failed to Load Resources';

            const p = document.createElement('p');
            p.textContent = error.message;

            const hint = document.createElement('p');
            hint.className = 'error-hint';
            hint.textContent = 'Make sure the nodebuilder service is running.';

            const retryBtn = document.createElement('button');
            retryBtn.className = 'wizard-btn wizard-btn-primary wizard-retry-btn';
            retryBtn.textContent = 'Retry';
            retryBtn.addEventListener('click', () => this.loadAvailableData());

            errDiv.appendChild(icon);
            errDiv.appendChild(h3);
            errDiv.appendChild(p);
            errDiv.appendChild(hint);
            errDiv.appendChild(retryBtn);
            content.appendChild(errDiv);
            return;
        }

        // Check if we have any available IPs
        if (this.availableIps.length === 0) {
            content.innerHTML = '';
            const errDiv = document.createElement('div');
            errDiv.className = 'wizard-error';

            const icon = document.createElement('div');
            icon.className = 'error-icon';
            icon.textContent = '\u26a0';

            const h3 = document.createElement('h3');
            h3.textContent = 'No Available IP Addresses';

            const p = document.createElement('p');
            p.textContent = 'All IPs from the DHCP pool are currently in use.';

            const retryBtn = document.createElement('button');
            retryBtn.className = 'wizard-btn wizard-btn-secondary wizard-retry-btn';
            retryBtn.textContent = 'Refresh';
            retryBtn.addEventListener('click', () => this.loadAvailableData());

            errDiv.appendChild(icon);
            errDiv.appendChild(h3);
            errDiv.appendChild(p);
            errDiv.appendChild(retryBtn);
            content.appendChild(errDiv);
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
            nextBtn.textContent = 'Create CloudEOS Node';
            nextBtn.classList.add('wizard-btn-create');
        } else {
            nextBtn.textContent = 'Next';
            nextBtn.classList.remove('wizard-btn-create');
        }

        // Render step content
        content.innerHTML = '';
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
     * Step 1: Device Type and Name Selection
     * Builds the form using DOM methods to avoid innerHTML with user-controlled data.
     */
    renderNameStep(content) {
        // Auto-generate name if not yet set
        if (!this.cloudeosConfig.name) {
            this.cloudeosConfig.name = this.getNextAvailableName(this.cloudeosConfig.device_type);
        }

        const stepDiv = document.createElement('div');
        stepDiv.className = 'wizard-step wizard-step-name';

        const h3 = document.createElement('h3');
        h3.textContent = 'Select Device Type and Name';

        const desc = document.createElement('p');
        desc.className = 'step-description';
        desc.textContent = 'Select the CloudEOS device type and assign a name. The name is auto-generated based on the type but can be customized.';

        // Device type group
        const typeGroup = document.createElement('div');
        typeGroup.className = 'form-group';

        const typeLabel = document.createElement('label');
        typeLabel.htmlFor = 'cloudeos-device-type';
        typeLabel.textContent = 'Device Type';

        const typeSelect = document.createElement('select');
        typeSelect.id = 'cloudeos-device-type';
        typeSelect.className = 'form-input';

        AddCloudeosWizard.VALID_DEVICE_TYPES
            .slice().sort((a, b) => (a.tier || 9) - (b.tier || 9))
            .forEach(dt => {
                const opt = document.createElement('option');
                opt.value = dt.value;
                opt.textContent = dt.label + ' (Tier ' + dt.tier + ')';
                if (dt.value === this.cloudeosConfig.device_type) opt.selected = true;
                typeSelect.appendChild(opt);
            });

        const typeHint = document.createElement('p');
        typeHint.className = 'field-hint';
        typeHint.textContent = 'Device type determines diagram placement and naming convention.';

        typeGroup.appendChild(typeLabel);
        typeGroup.appendChild(typeSelect);
        typeGroup.appendChild(typeHint);

        // Name group
        const nameGroup = document.createElement('div');
        nameGroup.className = 'form-group';

        const nameLabel = document.createElement('label');
        nameLabel.htmlFor = 'cloudeos-name';
        nameLabel.textContent = 'Device Name';

        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.id = 'cloudeos-name';
        nameInput.className = 'form-input';
        nameInput.value = this.cloudeosConfig.name;
        nameInput.maxLength = AddCloudeosWizard.MAX_NAME_LENGTH;
        nameInput.placeholder = 'e.g. leaf3';
        nameInput.setAttribute('aria-describedby', 'cloudeos-name-validation');

        const validationMsg = document.createElement('div');
        validationMsg.id = 'cloudeos-name-validation';
        validationMsg.className = 'validation-message' +
            (this.nameError ? ' error' : this.nameValid ? ' success' : '');
        validationMsg.setAttribute('role', 'alert');
        validationMsg.setAttribute('aria-live', 'polite');
        validationMsg.textContent = this.nameError || (this.nameValid ? 'Name is available' : '');

        const nameHint = document.createElement('p');
        nameHint.className = 'field-hint';
        nameHint.textContent = 'Must start with a letter. Letters, numbers, dashes and underscores allowed.';

        nameGroup.appendChild(nameLabel);
        nameGroup.appendChild(nameInput);
        nameGroup.appendChild(validationMsg);
        nameGroup.appendChild(nameHint);

        // Info box
        const infoBox = document.createElement('div');
        infoBox.className = 'cloudeos-info-box';

        const infoH4 = document.createElement('h4');
        infoH4.textContent = 'About CloudEOS';

        const infoP = document.createElement('p');
        infoP.textContent = 'CloudEOS nodes run as lightweight EOS containers sharing the management network. They support the full EOS feature set and integrate with CVP.';

        const badge = document.createElement('div');
        badge.className = 'cloudeos-limit-badge';
        const existingNames = this.existingNodes
            .map(n => n.name || Object.keys(n)[0])
            .slice(0, 8)
            .join(', ');
        badge.textContent = 'Existing nodes: ' + existingNames +
            (this.existingNodes.length > 8 ? '...' : '');

        infoBox.appendChild(infoH4);
        infoBox.appendChild(infoP);
        infoBox.appendChild(badge);

        stepDiv.appendChild(h3);
        stepDiv.appendChild(desc);
        stepDiv.appendChild(typeGroup);
        stepDiv.appendChild(nameGroup);
        stepDiv.appendChild(infoBox);
        content.appendChild(stepDiv);

        typeSelect.focus();

        // Handle device type change - auto-generate name
        typeSelect.addEventListener('change', (e) => {
            this.cloudeosConfig.device_type = e.target.value;
            this.cloudeosConfig.name = this.getNextAvailableName(e.target.value);
            nameInput.value = this.cloudeosConfig.name;
            this.validateName();
        });

        // Handle name input with debounced validation
        let debounceTimer = null;
        nameInput.addEventListener('input', (e) => {
            this.cloudeosConfig.name = e.target.value;
            this.nameValid = false;
            this.nameError = '';
            clearTimeout(debounceTimer);
            debounceTimer = setTimeout(() => this.validateName(), AddCloudeosWizard.NAME_VALIDATION_DEBOUNCE_MS);
            this.updateNextButtonState();
        });

        // Allow Enter to advance
        nameInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && this.nameValid) {
                this.nextStep();
            }
        });

        // Validate initial auto-generated name
        this.validateName();
    }

    /**
     * Get the next available name for a device type.
     * Scans existing nodes and finds the next sequential number.
     */
    getNextAvailableName(deviceType) {
        if (!deviceType) return '';

        const typeLower = deviceType.toLowerCase();
        const existingNumbers = [];

        for (const node of this.existingNodes) {
            const nodeName = (node.name || Object.keys(node)[0] || '').toLowerCase();
            if (nodeName.startsWith(typeLower)) {
                const suffix = nodeName.substring(typeLower.length);
                const num = parseInt(suffix, 10);
                if (!isNaN(num)) {
                    existingNumbers.push(num);
                }
            }
        }

        let nextNum = 1;
        if (existingNumbers.length > 0) {
            existingNumbers.sort((a, b) => a - b);
            for (const num of existingNumbers) {
                if (num === nextNum) {
                    nextNum++;
                } else if (num > nextNum) {
                    break;
                }
            }
        }

        return deviceType + nextNum;
    }

    /**
     * Validate device name via API
     */
    async validateName() {
        const name = this.cloudeosConfig.name;
        const validationMsg = this.overlay ? this.overlay.querySelector('#cloudeos-name-validation') : null;
        if (!validationMsg) return;

        if (!name) {
            this.nameValid = false;
            this.nameError = '';
            validationMsg.className = 'validation-message';
            validationMsg.textContent = '';
            this.updateNextButtonState();
            return;
        }

        // Client-side validation
        if (!/^[a-zA-Z][a-zA-Z0-9_-]*$/.test(name)) {
            this.nameValid = false;
            this.nameError = 'Name must start with a letter and contain only letters, numbers, dashes, and underscores';
            validationMsg.className = 'validation-message error';
            validationMsg.textContent = this.nameError;
            this.updateNextButtonState();
            return;
        }

        if (name.length > AddCloudeosWizard.MAX_NAME_LENGTH) {
            this.nameValid = false;
            this.nameError = 'Name must be ' + AddCloudeosWizard.MAX_NAME_LENGTH + ' characters or less';
            validationMsg.className = 'validation-message error';
            validationMsg.textContent = this.nameError;
            this.updateNextButtonState();
            return;
        }

        // Server-side validation
        const expectedRequestId = NodeBuilderAPI.getValidationRequestId() + 1;
        this.pendingValidationRequestId = expectedRequestId;
        this.pendingValidationName = name;

        try {
            const result = await NodeBuilderAPI.validateNode(name);

            // Ignore stale responses
            if (result.requestId !== this.pendingValidationRequestId ||
                result.validatedName !== this.pendingValidationName) {
                console.log('[AddCloudeosWizard] Ignoring stale validation response for:', result.validatedName);
                return;
            }

            if (result.valid) {
                this.nameValid = true;
                this.nameError = '';
                validationMsg.className = 'validation-message success';
                validationMsg.textContent = 'Name is available';
            } else {
                this.nameValid = false;
                this.nameError = (result.errors && result.errors[0]) || 'Invalid name';
                validationMsg.className = 'validation-message error';
                validationMsg.textContent = this.nameError;
            }
        } catch (error) {
            console.error('[AddCloudeosWizard] Error validating name:', error);
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
        const stepDiv = document.createElement('div');
        stepDiv.className = 'wizard-step wizard-step-ip';

        const h3 = document.createElement('h3');
        h3.textContent = 'Select Management IP';

        const desc = document.createElement('p');
        desc.className = 'step-description';
        desc.textContent = 'Choose an available IP address for management access. This will be the CloudEOS node\'s management interface address.';

        const formGroup = document.createElement('div');
        formGroup.className = 'form-group';

        const label = document.createElement('label');
        label.htmlFor = 'cloudeos-ip';
        label.textContent = 'Management IP Address';

        const select = document.createElement('select');
        select.id = 'cloudeos-ip';
        select.className = 'form-select';

        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = 'Select an IP address...';
        select.appendChild(defaultOpt);

        this.availableIps.forEach(entry => {
            const opt = document.createElement('option');
            opt.value = entry.ip;
            opt.dataset.hostname = entry.hostname || '';
            opt.textContent = entry.ip + (entry.hostname ? ' (' + entry.hostname + ')' : '');
            select.appendChild(opt);
        });

        // Restore previous selection
        if (this.cloudeosConfig.ip) {
            select.value = this.cloudeosConfig.ip;
        }

        formGroup.appendChild(label);
        formGroup.appendChild(select);

        const ipInfo = document.createElement('div');
        ipInfo.className = 'ip-info';
        const ipInfoP = document.createElement('p');
        const strong = document.createElement('strong');
        strong.textContent = this.availableIps.length;
        ipInfoP.appendChild(strong);
        ipInfoP.appendChild(document.createTextNode(' IP addresses available in the management pool'));
        ipInfo.appendChild(ipInfoP);

        const infoBox = document.createElement('div');
        infoBox.className = 'cloudeos-info-box';
        const infoH4 = document.createElement('h4');
        infoH4.textContent = 'Management Interface';
        const infoP = document.createElement('p');
        infoP.textContent = 'The selected IP will be assigned to the CloudEOS management interface (Ma1). SSH access will be available at this address once the node boots.';
        infoBox.appendChild(infoH4);
        infoBox.appendChild(infoP);

        stepDiv.appendChild(h3);
        stepDiv.appendChild(desc);
        stepDiv.appendChild(formGroup);
        stepDiv.appendChild(ipInfo);
        stepDiv.appendChild(infoBox);
        content.appendChild(stepDiv);

        select.addEventListener('change', (e) => {
            this.cloudeosConfig.ip = e.target.value;
            this.updateNextButtonState();
        });
    }

    /**
     * Step 3: Connection Configuration
     */
    renderConnectionsStep(content) {
        const stepDiv = document.createElement('div');
        stepDiv.className = 'wizard-step wizard-step-connections';

        const h3 = document.createElement('h3');
        h3.textContent = 'Configure Connections';

        const desc = document.createElement('p');
        desc.className = 'step-description';
        desc.textContent = 'Add connections to existing devices. CloudEOS supports multiple connections. Connections are optional and can be added later.';

        const hint = document.createElement('div');
        hint.className = 'connection-hint';
        hint.innerHTML = '<strong>Tip:</strong> Interfaces are automatically assigned starting from Ethernet1. You can add multiple connections, including multiple connections to the same device.';

        const formGroup = document.createElement('div');
        formGroup.className = 'form-group';

        const label = document.createElement('label');
        label.htmlFor = 'cloudeos-target-device';
        label.textContent = 'Add Connection To';

        const addRow = document.createElement('div');
        addRow.className = 'add-connection-row';

        const deviceSelect = document.createElement('select');
        deviceSelect.id = 'cloudeos-target-device';
        deviceSelect.className = 'form-select';

        const defaultOpt = document.createElement('option');
        defaultOpt.value = '';
        defaultOpt.textContent = 'Select a device...';
        deviceSelect.appendChild(defaultOpt);

        this.targetDevices.forEach(device => {
            const opt = document.createElement('option');
            opt.value = device.name;
            opt.dataset.nextPort = device.next_available_port;
            opt.textContent = device.name + ' (next: ' + device.next_available_port + ')';
            deviceSelect.appendChild(opt);
        });

        const addBtn = document.createElement('button');
        addBtn.type = 'button';
        addBtn.className = 'wizard-btn wizard-btn-primary add-connection-btn';
        addBtn.disabled = true;
        addBtn.textContent = 'Add';

        addRow.appendChild(deviceSelect);
        addRow.appendChild(addBtn);
        formGroup.appendChild(label);
        formGroup.appendChild(addRow);

        const summary = document.createElement('div');
        summary.className = 'connection-summary';

        const summaryH4 = document.createElement('h4');
        summaryH4.textContent = 'Connections (' + this.cloudeosConfig.connections.length + ')';

        const listDiv = document.createElement('div');
        listDiv.className = 'connection-list';
        listDiv.innerHTML = this.renderConnectionSummary();

        summary.appendChild(summaryH4);
        summary.appendChild(listDiv);

        stepDiv.appendChild(h3);
        stepDiv.appendChild(desc);
        stepDiv.appendChild(hint);
        stepDiv.appendChild(formGroup);
        stepDiv.appendChild(summary);
        content.appendChild(stepDiv);

        // Enable/disable add button
        deviceSelect.addEventListener('change', () => {
            addBtn.disabled = !deviceSelect.value;
        });

        // Handle add connection
        addBtn.addEventListener('click', () => {
            const selectedOption = deviceSelect.selectedOptions[0];
            if (!selectedOption || !selectedOption.value) return;

            const deviceName = selectedOption.value;
            const nextPort = selectedOption.dataset.nextPort || 'Ethernet1';

            this.cloudeosConfig.connections.push({
                target_device: deviceName,
                target_port: nextPort,
                local_port: 'Ethernet' + (this.cloudeosConfig.connections.length + 1)
            });

            this.updateConnectionsSummary(content);

            deviceSelect.value = '';
            addBtn.disabled = true;
        });

        this.attachRemoveHandlers(content);
    }

    /**
     * Update the connections summary display
     */
    updateConnectionsSummary(content) {
        const summaryContainer = content.querySelector('.connection-summary');
        if (!summaryContainer) return;

        // Use textContent for the h4 and innerHTML for the list (escapeHtml guards all values)
        const h4 = summaryContainer.querySelector('h4');
        if (h4) h4.textContent = 'Connections (' + this.cloudeosConfig.connections.length + ')';

        const listDiv = summaryContainer.querySelector('.connection-list');
        if (listDiv) listDiv.innerHTML = this.renderConnectionSummary();

        this.attachRemoveHandlers(content);
        this.updateNextButtonState();
    }

    /**
     * Render connection summary list with remove buttons
     * All values run through escapeHtml before insertion into innerHTML.
     */
    renderConnectionSummary() {
        if (this.cloudeosConfig.connections.length === 0) {
            return '<p class="no-connections">No connections added yet</p>';
        }

        return this.cloudeosConfig.connections.map((conn, index) => {
            const localPort = this.escapeHtml('Ethernet' + (index + 1));
            const targetDevice = this.escapeHtml(conn.target_device);
            const targetPort = this.escapeHtml(conn.target_port);
            return '<div class="connection-item" data-index="' + index + '">' +
                '<span class="local-port">' + localPort + '</span>' +
                '<span class="connection-arrow">\u21d4</span>' +
                '<span class="remote-info">' + targetDevice + ' (' + targetPort + ')</span>' +
                '<button type="button" class="remove-connection-btn" data-index="' + index + '" title="Remove connection">\u00d7</button>' +
                '</div>';
        }).join('');
    }

    /**
     * Attach remove handlers to connection items
     */
    attachRemoveHandlers(content) {
        content.querySelectorAll('.remove-connection-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const index = parseInt(btn.dataset.index, 10);
                if (index >= 0 && index < this.cloudeosConfig.connections.length) {
                    this.cloudeosConfig.connections.splice(index, 1);
                    // Recalculate local_port for remaining connections
                    this.cloudeosConfig.connections.forEach((conn, i) => {
                        conn.local_port = 'Ethernet' + (i + 1);
                    });
                    this.updateConnectionsSummary(content);
                }
            });
        });
    }

    /**
     * Step 4: Review and Confirm
     */
    renderReviewStep(content) {
        const stepDiv = document.createElement('div');
        stepDiv.className = 'wizard-step wizard-step-review';

        const h3 = document.createElement('h3');
        h3.textContent = 'Review Configuration';

        const desc = document.createElement('p');
        desc.className = 'step-description';
        desc.textContent = 'Please review the configuration before creating the CloudEOS node.';

        const deviceTypeInfo = AddCloudeosWizard.VALID_DEVICE_TYPES.find(
            dt => dt.value === this.cloudeosConfig.device_type
        );
        const deviceTypeLabel = deviceTypeInfo
            ? deviceTypeInfo.label + ' (Tier ' + deviceTypeInfo.tier + ')'
            : this.cloudeosConfig.device_type;

        // Device info section
        const deviceSection = document.createElement('div');
        deviceSection.className = 'review-section';

        const deviceH4 = document.createElement('h4');
        deviceH4.textContent = 'Node Information';

        const deviceTable = document.createElement('table');
        deviceTable.className = 'review-table';

        const deviceRows = [
            ['Device Name', this.cloudeosConfig.name],
            ['Device Type', deviceTypeLabel],
            ['Management IP', this.cloudeosConfig.ip],
            ['Platform', 'CloudEOS (cEOS Container)']
        ];

        deviceRows.forEach(([th, td]) => {
            const tr = document.createElement('tr');
            const thEl = document.createElement('th');
            thEl.textContent = th;
            const tdEl = document.createElement('td');
            tdEl.textContent = td;
            tr.appendChild(thEl);
            tr.appendChild(tdEl);
            deviceTable.appendChild(tr);
        });

        deviceSection.appendChild(deviceH4);
        deviceSection.appendChild(deviceTable);

        // Connections section
        const connSection = document.createElement('div');
        connSection.className = 'review-section';

        const connH4 = document.createElement('h4');
        connH4.textContent = 'Connections (' + this.cloudeosConfig.connections.length + ')';

        const connTable = document.createElement('table');
        connTable.className = 'review-table connections-table';

        const thead = document.createElement('thead');
        const headerRow = document.createElement('tr');
        ['Local Port', 'Target Device', 'Target Port'].forEach(text => {
            const th = document.createElement('th');
            th.textContent = text;
            headerRow.appendChild(th);
        });
        thead.appendChild(headerRow);
        connTable.appendChild(thead);

        const tbody = document.createElement('tbody');
        if (this.cloudeosConfig.connections.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 3;
            td.className = 'no-data';
            td.textContent = 'No connections configured';
            tr.appendChild(td);
            tbody.appendChild(tr);
        } else {
            this.cloudeosConfig.connections.forEach((conn, index) => {
                const tr = document.createElement('tr');
                [
                    'Ethernet' + (index + 1),
                    conn.target_device,
                    conn.target_port
                ].forEach(text => {
                    const td = document.createElement('td');
                    td.textContent = text;
                    tr.appendChild(td);
                });
                tbody.appendChild(tr);
            });
        }
        connTable.appendChild(tbody);

        connSection.appendChild(connH4);
        connSection.appendChild(connTable);

        // Notes section
        const notesDiv = document.createElement('div');
        notesDiv.className = 'review-notes';

        const notesH4 = document.createElement('h4');
        notesH4.textContent = 'What happens next:';

        const notesList = document.createElement('ul');
        [
            'A new CloudEOS container will be created with the specified configuration',
            'Virtual interfaces will be created for each connection',
            'The node will boot and be accessible via SSH within ~60 seconds',
            'The node will register with CVP automatically via ZTP',
            'The topology diagram will update automatically'
        ].forEach(text => {
            const li = document.createElement('li');
            li.textContent = text;
            notesList.appendChild(li);
        });

        notesDiv.appendChild(notesH4);
        notesDiv.appendChild(notesList);

        stepDiv.appendChild(h3);
        stepDiv.appendChild(desc);
        stepDiv.appendChild(deviceSection);
        stepDiv.appendChild(connSection);
        stepDiv.appendChild(notesDiv);
        content.appendChild(stepDiv);
    }

    /**
     * Update next button enabled/disabled state
     */
    updateNextButtonState() {
        const nextBtn = this.overlay ? this.overlay.querySelector('.wizard-next-btn') : null;
        if (!nextBtn) return;

        let canProceed = false;

        switch (this.currentStep) {
            case 1:
                canProceed = this.nameValid && this.cloudeosConfig.name.length > 0;
                break;
            case 2:
                canProceed = this.cloudeosConfig.ip !== '';
                break;
            case 3:
                // Connections are optional
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
        if (this.isProgressing || this.isSubmitting) return;

        if (this.currentStep === this.totalSteps) {
            await this.submitCloudeos();
            return;
        }

        this.isProgressing = true;
        try {
            this.currentStep++;
            this.renderStep();
        } finally {
            this.isProgressing = false;
        }
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
     * Submit the CloudEOS creation request
     */
    async submitCloudeos() {
        if (this.isSubmitting) return;

        this.isSubmitting = true;
        const nextBtn = this.overlay.querySelector('.wizard-next-btn');
        nextBtn.textContent = 'Creating...';
        nextBtn.disabled = true;

        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = '';

        const creatingDiv = document.createElement('div');
        creatingDiv.className = 'wizard-creating';

        const spinner = document.createElement('div');
        spinner.className = 'spinner large';

        const creatingH3 = document.createElement('h3');
        creatingH3.textContent = 'Creating CloudEOS Node...';

        const creatingP = document.createElement('p');
        creatingP.textContent = 'This may take up to 60 seconds. Please wait.';

        const log = document.createElement('div');
        log.className = 'creation-log';

        creatingDiv.appendChild(spinner);
        creatingDiv.appendChild(creatingH3);
        creatingDiv.appendChild(creatingP);
        creatingDiv.appendChild(log);
        content.appendChild(creatingDiv);

        try {
            this.logMessage(log, 'Sending request to nodebuilder service...');

            const payload = {
                name: this.cloudeosConfig.name,
                ip: this.cloudeosConfig.ip,
                device_type: this.cloudeosConfig.device_type,
                connections: this.cloudeosConfig.connections.map((conn, index) => ({
                    target_device: conn.target_device,
                    target_port: conn.target_port,
                    local_port: 'Ethernet' + (index + 1)
                }))
            };

            const response = await fetch('/td-api/nodes/add-cloudeos', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            const result = await response.json();
            if (!response.ok) {
                throw new Error(result.error || 'Failed to create CloudEOS node');
            }

            // Invalidate caches after successful creation
            NodeBuilderAPI.invalidateCache('available-ips');
            NodeBuilderAPI.invalidateCache('existing-nodes');
            NodeBuilderAPI.invalidateCache('target-devices');

            this.logMessage(log, 'CloudEOS node created successfully!', 'success');
            this.logMessage(log, 'Node: ' + (result.node ? result.node.name : this.cloudeosConfig.name));
            this.logMessage(log, 'Management IP: ' + (result.node ? result.node.ip : this.cloudeosConfig.ip));

            // Use API-provided reboot info
            const rebootTargets = result.targets_need_reboot || [];
            const reusedSlots = result.targets_reused_slots || [];

            if (reusedSlots.length > 0) {
                this.logMessage(log, 'Optimized: ' + reusedSlots.join(', ') + ' reused existing interface slots (no reboot needed)', 'success');
            }

            // Use shared DeviceRebootManager for reboot section
            const rebootManager = new DeviceRebootManager(this.targetDevices);

            // Build success view using DOM methods
            content.innerHTML = '';
            const successDiv = document.createElement('div');
            successDiv.className = 'wizard-success';

            const successIcon = document.createElement('div');
            successIcon.className = 'success-icon';
            successIcon.textContent = '\u2714';

            const successH3 = document.createElement('h3');
            successH3.textContent = 'CloudEOS Node Created!';

            const successP1 = document.createElement('p');
            const strongName = document.createElement('strong');
            strongName.textContent = this.cloudeosConfig.name;
            successP1.append('The node ');
            successP1.appendChild(strongName);
            successP1.append(' has been created.');

            const successP2 = document.createElement('p');
            successP2.textContent = 'It will be ready for SSH access within ~60 seconds.';

            const detailsDiv = document.createElement('div');
            detailsDiv.className = 'success-details';

            const ipP = document.createElement('p');
            ipP.append('Management IP: ');
            const ipCode = document.createElement('code');
            ipCode.textContent = this.cloudeosConfig.ip;
            ipP.appendChild(ipCode);

            const typeP = document.createElement('p');
            typeP.append('Device Type: ');
            const typeCode = document.createElement('code');
            typeCode.textContent = this.cloudeosConfig.device_type;
            typeP.appendChild(typeCode);

            detailsDiv.appendChild(ipP);
            detailsDiv.appendChild(typeP);

            if (this.cloudeosConfig.connections.length > 0) {
                const connP = document.createElement('p');
                connP.textContent = 'Connections: ' + this.cloudeosConfig.connections.map(c => c.target_device).join(', ');
                detailsDiv.appendChild(connP);
            }

            successDiv.appendChild(successIcon);
            successDiv.appendChild(successH3);
            successDiv.appendChild(successP1);
            successDiv.appendChild(successP2);
            successDiv.appendChild(detailsDiv);

            // Reused slots info
            if (reusedSlots.length > 0) {
                const reusedP = document.createElement('p');
                reusedP.className = 'reused-slots-info';
                const infoIcon = document.createElement('span');
                infoIcon.className = 'info-icon';
                infoIcon.textContent = '\u24d8';
                const boldSlots = document.createElement('strong');
                boldSlots.textContent = reusedSlots.join(', ');
                reusedP.appendChild(infoIcon);
                reusedP.append(' Reused interface slots on ');
                reusedP.appendChild(boldSlots);
                reusedP.append(' - no reboot needed for these devices.');
                successDiv.appendChild(reusedP);
            }

            content.appendChild(successDiv);

            // Inject reboot section if needed
            if (rebootTargets.length > 0) {
                successDiv.insertAdjacentHTML('beforeend', rebootManager.renderRebootSection(rebootTargets));
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
            console.error('[AddCloudeosWizard] Error creating CloudEOS node:', error);

            content.innerHTML = '';
            const errDiv = document.createElement('div');
            errDiv.className = 'wizard-error';

            const errIcon = document.createElement('div');
            errIcon.className = 'error-icon';
            errIcon.textContent = '\u2718';

            const errH3 = document.createElement('h3');
            errH3.textContent = 'Failed to Create CloudEOS Node';

            const errP = document.createElement('p');
            errP.textContent = error.message;

            const errHint = document.createElement('p');
            errHint.className = 'error-hint';
            errHint.textContent = 'Check the nodebuilder service logs for more details.';

            errDiv.appendChild(errIcon);
            errDiv.appendChild(errH3);
            errDiv.appendChild(errP);
            errDiv.appendChild(errHint);
            content.appendChild(errDiv);

            nextBtn.textContent = 'Retry';
            nextBtn.disabled = false;
            nextBtn.onclick = () => this.submitCloudeos();

            this.isSubmitting = false;
        }
    }

    /**
     * Log a message to the creation log
     */
    logMessage(logElement, message, type) {
        type = type || 'info';
        const entry = document.createElement('div');
        entry.className = 'log-entry log-' + type;
        entry.textContent = '[' + new Date().toLocaleTimeString() + '] ' + message;
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
window.AddCloudeosWizard = AddCloudeosWizard;

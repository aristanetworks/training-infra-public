/**
 * Add Cluster Wizard for ATD Topology
 *
 * Wizard for adding pre-configured clusters of vEOS nodes to running KVM labs.
 * Uses cluster templates to create multiple interconnected nodes at once.
 *
 * Only available for KVM/vEOS labs, disabled for container labs.
 */

class AddClusterWizard {
    // Configuration constants
    static MAX_LATENCY_MS = 10000;
    static MAX_JITTER_MS = 500;
    static MAX_LOSS_PERCENT = 100;

    constructor(topologyManager) {
        this.topologyManager = topologyManager;
        this.overlay = null;

        // Wizard state
        this.templates = [];
        this.targetDevices = [];
        this.availableIps = [];
        this.selectedTemplate = null;
        this.namePrefix = '';
        this.externalConnections = [];
        this.impairments = {
            latency_ms: 0,
            jitter_ms: 0,
            loss_percent: 0
        };

        this.isSubmitting = false;

        // Event handler references for cleanup
        this.escapeHandler = null;
    }

    /**
     * Check if add-cluster feature is available (KVM mode only)
     */
    isAvailable() {
        const eventManager = this.topologyManager?.eventManager;
        if (eventManager && eventManager.isCeosLab) {
            return false;
        }
        return true;
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
     * Show the wizard
     */
    async show() {
        if (!this.isAvailable()) {
            console.log('[AddClusterWizard] Not available for container labs');
            return;
        }

        // Reset state
        this.selectedTemplate = null;
        this.namePrefix = '';
        this.externalConnections = [];
        this.impairments = { latency_ms: 0, jitter_ms: 0, loss_percent: 0 };
        this.isSubmitting = false;

        // Create overlay
        this.createOverlay();

        // Load data from API
        await this.loadData();
    }

    /**
     * Hide and cleanup the wizard
     */
    hide() {
        // Clean up escape handler
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
        overlay.id = 'add-cluster-wizard-overlay';

        overlay.innerHTML = `
            <div class="add-node-wizard cluster-wizard">
                <div class="wizard-header">
                    <h2>Add Node Cluster</h2>
                    <button class="wizard-close-btn" title="Close" aria-label="Close wizard">&times;</button>
                </div>
                <div class="wizard-content">
                    <div class="wizard-loading">
                        <div class="spinner"></div>
                        <p>Loading cluster templates...</p>
                    </div>
                </div>
                <div class="wizard-footer">
                    <div class="wizard-footer-spacer"></div>
                    <button class="wizard-btn wizard-btn-secondary wizard-cancel-btn">Cancel</button>
                    <button class="wizard-btn wizard-btn-primary wizard-create-btn" disabled>
                        Create Cluster
                    </button>
                </div>
            </div>
        `;

        // Event listeners
        overlay.querySelector('.wizard-close-btn').addEventListener('click', () => this.hide());
        overlay.querySelector('.wizard-cancel-btn').addEventListener('click', () => this.hide());
        overlay.querySelector('.wizard-create-btn').addEventListener('click', () => this.submitCluster());

        // Close on overlay click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hide();
            }
        });

        // Close on escape key - store reference for cleanup
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
     * Load data from API
     */
    async loadData() {
        const content = this.overlay.querySelector('.wizard-content');

        try {
            // Clear cache before loading to ensure fresh data
            NodeBuilderAPI.invalidateCache('available-ips');
            NodeBuilderAPI.invalidateCache('target-devices');
            NodeBuilderAPI.invalidateCache('cluster-templates');

            const data = await NodeBuilderAPI.loadClusterData();

            // Validate data structure
            if (!data || typeof data !== 'object') {
                throw new Error('Invalid response from server: expected data object');
            }

            this.templates = Array.isArray(data.templates) ? data.templates : [];
            this.targetDevices = Array.isArray(data.targetDevices) ? data.targetDevices : [];
            this.availableIps = Array.isArray(data.availableIps) ? data.availableIps : [];

            // Render the form
            this.renderForm();

        } catch (error) {
            console.error('[AddClusterWizard] Error loading data:', error);
            content.innerHTML = `
                <div class="wizard-error">
                    <div class="error-icon">&#10008;</div>
                    <h3>Failed to Load Data</h3>
                    <p>${this.escapeHtml(error.message)}</p>
                    <p class="error-hint">Make sure the nodebuilder service is running.</p>
                    <button class="wizard-btn wizard-btn-primary wizard-retry-btn">Retry</button>
                </div>
            `;
            // Add retry handler
            const retryBtn = content.querySelector('.wizard-retry-btn');
            if (retryBtn) {
                retryBtn.addEventListener('click', () => this.loadData());
            }
        }
    }

    /**
     * Render the main form
     */
    renderForm() {
        const content = this.overlay.querySelector('.wizard-content');

        // Build template options
        const templateOptions = this.templates.map(t => `
            <option value="${this.escapeHtml(t.id)}"
                    data-nodes="${t.node_count}"
                    data-description="${this.escapeHtml(t.description)}">
                ${this.escapeHtml(t.display_name)} (${t.node_count} nodes)
            </option>
        `).join('');

        content.innerHTML = `
            <div class="wizard-step">
                <div class="form-group">
                    <label for="cluster-template">Cluster Template</label>
                    <select id="cluster-template" class="form-select" aria-describedby="template-description">
                        <option value="">Select a template...</option>
                        ${templateOptions}
                    </select>
                    <p id="template-description" class="validation-message"></p>
                </div>

                <!-- Template Preview Section -->
                <div id="template-preview-section" class="cluster-template-preview hidden">
                    <h4>Template Preview</h4>
                    <div id="template-preview-content" class="preview-placeholder">
                        Select a template to see the preview
                    </div>
                </div>

                <div class="form-group">
                    <label for="cluster-prefix">Name Prefix (optional)</label>
                    <input type="text" id="cluster-prefix" class="form-input"
                           placeholder="e.g., dc1 creates dc1_isp1, dc1_isp2"
                           maxlength="16"
                           aria-describedby="prefix-hint">
                    <p id="prefix-hint" class="validation-message">Prefix added to all node names in the cluster</p>
                </div>

                <div id="external-connections-section" class="form-group hidden">
                    <label>External Connections</label>
                    <p class="step-description">Connect cluster nodes to existing topology devices</p>
                    <div id="external-connections-list"></div>
                </div>

                <div id="impairments-section" class="form-group hidden">
                    <label>Link Impairments (Internal Links)</label>
                    <p class="step-description">Apply network impairments to connections between cluster nodes</p>
                    <div class="cluster-impairment-grid">
                        <div class="cluster-impairment-input">
                            <label for="cluster-latency">Latency (ms)</label>
                            <input type="number" id="cluster-latency" class="form-input"
                                   min="0" max="${AddClusterWizard.MAX_LATENCY_MS}" step="1" placeholder="0"
                                   aria-describedby="latency-range">
                        </div>
                        <div class="cluster-impairment-input">
                            <label for="cluster-jitter">Jitter (ms)</label>
                            <input type="number" id="cluster-jitter" class="form-input"
                                   min="0" max="${AddClusterWizard.MAX_JITTER_MS}" step="1" placeholder="0"
                                   aria-describedby="jitter-range">
                        </div>
                        <div class="cluster-impairment-input">
                            <label for="cluster-loss">Packet Loss (%)</label>
                            <input type="number" id="cluster-loss" class="form-input"
                                   min="0" max="${AddClusterWizard.MAX_LOSS_PERCENT}" step="0.1" placeholder="0"
                                   aria-describedby="loss-range">
                        </div>
                    </div>
                    <p id="default-impairments" class="validation-message"></p>
                </div>

                <div id="cluster-summary" class="cluster-summary-box hidden">
                    <h4>Summary</h4>
                    <p>Available IPs: <strong>${this.availableIps.length}</strong></p>
                    <p id="nodes-to-create"></p>
                    <p id="internal-connections-info"></p>
                </div>
            </div>
        `;

        // Setup event handlers
        this.setupFormHandlers();
    }

    /**
     * Setup form event handlers
     */
    setupFormHandlers() {
        const templateSelect = this.overlay.querySelector('#cluster-template');
        const prefixInput = this.overlay.querySelector('#cluster-prefix');

        // Template selection
        templateSelect.addEventListener('change', () => {
            const templateId = templateSelect.value;
            this.selectedTemplate = this.templates.find(t => t.id === templateId) || null;
            this.updateFormForTemplate();
            this.updateCreateButtonState();
        });

        // Prefix input
        prefixInput.addEventListener('input', (e) => {
            this.namePrefix = e.target.value.trim();
            this.updateNodeNames();
        });
    }

    /**
     * Update form when template is selected
     */
    updateFormForTemplate() {
        const descriptionEl = this.overlay.querySelector('#template-description');
        const previewSection = this.overlay.querySelector('#template-preview-section');
        const previewContent = this.overlay.querySelector('#template-preview-content');
        const externalSection = this.overlay.querySelector('#external-connections-section');
        const externalList = this.overlay.querySelector('#external-connections-list');
        const impairmentsSection = this.overlay.querySelector('#impairments-section');
        const summarySection = this.overlay.querySelector('#cluster-summary');
        const defaultImpairmentsEl = this.overlay.querySelector('#default-impairments');
        const latencyInput = this.overlay.querySelector('#cluster-latency');
        const jitterInput = this.overlay.querySelector('#cluster-jitter');
        const lossInput = this.overlay.querySelector('#cluster-loss');

        if (!this.selectedTemplate) {
            descriptionEl.textContent = '';
            previewSection.classList.add('hidden');
            externalSection.classList.add('hidden');
            impairmentsSection.classList.add('hidden');
            summarySection.classList.add('hidden');
            return;
        }

        // Show description
        descriptionEl.textContent = this.selectedTemplate.description;

        // Show template preview
        previewSection.classList.remove('hidden');
        this.renderTemplatePreview(previewContent);

        // Build target device options
        const targetOptions = this.targetDevices.map(d =>
            `<option value="${this.escapeHtml(d.name)}">${this.escapeHtml(d.name)}</option>`
        ).join('');

        // Show external connections
        externalSection.classList.remove('hidden');
        externalList.innerHTML = this.selectedTemplate.external_connections.map((ext, i) => `
            <div class="cluster-connection-row">
                <label for="ext-conn-${i}">
                    <span class="connection-node-name">${this.escapeHtml(ext.from_node)}</span>: ${this.escapeHtml(ext.description)}
                    ${ext.required ? '<span class="connection-required">*</span>' : ''}
                </label>
                <select id="ext-conn-${i}" class="form-select ext-conn-select"
                        data-from-node="${this.escapeHtml(ext.from_node)}"
                        ${ext.required ? 'required aria-required="true"' : ''}>
                    <option value="">Select target device...</option>
                    ${targetOptions}
                </select>
            </div>
        `).join('');

        // Add change handlers to external connection selects
        this.overlay.querySelectorAll('.ext-conn-select').forEach(select => {
            select.addEventListener('change', () => this.updateCreateButtonState());
        });

        // Show impairments section if template has internal connections
        if (this.selectedTemplate.internal_connections && this.selectedTemplate.internal_connections.length > 0) {
            impairmentsSection.classList.remove('hidden');

            // Set default impairments from template
            const defaults = this.selectedTemplate.default_impairments || {};
            latencyInput.value = defaults.latency_ms || '';
            jitterInput.value = defaults.jitter_ms || '';
            lossInput.value = defaults.loss_percent || '';

            if (Object.keys(defaults).length > 0) {
                const defaultParts = [];
                if (defaults.latency_ms) defaultParts.push(`${defaults.latency_ms}ms latency`);
                if (defaults.jitter_ms) defaultParts.push(`${defaults.jitter_ms}ms jitter`);
                if (defaults.loss_percent) defaultParts.push(`${defaults.loss_percent}% loss`);
                defaultImpairmentsEl.textContent = `Template defaults: ${defaultParts.join(', ')}`;
            } else {
                defaultImpairmentsEl.textContent = 'No default impairments for this template';
            }
        } else {
            impairmentsSection.classList.add('hidden');
        }

        // Show summary
        summarySection.classList.remove('hidden');
        this.updateNodeNames();
        this.updateInternalConnectionsInfo();

        // Check IP availability
        this.checkIpAvailability();
    }

    /**
     * Update node names display based on prefix
     */
    updateNodeNames() {
        if (!this.selectedTemplate) return;

        const nodesToCreateEl = this.overlay.querySelector('#nodes-to-create');
        const prefix = this.namePrefix;
        const nodeNames = this.selectedTemplate.nodes.map(n =>
            prefix ? `${prefix}_${n.name_suffix}` : n.name_suffix
        ).join(', ');

        nodesToCreateEl.innerHTML = `Will create: <strong>${this.escapeHtml(nodeNames)}</strong>`;

        // Also update the template preview to reflect new prefix
        const previewContent = this.overlay.querySelector('#template-preview-content');
        if (previewContent) {
            this.renderTemplatePreview(previewContent);
        }
    }

    /**
     * Render template preview showing nodes and connections with visual diagram
     */
    renderTemplatePreview(container) {
        if (!this.selectedTemplate) {
            container.innerHTML = '<div class="preview-placeholder">Select a template to see the preview</div>';
            return;
        }

        const template = this.selectedTemplate;
        const prefix = this.namePrefix;

        // Build visual topology diagram
        const diagramHtml = this.buildTopologyDiagram(template, prefix);

        // Build connections legend
        let connectionsLegend = '';
        if (template.internal_connections && template.internal_connections.length > 0) {
            const connLines = template.internal_connections.map(conn => {
                const fromName = prefix ? `${prefix}_${conn.from}` : conn.from;
                const toName = prefix ? `${prefix}_${conn.to}` : conn.to;
                return `<span class="conn-badge">${this.escapeHtml(fromName)} ↔ ${this.escapeHtml(toName)}</span>`;
            }).join(' ');

            connectionsLegend = `
                <div class="connections-legend">
                    <span class="legend-label">Internal Links:</span>
                    ${connLines}
                </div>
            `;
        }

        // Build external connections info
        let externalInfo = '';
        if (template.external_connections && template.external_connections.length > 0) {
            const extBadges = template.external_connections.map(ext => {
                const fromName = prefix ? `${prefix}_${ext.from_node}` : ext.from_node;
                const requiredClass = ext.required ? 'required' : 'optional';
                return `<span class="conn-badge ${requiredClass}">${this.escapeHtml(fromName)} → Existing</span>`;
            }).join(' ');

            externalInfo = `
                <div class="connections-legend external">
                    <span class="legend-label">External Links:</span>
                    ${extBadges}
                </div>
            `;
        }

        container.innerHTML = `
            <div class="preview-content">
                <div class="cluster-topology-diagram">
                    ${diagramHtml}
                </div>
                ${connectionsLegend}
                ${externalInfo}
            </div>
        `;
    }

    /**
     * Build a visual topology diagram for the cluster
     */
    buildTopologyDiagram(template, prefix) {
        const nodes = template.nodes || [];
        const internalConns = template.internal_connections || [];

        // Group nodes by type for layout
        const nodesByType = {};
        nodes.forEach(node => {
            const type = node.device_type || 'veos';
            if (!nodesByType[type]) nodesByType[type] = [];
            nodesByType[type].push(node);
        });

        // Determine layout based on node count
        if (nodes.length <= 2) {
            // Simple horizontal layout for 2 nodes
            return this.buildHorizontalDiagram(nodes, internalConns, prefix);
        } else if (nodes.length <= 4) {
            // Grid layout for 3-4 nodes
            return this.buildGridDiagram(nodes, internalConns, prefix);
        } else {
            // List layout for larger clusters
            return this.buildListDiagram(nodes, internalConns, prefix);
        }
    }

    /**
     * Build horizontal diagram for 2 nodes
     */
    buildHorizontalDiagram(nodes, connections, prefix) {
        const boxes = nodes.map((node, idx) => {
            const displayName = prefix ? `${prefix}_${node.name_suffix}` : node.name_suffix;
            const colorClass = this.getNodeColorClass(node.device_type, idx);
            return `
                <div class="diagram-box ${colorClass}">
                    <span class="diagram-label">${this.escapeHtml(displayName)}</span>
                    <span class="diagram-type">${this.escapeHtml(node.device_type || 'veos')}</span>
                </div>
            `;
        });

        // Add arrow between nodes if connected
        if (connections.length > 0 && nodes.length === 2) {
            return `
                <div class="cluster-diagram horizontal">
                    ${boxes[0]}
                    <div class="diagram-arrow-container">
                        <div class="diagram-arrow-line"></div>
                        <div class="diagram-arrow-text">↔</div>
                    </div>
                    ${boxes[1]}
                </div>
            `;
        }

        return `<div class="cluster-diagram horizontal">${boxes.join('')}</div>`;
    }

    /**
     * Build grid diagram for 3-4 nodes
     */
    buildGridDiagram(nodes, connections, prefix) {
        const boxes = nodes.map((node, idx) => {
            const displayName = prefix ? `${prefix}_${node.name_suffix}` : node.name_suffix;
            const colorClass = this.getNodeColorClass(node.device_type, idx);
            return `
                <div class="diagram-box ${colorClass}" data-node="${node.name_suffix}">
                    <span class="diagram-label">${this.escapeHtml(displayName)}</span>
                    <span class="diagram-type">${this.escapeHtml(node.device_type || 'veos')}</span>
                </div>
            `;
        });

        return `
            <div class="cluster-diagram grid">
                ${boxes.join('')}
            </div>
            <div class="diagram-connection-count">
                <span class="conn-count">${connections.length}</span> internal connection${connections.length !== 1 ? 's' : ''}
            </div>
        `;
    }

    /**
     * Build list diagram for larger clusters
     */
    buildListDiagram(nodes, connections, prefix) {
        const boxes = nodes.map((node, idx) => {
            const displayName = prefix ? `${prefix}_${node.name_suffix}` : node.name_suffix;
            const colorClass = this.getNodeColorClass(node.device_type, idx);
            return `
                <div class="diagram-box-small ${colorClass}">
                    <span class="diagram-label">${this.escapeHtml(displayName)}</span>
                </div>
            `;
        });

        return `
            <div class="cluster-diagram list">
                ${boxes.join('')}
            </div>
            <div class="diagram-connection-count">
                <span class="conn-count">${connections.length}</span> internal connection${connections.length !== 1 ? 's' : ''}
            </div>
        `;
    }

    /**
     * Get color class for node based on type
     */
    getNodeColorClass(deviceType, index) {
        const typeColors = {
            'spine': 'node-spine',
            'leaf': 'node-leaf',
            'borderleaf': 'node-borderleaf',
            'host': 'node-host',
            'isp': 'node-isp',
            'core': 'node-core',
            'pe': 'node-pe',
            'p': 'node-p'
        };

        if (deviceType && typeColors[deviceType.toLowerCase()]) {
            return typeColors[deviceType.toLowerCase()];
        }

        // Alternate colors for unknown types
        const fallbackColors = ['node-primary', 'node-secondary', 'node-tertiary', 'node-quaternary'];
        return fallbackColors[index % fallbackColors.length];
    }

    /**
     * Update internal connections info
     */
    updateInternalConnectionsInfo() {
        if (!this.selectedTemplate) return;

        const internalConnectionsInfo = this.overlay.querySelector('#internal-connections-info');
        if (this.selectedTemplate.internal_connections && this.selectedTemplate.internal_connections.length > 0) {
            const intConns = this.selectedTemplate.internal_connections.map(c =>
                `${this.escapeHtml(c.from)} ↔ ${this.escapeHtml(c.to)}`
            ).join(', ');
            internalConnectionsInfo.innerHTML = `Internal links: <strong>${intConns}</strong>`;
        } else {
            internalConnectionsInfo.innerHTML = '';
        }
    }

    /**
     * Check if enough IPs are available
     */
    checkIpAvailability() {
        if (!this.selectedTemplate) return;

        const nodesToCreateEl = this.overlay.querySelector('#nodes-to-create');
        const createBtn = this.overlay.querySelector('.wizard-create-btn');

        if (this.availableIps.length < this.selectedTemplate.node_count) {
            nodesToCreateEl.innerHTML += `<br><span class="error-text">Not enough IPs! Need ${this.selectedTemplate.node_count}, have ${this.availableIps.length}</span>`;
            createBtn.disabled = true;
        }
    }

    /**
     * Update create button enabled/disabled state
     */
    updateCreateButtonState() {
        const createBtn = this.overlay.querySelector('.wizard-create-btn');
        if (!createBtn) return;

        // Must have template selected
        if (!this.selectedTemplate) {
            createBtn.disabled = true;
            return;
        }

        // Must have enough IPs
        if (this.availableIps.length < this.selectedTemplate.node_count) {
            createBtn.disabled = true;
            return;
        }

        // Check required external connections
        const requiredSelects = this.overlay.querySelectorAll('.ext-conn-select[required]');
        let allRequiredFilled = true;
        requiredSelects.forEach(select => {
            if (!select.value) {
                allRequiredFilled = false;
            }
        });

        createBtn.disabled = !allRequiredFilled || this.isSubmitting;
    }

    /**
     * Validate impairment values are within allowed ranges
     * @returns {Object} { valid: boolean, errors: string[] }
     */
    validateImpairments() {
        const errors = [];
        const latencyInput = this.overlay.querySelector('#cluster-latency');
        const jitterInput = this.overlay.querySelector('#cluster-jitter');
        const lossInput = this.overlay.querySelector('#cluster-loss');

        // Parse values with explicit NaN handling
        const latencyRaw = parseInt(latencyInput?.value, 10);
        const jitterRaw = parseInt(jitterInput?.value, 10);
        const lossRaw = parseFloat(lossInput?.value);

        // Check for NaN (invalid input) - treat empty as 0, but reject non-numeric
        const latencyVal = latencyInput?.value === '' ? 0 : latencyRaw;
        const jitterVal = jitterInput?.value === '' ? 0 : jitterRaw;
        const lossVal = lossInput?.value === '' ? 0 : lossRaw;

        if (latencyInput?.value !== '' && isNaN(latencyRaw)) {
            errors.push('Latency must be a valid number');
            latencyInput?.classList.add('error');
            return { valid: false, errors };
        }

        if (jitterInput?.value !== '' && isNaN(jitterRaw)) {
            errors.push('Jitter must be a valid number');
            jitterInput?.classList.add('error');
            return { valid: false, errors };
        }

        if (lossInput?.value !== '' && isNaN(lossRaw)) {
            errors.push('Packet loss must be a valid number');
            lossInput?.classList.add('error');
            return { valid: false, errors };
        }

        // Validate ranges
        if (latencyVal < 0 || latencyVal > AddClusterWizard.MAX_LATENCY_MS) {
            errors.push(`Latency must be between 0 and ${AddClusterWizard.MAX_LATENCY_MS}ms`);
            latencyInput?.classList.add('error');
        } else {
            latencyInput?.classList.remove('error');
        }

        if (jitterVal < 0 || jitterVal > AddClusterWizard.MAX_JITTER_MS) {
            errors.push(`Jitter must be between 0 and ${AddClusterWizard.MAX_JITTER_MS}ms`);
            jitterInput?.classList.add('error');
        } else {
            jitterInput?.classList.remove('error');
        }

        if (lossVal < 0 || lossVal > AddClusterWizard.MAX_LOSS_PERCENT) {
            errors.push(`Packet loss must be between 0 and ${AddClusterWizard.MAX_LOSS_PERCENT}%`);
            lossInput?.classList.add('error');
        } else {
            lossInput?.classList.remove('error');
        }

        // Validate jitter doesn't exceed latency (jitter should be less than latency)
        if (jitterVal > 0 && latencyVal > 0 && jitterVal > latencyVal) {
            errors.push('Jitter should not exceed latency');
            jitterInput?.classList.add('error');
        }

        return { valid: errors.length === 0, errors };
    }

    /**
     * Collect form data
     */
    collectFormData() {
        const externalConnections = [];
        this.overlay.querySelectorAll('.ext-conn-select').forEach(select => {
            const fromNode = select.dataset.fromNode;
            const targetDevice = select.value;
            if (targetDevice) {
                externalConnections.push({
                    from_node: fromNode,
                    target_device: targetDevice
                });
            }
        });

        const latencyVal = parseInt(this.overlay.querySelector('#cluster-latency')?.value, 10) || 0;
        const jitterVal = parseInt(this.overlay.querySelector('#cluster-jitter')?.value, 10) || 0;
        const lossVal = parseFloat(this.overlay.querySelector('#cluster-loss')?.value) || 0;

        const impairments = {};
        if (latencyVal > 0) impairments.latency_ms = latencyVal;
        if (jitterVal > 0) impairments.jitter_ms = jitterVal;
        if (lossVal > 0) impairments.loss_percent = lossVal;

        return {
            template_id: this.selectedTemplate.id,
            name_prefix: this.namePrefix,
            external_connections: externalConnections,
            impairments: Object.keys(impairments).length > 0 ? impairments : null
        };
    }

    /**
     * Validate external connections (check required fields)
     */
    validateExternalConnections() {
        let valid = true;
        this.overlay.querySelectorAll('.ext-conn-select').forEach(select => {
            const required = select.hasAttribute('required');
            if (required && !select.value) {
                valid = false;
                select.classList.add('error');
            } else {
                select.classList.remove('error');
            }
        });
        return valid;
    }

    /**
     * Show notification (toast)
     */
    showNotification(message, type = 'info') {
        // Use the EventManager's notification if available
        const eventManager = this.topologyManager?.eventManager;
        if (eventManager && eventManager.showNotification) {
            eventManager.showNotification(message, type);
            return;
        }

        // Fallback: simple alert for errors
        if (type === 'error') {
            console.error('[AddClusterWizard]', message);
        }
    }

    /**
     * Submit the cluster creation request
     */
    async submitCluster() {
        if (this.isSubmitting || !this.selectedTemplate) return;

        // Validate required connections
        if (!this.validateExternalConnections()) {
            this.showNotification('Please fill in all required connections', 'error');
            return;
        }

        // Validate impairment values are within allowed ranges
        const impairmentValidation = this.validateImpairments();
        if (!impairmentValidation.valid) {
            this.showNotification(impairmentValidation.errors[0], 'error');
            return;
        }

        this.isSubmitting = true;
        const createBtn = this.overlay.querySelector('.wizard-create-btn');
        const originalText = createBtn.textContent;
        createBtn.textContent = 'Creating...';
        createBtn.disabled = true;

        // IMPORTANT: Collect form data BEFORE replacing the content
        // (replacing content destroys the form elements)
        const formData = this.collectFormData();

        const content = this.overlay.querySelector('.wizard-content');
        content.innerHTML = `
            <div class="wizard-creating">
                <div class="spinner large"></div>
                <h3>Creating Cluster...</h3>
                <p>This may take a few minutes. Please wait.</p>
            </div>
        `;

        try {
            const result = await NodeBuilderAPI.addCluster(formData);

            // Apply impairments to internal bridges if any were specified
            if (result.impairments_to_apply && result.impairments_to_apply.length > 0) {
                createBtn.textContent = 'Applying impairments...';

                for (const impInfo of result.impairments_to_apply) {
                    try {
                        await NodeBuilderAPI.configureImpairments(impInfo.bridge, impInfo.impairments);
                    } catch (impError) {
                        console.warn(`[AddClusterWizard] Failed to apply impairments to ${impInfo.bridge}:`, impError);
                    }
                }
            }

            // Show success page
            this.showSuccessPage(result, formData.external_connections);

        } catch (error) {
            console.error('[AddClusterWizard] Error creating cluster:', error);
            this.showErrorPage(error.message);
        }
    }

    /**
     * Show success page with reboot options
     */
    showSuccessPage(result, externalConnections) {
        const content = this.overlay.querySelector('.wizard-content');
        const footer = this.overlay.querySelector('.wizard-footer');

        const createdNodes = result.nodes || [];
        const rebootTargets = [...new Set(externalConnections.map(c => c.target_device))];

        // Create reboot manager
        const rebootManager = new DeviceRebootManager(this.targetDevices);

        content.innerHTML = `
            <div class="wizard-success">
                <div class="success-icon">&#10004;</div>
                <h3>Cluster Created Successfully!</h3>
                <p><strong>${createdNodes.length}</strong> nodes have been created.</p>
                <p>They will boot and register with CVP automatically via ZTP.</p>

                <div class="success-details">
                    <h4>Created Nodes:</h4>
                    <ul class="created-nodes-list">
                        ${createdNodes.map(node => `
                            <li><strong>${this.escapeHtml(node.name)}</strong> - ${this.escapeHtml(node.ip)}</li>
                        `).join('')}
                    </ul>
                </div>

                ${rebootTargets.length > 0 ? rebootManager.renderRebootSection(rebootTargets) : ''}
            </div>
        `;

        // Attach reboot handlers if there are targets
        if (rebootTargets.length > 0) {
            rebootManager.attachEventHandlers(content);
        }

        // Update footer
        footer.innerHTML = `
            <div class="wizard-footer-spacer"></div>
            <button class="wizard-btn wizard-btn-primary wizard-close-final-btn">Close</button>
        `;

        footer.querySelector('.wizard-close-final-btn').addEventListener('click', async () => {
            this.hide();
            // Refresh topology
            if (this.topologyManager) {
                await this.topologyManager.refreshTopology();
            }
        });
    }

    /**
     * Show error page
     */
    showErrorPage(errorMessage) {
        const content = this.overlay.querySelector('.wizard-content');
        const footer = this.overlay.querySelector('.wizard-footer');

        content.innerHTML = `
            <div class="wizard-error">
                <div class="error-icon">&#10008;</div>
                <h3>Failed to Create Cluster</h3>
                <p class="error-detail">${this.escapeHtml(errorMessage)}</p>
                <p class="error-hint">Check the nodebuilder service logs for more details.</p>
            </div>
        `;

        footer.innerHTML = `
            <div class="wizard-footer-spacer"></div>
            <button class="wizard-btn wizard-btn-secondary wizard-retry-btn">Try Again</button>
            <button class="wizard-btn wizard-btn-primary wizard-close-error-btn">Close</button>
        `;

        footer.querySelector('.wizard-retry-btn').addEventListener('click', () => {
            this.isSubmitting = false;
            this.restoreDefaultFooter();
            this.renderForm();
            this.updateFormForTemplate();
        });

        footer.querySelector('.wizard-close-error-btn').addEventListener('click', () => {
            this.hide();
        });
    }

    /**
     * Restore the default footer with Cancel and Create buttons
     */
    restoreDefaultFooter() {
        const footer = this.overlay?.querySelector('.wizard-footer');
        if (!footer) return;

        footer.innerHTML = `
            <div class="wizard-footer-spacer"></div>
            <button class="wizard-btn wizard-btn-secondary wizard-cancel-btn">Cancel</button>
            <button class="wizard-btn wizard-btn-primary wizard-create-btn" disabled>
                Create Cluster
            </button>
        `;

        // Re-attach event listeners
        footer.querySelector('.wizard-cancel-btn').addEventListener('click', () => this.hide());
        footer.querySelector('.wizard-create-btn').addEventListener('click', () => this.submitCluster());
    }
}

// Export for use in other modules
window.AddClusterWizard = AddClusterWizard;

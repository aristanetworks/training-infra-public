/**
 * Event Handlers for ATL Interactive Topology Diagram
 * Handles click-to-SSH, hover tooltips, and path highlighting
 */

export class EventManager {
    constructor(cy, container, options = {}) {
        this.cy = cy;
        this.container = container;
        this.options = options;
        this.tooltip = null;
        this.contextMenu = null;
        this.detailsPanel = null;  // Static details panel for copyable device info
        this.runningConfigModal = null;  // Running config modal popup
        this.focusMode = false;
        this.focusedNode = null;
        this.terminalWindow = null;  // Reference to terminal window for tab reuse

        // Custom terminal handler (for embedding in terminal page)
        this.customTerminalHandler = options.onOpenTerminal || null;
        console.log('[EventManager] Custom terminal handler:', this.customTerminalHandler ? 'provided' : 'not provided');

        // EOS type for detecting cEOS labs (packet capture not supported)
        this.eosType = options.eosType || 'veos';
        this.isCeosLab = this.eosType === 'container-labs';
        console.log('[EventManager] EOS type:', this.eosType, 'isCeosLab:', this.isCeosLab);

        // Capture panel reference (set externally by TopologyManager)
        this.capturePanel = null;

        // Legacy latency state (for backwards compatibility)
        this.latencyState = {};
        // Impairment state: { bridgeName: { latency_ms, loss_percent, dup_percent, corrupt_percent, edge } }
        this.impairmentState = {};
        // Impairment/Latency change callback (for TopologyManager to update edge styles)
        this.onLatencyChange = options.onLatencyChange || null;
        this.onImpairmentChange = options.onImpairmentChange || null;
        // Latency dialog reference (legacy)
        this.latencyDialog = null;
        // Impairment dialog reference
        this.impairmentDialog = null;

        // Store bound handler reference for proper cleanup (prevents memory leak)
        this.boundKeyDownHandler = (evt) => this.handleKeyDown(evt);
        this.boundClickHandler = (evt) => this.handleDocumentClick(evt);

        // Interface stats cache and debounce
        this.statsCache = {};  // { 'device:interface': { timestamp, data } }
        this.statsCacheTTL = 10000;  // 10 seconds
        this.statsDebounceTimer = null;
        this.statsDebounceDelay = 300;  // ms before fetching stats

        this.registerHandlers();
    }

    /**
     * Register all event handlers
     */
    registerHandlers() {
        // Node click - open SSH (removed - now via context menu)
        // this.cy.on('tap', 'node', (evt) => this.handleNodeClick(evt));

        // Node right-click - show context menu
        this.cy.on('cxttap', 'node', (evt) => this.showContextMenu(evt));

        // Node hover - show tooltip (only when not in focus mode)
        this.cy.on('mouseover', 'node', (evt) => this.handleNodeMouseOver(evt));
        this.cy.on('mouseout', 'node', (evt) => this.handleNodeMouseOut(evt));

        // Edge hover - highlight path
        this.cy.on('mouseover', 'edge', (evt) => this.handleEdgeMouseOver(evt));
        this.cy.on('mouseout', 'edge', (evt) => this.handleEdgeMouseOut(evt));

        // Edge right-click - show edge context menu (for capture, etc.)
        this.cy.on('cxttap', 'edge', (evt) => this.showEdgeContextMenu(evt));

        // Background click - clear selections and exit focus mode
        this.cy.on('tap', (evt) => {
            if (evt.target === this.cy) {
                this.hideContextMenu();
                this.exitFocusMode();
                this.clearHighlights();
            }
        });

        // Background right-click - show add node menu (KVM labs only)
        this.cy.on('cxttap', (evt) => {
            if (evt.target === this.cy) {
                this.showBackgroundContextMenu(evt);
            }
        });

        // Prevent browser context menu on the topology container
        this.container.addEventListener('contextmenu', (evt) => {
            evt.preventDefault();
        });

        // Keyboard shortcuts (using stored reference for cleanup)
        document.addEventListener('keydown', this.boundKeyDownHandler);

        // Click anywhere to close context menu
        document.addEventListener('click', this.boundClickHandler);
    }

    /**
     * Handle clicks on document to close context menu
     */
    handleDocumentClick(evt) {
        if (this.contextMenu && !this.contextMenu.contains(evt.target)) {
            this.hideContextMenu();
        }
    }

    /**
     * Open SSH session in terminal page
     * Uses postMessage to communicate with existing terminal window,
     * or calls custom handler if provided (for embedding in terminal page)
     */
    openTerminal(deviceName, ip) {
        if (!ip || ip === 'N/A') return;

        // Use custom handler if provided (e.g., when embedded in terminal page)
        if (this.customTerminalHandler) {
            console.log('[EventManager] Using custom terminal handler for', deviceName, ip);
            this.customTerminalHandler(deviceName, ip);
            return;
        }

        console.log('[EventManager] Using default terminal handler for', deviceName, ip);

        // Check if we're already on the terminal page - if so, use TerminalManager directly
        if (window.location.pathname === '/terminal' && typeof TerminalManager !== 'undefined') {
            console.log('[EventManager] On terminal page, using TerminalManager directly');
            TerminalManager.openTerminal(deviceName, ip);
            return;
        }

        const deviceData = { type: 'openDevice', device: deviceName, ip: ip };

        // Check if we have an existing terminal window that's still open
        if (this.terminalWindow && !this.terminalWindow.closed) {
            // Send message to existing terminal window to open new tab
            this.terminalWindow.postMessage(deviceData, window.location.origin);
            this.terminalWindow.focus();
        } else {
            // Open new terminal window with device parameters
            const terminalUrl = `/terminal?device=${encodeURIComponent(deviceName)}&ip=${encodeURIComponent(ip)}`;
            this.terminalWindow = window.open(terminalUrl, 'terminal-page');
        }
    }

    /**
     * Show context menu for a node
     */
    showContextMenu(evt) {
        const node = evt.target;
        const data = node.data();

        // Hide any existing menu
        this.hideContextMenu();
        this.hideTooltip();

        // Create context menu
        const menu = document.createElement('div');
        menu.id = 'topo-context-menu';
        menu.className = 'topology-context-menu';

        // Menu items
        const menuItems = [
            {
                label: 'Open Terminal',
                action: () => {
                    this.openTerminal(data.label, data.ip);
                    this.hideContextMenu();
                },
                disabled: !data.ip || data.ip === 'N/A'
            },
            {
                label: 'Open Console (Serial)',
                action: () => {
                    // Open console page in new tab with device name
                    window.open(`/console?device=${encodeURIComponent(data.label)}`, '_blank');
                    this.hideContextMenu();
                },
                // Only show for KVM labs (virsh console not available for cEOS)
                hidden: this.isCeosLab,
                disabled: !data.label
            },
            {
                label: 'Focus on Device',
                action: () => {
                    this.enterFocusMode(node);
                    this.hideContextMenu();
                }
            },
            {
                label: 'Show Details',
                action: () => {
                    this.showDetailsPanel(node);
                    this.hideContextMenu();
                }
            },
            {
                label: 'View Running Config',
                action: () => {
                    this.showRunningConfigModal(node);
                    this.hideContextMenu();
                },
                disabled: !data.ip || data.ip === 'N/A'
            },
            {
                type: 'separator'
            },
            {
                label: 'Copy IP Address',
                action: () => {
                    if (data.ip && data.ip !== 'N/A') {
                        navigator.clipboard.writeText(data.ip);
                    }
                    this.hideContextMenu();
                },
                disabled: !data.ip || data.ip === 'N/A'
            },
            {
                type: 'separator',
                // Only show separator if edit/delete options are visible
                hidden: !data.user_added || this.isCeosLab
            },
            {
                label: 'Edit Connections',
                action: () => {
                    this.showEditConnectionsDialog(data.label);
                    this.hideContextMenu();
                },
                // Only show for user-added nodes in KVM labs
                hidden: !data.user_added || this.isCeosLab
            },
            {
                label: 'Delete Node',
                action: () => {
                    this.confirmDeleteNode(data.label, data.ip);
                    this.hideContextMenu();
                },
                // Only show for user-added nodes in KVM labs
                hidden: !data.user_added || this.isCeosLab,
                className: 'danger'
            }
        ];

        // Build menu HTML
        menuItems.forEach(item => {
            // Skip hidden items (e.g., console option on cEOS labs)
            if (item.hidden) return;

            if (item.type === 'separator') {
                const sep = document.createElement('div');
                sep.className = 'context-menu-separator';
                menu.appendChild(sep);
            } else {
                const menuItem = document.createElement('div');
                let className = 'context-menu-item';
                if (item.disabled) className += ' disabled';
                if (item.className) className += ' ' + item.className;
                menuItem.className = className;

                // Only add icon if provided
                if (item.icon) {
                    const icon = document.createElement('span');
                    icon.className = 'context-menu-icon';
                    icon.textContent = item.icon;
                    menuItem.appendChild(icon);
                }

                const label = document.createElement('span');
                label.className = 'context-menu-label';
                label.textContent = item.label;

                menuItem.appendChild(label);

                if (!item.disabled) {
                    menuItem.addEventListener('click', (e) => {
                        e.stopPropagation();
                        item.action();
                    });
                }

                menu.appendChild(menuItem);
            }
        });

        // Add header with device name
        const header = document.createElement('div');
        header.className = 'context-menu-header';
        header.textContent = data.label;
        menu.insertBefore(header, menu.firstChild);

        // Position menu
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        menu.style.position = 'fixed';
        menu.style.left = (renderedPos.x + containerRect.left) + 'px';
        menu.style.top = (renderedPos.y + containerRect.top) + 'px';

        document.body.appendChild(menu);
        this.contextMenu = menu;

        // Adjust position if off-screen
        this.adjustMenuPosition(menu);
    }

    /**
     * Adjust context menu position to keep it on screen
     */
    adjustMenuPosition(menu) {
        const rect = menu.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        if (rect.right > viewportWidth - 10) {
            menu.style.left = (viewportWidth - rect.width - 10) + 'px';
        }
        if (rect.bottom > viewportHeight - 10) {
            menu.style.top = (viewportHeight - rect.height - 10) + 'px';
        }
    }

    /**
     * Hide context menu
     */
    hideContextMenu() {
        if (this.contextMenu) {
            this.contextMenu.remove();
            this.contextMenu = null;
        }
        const existing = document.getElementById('topo-context-menu');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Show confirmation dialog for deleting a user-added node
     */
    confirmDeleteNode(nodeName, nodeIp) {
        // Create modal overlay
        const overlay = document.createElement('div');
        overlay.className = 'modal-overlay';
        overlay.id = 'delete-node-modal';

        const modal = document.createElement('div');
        modal.className = 'modal-dialog';

        modal.innerHTML = `
            <div class="modal-header">
                <h3>Delete Node</h3>
            </div>
            <div class="modal-body">
                <p>Are you sure you want to delete <strong>${nodeName}</strong>?</p>
                <p class="warning-text">This will:</p>
                <ul>
                    <li>Stop and remove the VM</li>
                    <li>Delete the disk image</li>
                    <li>Remove all network connections</li>
                </ul>
                <p class="warning-text">This action cannot be undone.</p>
            </div>
            <div class="modal-footer">
                <button class="btn btn-secondary" id="cancel-delete-btn">Cancel</button>
                <button class="btn btn-danger" id="confirm-delete-btn">Delete Node</button>
            </div>
        `;

        overlay.appendChild(modal);
        document.body.appendChild(overlay);

        // Handle cancel
        document.getElementById('cancel-delete-btn').addEventListener('click', () => {
            overlay.remove();
        });

        // Handle confirm
        document.getElementById('confirm-delete-btn').addEventListener('click', async () => {
            const confirmBtn = document.getElementById('confirm-delete-btn');
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Deleting...';

            try {
                const response = await fetch('/td-api/nodes/delete-node', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: nodeName })
                });

                const result = await response.json();

                if (!response.ok) {
                    throw new Error(result.error || 'Failed to delete node');
                }

                // Success - refresh topology
                overlay.remove();
                if (window.topologyManager) {
                    await window.topologyManager.refreshTopology();
                }

                // Show success notification
                this.showNotification(`Node ${nodeName} deleted successfully`, 'success');

            } catch (error) {
                console.error('Error deleting node:', error);
                this.showNotification(`Failed to delete node: ${error.message}`, 'error');
                overlay.remove();
            }
        });

        // Close on overlay click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                overlay.remove();
            }
        });

        // Close on escape key
        const escHandler = (e) => {
            if (e.key === 'Escape') {
                overlay.remove();
                document.removeEventListener('keydown', escHandler);
            }
        };
        document.addEventListener('keydown', escHandler);
    }

    /**
     * Show a notification toast
     */
    showNotification(message, type = 'info') {
        // Create or get notification container
        let container = document.getElementById('notification-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'notification-container';
            document.body.appendChild(container);
        }

        const notification = document.createElement('div');
        notification.className = `notification notification-${type}`;
        notification.textContent = message;

        container.appendChild(notification);

        // Auto-remove after 5 seconds
        setTimeout(() => {
            notification.classList.add('fade-out');
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    }

    /**
     * Show dialog for editing connections on a user-added node
     */
    async showEditConnectionsDialog(nodeName) {
        // Fetch current connections and available targets
        try {
            const [connectionsResp, targetsResp] = await Promise.all([
                fetch(`/td-api/nodes/node-connections/${encodeURIComponent(nodeName)}`),
                fetch('/td-api/nodes/target-devices')
            ]);

            const connectionsData = await connectionsResp.json();
            const targetsData = await targetsResp.json();

            if (!connectionsResp.ok) {
                throw new Error(connectionsData.error || 'Failed to fetch connections');
            }

            const connections = connectionsData.connections || [];
            const targetDevices = targetsData.devices || [];

            // Create modal overlay
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            overlay.id = 'edit-connections-modal';

            const modal = document.createElement('div');
            modal.className = 'modal-dialog edit-connections-dialog';

            // Build connection list HTML
            let connectionsHtml = connections.map((conn, i) => `
                <div class="connection-row" data-index="${i}">
                    <span class="connection-info">
                        <strong>${conn.local_port}</strong> &rarr;
                        ${conn.target_device}:${conn.target_port}
                    </span>
                    <button class="btn btn-sm btn-danger remove-connection-btn"
                            data-local-port="${conn.local_port}"
                            data-target-device="${conn.target_device}"
                            data-target-port="${conn.target_port}">
                        Remove
                    </button>
                </div>
            `).join('');

            if (connections.length === 0) {
                connectionsHtml = '<p class="no-connections">No connections configured</p>';
            }

            // Build target device options
            const targetOptions = targetDevices.map(device =>
                `<option value="${device.name}">${device.name}</option>`
            ).join('');

            modal.innerHTML = `
                <div class="modal-header">
                    <h3>Edit Connections - ${nodeName}</h3>
                </div>
                <div class="modal-body">
                    <div class="edit-section">
                        <h4>Current Connections</h4>
                        <div class="connections-list">${connectionsHtml}</div>
                    </div>
                    <div class="edit-section">
                        <h4>Add Connection</h4>
                        <div class="add-connection-form">
                            <select id="add-target-device" class="form-control">
                                <option value="">Select target device...</option>
                                ${targetOptions}
                            </select>
                            <button class="btn btn-primary" id="add-connection-btn" disabled>
                                Add Connection
                            </button>
                        </div>
                    </div>
                    <div id="edit-status" class="edit-status hidden"></div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" id="close-edit-btn">Close</button>
                </div>
            `;

            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            // Track changes
            const pendingRemovals = [];

            // Enable add button when target selected
            const addTargetSelect = document.getElementById('add-target-device');
            const addBtn = document.getElementById('add-connection-btn');

            addTargetSelect.addEventListener('change', () => {
                addBtn.disabled = !addTargetSelect.value;
            });

            // Handle remove connection
            modal.querySelectorAll('.remove-connection-btn').forEach(btn => {
                btn.addEventListener('click', async () => {
                    const localPort = btn.dataset.localPort;
                    const targetDevice = btn.dataset.targetDevice;
                    const targetPort = btn.dataset.targetPort;

                    btn.disabled = true;
                    btn.textContent = 'Removing...';

                    try {
                        const response = await fetch('/td-api/nodes/edit-node', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                name: nodeName,
                                remove_connections: [{
                                    local_port: localPort,
                                    target_device: targetDevice,
                                    target_port: targetPort
                                }]
                            })
                        });

                        const result = await response.json();
                        if (!response.ok) throw new Error(result.error);

                        // Remove from UI
                        btn.closest('.connection-row').remove();

                        // Check if no connections left
                        if (modal.querySelectorAll('.connection-row').length === 0) {
                            modal.querySelector('.connections-list').innerHTML =
                                '<p class="no-connections">No connections configured</p>';
                        }

                        this.showNotification(`Removed connection ${localPort}`, 'success');

                        // Refresh topology
                        if (window.topologyManager) {
                            await window.topologyManager.refreshTopology();
                        }

                    } catch (error) {
                        btn.disabled = false;
                        btn.textContent = 'Remove';
                        this.showNotification(`Failed to remove: ${error.message}`, 'error');
                    }
                });
            });

            // Handle add connection
            addBtn.addEventListener('click', async () => {
                const targetDevice = addTargetSelect.value;
                if (!targetDevice) return;

                addBtn.disabled = true;
                addBtn.textContent = 'Adding...';

                try {
                    const response = await fetch('/td-api/nodes/edit-node', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            name: nodeName,
                            add_connections: [{ target_device: targetDevice }]
                        })
                    });

                    const result = await response.json();
                    if (!response.ok) throw new Error(result.error);

                    // Refresh the dialog to show new connection
                    overlay.remove();
                    this.showEditConnectionsDialog(nodeName);

                    this.showNotification(`Added connection to ${targetDevice}`, 'success');

                    // Refresh topology
                    if (window.topologyManager) {
                        await window.topologyManager.refreshTopology();
                    }

                } catch (error) {
                    addBtn.disabled = false;
                    addBtn.textContent = 'Add Connection';
                    this.showNotification(`Failed to add: ${error.message}`, 'error');
                }
            });

            // Handle close
            document.getElementById('close-edit-btn').addEventListener('click', () => {
                overlay.remove();
            });

            // Close on overlay click
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) {
                    overlay.remove();
                }
            });

            // Close on escape
            const escHandler = (e) => {
                if (e.key === 'Escape') {
                    overlay.remove();
                    document.removeEventListener('keydown', escHandler);
                }
            };
            document.addEventListener('keydown', escHandler);

        } catch (error) {
            console.error('Error showing edit dialog:', error);
            this.showNotification(`Failed to load connections: ${error.message}`, 'error');
        }
    }

    /**
     * Show dialog for adding a cluster of nodes
     */
    async showAddClusterDialog() {
        try {
            // Fetch templates and available targets
            const [templatesResp, targetsResp, ipsResp] = await Promise.all([
                fetch('/td-api/nodes/cluster-templates'),
                fetch('/td-api/nodes/target-devices'),
                fetch('/td-api/nodes/available-ips')
            ]);

            const templatesData = await templatesResp.json();
            const targetsData = await targetsResp.json();
            const ipsData = await ipsResp.json();

            if (!templatesResp.ok) {
                throw new Error(templatesData.error || 'Failed to fetch templates');
            }

            const templates = templatesData.templates || [];
            const targetDevices = targetsData.devices || [];
            const availableIps = ipsData.available_ips || [];

            // Create modal overlay
            const overlay = document.createElement('div');
            overlay.className = 'modal-overlay';
            overlay.id = 'add-cluster-modal';

            const modal = document.createElement('div');
            modal.className = 'modal-dialog cluster-dialog';

            // Build template options
            const templateOptions = templates.map(t => `
                <option value="${t.id}"
                        data-nodes="${t.node_count}"
                        data-description="${t.description}">
                    ${t.display_name} (${t.node_count} nodes)
                </option>
            `).join('');

            // Build target device options
            const targetOptions = targetDevices.map(d =>
                `<option value="${d.name}">${d.name}</option>`
            ).join('');

            modal.innerHTML = `
                <div class="modal-header">
                    <h3>Add Node Cluster</h3>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Cluster Template</label>
                        <select id="cluster-template" class="form-control">
                            <option value="">Select a template...</option>
                            ${templateOptions}
                        </select>
                        <p id="template-description" class="help-text"></p>
                    </div>

                    <div class="form-group">
                        <label>Name Prefix (optional)</label>
                        <input type="text" id="cluster-prefix" class="form-control"
                               placeholder="e.g., dc1 creates dc1_isp1, dc1_isp2">
                        <p class="help-text">Prefix added to all node names in the cluster</p>
                    </div>

                    <div id="external-connections-section" class="form-group hidden">
                        <label>External Connections</label>
                        <p class="help-text">Connect cluster nodes to existing topology devices</p>
                        <div id="external-connections-list"></div>
                    </div>

                    <div id="impairments-section" class="form-group hidden">
                        <label>Link Impairments (Internal Links)</label>
                        <p class="help-text">Apply network impairments to connections between cluster nodes</p>
                        <div class="impairment-controls-grid">
                            <div class="impairment-input-group">
                                <label>Latency (ms)</label>
                                <input type="number" id="cluster-latency" class="form-control"
                                       min="0" max="1000" step="1" placeholder="0">
                            </div>
                            <div class="impairment-input-group">
                                <label>Jitter (ms)</label>
                                <input type="number" id="cluster-jitter" class="form-control"
                                       min="0" max="500" step="1" placeholder="0">
                            </div>
                            <div class="impairment-input-group">
                                <label>Packet Loss (%)</label>
                                <input type="number" id="cluster-loss" class="form-control"
                                       min="0" max="100" step="0.1" placeholder="0">
                            </div>
                        </div>
                        <p id="default-impairments" class="help-text"></p>
                    </div>

                    <div id="cluster-summary" class="cluster-summary hidden">
                        <h4>Summary</h4>
                        <p>Available IPs: <strong>${availableIps.length}</strong></p>
                        <p id="nodes-to-create"></p>
                        <p id="internal-connections-info"></p>
                    </div>
                </div>
                <div class="modal-footer">
                    <button class="btn btn-secondary" id="cancel-cluster-btn">Cancel</button>
                    <button class="btn btn-primary" id="create-cluster-btn" disabled>
                        Create Cluster
                    </button>
                </div>
            `;

            overlay.appendChild(modal);
            document.body.appendChild(overlay);

            const templateSelect = document.getElementById('cluster-template');
            const prefixInput = document.getElementById('cluster-prefix');
            const createBtn = document.getElementById('create-cluster-btn');
            const descriptionEl = document.getElementById('template-description');
            const externalSection = document.getElementById('external-connections-section');
            const externalList = document.getElementById('external-connections-list');
            const impairmentsSection = document.getElementById('impairments-section');
            const summarySection = document.getElementById('cluster-summary');
            const nodesToCreateEl = document.getElementById('nodes-to-create');
            const internalConnectionsInfo = document.getElementById('internal-connections-info');
            const defaultImpairmentsEl = document.getElementById('default-impairments');
            const latencyInput = document.getElementById('cluster-latency');
            const jitterInput = document.getElementById('cluster-jitter');
            const lossInput = document.getElementById('cluster-loss');

            let selectedTemplate = null;

            // Handle template selection
            templateSelect.addEventListener('change', () => {
                const templateId = templateSelect.value;
                selectedTemplate = templates.find(t => t.id === templateId);

                if (selectedTemplate) {
                    descriptionEl.textContent = selectedTemplate.description;

                    // Show external connections
                    externalSection.classList.remove('hidden');
                    externalList.innerHTML = selectedTemplate.external_connections.map((ext, i) => `
                        <div class="external-connection-row">
                            <label>
                                ${ext.from_node}: ${ext.description}
                                ${ext.required ? '<span class="required">*</span>' : ''}
                            </label>
                            <select id="ext-conn-${i}" class="form-control ext-conn-select"
                                    data-from-node="${ext.from_node}" ${ext.required ? 'required' : ''}>
                                <option value="">Select target device...</option>
                                ${targetOptions}
                            </select>
                        </div>
                    `).join('');

                    // Show impairments section if template has internal connections
                    if (selectedTemplate.internal_connections && selectedTemplate.internal_connections.length > 0) {
                        impairmentsSection.classList.remove('hidden');

                        // Set default impairments from template
                        const defaults = selectedTemplate.default_impairments || {};
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
                    const prefix = prefixInput.value.trim();
                    const nodeNames = selectedTemplate.nodes.map(n =>
                        prefix ? `${prefix}_${n.name_suffix}` : n.name_suffix
                    ).join(', ');
                    nodesToCreateEl.innerHTML = `Will create: <strong>${nodeNames}</strong>`;

                    // Show internal connections info
                    if (selectedTemplate.internal_connections && selectedTemplate.internal_connections.length > 0) {
                        const intConns = selectedTemplate.internal_connections.map(c =>
                            `${c.from} ↔ ${c.to}`
                        ).join(', ');
                        internalConnectionsInfo.innerHTML = `Internal links: <strong>${intConns}</strong>`;
                    } else {
                        internalConnectionsInfo.innerHTML = '';
                    }

                    // Check if we have enough IPs
                    if (availableIps.length < selectedTemplate.node_count) {
                        nodesToCreateEl.innerHTML += `<br><span class="error-text">Not enough IPs! Need ${selectedTemplate.node_count}, have ${availableIps.length}</span>`;
                        createBtn.disabled = true;
                    } else {
                        createBtn.disabled = false;
                    }
                } else {
                    descriptionEl.textContent = '';
                    externalSection.classList.add('hidden');
                    impairmentsSection.classList.add('hidden');
                    summarySection.classList.add('hidden');
                    createBtn.disabled = true;
                }
            });

            // Update node names when prefix changes
            prefixInput.addEventListener('input', () => {
                if (selectedTemplate) {
                    const prefix = prefixInput.value.trim();
                    const nodeNames = selectedTemplate.nodes.map(n =>
                        prefix ? `${prefix}_${n.name_suffix}` : n.name_suffix
                    ).join(', ');
                    nodesToCreateEl.innerHTML = `Will create: <strong>${nodeNames}</strong>`;
                }
            });

            // Handle create
            createBtn.addEventListener('click', async () => {
                if (!selectedTemplate) return;

                // Collect external connections
                const externalConnections = [];
                const extSelects = modal.querySelectorAll('.ext-conn-select');
                let valid = true;

                extSelects.forEach(select => {
                    const fromNode = select.dataset.fromNode;
                    const targetDevice = select.value;
                    const required = select.hasAttribute('required');

                    if (required && !targetDevice) {
                        valid = false;
                        select.classList.add('error');
                    } else {
                        select.classList.remove('error');
                        if (targetDevice) {
                            externalConnections.push({
                                from_node: fromNode,
                                target_device: targetDevice
                            });
                        }
                    }
                });

                if (!valid) {
                    this.showNotification('Please fill in all required connections', 'error');
                    return;
                }

                createBtn.disabled = true;
                createBtn.textContent = 'Creating...';

                try {
                    // Collect impairment values
                    const impairments = {};
                    const latencyVal = parseInt(latencyInput.value, 10) || 0;
                    const jitterVal = parseInt(jitterInput.value, 10) || 0;
                    const lossVal = parseFloat(lossInput.value) || 0;

                    if (latencyVal > 0) impairments.latency_ms = latencyVal;
                    if (jitterVal > 0) impairments.jitter_ms = jitterVal;
                    if (lossVal > 0) impairments.loss_percent = lossVal;

                    const response = await fetch('/td-api/nodes/add-cluster', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            template_id: selectedTemplate.id,
                            name_prefix: prefixInput.value.trim(),
                            external_connections: externalConnections,
                            impairments: Object.keys(impairments).length > 0 ? impairments : null
                        })
                    });

                    const result = await response.json();
                    if (!response.ok) throw new Error(result.error);

                    // Apply impairments to internal bridges if any were specified
                    if (result.impairments_to_apply && result.impairments_to_apply.length > 0) {
                        createBtn.textContent = 'Applying impairments...';

                        for (const impInfo of result.impairments_to_apply) {
                            try {
                                await fetch('/td-api/impairments/configure', {
                                    method: 'POST',
                                    headers: { 'Content-Type': 'application/json' },
                                    body: JSON.stringify({
                                        bridge: impInfo.bridge,
                                        latency_ms: impInfo.impairments.latency_ms || 0,
                                        loss_percent: impInfo.impairments.loss_percent || 0,
                                        duplication_percent: 0,
                                        corruption_percent: 0
                                    })
                                });
                            } catch (impError) {
                                console.warn(`Failed to apply impairments to ${impInfo.bridge}:`, impError);
                            }
                        }
                    }

                    overlay.remove();
                    this.showNotification(
                        `Created cluster with ${result.nodes.length} nodes`,
                        'success'
                    );

                    // Refresh topology
                    if (window.topologyManager) {
                        await window.topologyManager.refreshTopology();
                    }

                } catch (error) {
                    createBtn.disabled = false;
                    createBtn.textContent = 'Create Cluster';
                    this.showNotification(`Failed: ${error.message}`, 'error');
                }
            });

            // Handle cancel
            document.getElementById('cancel-cluster-btn').addEventListener('click', () => {
                overlay.remove();
            });

            // Close on overlay click
            overlay.addEventListener('click', (e) => {
                if (e.target === overlay) overlay.remove();
            });

            // Close on escape
            const escHandler = (e) => {
                if (e.key === 'Escape') {
                    overlay.remove();
                    document.removeEventListener('keydown', escHandler);
                }
            };
            document.addEventListener('keydown', escHandler);

        } catch (error) {
            console.error('Error showing cluster dialog:', error);
            this.showNotification(`Failed to load: ${error.message}`, 'error');
        }
    }

    /**
     * Show context menu when right-clicking on empty canvas background
     * Provides options like "Add New Node" (KVM only), "Fit to View", etc.
     */
    showBackgroundContextMenu(evt) {
        // Hide any existing menu
        this.hideContextMenu();
        this.hideTooltip();

        // Create context menu
        const menu = document.createElement('div');
        menu.id = 'topo-context-menu';
        menu.className = 'topology-context-menu';

        // Menu items for background
        const menuItems = [
            {
                label: this.isCeosLab ? 'Add New Node (vEOS only)' : 'Add New Node',
                action: () => {
                    this.hideContextMenu();
                    if (window.addNodeWizard) {
                        window.addNodeWizard.show();
                    } else {
                        console.error('AddNodeWizard not initialized');
                    }
                },
                disabled: this.isCeosLab
            },
            {
                label: this.isCeosLab ? 'Add Cluster (vEOS only)' : 'Add Cluster',
                action: () => {
                    this.hideContextMenu();
                    this.showAddClusterDialog();
                },
                disabled: this.isCeosLab
            },
            {
                type: 'separator'
            },
            {
                label: 'Fit to View',
                action: () => {
                    this.hideContextMenu();
                    this.cy.fit(50);
                }
            },
            {
                label: 'Reset Zoom',
                action: () => {
                    this.hideContextMenu();
                    this.cy.zoom(1);
                    this.cy.center();
                }
            }
        ];

        // Build menu HTML using same pattern as showContextMenu
        menuItems.forEach(item => {
            if (item.type === 'separator') {
                const separator = document.createElement('div');
                separator.className = 'context-menu-separator';
                menu.appendChild(separator);
            } else {
                const menuItem = document.createElement('div');
                menuItem.className = 'context-menu-item' + (item.disabled ? ' disabled' : '');
                menuItem.textContent = item.label;
                if (!item.disabled) {
                    menuItem.addEventListener('click', item.action);
                }
                menu.appendChild(menuItem);
            }
        });

        // Position the menu at click location
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        menu.style.position = 'fixed';
        menu.style.left = (renderedPos.x + containerRect.left) + 'px';
        menu.style.top = (renderedPos.y + containerRect.top) + 'px';

        document.body.appendChild(menu);
        this.contextMenu = menu;

        // Adjust position if menu would go off screen
        this.adjustMenuPosition(menu);
    }

    /**
     * Show context menu for an edge (link)
     */
    showEdgeContextMenu(evt) {
        const edge = evt.target;
        const data = edge.data();

        // Hide any existing menu
        this.hideContextMenu();
        this.hideTooltip();

        // Create context menu
        const menu = document.createElement('div');
        menu.id = 'topo-context-menu';
        menu.className = 'topology-context-menu';

        // Build descriptive link label (escaped to prevent XSS)
        const linkLabel = `${this.escapeHtml(data.source)}:${this.escapeHtml(data.source_port)} ↔ ${this.escapeHtml(data.target)}:${this.escapeHtml(data.target_port)}`;

        // Check if this edge has any impairments applied
        const impairmentInfo = this.getEdgeImpairmentInfo(edge);
        const hasImpairments = impairmentInfo && impairmentInfo.hasAnyImpairment;

        // Build impairment label for menu
        let impairmentLabel = 'Advanced Link Tools';
        if (hasImpairments) {
            const parts = [];
            if (impairmentInfo.latency_ms > 0) parts.push(`${impairmentInfo.latency_ms}ms`);
            if (impairmentInfo.loss_percent > 0) parts.push(`${impairmentInfo.loss_percent}% loss`);
            if (impairmentInfo.dup_percent > 0) parts.push(`${impairmentInfo.dup_percent}% dup`);
            if (impairmentInfo.corrupt_percent > 0) parts.push(`${impairmentInfo.corrupt_percent}% corrupt`);
            if (parts.length > 0) {
                impairmentLabel = `Advanced Link Tools (${parts.join(', ')})`;
            }
        }

        // Menu items for edge
        const menuItems = [
            {
                label: this.isCeosLab ? 'Packet Capture (vEOS only)' : 'Start Packet Capture',
                action: () => {
                    this.startEdgeCapture(edge);
                    this.hideContextMenu();
                },
                disabled: this.isCeosLab
            },
            {
                label: 'View Link Stats',
                action: () => {
                    // Stats are already shown in edge tooltip
                    this.showEdgeTooltip(evt);
                    this.hideContextMenu();
                }
            },
            {
                type: 'separator'
            },
            // Advanced Link Tools (Impairment configuration)
            {
                label: this.isCeosLab ? 'Advanced Link Tools (vEOS only)' : impairmentLabel,
                action: () => {
                    this.showImpairmentDialog(edge);
                    this.hideContextMenu();
                },
                disabled: this.isCeosLab
            },
            {
                type: 'separator'
            },
            {
                label: 'Focus Source',
                action: () => {
                    const sourceNode = this.cy.$id(data.source);
                    if (!sourceNode.empty()) {
                        this.enterFocusMode(sourceNode);
                    }
                    this.hideContextMenu();
                }
            },
            {
                label: 'Focus Target',
                action: () => {
                    const targetNode = this.cy.$id(data.target);
                    if (!targetNode.empty()) {
                        this.enterFocusMode(targetNode);
                    }
                    this.hideContextMenu();
                }
            }
        ];

        // Build menu HTML
        menuItems.forEach(item => {
            if (item.type === 'separator') {
                const sep = document.createElement('div');
                sep.className = 'context-menu-separator';
                menu.appendChild(sep);
            } else {
                const menuItem = document.createElement('div');
                menuItem.className = 'context-menu-item' + (item.disabled ? ' disabled' : '');

                // Only add icon if provided
                if (item.icon) {
                    const icon = document.createElement('span');
                    icon.className = 'context-menu-icon';
                    icon.textContent = item.icon;
                    menuItem.appendChild(icon);
                }

                const label = document.createElement('span');
                label.className = 'context-menu-label';
                label.textContent = item.label;

                menuItem.appendChild(label);

                if (!item.disabled) {
                    menuItem.addEventListener('click', (e) => {
                        e.stopPropagation();
                        item.action();
                    });
                }

                menu.appendChild(menuItem);
            }
        });

        // Add header with link info
        const header = document.createElement('div');
        header.className = 'context-menu-header';
        header.textContent = linkLabel;
        header.style.fontSize = '12px';  // Slightly smaller for longer text
        menu.insertBefore(header, menu.firstChild);

        // Position menu
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        menu.style.position = 'fixed';
        menu.style.left = (renderedPos.x + containerRect.left) + 'px';
        menu.style.top = (renderedPos.y + containerRect.top) + 'px';

        document.body.appendChild(menu);
        this.contextMenu = menu;

        // Adjust position if off-screen
        this.adjustMenuPosition(menu);
    }

    /**
     * Start packet capture on an edge/link
     */
    startEdgeCapture(edge) {
        const data = edge.data();

        if (this.capturePanel) {
            // Pass edge data to capture panel - it will find the matching bridge
            const edgeData = {
                source: data.source,
                target: data.target,
                source_port: data.source_port,
                target_port: data.target_port
            };
            console.log('[EventManager] Opening capture panel for edge:', edgeData);
            this.capturePanel.show(edgeData);
        } else {
            console.warn('[EventManager] Capture panel not available');
            alert('Packet capture feature is not available on this page.\n\nPlease use the main topology diagram page.');
        }
    }

    /**
     * Get the bridge name for an edge by searching latencyState for a matching edge.
     * Returns the bridge name if found in latencyState, null otherwise.
     */
    getEdgeBridgeName(edge) {
        const data = edge.data();

        // First check if bridge_name is stored in edge data
        if (data.bridge_name) {
            return data.bridge_name;
        }

        // Search through latencyState to find a matching bridge by edge data
        for (const [bridgeName, info] of Object.entries(this.latencyState)) {
            if (info.edge) {
                const infoData = info.edge.data();
                // Check if this is the same edge (by id or by source/target/ports)
                if (infoData.id === data.id) {
                    return bridgeName;
                }
                // Also check by source/target/ports in case id doesn't match
                if (infoData.source === data.source &&
                    infoData.target === data.target &&
                    infoData.source_port === data.source_port &&
                    infoData.target_port === data.target_port) {
                    return bridgeName;
                }
                // Check reverse direction too
                if (infoData.source === data.target &&
                    infoData.target === data.source &&
                    infoData.source_port === data.target_port &&
                    infoData.target_port === data.source_port) {
                    return bridgeName;
                }
            }
        }

        return null;
    }

    /**
     * Check if an edge has latency applied by searching latencyState
     * Returns { bridgeName, delay_ms } if found, null otherwise
     */
    getEdgeLatencyInfo(edge) {
        const data = edge.data();

        // Search through latencyState
        for (const [bridgeName, info] of Object.entries(this.latencyState)) {
            if (info.edge) {
                const infoData = info.edge.data();
                // Check if this is the same edge
                if (infoData.id === data.id) {
                    return { bridgeName, delay_ms: info.delay_ms };
                }
                // Check by source/target/ports
                if (infoData.source === data.source &&
                    infoData.target === data.target &&
                    infoData.source_port === data.source_port &&
                    infoData.target_port === data.target_port) {
                    return { bridgeName, delay_ms: info.delay_ms };
                }
                // Reverse direction
                if (infoData.source === data.target &&
                    infoData.target === data.source &&
                    infoData.source_port === data.target_port &&
                    infoData.target_port === data.source_port) {
                    return { bridgeName, delay_ms: info.delay_ms };
                }
            }
        }

        // Also check if edge has latency class applied (loaded from API on page load)
        if (edge.hasClass('has-latency')) {
            const delay_ms = edge.data('latency_ms');
            if (delay_ms) {
                return { bridgeName: null, delay_ms };
            }
        }

        return null;
    }

    /**
     * Check if an edge has any impairments applied
     * Returns impairment info object with all impairment values
     */
    getEdgeImpairmentInfo(edge) {
        const data = edge.data();

        // Default empty result
        const emptyResult = {
            bridgeName: null,
            latency_ms: 0,
            loss_percent: 0,
            dup_percent: 0,
            corrupt_percent: 0,
            reorder_delay_ms: 0,
            reorder_percent: 0,
            hasAnyImpairment: false
        };

        // Search through impairmentState
        for (const [bridgeName, info] of Object.entries(this.impairmentState)) {
            if (info.edge) {
                const infoData = info.edge.data();
                // Check if this is the same edge (by id or by source/target/ports)
                const matchById = infoData.id === data.id;
                const matchForward = (
                    infoData.source === data.source &&
                    infoData.target === data.target &&
                    infoData.source_port === data.source_port &&
                    infoData.target_port === data.target_port
                );
                const matchReverse = (
                    infoData.source === data.target &&
                    infoData.target === data.source &&
                    infoData.source_port === data.target_port &&
                    infoData.target_port === data.source_port
                );

                if (matchById || matchForward || matchReverse) {
                    const hasAnyImpairment = (
                        (info.latency_ms || 0) > 0 ||
                        (info.loss_percent || 0) > 0 ||
                        (info.dup_percent || 0) > 0 ||
                        (info.corrupt_percent || 0) > 0 ||
                        (info.reorder_percent || 0) > 0
                    );
                    return {
                        bridgeName,
                        latency_ms: info.latency_ms || 0,
                        loss_percent: info.loss_percent || 0,
                        dup_percent: info.dup_percent || 0,
                        corrupt_percent: info.corrupt_percent || 0,
                        reorder_delay_ms: info.reorder_delay_ms || 0,
                        reorder_percent: info.reorder_percent || 0,
                        hasAnyImpairment
                    };
                }
            }
        }

        // Also check legacy latencyState for backwards compatibility
        for (const [bridgeName, info] of Object.entries(this.latencyState)) {
            if (info.edge) {
                const infoData = info.edge.data();
                const matchById = infoData.id === data.id;
                const matchForward = (
                    infoData.source === data.source &&
                    infoData.target === data.target &&
                    infoData.source_port === data.source_port &&
                    infoData.target_port === data.target_port
                );
                const matchReverse = (
                    infoData.source === data.target &&
                    infoData.target === data.source &&
                    infoData.source_port === data.target_port &&
                    infoData.target_port === data.source_port
                );

                if (matchById || matchForward || matchReverse) {
                    return {
                        bridgeName,
                        latency_ms: info.delay_ms || 0,
                        loss_percent: 0,
                        dup_percent: 0,
                        corrupt_percent: 0,
                        hasAnyImpairment: (info.delay_ms || 0) > 0
                    };
                }
            }
        }

        // Also check if edge has impairment classes applied (loaded from API on page load)
        if (edge.hasClass('has-impairments') || edge.hasClass('has-latency')) {
            const latency_ms = edge.data('latency_ms') || 0;
            const loss_percent = edge.data('loss_percent') || 0;
            const dup_percent = edge.data('dup_percent') || 0;
            const corrupt_percent = edge.data('corrupt_percent') || 0;
            const hasAnyImpairment = latency_ms > 0 || loss_percent > 0 || dup_percent > 0 || corrupt_percent > 0;

            if (hasAnyImpairment) {
                return {
                    bridgeName: null,
                    latency_ms,
                    loss_percent,
                    dup_percent,
                    corrupt_percent,
                    hasAnyImpairment
                };
            }
        }

        return emptyResult;
    }

    /**
     * Show latency dialog for an edge (legacy - kept for backwards compatibility)
     */
    showLatencyDialog(edge) {
        // Hide any existing dialog
        this.hideLatencyDialog();

        const data = edge.data();
        // Escape link label to prevent XSS
        const linkLabel = `${this.escapeHtml(data.source)}:${this.escapeHtml(data.source_port)} ↔ ${this.escapeHtml(data.target)}:${this.escapeHtml(data.target_port)}`;

        // Create dialog overlay
        const overlay = document.createElement('div');
        overlay.id = 'latency-dialog-overlay';
        overlay.className = 'latency-dialog-overlay';

        // Create dialog
        const dialog = document.createElement('div');
        dialog.className = 'latency-dialog';

        dialog.innerHTML = `
            <div class="latency-dialog-header">
                <span class="latency-dialog-title">Add Latency</span>
                <button class="latency-dialog-close" title="Close">&times;</button>
            </div>
            <div class="latency-dialog-body">
                <div class="latency-dialog-link">${linkLabel}</div>
                <div class="latency-dialog-input-group">
                    <label for="latency-delay-input">Delay (milliseconds):</label>
                    <input type="number"
                           id="latency-delay-input"
                           class="latency-delay-input"
                           min="1"
                           max="10000"
                           value="100"
                           placeholder="1-10000">
                    <span class="latency-input-hint">Valid range: 1-10000ms</span>
                </div>
                <div class="latency-dialog-error" id="latency-dialog-error"></div>
            </div>
            <div class="latency-dialog-footer">
                <button class="latency-dialog-btn cancel" id="latency-cancel-btn">Cancel</button>
                <button class="latency-dialog-btn apply" id="latency-apply-btn">Apply</button>
            </div>
        `;

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);
        this.latencyDialog = overlay;

        // Focus the input
        const input = document.getElementById('latency-delay-input');
        input.focus();
        input.select();

        // Event handlers
        const closeBtn = dialog.querySelector('.latency-dialog-close');
        const cancelBtn = document.getElementById('latency-cancel-btn');
        const applyBtn = document.getElementById('latency-apply-btn');

        closeBtn.addEventListener('click', () => this.hideLatencyDialog());
        cancelBtn.addEventListener('click', () => this.hideLatencyDialog());

        applyBtn.addEventListener('click', () => {
            const delay = parseInt(input.value, 10);
            this.applyLatency(edge, delay);
        });

        // Enter key to apply
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const delay = parseInt(input.value, 10);
                this.applyLatency(edge, delay);
            } else if (e.key === 'Escape') {
                this.hideLatencyDialog();
            }
        });

        // Click outside to close
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hideLatencyDialog();
            }
        });
    }

    /**
     * Hide latency dialog
     */
    hideLatencyDialog() {
        if (this.latencyDialog) {
            this.latencyDialog.remove();
            this.latencyDialog = null;
        }
        const existing = document.getElementById('latency-dialog-overlay');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Apply latency to an edge
     */
    async applyLatency(edge, delayMs) {
        const errorEl = document.getElementById('latency-dialog-error');

        // Validate input
        if (isNaN(delayMs) || delayMs < 1 || delayMs > 10000) {
            if (errorEl) {
                errorEl.textContent = 'Please enter a valid delay between 1 and 10000ms';
                errorEl.style.display = 'block';
            }
            return;
        }

        const data = edge.data();
        const applyBtn = document.getElementById('latency-apply-btn');

        // Disable button while processing
        if (applyBtn) {
            applyBtn.disabled = true;
            applyBtn.textContent = 'Applying...';
        }

        try {
            // First, we need to find the bridge name for this edge
            // Fetch bridges and find matching one
            const bridgesResponse = await fetch('/td-api/latency/bridges');
            if (!bridgesResponse.ok) {
                throw new Error('Failed to fetch bridges');
            }
            const bridgesData = await bridgesResponse.json();
            const bridge = this.findMatchingBridge(data, bridgesData.bridges);

            if (!bridge) {
                throw new Error('No matching bridge found for this link');
            }

            // Apply latency
            const response = await fetch('/td-api/latency/enable', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    bridge: bridge.name,
                    delay_ms: delayMs
                })
            });

            const result = await response.json();

            if (!response.ok || result.error) {
                throw new Error(result.error || 'Failed to apply latency');
            }

            // Update local state
            this.latencyState[bridge.name] = {
                delay_ms: delayMs,
                edge: edge
            };

            // Notify callback (TopologyManager) to update edge styling
            if (this.onLatencyChange) {
                this.onLatencyChange(bridge.name, delayMs, edge);
            }

            // Close dialog
            this.hideLatencyDialog();

            console.log(`[EventManager] Applied ${delayMs}ms latency to ${bridge.name}`);

        } catch (error) {
            console.error('[EventManager] Error applying latency:', error);
            if (errorEl) {
                errorEl.textContent = error.message || 'Failed to apply latency';
                errorEl.style.display = 'block';
            }
            if (applyBtn) {
                applyBtn.disabled = false;
                applyBtn.textContent = 'Apply';
            }
        }
    }

    /**
     * Remove latency from an edge
     */
    async removeLatency(edge) {
        const data = edge.data();

        try {
            // Find the bridge name
            const bridgesResponse = await fetch('/td-api/latency/bridges');
            if (!bridgesResponse.ok) {
                throw new Error('Failed to fetch bridges');
            }
            const bridgesData = await bridgesResponse.json();
            const bridge = this.findMatchingBridge(data, bridgesData.bridges);

            if (!bridge) {
                throw new Error('No matching bridge found for this link');
            }

            // Remove latency
            const response = await fetch('/td-api/latency/disable', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bridge: bridge.name })
            });

            const result = await response.json();

            if (!response.ok || result.error) {
                throw new Error(result.error || 'Failed to remove latency');
            }

            // Update local state
            delete this.latencyState[bridge.name];

            // Notify callback (TopologyManager) to update edge styling
            if (this.onLatencyChange) {
                this.onLatencyChange(bridge.name, null, edge);
            }

            console.log(`[EventManager] Removed latency from ${bridge.name}`);

        } catch (error) {
            console.error('[EventManager] Error removing latency:', error);
            alert('Failed to remove latency: ' + (error.message || 'Unknown error'));
        }
    }

    /**
     * Find matching bridge for edge data
     */
    findMatchingBridge(edgeData, bridges) {
        if (!edgeData || !bridges || !bridges.length) {
            return null;
        }

        const srcLower = (edgeData.source || '').toLowerCase();
        const tgtLower = (edgeData.target || '').toLowerCase();
        const srcPortLower = (edgeData.source_port || '').toLowerCase();
        const tgtPortLower = (edgeData.target_port || '').toLowerCase();

        for (const bridge of bridges) {
            const bSrcDevice = (bridge.source_device_name || '').toLowerCase();
            const bTgtDevice = (bridge.target_device_name || '').toLowerCase();
            const bSrcPort = (bridge.source_port_name || '').toLowerCase();
            const bTgtPort = (bridge.target_port_name || '').toLowerCase();

            // Check both directions
            const matchForward = (
                bSrcDevice === srcLower &&
                bTgtDevice === tgtLower &&
                bSrcPort === srcPortLower &&
                bTgtPort === tgtPortLower
            );

            const matchReverse = (
                bSrcDevice === tgtLower &&
                bTgtDevice === srcLower &&
                bSrcPort === tgtPortLower &&
                bTgtPort === srcPortLower
            );

            if (matchForward || matchReverse) {
                return bridge;
            }
        }

        return null;
    }

    /**
     * Update latency state from API data (called by TopologyManager on init)
     */
    updateLatencyState(bridges) {
        for (const bridge of bridges) {
            if (bridge.latency_enabled && bridge.latency_delay_ms) {
                this.latencyState[bridge.name] = {
                    delay_ms: bridge.latency_delay_ms,
                    edge: null  // Edge reference will be set when we find it
                };
            }
        }
    }

    /**
     * Update impairment state from API data (called by TopologyManager on init)
     */
    updateImpairmentState(bridges) {
        for (const bridge of bridges) {
            const impairments = bridge.impairments || {};
            const hasAny = (
                (impairments.latency_ms || 0) > 0 ||
                (impairments.loss_percent || 0) > 0 ||
                (impairments.duplication_percent || 0) > 0 ||
                (impairments.corruption_percent || 0) > 0
            );

            if (hasAny) {
                this.impairmentState[bridge.name] = {
                    latency_ms: impairments.latency_ms || 0,
                    loss_percent: impairments.loss_percent || 0,
                    dup_percent: impairments.duplication_percent || 0,
                    corrupt_percent: impairments.corruption_percent || 0,
                    edge: null  // Edge reference will be set when we find it
                };
            }
        }
    }

    /**
     * Show impairment configuration dialog for an edge
     */
    showImpairmentDialog(edge) {
        // Hide any existing dialog
        this.hideImpairmentDialog();

        const data = edge.data();
        // Escape link label to prevent XSS
        const linkLabel = `${this.escapeHtml(data.source)}:${this.escapeHtml(data.source_port)} ↔ ${this.escapeHtml(data.target)}:${this.escapeHtml(data.target_port)}`;

        // Get current impairment values for this edge
        const currentInfo = this.getEdgeImpairmentInfo(edge);

        // Create dialog overlay
        const overlay = document.createElement('div');
        overlay.id = 'impairment-dialog-overlay';
        overlay.className = 'impairment-dialog-overlay';

        // Percentage options for dropdowns
        const percentOptions = [0, 10, 20, 30, 40, 50];
        const makeOptions = (selected, label) => percentOptions.map(p =>
            `<option value="${p}" ${p === selected ? 'selected' : ''}>${p === 0 ? 'None' : p + '%'}</option>`
        ).join('');

        // Create dialog
        const dialog = document.createElement('div');
        dialog.className = 'impairment-dialog';

        dialog.innerHTML = `
            <div class="impairment-dialog-header">
                <span class="impairment-dialog-title">Advanced Link Tools</span>
                <button class="impairment-dialog-close" title="Close">&times;</button>
            </div>
            <div class="impairment-dialog-body">
                <div class="impairment-dialog-link">${linkLabel}</div>

                <div class="impairment-row latency-row">
                    <label class="impairment-label latency-label">
                        <span class="impairment-color-dot latency-dot"></span>
                        Latency (ms):
                    </label>
                    <input type="number"
                           id="impairment-latency-input"
                           class="impairment-input"
                           min="0"
                           max="10000"
                           value="${currentInfo.latency_ms || 0}"
                           placeholder="0-10000">
                </div>

                <div class="impairment-row loss-row">
                    <label class="impairment-label loss-label">
                        <span class="impairment-color-dot loss-dot"></span>
                        Packet Loss:
                    </label>
                    <select id="impairment-loss-select" class="impairment-select">
                        ${makeOptions(currentInfo.loss_percent || 0)}
                    </select>
                </div>

                <div class="impairment-row dup-row">
                    <label class="impairment-label dup-label">
                        <span class="impairment-color-dot dup-dot"></span>
                        Duplication:
                    </label>
                    <select id="impairment-dup-select" class="impairment-select">
                        ${makeOptions(currentInfo.dup_percent || 0)}
                    </select>
                </div>

                <div class="impairment-row corrupt-row">
                    <label class="impairment-label corrupt-label">
                        <span class="impairment-color-dot corrupt-dot"></span>
                        Corruption:
                    </label>
                    <select id="impairment-corrupt-select" class="impairment-select">
                        ${makeOptions(currentInfo.corrupt_percent || 0)}
                    </select>
                </div>

                <div class="impairment-row reorder-row">
                    <label class="impairment-label reorder-label">
                        <span class="impairment-color-dot reorder-dot"></span>
                        Reorder:
                    </label>
                    <div class="reorder-inputs">
                        <input type="number"
                               id="impairment-reorder-delay-input"
                               class="impairment-input reorder-delay-input"
                               min="100"
                               max="10000"
                               value="${currentInfo.reorder_delay_ms || 0}"
                               placeholder="100-10000ms">
                        <select id="impairment-reorder-select" class="impairment-select">
                            ${makeOptions(currentInfo.reorder_percent || 0)}
                        </select>
                    </div>
                </div>

                <div class="impairment-dialog-error" id="impairment-dialog-error"></div>
            </div>
            <div class="impairment-dialog-footer">
                <button class="impairment-dialog-btn clear" id="impairment-clear-btn">Clear All</button>
                <button class="impairment-dialog-btn cancel" id="impairment-cancel-btn">Cancel</button>
                <button class="impairment-dialog-btn apply" id="impairment-apply-btn">Apply</button>
            </div>
        `;

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);
        this.impairmentDialog = overlay;

        // Event handlers
        const closeBtn = dialog.querySelector('.impairment-dialog-close');
        const cancelBtn = document.getElementById('impairment-cancel-btn');
        const applyBtn = document.getElementById('impairment-apply-btn');
        const clearBtn = document.getElementById('impairment-clear-btn');
        const latencyInput = document.getElementById('impairment-latency-input');

        closeBtn.addEventListener('click', () => this.hideImpairmentDialog());
        cancelBtn.addEventListener('click', () => this.hideImpairmentDialog());

        applyBtn.addEventListener('click', () => {
            const latency = parseInt(latencyInput.value, 10) || 0;
            const loss = parseInt(document.getElementById('impairment-loss-select').value, 10) || 0;
            const dup = parseInt(document.getElementById('impairment-dup-select').value, 10) || 0;
            const corrupt = parseInt(document.getElementById('impairment-corrupt-select').value, 10) || 0;
            const reorderDelay = parseInt(document.getElementById('impairment-reorder-delay-input').value, 10) || 0;
            const reorderPercent = parseInt(document.getElementById('impairment-reorder-select').value, 10) || 0;
            this.applyImpairments(edge, latency, loss, dup, corrupt, reorderDelay, reorderPercent);
        });

        clearBtn.addEventListener('click', () => {
            this.clearImpairments(edge);
        });

        // Enter key to apply
        latencyInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                applyBtn.click();
            } else if (e.key === 'Escape') {
                this.hideImpairmentDialog();
            }
        });

        // Click outside to close
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hideImpairmentDialog();
            }
        });
    }

    /**
     * Hide impairment dialog
     */
    hideImpairmentDialog() {
        if (this.impairmentDialog) {
            this.impairmentDialog.remove();
            this.impairmentDialog = null;
        }
        const existing = document.getElementById('impairment-dialog-overlay');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Apply impairments to an edge
     */
    async applyImpairments(edge, latencyMs, lossPercent, dupPercent, corruptPercent, reorderDelayMs = 0, reorderPercent = 0) {
        const errorEl = document.getElementById('impairment-dialog-error');

        // Validate latency input
        if (latencyMs < 0 || latencyMs > 10000) {
            if (errorEl) {
                errorEl.textContent = 'Latency must be between 0 and 10000ms';
                errorEl.style.display = 'block';
            }
            return;
        }

        // Validate percentage inputs (0-100 range)
        if (lossPercent < 0 || lossPercent > 100) {
            if (errorEl) {
                errorEl.textContent = 'Packet loss must be between 0 and 100%';
                errorEl.style.display = 'block';
            }
            return;
        }
        if (dupPercent < 0 || dupPercent > 100) {
            if (errorEl) {
                errorEl.textContent = 'Duplication must be between 0 and 100%';
                errorEl.style.display = 'block';
            }
            return;
        }
        if (corruptPercent < 0 || corruptPercent > 100) {
            if (errorEl) {
                errorEl.textContent = 'Corruption must be between 0 and 100%';
                errorEl.style.display = 'block';
            }
            return;
        }

        // Validate reorder inputs
        if (reorderPercent > 0 && (reorderDelayMs < 100 || reorderDelayMs > 10000)) {
            if (errorEl) {
                errorEl.textContent = 'Reorder delay must be between 100 and 10000ms when reorder is enabled';
                errorEl.style.display = 'block';
            }
            return;
        }
        if (reorderPercent < 0 || reorderPercent > 100) {
            if (errorEl) {
                errorEl.textContent = 'Reorder percent must be between 0 and 100%';
                errorEl.style.display = 'block';
            }
            return;
        }

        const data = edge.data();
        const applyBtn = document.getElementById('impairment-apply-btn');

        // Disable button while processing
        if (applyBtn) {
            applyBtn.disabled = true;
            applyBtn.textContent = 'Applying...';
        }

        try {
            // Find the bridge name for this edge
            const bridgesResponse = await fetch('/td-api/impairments/bridges');
            if (!bridgesResponse.ok) {
                throw new Error('Failed to fetch bridges');
            }
            const bridgesData = await bridgesResponse.json();
            const bridge = this.findMatchingBridge(data, bridgesData.bridges);

            if (!bridge) {
                throw new Error('No matching bridge found for this link');
            }

            // Apply impairments
            const response = await fetch('/td-api/impairments/configure', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    bridge: bridge.name,
                    latency_ms: latencyMs,
                    loss_percent: lossPercent,
                    duplication_percent: dupPercent,
                    corruption_percent: corruptPercent,
                    reorder_delay_ms: reorderDelayMs,
                    reorder_percent: reorderPercent
                })
            });

            const result = await response.json();

            if (!response.ok || result.error) {
                throw new Error(result.error || 'Failed to apply impairments');
            }

            // Update local state
            this.impairmentState[bridge.name] = {
                latency_ms: latencyMs,
                loss_percent: lossPercent,
                dup_percent: dupPercent,
                corrupt_percent: corruptPercent,
                reorder_delay_ms: reorderDelayMs,
                reorder_percent: reorderPercent,
                edge: edge
            };

            // Also update legacy latencyState for backwards compatibility
            if (latencyMs > 0) {
                this.latencyState[bridge.name] = {
                    delay_ms: latencyMs,
                    edge: edge
                };
            } else {
                delete this.latencyState[bridge.name];
            }

            // Notify callback (TopologyManager) to update edge styling
            if (this.onImpairmentChange) {
                this.onImpairmentChange(bridge.name, {
                    latency_ms: latencyMs,
                    loss_percent: lossPercent,
                    dup_percent: dupPercent,
                    corrupt_percent: corruptPercent,
                    reorder_delay_ms: reorderDelayMs,
                    reorder_percent: reorderPercent
                }, edge);
            } else if (this.onLatencyChange) {
                // Fallback to legacy callback
                this.onLatencyChange(bridge.name, latencyMs, edge);
            }

            // Close dialog
            this.hideImpairmentDialog();

            console.log(`[EventManager] Applied impairments to ${bridge.name}:`, {
                latencyMs, lossPercent, dupPercent, corruptPercent, reorderDelayMs, reorderPercent
            });

        } catch (error) {
            console.error('[EventManager] Error applying impairments:', error);
            if (errorEl) {
                errorEl.textContent = error.message || 'Failed to apply impairments';
                errorEl.style.display = 'block';
            }
            if (applyBtn) {
                applyBtn.disabled = false;
                applyBtn.textContent = 'Apply';
            }
        }
    }

    /**
     * Clear all impairments from an edge
     */
    async clearImpairments(edge) {
        const data = edge.data();
        const clearBtn = document.getElementById('impairment-clear-btn');

        // Disable button while processing
        if (clearBtn) {
            clearBtn.disabled = true;
            clearBtn.textContent = 'Clearing...';
        }

        try {
            // Find the bridge name
            const bridgesResponse = await fetch('/td-api/impairments/bridges');
            if (!bridgesResponse.ok) {
                throw new Error('Failed to fetch bridges');
            }
            const bridgesData = await bridgesResponse.json();
            const bridge = this.findMatchingBridge(data, bridgesData.bridges);

            if (!bridge) {
                throw new Error('No matching bridge found for this link');
            }

            // Clear impairments
            const response = await fetch('/td-api/impairments/clear', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ bridge: bridge.name })
            });

            const result = await response.json();

            if (!response.ok || result.error) {
                throw new Error(result.error || 'Failed to clear impairments');
            }

            // Update local state
            delete this.impairmentState[bridge.name];
            delete this.latencyState[bridge.name];

            // Notify callback (TopologyManager) to update edge styling
            if (this.onImpairmentChange) {
                this.onImpairmentChange(bridge.name, null, edge);
            } else if (this.onLatencyChange) {
                this.onLatencyChange(bridge.name, null, edge);
            }

            // Close dialog
            this.hideImpairmentDialog();

            console.log(`[EventManager] Cleared impairments from ${bridge.name}`);

        } catch (error) {
            console.error('[EventManager] Error clearing impairments:', error);
            alert('Failed to clear impairments: ' + (error.message || 'Unknown error'));
            if (clearBtn) {
                clearBtn.disabled = false;
                clearBtn.textContent = 'Clear All';
            }
        }
    }

    /**
     * Enter focus mode for a node
     * @param {Object} node - Cytoscape node to focus on
     * @param {Object} options - Options for focus mode
     * @param {boolean} options.showIndicator - Whether to show focus indicator (default: true)
     */
    enterFocusMode(node, options = {}) {
        const { showIndicator = true } = options;

        // If already focused on this node, exit focus mode
        if (this.focusMode && this.focusedNode === node.id()) {
            this.exitFocusMode();
            return;
        }

        // Enter focus mode
        this.focusMode = true;
        this.focusedNode = node.id();

        // Clear any existing highlights
        this.cy.elements().removeClass('highlighted faded hover focused');

        // Get connected elements
        const connectedEdges = node.connectedEdges();
        const connectedNodes = connectedEdges.connectedNodes();

        // Apply focus styling
        node.addClass('focused');
        connectedEdges.addClass('highlighted');
        connectedNodes.addClass('highlighted');

        // Fade everything else
        this.cy.elements()
            .not(node)
            .not(connectedEdges)
            .not(connectedNodes)
            .addClass('faded');

        // Animate zoom to the focused node
        this.cy.animate({
            center: { eles: node },
            zoom: 1.5
        }, {
            duration: 400,
            easing: 'ease-out-cubic'
        });

        // Show focus mode indicator (unless suppressed, e.g., for auto-focus)
        if (showIndicator) {
            this.showFocusIndicator(node.data('label'));
        } else {
            this.hideFocusIndicator();
        }
    }

    /**
     * Exit focus mode and restore normal view
     */
    exitFocusMode() {
        if (!this.focusMode) return;

        this.focusMode = false;
        this.focusedNode = null;

        // Clear all focus-related classes
        this.cy.elements().removeClass('highlighted faded hover focused');

        // Hide focus indicator
        this.hideFocusIndicator();

        // Fit the graph back to view
        this.cy.animate({
            fit: { padding: 50 }
        }, {
            duration: 400,
            easing: 'ease-out-cubic'
        });
    }

    /**
     * Show focus mode indicator
     */
    showFocusIndicator(deviceName) {
        this.hideFocusIndicator();

        const indicator = document.createElement('div');
        indicator.id = 'focus-indicator';
        indicator.className = 'focus-mode-indicator';
        indicator.innerHTML = `
            <span class="focus-label">Focus: <strong>${deviceName}</strong></span>
            <button class="focus-exit-btn" title="Exit focus mode (Esc)">×</button>
        `;

        // Add click handler to exit button
        indicator.querySelector('.focus-exit-btn').addEventListener('click', () => {
            this.exitFocusMode();
        });

        this.container.appendChild(indicator);
    }

    /**
     * Hide focus mode indicator
     */
    hideFocusIndicator() {
        const existing = document.getElementById('focus-indicator');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Handle node mouse over - show tooltip and highlight connections
     */
    handleNodeMouseOver(evt) {
        // Don't show hover effects in focus mode
        if (this.focusMode) return;

        const node = evt.target;
        node.addClass('hover');

        // Highlight connected edges and nodes
        const connectedEdges = node.connectedEdges();
        const connectedNodes = connectedEdges.connectedNodes();

        connectedEdges.addClass('highlighted');
        connectedNodes.addClass('highlighted');

        // Fade non-connected elements
        this.cy.elements().not(node).not(connectedEdges).not(connectedNodes).addClass('faded');

        // Show tooltip
        this.showTooltip(evt);
    }

    /**
     * Handle node mouse out - hide tooltip and clear highlights
     */
    handleNodeMouseOut(evt) {
        // Don't clear in focus mode
        if (this.focusMode) return;

        const node = evt.target;
        node.removeClass('hover');
        this.hideTooltip();
        this.clearHighlights();
    }

    /**
     * Handle edge mouse over - highlight the edge and connected nodes
     */
    handleEdgeMouseOver(evt) {
        const edge = evt.target;
        edge.addClass('hover highlighted');

        const connectedNodes = edge.connectedNodes();
        connectedNodes.addClass('highlighted');

        // Fade other elements
        this.cy.elements().not(edge).not(connectedNodes).addClass('faded');

        // Show edge tooltip
        this.showEdgeTooltip(evt);
    }

    /**
     * Handle edge mouse out
     */
    handleEdgeMouseOut(evt) {
        const edge = evt.target;
        edge.removeClass('hover');
        this.hideTooltip();
        this.clearHighlights();
    }

    /**
     * Show tooltip for a node
     */
    showTooltip(evt) {
        const node = evt.target;
        const data = node.data();

        // Remove existing tooltip
        this.hideTooltip();

        // Create tooltip element
        const tooltip = document.createElement('div');
        tooltip.id = 'topo-tooltip';
        tooltip.className = 'topology-tooltip';

        // Build port list (only include connections to nodes that exist in diagram)
        let portsHtml = '';
        if (data.ports && data.ports.length > 0) {
            // Filter to only include ports where neighbor exists as a node in the topology
            const validPorts = data.ports.filter(p => this.cy.$id(p.neighbor).length > 0);

            if (validPorts.length > 0) {
                const portItems = validPorts.slice(0, 5).map(p =>
                    `<li>${p.port} → ${p.neighbor}:${p.neighbor_port}</li>`
                ).join('');
                const moreCount = validPorts.length - 5;
                portsHtml = `
                    <div class="tooltip-ports">
                        <strong>Connections:</strong>
                        <ul>${portItems}</ul>
                        ${moreCount > 0 ? `<em>+${moreCount} more</em>` : ''}
                    </div>
                `;
            }
        }

        // Format status display with indicator dot
        const status = data.status || 'unknown';
        const statusDisplay = status.charAt(0).toUpperCase() + status.slice(1);

        tooltip.innerHTML = `
            <div class="tooltip-header">
                <strong>${data.label}</strong>
                <span class="tooltip-type device-type-${data.device_type}">${data.device_type}</span>
            </div>
            <div class="tooltip-body">
                <div class="tooltip-row">
                    <span class="tooltip-label">IP:</span>
                    <span class="tooltip-value">${data.ip}</span>
                </div>
                <div class="tooltip-row">
                    <span class="tooltip-label">MAC:</span>
                    <span class="tooltip-value">${data.sys_mac}</span>
                </div>
                <div class="tooltip-row">
                    <span class="tooltip-label">Status:</span>
                    <span class="tooltip-value status-${status}">
                        <span class="status-indicator status-${status}"></span>${statusDisplay}
                    </span>
                </div>
                ${data.version ? `
                <div class="tooltip-row">
                    <span class="tooltip-label">Version:</span>
                    <span class="tooltip-value">${data.version}</span>
                </div>
                ` : ''}
                ${portsHtml}
            </div>
            <div class="tooltip-footer">
                Right-click for options
            </div>
        `;

        // Position tooltip using fixed positioning
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        tooltip.style.left = (renderedPos.x + containerRect.left + 15) + 'px';
        tooltip.style.top = (renderedPos.y + containerRect.top - 10) + 'px';

        document.body.appendChild(tooltip);
        this.tooltip = tooltip;

        // Adjust position if off-screen
        this.adjustTooltipPosition(tooltip);
    }

    /**
     * Show tooltip for an edge with interface statistics
     */
    showEdgeTooltip(evt) {
        const edge = evt.target;
        const data = edge.data();

        this.hideTooltip();

        // Clear any pending stats fetch
        if (this.statsDebounceTimer) {
            clearTimeout(this.statsDebounceTimer);
        }

        const tooltip = document.createElement('div');
        tooltip.id = 'topo-tooltip';
        tooltip.className = 'topology-tooltip edge-tooltip';

        // Initial tooltip with loading state for stats (escape HTML to prevent XSS)
        tooltip.innerHTML = `
            <div class="tooltip-header">
                <strong>Link Statistics</strong>
            </div>
            <div class="tooltip-body">
                <div class="tooltip-section">
                    <span class="section-title">${this.escapeHtml(data.source)}:${this.escapeHtml(data.source_port)}</span>
                    <div class="tooltip-stats-loading">Loading stats...</div>
                </div>
                <div class="tooltip-section">
                    <span class="section-title">${this.escapeHtml(data.target)}:${this.escapeHtml(data.target_port)}</span>
                    <div class="tooltip-stats-loading">Loading stats...</div>
                </div>
            </div>
        `;

        // Position tooltip using fixed positioning
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        tooltip.style.left = (renderedPos.x + containerRect.left + 15) + 'px';
        tooltip.style.top = (renderedPos.y + containerRect.top - 10) + 'px';

        document.body.appendChild(tooltip);
        this.tooltip = tooltip;

        this.adjustTooltipPosition(tooltip);

        // Debounce the stats fetch to avoid excessive API calls
        this.statsDebounceTimer = setTimeout(() => {
            this.fetchAndDisplayEdgeStats(edge, tooltip);
        }, this.statsDebounceDelay);
    }

    /**
     * Fetch interface stats for both ends of an edge and update tooltip
     */
    async fetchAndDisplayEdgeStats(edge, tooltip) {
        const data = edge.data();

        try {
            // Fetch stats for both interfaces in parallel
            const [sourceStats, targetStats] = await Promise.all([
                this.fetchInterfaceStats(data.source, data.source_port),
                this.fetchInterfaceStats(data.target, data.target_port)
            ]);

            // Check if tooltip is still visible (user might have moved away)
            if (!this.tooltip || this.tooltip !== tooltip) {
                return;
            }

            // Update tooltip with stats (pass edge for latency check)
            tooltip.innerHTML = this.buildEdgeStatsTooltipHTML(edge, data, sourceStats, targetStats);
            this.adjustTooltipPosition(tooltip);

            // Update edge styling based on utilization
            this.updateEdgeUtilizationClass(edge, sourceStats, targetStats);

        } catch (error) {
            console.error('[EventManager] Error fetching edge stats:', error);

            // Check if tooltip is still visible
            if (!this.tooltip || this.tooltip !== tooltip) {
                return;
            }

            // Show error state (escape HTML to prevent XSS)
            tooltip.innerHTML = `
                <div class="tooltip-header">
                    <strong>Link</strong>
                </div>
                <div class="tooltip-body">
                    <div class="tooltip-row">
                        <span class="tooltip-label">From:</span>
                        <span class="tooltip-value">${this.escapeHtml(data.source)}:${this.escapeHtml(data.source_port)}</span>
                    </div>
                    <div class="tooltip-row">
                        <span class="tooltip-label">To:</span>
                        <span class="tooltip-value">${this.escapeHtml(data.target)}:${this.escapeHtml(data.target_port)}</span>
                    </div>
                    <div class="tooltip-row tooltip-error">
                        <span class="tooltip-value">Stats unavailable</span>
                    </div>
                </div>
            `;
        }
    }

    /**
     * Fetch interface stats from API with caching
     */
    async fetchInterfaceStats(device, interfaceName) {
        const cacheKey = `${device}:${interfaceName}`;
        const now = Date.now();

        // Check cache
        if (this.statsCache[cacheKey]) {
            const cached = this.statsCache[cacheKey];
            if (now - cached.timestamp < this.statsCacheTTL) {
                return cached.data;
            }
        }

        // Fetch from API
        const response = await fetch(`/td-api/interface-stats?device=${encodeURIComponent(device)}&interface=${encodeURIComponent(interfaceName)}`);

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        const data = await response.json();

        // Cache the result
        this.statsCache[cacheKey] = {
            timestamp: now,
            data: data
        };

        return data;
    }

    /**
     * Build HTML for edge stats tooltip
     */
    buildEdgeStatsTooltipHTML(edge, edgeData, sourceStats, targetStats) {
        const formatRate = (bps) => {
            if (bps >= 1000000000) {
                return `${(bps / 1000000000).toFixed(2)} Gbps`;
            } else if (bps >= 1000000) {
                return `${(bps / 1000000).toFixed(2)} Mbps`;
            } else if (bps >= 1000) {
                return `${(bps / 1000).toFixed(2)} Kbps`;
            }
            return `${bps.toFixed(0)} bps`;
        };

        const formatUtilization = (pct) => {
            if (pct > 80) {
                return `<span class="utilization-high">${pct.toFixed(1)}%</span>`;
            } else if (pct > 50) {
                return `<span class="utilization-medium">${pct.toFixed(1)}%</span>`;
            }
            return `${pct.toFixed(1)}%`;
        };

        const formatErrors = (errors) => {
            if (errors > 0) {
                return `<span class="has-errors">${errors}</span>`;
            }
            return `<span class="no-errors">0</span>`;
        };

        const buildInterfaceSection = (title, stats) => {
            if (!stats || !stats.stats) {
                return `
                    <div class="tooltip-section">
                        <span class="section-title">${title}</span>
                        <div class="tooltip-row">
                            <span class="tooltip-value">Stats unavailable</span>
                        </div>
                    </div>
                `;
            }

            const s = stats.stats;
            const statusClass = s.operational_status === 'connected' ? 'status-up' : 'status-down';

            return `
                <div class="tooltip-section">
                    <span class="section-title">${title}</span>
                    <div class="tooltip-row">
                        <span class="tooltip-label">Status:</span>
                        <span class="tooltip-value ${statusClass}">${s.operational_status}</span>
                    </div>
                    <div class="tooltip-row">
                        <span class="tooltip-label">TX:</span>
                        <span class="tooltip-value">${formatRate(s.out_rate_bps)} (${formatUtilization(s.utilization_out)})</span>
                    </div>
                    <div class="tooltip-row">
                        <span class="tooltip-label">RX:</span>
                        <span class="tooltip-value">${formatRate(s.in_rate_bps)} (${formatUtilization(s.utilization_in)})</span>
                    </div>
                    <div class="tooltip-row">
                        <span class="tooltip-label">Errors:</span>
                        <span class="tooltip-value">${formatErrors(s.in_errors + s.out_errors)}</span>
                    </div>
                </div>
            `;
        };

        // Calculate time since last update
        let updateInfo = '';
        if (sourceStats && sourceStats.stats && sourceStats.stats.last_updated) {
            const lastUpdate = new Date(sourceStats.stats.last_updated);
            const secondsAgo = Math.round((Date.now() - lastUpdate.getTime()) / 1000);
            updateInfo = `<div class="tooltip-footer">Updated: ${secondsAgo}s ago</div>`;
        }

        // Check for impairment info
        const impairmentInfo = this.getEdgeImpairmentInfo(edge);
        let impairmentSection = '';
        if (impairmentInfo && impairmentInfo.hasAnyImpairment) {
            const rows = [];

            if (impairmentInfo.latency_ms > 0) {
                rows.push(`
                    <div class="tooltip-row impairment-row">
                        <span class="tooltip-label latency-label">Latency:</span>
                        <span class="tooltip-value impairment-value latency">${impairmentInfo.latency_ms}ms</span>
                    </div>
                `);
            }
            if (impairmentInfo.loss_percent > 0) {
                rows.push(`
                    <div class="tooltip-row impairment-row">
                        <span class="tooltip-label loss-label">Packet Loss:</span>
                        <span class="tooltip-value impairment-value loss">${impairmentInfo.loss_percent}%</span>
                    </div>
                `);
            }
            if (impairmentInfo.dup_percent > 0) {
                rows.push(`
                    <div class="tooltip-row impairment-row">
                        <span class="tooltip-label dup-label">Duplication:</span>
                        <span class="tooltip-value impairment-value dup">${impairmentInfo.dup_percent}%</span>
                    </div>
                `);
            }
            if (impairmentInfo.corrupt_percent > 0) {
                rows.push(`
                    <div class="tooltip-row impairment-row">
                        <span class="tooltip-label corrupt-label">Corruption:</span>
                        <span class="tooltip-value impairment-value corrupt">${impairmentInfo.corrupt_percent}%</span>
                    </div>
                `);
            }
            if (impairmentInfo.reorder_percent > 0) {
                rows.push(`
                    <div class="tooltip-row impairment-row">
                        <span class="tooltip-label reorder-label">Reorder:</span>
                        <span class="tooltip-value impairment-value reorder">${impairmentInfo.reorder_percent}% @ ${impairmentInfo.reorder_delay_ms}ms</span>
                    </div>
                `);
            }

            if (rows.length > 0) {
                impairmentSection = `
                    <div class="tooltip-section impairment-section">
                        <span class="section-title">Network Impairments</span>
                        ${rows.join('')}
                    </div>
                `;
            }
        }

        // Escape device/port names for XSS prevention
        const srcLabel = `${this.escapeHtml(edgeData.source)}:${this.escapeHtml(edgeData.source_port)}`;
        const tgtLabel = `${this.escapeHtml(edgeData.target)}:${this.escapeHtml(edgeData.target_port)}`;

        return `
            <div class="tooltip-header">
                <strong>Link Statistics</strong>
            </div>
            <div class="tooltip-body">
                ${impairmentSection}
                ${buildInterfaceSection(srcLabel, sourceStats)}
                ${buildInterfaceSection(tgtLabel, targetStats)}
            </div>
            ${updateInfo}
        `;
    }

    /**
     * Update edge CSS class based on utilization
     */
    updateEdgeUtilizationClass(edge, sourceStats, targetStats) {
        // Remove existing utilization classes
        edge.removeClass('utilization-low utilization-medium utilization-high utilization-critical has-errors');

        // Get max utilization from either end
        let maxUtilization = 0;
        let hasErrors = false;

        if (sourceStats && sourceStats.stats) {
            maxUtilization = Math.max(maxUtilization, sourceStats.stats.utilization_in, sourceStats.stats.utilization_out);
            hasErrors = hasErrors || (sourceStats.stats.in_errors + sourceStats.stats.out_errors) > 0;
        }
        if (targetStats && targetStats.stats) {
            maxUtilization = Math.max(maxUtilization, targetStats.stats.utilization_in, targetStats.stats.utilization_out);
            hasErrors = hasErrors || (targetStats.stats.in_errors + targetStats.stats.out_errors) > 0;
        }

        // Apply appropriate class
        if (hasErrors) {
            edge.addClass('has-errors');
        } else if (maxUtilization > 95) {
            edge.addClass('utilization-critical');
        } else if (maxUtilization > 80) {
            edge.addClass('utilization-high');
        } else if (maxUtilization > 50) {
            edge.addClass('utilization-medium');
        } else if (maxUtilization > 25) {
            edge.addClass('utilization-low');
        }
    }

    /**
     * Adjust tooltip position to keep it on screen
     */
    adjustTooltipPosition(tooltip) {
        const rect = tooltip.getBoundingClientRect();
        const viewportWidth = window.innerWidth;
        const viewportHeight = window.innerHeight;

        // Adjust horizontal position
        if (rect.right > viewportWidth - 10) {
            tooltip.style.left = (viewportWidth - rect.width - 20) + 'px';
        }
        if (rect.left < 10) {
            tooltip.style.left = '10px';
        }

        // Adjust vertical position
        if (rect.bottom > viewportHeight - 10) {
            tooltip.style.top = (viewportHeight - rect.height - 20) + 'px';
        }
        if (rect.top < 10) {
            tooltip.style.top = '10px';
        }
    }

    /**
     * Hide the tooltip
     */
    hideTooltip() {
        if (this.tooltip) {
            this.tooltip.remove();
            this.tooltip = null;
        }
        const existing = document.getElementById('topo-tooltip');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Clear all highlights and fades
     */
    clearHighlights() {
        this.cy.elements().removeClass('highlighted faded hover');
    }

    /**
     * Handle keyboard shortcuts
     */
    handleKeyDown(evt) {
        // Escape - close modals, context menu, details panel, exit focus mode, clear selection and highlights
        if (evt.key === 'Escape') {
            this.hideRunningConfigModal();
            this.hideContextMenu();
            this.hideDetailsPanel();
            if (this.focusMode) {
                this.exitFocusMode();
            } else {
                this.cy.$(':selected').unselect();
                this.clearHighlights();
            }
            this.hideTooltip();
        }

        // 'f' - fit graph to view (exits focus mode first)
        if (evt.key === 'f' && !evt.ctrlKey && !evt.metaKey) {
            const activeElement = document.activeElement;
            if (activeElement.tagName !== 'INPUT' && activeElement.tagName !== 'TEXTAREA') {
                this.hideContextMenu();
                this.exitFocusMode();
                this.cy.fit(50);
            }
        }

        // 'r' - reset zoom (exits focus mode first)
        if (evt.key === 'r' && !evt.ctrlKey && !evt.metaKey) {
            const activeElement = document.activeElement;
            if (activeElement.tagName !== 'INPUT' && activeElement.tagName !== 'TEXTAREA') {
                this.hideContextMenu();
                this.exitFocusMode();
                this.cy.reset();
            }
        }
    }

    /**
     * Highlight shortest path between two nodes
     */
    highlightPath(sourceId, targetId) {
        this.clearHighlights();

        const source = this.cy.$id(sourceId);
        const target = this.cy.$id(targetId);

        if (source.empty() || target.empty()) {
            console.warn('Source or target node not found');
            return;
        }

        const dijkstra = this.cy.elements().dijkstra(source, function(edge) {
            return 1; // Unweighted
        });

        const path = dijkstra.pathTo(target);

        if (path.empty()) {
            console.warn('No path found between nodes');
            return;
        }

        // Highlight path
        path.addClass('highlighted');

        // Fade other elements
        this.cy.elements().not(path).addClass('faded');
    }

    /**
     * Show static details panel for a node (bottom-left, copyable)
     */
    showDetailsPanel(node) {
        const data = node.data();

        // Hide existing panel
        this.hideDetailsPanel();

        // Create details panel
        const panel = document.createElement('div');
        panel.id = 'topo-details-panel';
        panel.className = 'topology-details-panel';

        // Build ports/connections list (only include connections to nodes that exist in diagram)
        let portsHtml = '';
        if (data.ports && data.ports.length > 0) {
            // Filter to only include ports where neighbor exists as a node in the topology
            const validPorts = data.ports.filter(port => {
                return this.cy.$id(port.neighbor).length > 0;
            });

            if (validPorts.length > 0) {
                const portItems = validPorts.map(port =>
                    `<li><span class="port-local">${port.port}</span> → <span class="port-remote">${port.neighbor}:${port.neighbor_port}</span></li>`
                ).join('');
                portsHtml = `
                    <div class="details-section">
                        <div class="details-section-title">Connections</div>
                        <ul class="details-ports-list">${portItems}</ul>
                    </div>
                `;
            }
        }

        // Format status display
        const status = data.status || 'unknown';
        const statusDisplay = status.charAt(0).toUpperCase() + status.slice(1);

        panel.innerHTML = `
            <div class="details-header">
                <span class="details-title">${data.label}</span>
                <span class="details-type device-type-${data.device_type}">${data.device_type}</span>
                <button class="details-close-btn" title="Close (Esc)">×</button>
            </div>
            <div class="details-body">
                <div class="details-row">
                    <span class="details-label">IP Address:</span>
                    <span class="details-value selectable">${data.ip || 'N/A'}</span>
                </div>
                <div class="details-row">
                    <span class="details-label">MAC Address:</span>
                    <span class="details-value selectable">${data.sys_mac || 'N/A'}</span>
                </div>
                <div class="details-row">
                    <span class="details-label">Status:</span>
                    <span class="details-value">
                        <span class="status-indicator status-${status}"></span>${statusDisplay}
                    </span>
                </div>
                ${data.version ? `
                <div class="details-row">
                    <span class="details-label">Version:</span>
                    <span class="details-value selectable">${data.version}</span>
                </div>
                ` : ''}
                ${portsHtml}
            </div>
            <div class="details-footer">
                <span class="details-hint">Text is selectable for copying</span>
            </div>
        `;

        // Close button handler
        panel.querySelector('.details-close-btn').addEventListener('click', () => {
            this.hideDetailsPanel();
        });

        // Prevent clicks inside panel from closing it
        panel.addEventListener('click', (e) => {
            e.stopPropagation();
        });

        // Position panel relative to the container
        const containerRect = this.container.getBoundingClientRect();
        panel.style.position = 'fixed';
        panel.style.bottom = (window.innerHeight - containerRect.bottom + 15) + 'px';
        panel.style.left = (containerRect.left + 15) + 'px';

        // Append to body to avoid Cytoscape capturing wheel events
        document.body.appendChild(panel);
        this.detailsPanel = panel;
    }

    /**
     * Hide the static details panel
     */
    hideDetailsPanel() {
        if (this.detailsPanel) {
            this.detailsPanel.remove();
            this.detailsPanel = null;
        }
        const existing = document.getElementById('topo-details-panel');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Show running config modal for a device
     */
    showRunningConfigModal(node) {
        const data = node.data();
        const deviceName = data.label;

        // Hide any existing modal
        this.hideRunningConfigModal();

        // Create overlay
        const overlay = document.createElement('div');
        overlay.id = 'running-config-overlay';
        overlay.className = 'running-config-overlay';

        // Create modal
        const modal = document.createElement('div');
        modal.id = 'running-config-modal';
        modal.className = 'running-config-modal';

        modal.innerHTML = `
            <div class="running-config-header">
                <span class="running-config-title">${deviceName} - Running Config</span>
                <div class="running-config-actions">
                    <button class="running-config-copy-btn" title="Copy to Clipboard">
                        <span class="copy-icon">📋</span>
                        <span class="copy-text">Copy</span>
                    </button>
                    <button class="running-config-close-btn" title="Close (Esc)">×</button>
                </div>
            </div>
            <div class="running-config-body">
                <div class="running-config-loading">
                    <div class="loading-spinner"></div>
                    <span>Fetching configuration...</span>
                </div>
            </div>
        `;

        // Close button handler
        modal.querySelector('.running-config-close-btn').addEventListener('click', () => {
            this.hideRunningConfigModal();
        });

        // Copy button handler
        const copyBtn = modal.querySelector('.running-config-copy-btn');
        copyBtn.addEventListener('click', () => {
            const content = modal.querySelector('.running-config-content');
            if (content) {
                navigator.clipboard.writeText(content.textContent).then(() => {
                    // Show copied feedback
                    const copyText = copyBtn.querySelector('.copy-text');
                    const originalText = copyText.textContent;
                    copyText.textContent = 'Copied!';
                    copyBtn.classList.add('copied');
                    setTimeout(() => {
                        copyText.textContent = originalText;
                        copyBtn.classList.remove('copied');
                    }, 2000);
                });
            }
        });

        // Close on overlay click
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hideRunningConfigModal();
            }
        });

        // Position overlay to cover the container
        const containerRect = this.container.getBoundingClientRect();
        overlay.style.position = 'fixed';
        overlay.style.top = containerRect.top + 'px';
        overlay.style.left = containerRect.left + 'px';
        overlay.style.width = containerRect.width + 'px';
        overlay.style.height = containerRect.height + 'px';

        overlay.appendChild(modal);
        // Append to body to avoid Cytoscape capturing wheel events
        document.body.appendChild(overlay);
        this.runningConfigModal = overlay;

        // Fetch the running config
        this.fetchRunningConfig(deviceName, modal);
    }

    /**
     * Fetch running config from API
     */
    async fetchRunningConfig(deviceName, modal) {
        const body = modal.querySelector('.running-config-body');

        try {
            const response = await fetch(`/td-api/running-config?device=${encodeURIComponent(deviceName)}`);

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `HTTP ${response.status}`);
            }

            const data = await response.json();

            // Display the config
            body.innerHTML = `
                <pre class="running-config-content">${this.escapeHtml(data.config)}</pre>
            `;

        } catch (error) {
            console.error('Failed to fetch running config:', error);
            body.innerHTML = `
                <div class="running-config-error">
                    <span class="error-icon">⚠️</span>
                    <span class="error-message">Failed to fetch configuration</span>
                    <span class="error-detail">${this.escapeHtml(error.message)}</span>
                    <button class="retry-btn" onclick="this.closest('.running-config-overlay').remove()">Close</button>
                </div>
            `;
        }
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Hide the running config modal
     */
    hideRunningConfigModal() {
        if (this.runningConfigModal) {
            this.runningConfigModal.remove();
            this.runningConfigModal = null;
        }
        const existing = document.getElementById('running-config-overlay');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Destroy event handlers and clean up resources
     */
    destroy() {
        this.hideTooltip();
        this.hideContextMenu();
        this.hideDetailsPanel();
        this.hideRunningConfigModal();
        this.hideFocusIndicator();
        this.hideLatencyDialog();

        // Remove global listeners to prevent memory leak
        document.removeEventListener('keydown', this.boundKeyDownHandler);
        document.removeEventListener('click', this.boundClickHandler);

        // Remove all Cytoscape event listeners
        this.cy.removeAllListeners();
    }
}

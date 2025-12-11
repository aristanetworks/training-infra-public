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

        // Latency state: { bridgeName: { delay_ms: number, edge: cytoscape edge } }
        this.latencyState = {};
        // Latency change callback (for TopologyManager to update edge styles)
        this.onLatencyChange = options.onLatencyChange || null;
        // Latency dialog reference
        this.latencyDialog = null;

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

        // Build descriptive link label
        const linkLabel = `${data.source}:${data.source_port} ↔ ${data.target}:${data.target_port}`;

        // Check if this edge has latency applied
        const edgeBridgeName = this.getEdgeBridgeName(edge);
        const latencyInfo = edgeBridgeName ? this.latencyState[edgeBridgeName] : null;
        const hasLatency = latencyInfo && latencyInfo.delay_ms;

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
            // Latency options
            hasLatency ? {
                label: `Remove Latency (${latencyInfo.delay_ms}ms)`,
                action: () => {
                    this.removeLatency(edge);
                    this.hideContextMenu();
                },
                disabled: this.isCeosLab
            } : {
                label: this.isCeosLab ? 'Add Latency (vEOS only)' : 'Add Latency',
                action: () => {
                    this.showLatencyDialog(edge);
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
     * Get the bridge name for an edge based on device/port names
     * Bridge naming convention: {prefix}{device#}{port#}-{prefix}{device#}{port#}
     */
    getEdgeBridgeName(edge) {
        const data = edge.data();
        // The bridge name is stored in edge data if available
        if (data.bridge_name) {
            return data.bridge_name;
        }
        // Otherwise construct from source/target (this may not exactly match bridge names)
        // Return null to indicate we need to look up from the API
        return null;
    }

    /**
     * Show latency dialog for an edge
     */
    showLatencyDialog(edge) {
        // Hide any existing dialog
        this.hideLatencyDialog();

        const data = edge.data();
        const linkLabel = `${data.source}:${data.source_port} ↔ ${data.target}:${data.target_port}`;

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

        // Initial tooltip with loading state for stats
        tooltip.innerHTML = `
            <div class="tooltip-header">
                <strong>Link Statistics</strong>
            </div>
            <div class="tooltip-body">
                <div class="tooltip-section">
                    <span class="section-title">${data.source}:${data.source_port}</span>
                    <div class="tooltip-stats-loading">Loading stats...</div>
                </div>
                <div class="tooltip-section">
                    <span class="section-title">${data.target}:${data.target_port}</span>
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

            // Update tooltip with stats
            tooltip.innerHTML = this.buildEdgeStatsTooltipHTML(data, sourceStats, targetStats);
            this.adjustTooltipPosition(tooltip);

            // Update edge styling based on utilization
            this.updateEdgeUtilizationClass(edge, sourceStats, targetStats);

        } catch (error) {
            console.error('[EventManager] Error fetching edge stats:', error);

            // Check if tooltip is still visible
            if (!this.tooltip || this.tooltip !== tooltip) {
                return;
            }

            // Show error state
            tooltip.innerHTML = `
                <div class="tooltip-header">
                    <strong>Link</strong>
                </div>
                <div class="tooltip-body">
                    <div class="tooltip-row">
                        <span class="tooltip-label">From:</span>
                        <span class="tooltip-value">${data.source}:${data.source_port}</span>
                    </div>
                    <div class="tooltip-row">
                        <span class="tooltip-label">To:</span>
                        <span class="tooltip-value">${data.target}:${data.target_port}</span>
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
    buildEdgeStatsTooltipHTML(edgeData, sourceStats, targetStats) {
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

        return `
            <div class="tooltip-header">
                <strong>Link Statistics</strong>
            </div>
            <div class="tooltip-body">
                ${buildInterfaceSection(`${edgeData.source}:${edgeData.source_port}`, sourceStats)}
                ${buildInterfaceSection(`${edgeData.target}:${edgeData.target_port}`, targetStats)}
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

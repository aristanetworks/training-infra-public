/**
 * Topology Manager for ATL Interactive Topology Diagram
 * Main orchestrator that brings together all topology components
 */

import { getCytoscapeStyles } from './cytoscape-styles.js';
import { getLayout, LAYOUT_OPTIONS } from './layout-config.js';
import { EventManager } from './event-handlers.js';
import { FilterManager, DEVICE_TYPE_INFO, loadDeviceTypeInfo, getDeviceTypeInfo } from './filter-manager.js';
import { StatusUpdater } from './status-updater.js';
import { CapturePanel } from './capture-panel.js';

/**
 * Impairment type color constants
 * Used for edge styling and gradient calculation
 */
export const IMPAIRMENT_COLORS = {
    latency: '#fbb500',      // Yellow - ATL secondary color
    loss: '#e30909',         // Red - Error/danger
    duplication: '#4c5cae',  // Blue - Primary accent
    corruption: '#ff8c00',   // Orange - Warning
    reorder: '#9b59b6'       // Purple - Reorder/jitter
};

export class TopologyManager {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.options = {
            apiUrl: options.apiUrl || '/td-api/topology',
            wsUrl: options.wsUrl || this.getDefaultWsUrl(),
            layout: options.layout || 'preset',  // Default to preset (server-calculated positions)
            enableStatus: options.enableStatus !== false,
            enableFilters: options.enableFilters !== false,
            ...options
        };

        this.cy = null;
        this.eventManager = null;
        this.filterManager = null;
        this.statusUpdater = null;
        this.capturePanel = null;  // Packet capture panel
        this.isInitialized = false;
        this.topologyData = null;
        this.originalPositions = {};  // Store original positions for reset
        this.positionStorageKey = 'atd-topology-positions';  // localStorage key for saved positions
    }

    /**
     * Get default WebSocket URL based on current location
     */
    getDefaultWsUrl() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        return `${protocol}//${window.location.host}/td-ws`;
    }

    /**
     * Initialize the topology diagram
     */
    async init() {
        if (this.isInitialized) {
            console.warn('[TopologyManager] Already initialized');
            return;
        }

        try {
            this.showLoading();

            // Load device type metadata from API (in parallel with topology fetch)
            const [_, topologyData] = await Promise.all([
                loadDeviceTypeInfo(),
                this.fetchTopology()
            ]);
            this.topologyData = topologyData;

            if (!this.topologyData || !this.topologyData.nodes) {
                throw new Error('Invalid topology data received');
            }

            // Initialize Cytoscape
            this.initCytoscape();

            // Extract eos_type from metadata (for cEOS detection)
            const eosType = this.topologyData.metadata?.eos_type || 'veos';

            // Setup components
            console.log('[TopologyManager] onOpenTerminal option:', this.options.onOpenTerminal ? 'provided' : 'not provided');
            console.log('[TopologyManager] eos_type:', eosType);
            this.eventManager = new EventManager(this.cy, this.container, {
                onOpenTerminal: this.options.onOpenTerminal,
                eosType: eosType,
                onLatencyChange: (bridgeName, delayMs, edge) => this.handleLatencyChange(bridgeName, delayMs, edge),
                onImpairmentChange: (bridgeName, impairments, edge) => this.handleImpairmentChange(bridgeName, impairments, edge)
            });

            if (this.options.enableFilters) {
                this.filterManager = new FilterManager(this.cy, this.container);
            }

            // Initialize capture panel (if not disabled)
            if (this.options.enableCapture !== false) {
                this.capturePanel = new CapturePanel({
                    maxPackets: 5000,
                    onEdgeHighlight: (bridgeName) => this.highlightBridgeEdge(bridgeName)
                });
                this.capturePanel.init();

                // Connect capture panel to event manager
                if (this.eventManager) {
                    this.eventManager.capturePanel = this.capturePanel;
                }
            }

            // Load initial impairment state (don't block on this, but do it early)
            this.loadImpairmentStatus().catch(err => {
                console.warn('[TopologyManager] Failed to load initial impairment status:', err);
            });

            // Add help button
            this.createHelpButton();

            // Add impairment controls (hidden by default, shown when impairments are active)
            this.createImpairmentControls();

            if (this.options.enableStatus) {
                this.statusUpdater = new StatusUpdater(this.cy, this.options.wsUrl);
                this.statusUpdater.connect();
                // Start polling device status via eAPI for real-time status indicators
                this.statusUpdater.startStatusPolling();
            }

            // Initialize AddNodeWizard for dynamic node addition (KVM labs only)
            if (typeof window.AddNodeWizard !== 'undefined') {
                window.addNodeWizard = new window.AddNodeWizard(this);
                console.log('[TopologyManager] AddNodeWizard initialized');

                // Check if there are user nodes that need restoration (after reboot)
                // This shows a notification banner if user-added VMs are not running
                window.addNodeWizard.showRestoreNotificationIfNeeded();
            }

            // Initialize AddClusterWizard for cluster addition (KVM labs only)
            if (typeof window.AddClusterWizard !== 'undefined') {
                window.addClusterWizard = new window.AddClusterWizard(this);
                console.log('[TopologyManager] AddClusterWizard initialized');
            }

            this.isInitialized = true;
            this.hideLoading();

            console.log('[TopologyManager] Initialized successfully', {
                nodes: this.topologyData.nodes.length,
                edges: this.topologyData.edges.length
            });

            return this;

        } catch (error) {
            console.error('[TopologyManager] Initialization failed', error);
            this.showError(error.message);
            throw error;
        }
    }

    /**
     * Fetch topology data from API
     */
    async fetchTopology() {
        const response = await fetch(this.options.apiUrl);

        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        return await response.json();
    }

    /**
     * Initialize Cytoscape instance
     */
    initCytoscape() {
        // Prepare elements
        const elements = [
            ...this.topologyData.nodes,
            ...this.topologyData.edges
        ];

        // Store original positions for reset functionality
        this.topologyData.nodes.forEach(node => {
            if (node.position) {
                this.originalPositions[node.data.id] = {
                    x: node.position.x,
                    y: node.position.y
                };
            }
        });

        // Create Cytoscape instance
        // Use preset layout with no animation in constructor - we handle layout manually below
        this.cy = cytoscape({
            container: this.container,
            elements: elements,
            style: getCytoscapeStyles(),
            layout: { name: 'preset' },  // Use preset positions from server data, no animation
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
            boxSelectionEnabled: true,      // Enable box/marquee selection by dragging on background
            selectionType: 'additive',      // Allow multi-select with Shift+click or box selection
            autoungrabifyNodes: false,      // Ensure nodes are draggable
            panningEnabled: true,
            userPanningEnabled: true
        });

        // Check if we have saved positions before running layout
        const hasSavedPositions = this.hasSavedPositions();

        if (hasSavedPositions) {
            // Skip layout animation and directly apply saved positions
            // This prevents the race condition where layout animation overwrites saved positions
            this.loadSavedPositions();
            console.log('[TopologyManager] Applied saved positions, skipped layout animation');
        } else {
            // Run layout with animation (no saved positions to apply)
            const layout = this.cy.layout(getLayout(this.options.layout));
            layout.run();
        }

        // Setup drag event handler to save positions when nodes are moved
        this.cy.on('free', 'node', () => {
            this.savePositions();
        });
    }

    /**
     * Check if there are saved positions in localStorage
     * @returns {boolean} True if saved positions exist
     */
    hasSavedPositions() {
        try {
            const saved = localStorage.getItem(this.positionStorageKey);
            if (!saved) return false;
            const positions = JSON.parse(saved);
            return Object.keys(positions).length > 0;
        } catch (error) {
            return false;
        }
    }

    /**
     * Save current node positions to localStorage
     */
    savePositions() {
        if (!this.cy) return;

        try {
            const positions = {};
            this.cy.nodes().forEach(node => {
                const pos = node.position();
                positions[node.id()] = { x: pos.x, y: pos.y };
            });

            localStorage.setItem(this.positionStorageKey, JSON.stringify(positions));
            console.log('[TopologyManager] Saved positions for', Object.keys(positions).length, 'nodes');
        } catch (error) {
            console.warn('[TopologyManager] Failed to save positions:', error);
        }
    }

    /**
     * Load saved positions from localStorage and apply to nodes
     */
    loadSavedPositions() {
        if (!this.cy) return;

        try {
            const saved = localStorage.getItem(this.positionStorageKey);
            if (!saved) return;

            const positions = JSON.parse(saved);
            let appliedCount = 0;

            this.cy.nodes().forEach(node => {
                const savedPos = positions[node.id()];
                if (savedPos && typeof savedPos.x === 'number' && typeof savedPos.y === 'number') {
                    node.position(savedPos);
                    appliedCount++;
                }
            });

            if (appliedCount > 0) {
                console.log('[TopologyManager] Loaded saved positions for', appliedCount, 'nodes');
                // Fit to view after applying positions
                this.cy.fit(50);
            }
        } catch (error) {
            console.warn('[TopologyManager] Failed to load saved positions:', error);
        }
    }

    /**
     * Clear saved positions and reset to server-calculated positions
     */
    clearSavedPositions() {
        try {
            localStorage.removeItem(this.positionStorageKey);
            console.log('[TopologyManager] Cleared saved positions');
        } catch (error) {
            console.warn('[TopologyManager] Failed to clear saved positions:', error);
        }
    }

    /**
     * Reset layout to server-calculated positions and clear saved positions
     */
    resetToServerPositions() {
        // Clear saved positions
        this.clearSavedPositions();

        // Animate to original server positions
        if (Object.keys(this.originalPositions).length > 0) {
            this.cy.nodes().forEach(node => {
                const originalPos = this.originalPositions[node.id()];
                if (originalPos) {
                    node.animate({
                        position: originalPos
                    }, {
                        duration: 400,
                        easing: 'ease-out-cubic'
                    });
                }
            });

            // Fit after animation completes
            setTimeout(() => {
                this.cy.animate({
                    fit: { padding: 50 }
                }, {
                    duration: 200
                });
            }, 400);
        }
    }

    /**
     * Show loading indicator
     */
    showLoading() {
        if (!this.container) return;

        const loader = document.createElement('div');
        loader.id = 'topo-loader';
        loader.className = 'topology-loader';
        loader.innerHTML = `
            <div class="loader-spinner"></div>
            <div class="loader-text">Loading topology...</div>
        `;
        this.container.appendChild(loader);
    }

    /**
     * Hide loading indicator
     */
    hideLoading() {
        const loader = document.getElementById('topo-loader');
        if (loader) {
            loader.remove();
        }
    }

    /**
     * Show error message
     * @param {string} message - Error message to display
     */
    showError(message) {
        this.hideLoading();

        if (!this.container) return;

        // Create error container
        const error = document.createElement('div');
        error.id = 'topo-error';
        error.className = 'topology-error';
        error.setAttribute('role', 'alert');
        error.setAttribute('aria-live', 'assertive');

        // Create error icon
        const errorIcon = document.createElement('div');
        errorIcon.className = 'error-icon';
        errorIcon.textContent = '!';

        // Create error title
        const errorTitle = document.createElement('div');
        errorTitle.className = 'error-title';
        errorTitle.textContent = 'Failed to load topology';

        // Create error message (escaped to prevent XSS)
        const errorMessage = document.createElement('div');
        errorMessage.className = 'error-message';
        errorMessage.textContent = message;

        // Create retry button with proper event listener (no inline onclick)
        const retryBtn = document.createElement('button');
        retryBtn.className = 'error-retry';
        retryBtn.textContent = 'Retry';
        retryBtn.addEventListener('click', () => location.reload());

        // Assemble error element
        error.appendChild(errorIcon);
        error.appendChild(errorTitle);
        error.appendChild(errorMessage);
        error.appendChild(retryBtn);

        this.container.appendChild(error);
    }

    /**
     * Change layout algorithm
     */
    setLayout(layoutName) {
        if (!this.cy) return;

        // For preset layout, restore original server-calculated positions with animation
        if (layoutName === 'preset' && Object.keys(this.originalPositions).length > 0) {
            // Clear saved positions so next load uses server positions
            this.clearSavedPositions();

            this.cy.nodes().forEach(node => {
                const originalPos = this.originalPositions[node.id()];
                if (originalPos) {
                    node.animate({
                        position: originalPos
                    }, {
                        duration: 400,
                        easing: 'ease-out-cubic'
                    });
                }
            });
            // Fit after animation completes
            setTimeout(() => {
                this.cy.animate({
                    fit: { padding: 50 }
                }, {
                    duration: 200
                });
            }, 400);
        } else {
            const layout = getLayout(layoutName);
            this.cy.layout(layout).run();
        }

        this.options.layout = layoutName;
    }

    /**
     * Fit graph to container
     */
    fit(padding = 50) {
        if (this.cy) {
            this.cy.fit(padding);
        }
    }

    /**
     * Reset zoom and pan
     */
    reset() {
        if (this.cy) {
            this.cy.reset();
        }
    }

    /**
     * Center on a specific node
     */
    centerOnNode(nodeId, zoom = 1.5) {
        if (!this.cy) return;

        const node = this.cy.$id(nodeId);
        if (!node.empty()) {
            this.cy.animate({
                center: { eles: node },
                zoom: zoom,
                duration: 500
            });
        }
    }

    /**
     * Focus on a device by name - highlights the node and its connections
     * Used by terminal page auto-focus feature
     * @param {string} deviceName - Name of the device to focus on
     * @param {Object} options - Options for focus behavior
     * @param {boolean} options.animate - Whether to animate the transition (default: true)
     * @param {boolean} options.showIndicator - Whether to show focus indicator (default: false for API calls)
     * @returns {boolean} - True if device was found and focused
     */
    focusOnDevice(deviceName, options = {}) {
        const { animate = true, showIndicator = false } = options;

        if (!this.cy || !deviceName) return false;

        // Find node by label (device name) - case insensitive
        const deviceNameLower = deviceName.toLowerCase();
        const node = this.cy.nodes().filter(n =>
            n.data('label').toLowerCase() === deviceNameLower
        ).first();

        if (node.empty()) {
            console.warn('[TopologyManager] Device not found:', deviceName);
            return false;
        }

        // Use EventManager's enterFocusMode if available
        if (this.eventManager) {
            this.eventManager.enterFocusMode(node, { showIndicator });
        } else {
            // Fallback: manual focus without EventManager
            this.cy.elements().removeClass('highlighted faded hover focused');

            const connectedEdges = node.connectedEdges();
            const connectedNodes = connectedEdges.connectedNodes();

            node.addClass('focused');
            connectedEdges.addClass('highlighted');
            connectedNodes.addClass('highlighted');

            this.cy.elements()
                .not(node)
                .not(connectedEdges)
                .not(connectedNodes)
                .addClass('faded');

            if (animate) {
                this.cy.animate({
                    center: { eles: node },
                    zoom: 1.5
                }, {
                    duration: 400,
                    easing: 'ease-out-cubic'
                });
            }
        }

        return true;
    }

    /**
     * Clear focus mode and restore normal view
     */
    clearFocus() {
        if (this.eventManager) {
            this.eventManager.exitFocusMode();
        } else {
            this.cy.elements().removeClass('highlighted faded hover focused');
            this.cy.animate({
                fit: { padding: 50 }
            }, {
                duration: 400,
                easing: 'ease-out-cubic'
            });
        }
    }

    /**
     * Get node by ID
     */
    getNode(nodeId) {
        if (!this.cy) return null;
        const node = this.cy.$id(nodeId);
        return node.empty() ? null : node;
    }

    /**
     * Get all nodes
     */
    getNodes() {
        if (!this.cy) return [];
        return this.cy.nodes().toArray();
    }

    /**
     * Get all edges
     */
    getEdges() {
        if (!this.cy) return [];
        return this.cy.edges().toArray();
    }

    /**
     * Search for nodes by name or IP
     */
    search(term) {
        if (this.filterManager) {
            this.filterManager.setSearch(term);
        }
    }

    /**
     * Toggle device type visibility
     */
    toggleDeviceType(deviceType) {
        if (this.filterManager) {
            return this.filterManager.toggleFilter(deviceType);
        }
        return true;
    }

    /**
     * Get filter state
     */
    getFilters() {
        if (this.filterManager) {
            return this.filterManager.getFilters();
        }
        return {};
    }

    /**
     * Reset all filters
     */
    resetFilters() {
        if (this.filterManager) {
            this.filterManager.resetFilters();
        }
    }

    /**
     * Get device type counts
     */
    getDeviceTypeCounts() {
        if (this.filterManager) {
            return this.filterManager.getDeviceTypeCounts();
        }
        return {};
    }

    /**
     * Highlight path between two nodes
     */
    highlightPath(sourceId, targetId) {
        if (this.eventManager) {
            this.eventManager.highlightPath(sourceId, targetId);
        }
    }

    /**
     * Clear all highlights and exit focus mode
     */
    clearHighlights() {
        if (this.eventManager) {
            this.eventManager.exitFocusMode();
            this.eventManager.clearHighlights();
        }
    }

    /**
     * Highlight edge corresponding to a bridge name
     * Bridge naming convention: {dev1-short}{port1}-{dev2-short}{port2}
     * e.g., sp1Et1-le1Et1 for spine1:Ethernet1 <-> leaf1:Ethernet1
     */
    highlightBridgeEdge(bridgeName) {
        if (!this.cy || !bridgeName) return;

        // For now, just clear any existing highlights
        // In the future, we could parse the bridge name and find the matching edge
        this.cy.edges().removeClass('edge-capturing');

        console.log('[TopologyManager] Highlighting bridge edge:', bridgeName);
        // Edge highlighting by bridge name could be implemented by matching
        // the short device codes in the bridge name to full device names
    }

    /**
     * Show the capture panel
     */
    showCapturePanel() {
        if (this.capturePanel) {
            this.capturePanel.show();
        }
    }

    /**
     * Hide the capture panel
     */
    hideCapturePanel() {
        if (this.capturePanel) {
            this.capturePanel.hide();
        }
    }

    /**
     * Load impairment status from API and update edge styles
     */
    async loadImpairmentStatus() {
        try {
            const response = await fetch('/td-api/impairments/bridges');
            if (!response.ok) {
                console.warn('[TopologyManager] Failed to fetch impairment status');
                return;
            }

            const data = await response.json();
            const bridges = data.bridges || [];

            // Update edge styles for bridges with impairments
            // Also update EventManager's impairment state with edge references
            for (const bridge of bridges) {
                const impairments = bridge.impairments || {};
                const hasAny = (
                    (impairments.latency_ms || 0) > 0 ||
                    (impairments.loss_percent || 0) > 0 ||
                    (impairments.duplication_percent || 0) > 0 ||
                    (impairments.corruption_percent || 0) > 0 ||
                    (impairments.reorder_percent || 0) > 0
                );

                if (hasAny) {
                    const edge = this.findEdgeForBridge(bridge);
                    if (edge) {
                        this.updateEdgeImpairmentStyle(edge, {
                            latency_ms: impairments.latency_ms || 0,
                            loss_percent: impairments.loss_percent || 0,
                            dup_percent: impairments.duplication_percent || 0,
                            corrupt_percent: impairments.corruption_percent || 0,
                            reorder_delay_ms: impairments.reorder_delay_ms || 0,
                            reorder_percent: impairments.reorder_percent || 0
                        });
                        // Update EventManager's impairment state with edge reference
                        if (this.eventManager) {
                            this.eventManager.impairmentState[bridge.name] = {
                                latency_ms: impairments.latency_ms || 0,
                                loss_percent: impairments.loss_percent || 0,
                                dup_percent: impairments.duplication_percent || 0,
                                corrupt_percent: impairments.corruption_percent || 0,
                                reorder_delay_ms: impairments.reorder_delay_ms || 0,
                                reorder_percent: impairments.reorder_percent || 0,
                                edge: edge
                            };
                        }
                    }
                }
            }

            console.log('[TopologyManager] Loaded impairment status for', bridges.length, 'bridges');

            // Update visibility of Clear All Impairments button
            this.updateImpairmentControlsVisibility();

        } catch (error) {
            console.error('[TopologyManager] Error loading impairment status:', error);
        }
    }

    /**
     * Load latency status from API and update edge styles (legacy support)
     */
    async loadLatencyStatus() {
        // Redirect to impairment status loading
        return this.loadImpairmentStatus();
    }

    /**
     * Handle latency change (called by EventManager) - legacy support
     */
    handleLatencyChange(bridgeName, delayMs, edge) {
        if (!this.cy || !edge) return;

        if (delayMs) {
            // Latency enabled - use impairment style with just latency
            this.updateEdgeImpairmentStyle(edge, { latency_ms: delayMs, loss_percent: 0, dup_percent: 0, corrupt_percent: 0 });
            console.log(`[TopologyManager] Updated edge style for ${bridgeName} with ${delayMs}ms latency`);
        } else {
            // Latency removed
            this.clearEdgeImpairmentStyle(edge);
            console.log(`[TopologyManager] Cleared latency style for ${bridgeName}`);
        }

        // Update visibility of Clear All Impairments button
        this.updateImpairmentControlsVisibility();
    }

    /**
     * Handle impairment change (called by EventManager)
     */
    handleImpairmentChange(bridgeName, impairments, edge) {
        if (!this.cy || !edge) return;

        if (impairments) {
            // Impairments configured
            this.updateEdgeImpairmentStyle(edge, impairments);
            console.log(`[TopologyManager] Updated edge style for ${bridgeName} with impairments:`, impairments);
        } else {
            // All impairments cleared
            this.clearEdgeImpairmentStyle(edge);
            console.log(`[TopologyManager] Cleared impairment styles for ${bridgeName}`);
        }

        // Update visibility of Clear All Impairments button
        this.updateImpairmentControlsVisibility();
    }

    /**
     * Update edge styling to show latency is active (legacy)
     */
    updateEdgeLatencyStyle(edge, delayMs) {
        if (!edge) return;
        this.updateEdgeImpairmentStyle(edge, { latency_ms: delayMs, loss_percent: 0, dup_percent: 0, corrupt_percent: 0 });
    }

    /**
     * Clear latency styling from edge (legacy)
     */
    clearEdgeLatencyStyle(edge) {
        if (!edge) return;
        this.clearEdgeImpairmentStyle(edge);
    }

    /**
     * Update edge styling to show impairments are active
     * @param {Object} edge - Cytoscape edge
     * @param {Object} impairments - { latency_ms, loss_percent, dup_percent, corrupt_percent, reorder_delay_ms, reorder_percent }
     */
    updateEdgeImpairmentStyle(edge, impairments) {
        if (!edge) return;

        const { latency_ms = 0, loss_percent = 0, dup_percent = 0, corrupt_percent = 0, reorder_delay_ms = 0, reorder_percent = 0 } = impairments;

        // Store impairment values in edge data for reference
        edge.data('latency_ms', latency_ms);
        edge.data('loss_percent', loss_percent);
        edge.data('dup_percent', dup_percent);
        edge.data('corrupt_percent', corrupt_percent);
        edge.data('reorder_delay_ms', reorder_delay_ms);
        edge.data('reorder_percent', reorder_percent);

        // Remove all impairment-related classes first
        edge.removeClass('has-latency has-loss has-duplication has-corruption has-reorder has-impairments');

        // Count active impairments
        const activeTypes = [];
        if (latency_ms > 0) activeTypes.push('latency');
        if (loss_percent > 0) activeTypes.push('loss');
        if (dup_percent > 0) activeTypes.push('duplication');
        if (corrupt_percent > 0) activeTypes.push('corruption');
        if (reorder_percent > 0) activeTypes.push('reorder');

        if (activeTypes.length === 0) {
            // No impairments, clear styling
            this.clearEdgeImpairmentStyle(edge);
            return;
        }

        if (activeTypes.length === 1) {
            // Single impairment - use specific class for solid dashed color
            edge.addClass(`has-${activeTypes[0]}`);
        } else {
            // Multiple impairments - use gradient
            const gradient = this.calculateImpairmentGradient(impairments);
            edge.data('gradientColors', gradient.colors);
            edge.data('gradientPositions', gradient.positions);
            edge.addClass('has-impairments');
        }
    }

    /**
     * Calculate gradient colors and positions for multiple impairments
     * Creates a striped effect with hard edges
     */
    calculateImpairmentGradient(impairments) {
        const { latency_ms = 0, loss_percent = 0, dup_percent = 0, corrupt_percent = 0, reorder_percent = 0 } = impairments;

        // Use IMPAIRMENT_COLORS constants for consistency
        const colors = [];
        if (latency_ms > 0) colors.push(IMPAIRMENT_COLORS.latency);
        if (loss_percent > 0) colors.push(IMPAIRMENT_COLORS.loss);
        if (dup_percent > 0) colors.push(IMPAIRMENT_COLORS.duplication);
        if (corrupt_percent > 0) colors.push(IMPAIRMENT_COLORS.corruption);
        if (reorder_percent > 0) colors.push(IMPAIRMENT_COLORS.reorder);

        if (colors.length === 0) {
            return { colors: '#071c35', positions: '0% 100%' };
        }

        if (colors.length === 1) {
            return { colors: `${colors[0]} ${colors[0]}`, positions: '0% 100%' };
        }

        // Create striped gradient with equal segments
        const segmentSize = 100 / colors.length;
        const gradientColors = [];
        const gradientPositions = [];

        colors.forEach((color, i) => {
            const start = i * segmentSize;
            const end = (i + 1) * segmentSize;
            // Add color twice for hard edges (striped effect)
            gradientColors.push(color, color);
            gradientPositions.push(`${start}%`, `${end}%`);
        });

        return {
            colors: gradientColors.join(' '),
            positions: gradientPositions.join(' ')
        };
    }

    /**
     * Clear all impairment styling from edge
     */
    clearEdgeImpairmentStyle(edge) {
        if (!edge) return;

        edge.removeClass('has-latency has-loss has-duplication has-corruption has-reorder has-impairments');
        edge.removeData('latency_ms');
        edge.removeData('loss_percent');
        edge.removeData('dup_percent');
        edge.removeData('corrupt_percent');
        edge.removeData('reorder_delay_ms');
        edge.removeData('reorder_percent');
        edge.removeData('gradientColors');
        edge.removeData('gradientPositions');
    }

    /**
     * Find Cytoscape edge that matches a bridge
     */
    findEdgeForBridge(bridge) {
        if (!this.cy || !bridge) return null;

        const srcDevice = (bridge.source_device_name || '').toLowerCase();
        const tgtDevice = (bridge.target_device_name || '').toLowerCase();
        const srcPort = (bridge.source_port_name || '').toLowerCase();
        const tgtPort = (bridge.target_port_name || '').toLowerCase();

        // Search all edges for a match
        const edges = this.cy.edges();
        for (let i = 0; i < edges.length; i++) {
            const edge = edges[i];
            const data = edge.data();

            const edgeSrc = (data.source || '').toLowerCase();
            const edgeTgt = (data.target || '').toLowerCase();
            const edgeSrcPort = (data.source_port || '').toLowerCase();
            const edgeTgtPort = (data.target_port || '').toLowerCase();

            // Check both directions
            const matchForward = (
                edgeSrc === srcDevice &&
                edgeTgt === tgtDevice &&
                edgeSrcPort === srcPort &&
                edgeTgtPort === tgtPort
            );

            const matchReverse = (
                edgeSrc === tgtDevice &&
                edgeTgt === srcDevice &&
                edgeSrcPort === tgtPort &&
                edgeTgtPort === srcPort
            );

            if (matchForward || matchReverse) {
                return edge;
            }
        }

        return null;
    }

    /**
     * Remove all impairments from all links
     */
    async removeAllImpairments() {
        try {
            const response = await fetch('/td-api/impairments/clear-all', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: '{}'
            });

            const result = await response.json();

            if (!response.ok || result.error) {
                throw new Error(result.error || 'Failed to remove all impairments');
            }

            // Clear all edge impairment styles
            if (this.cy) {
                this.cy.edges('.has-latency, .has-loss, .has-duplication, .has-corruption, .has-reorder, .has-impairments').forEach(edge => {
                    this.clearEdgeImpairmentStyle(edge);
                });
            }

            // Clear EventManager state
            if (this.eventManager) {
                this.eventManager.latencyState = {};
                this.eventManager.impairmentState = {};
            }

            console.log('[TopologyManager] Removed all impairments:', result);
            return result;

        } catch (error) {
            console.error('[TopologyManager] Error removing all impairments:', error);
            throw error;
        }
    }

    /**
     * Remove all latency from all links (legacy - redirects to removeAllImpairments)
     */
    async removeAllLatency() {
        return this.removeAllImpairments();
    }

    /**
     * Get status summary
     */
    getStatusSummary() {
        if (this.statusUpdater) {
            return this.statusUpdater.getStatusSummary();
        }
        return { up: 0, down: 0, init: 0, unknown: 0, total: 0 };
    }

    /**
     * Export graph as PNG
     */
    exportPNG() {
        if (!this.cy) return null;

        return this.cy.png({
            output: 'blob',
            bg: '#ffffff',
            full: true,
            scale: 2
        });
    }

    /**
     * Export graph as JSON
     */
    exportJSON() {
        if (!this.cy) return null;

        return this.cy.json();
    }

    /**
     * Get topology metadata
     */
    getMetadata() {
        if (this.topologyData) {
            return this.topologyData.metadata;
        }
        return null;
    }

    /**
     * Create help button and overlay
     */
    createHelpButton() {
        // Create help button
        const helpBtn = document.createElement('button');
        helpBtn.id = 'topo-help-btn';
        helpBtn.className = 'topology-help-btn';
        helpBtn.innerHTML = '?';
        helpBtn.title = 'Keyboard & Mouse Controls';
        helpBtn.addEventListener('click', () => this.toggleHelpOverlay());
        this.container.appendChild(helpBtn);

        // Create help overlay (hidden by default)
        const overlay = document.createElement('div');
        overlay.id = 'topo-help-overlay';
        overlay.className = 'topology-help-overlay hidden';
        overlay.innerHTML = `
            <div class="help-header">
                <span>Keyboard & Mouse Controls</span>
                <button class="help-close-btn" title="Close">×</button>
            </div>
            <div class="help-content">
                <div class="help-section">
                    <h4>Navigation</h4>
                    <div class="help-row"><kbd>Drag</kbd> <span>Pan canvas</span></div>
                    <div class="help-row"><kbd>Scroll</kbd> <span>Zoom in/out</span></div>
                    <div class="help-row"><kbd>F</kbd> <span>Fit to view</span></div>
                    <div class="help-row"><kbd>R</kbd> <span>Reset zoom</span></div>
                </div>
                <div class="help-section">
                    <h4>Selection</h4>
                    <div class="help-row"><kbd>Click</kbd> <span>Select node</span></div>
                    <div class="help-row"><kbd>Shift</kbd> + <kbd>Click</kbd> <span>Add to selection</span></div>
                    <div class="help-row"><kbd>Shift</kbd> + <kbd>Drag</kbd> <span>Box select</span></div>
                    <div class="help-row"><kbd>Esc</kbd> <span>Clear selection</span></div>
                </div>
                <div class="help-section">
                    <h4>Nodes</h4>
                    <div class="help-row"><kbd>Drag Node</kbd> <span>Move node(s) (positions saved)</span></div>
                    <div class="help-row"><kbd>Right-click</kbd> <span>Context menu</span></div>
                    <div class="help-row"><kbd>Hover</kbd> <span>Show details</span></div>
                </div>
                <div class="help-section">
                    <h4>Layout</h4>
                    <div class="help-row"><span class="help-note">Node positions persist across reloads. Use Layout &rarr; Reset to restore default positions.</span></div>
                </div>
            </div>
        `;

        // Close button handler
        overlay.querySelector('.help-close-btn').addEventListener('click', () => {
            this.hideHelpOverlay();
        });

        // Close on click outside
        overlay.addEventListener('click', (e) => {
            if (e.target === overlay) {
                this.hideHelpOverlay();
            }
        });

        this.container.appendChild(overlay);
        this.helpOverlay = overlay;
    }

    /**
     * Toggle help overlay visibility
     */
    toggleHelpOverlay() {
        if (this.helpOverlay) {
            this.helpOverlay.classList.toggle('hidden');
        }
    }

    /**
     * Hide help overlay
     */
    hideHelpOverlay() {
        if (this.helpOverlay) {
            this.helpOverlay.classList.add('hidden');
        }
    }

    /**
     * Create impairment controls (Clear All Impairments button)
     */
    createImpairmentControls() {
        // Create container for impairment controls
        const container = document.createElement('div');
        container.id = 'impairment-controls';
        container.className = 'impairment-controls hidden';

        // Create Clear All Impairments button
        const clearBtn = document.createElement('button');
        clearBtn.id = 'clear-all-impairments-btn';
        clearBtn.className = 'impairment-clear-btn';
        clearBtn.innerHTML = '<span class="impairment-icon">⚡</span> Clear All Impairments';
        clearBtn.title = 'Remove all impairments from all links';
        clearBtn.addEventListener('click', async () => {
            if (confirm('Remove all impairments from all links?')) {
                try {
                    await this.removeAllImpairments();
                    this.updateImpairmentControlsVisibility();
                } catch (error) {
                    alert('Failed to remove impairments: ' + error.message);
                }
            }
        });

        container.appendChild(clearBtn);
        this.container.appendChild(container);
        this.impairmentControls = container;
    }

    /**
     * Create latency controls (legacy - redirects to createImpairmentControls)
     */
    createLatencyControls() {
        this.createImpairmentControls();
    }

    /**
     * Update impairment controls visibility based on whether any impairments are active
     */
    updateImpairmentControlsVisibility() {
        if (!this.impairmentControls) return;

        // Check if any edges have impairments
        const hasActiveImpairments = this.cy && this.cy.edges('.has-latency, .has-loss, .has-duplication, .has-corruption, .has-reorder, .has-impairments').length > 0;

        if (hasActiveImpairments) {
            this.impairmentControls.classList.remove('hidden');
        } else {
            this.impairmentControls.classList.add('hidden');
        }
    }

    /**
     * Update latency controls visibility (legacy - redirects to updateImpairmentControlsVisibility)
     */
    updateLatencyControlsVisibility() {
        this.updateImpairmentControlsVisibility();
    }

    /**
     * Refresh topology data from server and update the diagram
     * Called after adding new nodes to update the display
     */
    async refreshTopology() {
        console.log('[TopologyManager] Refreshing topology...');
        try {
            // Clear topology cache by adding timestamp
            const newData = await this.fetchTopology();

            if (!newData || !newData.nodes) {
                console.warn('[TopologyManager] Refresh returned invalid data');
                return;
            }

            // Find nodes that don't exist in current graph
            const existingNodeIds = new Set(this.cy.nodes().map(n => n.id()));
            const existingEdgeIds = new Set(this.cy.edges().map(e => e.id()));

            const newNodes = newData.nodes.filter(n => !existingNodeIds.has(n.data.id));
            const newEdges = newData.edges.filter(e => !existingEdgeIds.has(e.data.id));

            if (newNodes.length > 0 || newEdges.length > 0) {
                console.log(`[TopologyManager] Adding ${newNodes.length} new nodes and ${newEdges.length} new edges`);

                // Add new elements to the graph
                this.cy.add([...newNodes, ...newEdges]);

                // Re-run layout to position new nodes
                // Use preset layout to respect server-calculated positions
                this.cy.layout({
                    name: 'preset',
                    fit: true,
                    padding: 50
                }).run();

                // Update stored topology data
                this.topologyData = newData;

                // Store new node positions for reset
                newNodes.forEach(node => {
                    if (node.position) {
                        this.originalPositions[node.data.id] = { ...node.position };
                    }
                });
            } else {
                console.log('[TopologyManager] No new nodes or edges to add');
            }

            return newData;

        } catch (error) {
            console.error('[TopologyManager] Failed to refresh topology:', error);
            throw error;
        }
    }

    /**
     * Destroy the topology manager
     */
    destroy() {
        if (this.eventManager) {
            this.eventManager.destroy();
        }

        if (this.statusUpdater) {
            this.statusUpdater.destroy();
        }

        if (this.capturePanel) {
            this.capturePanel.destroy();
        }

        if (this.cy) {
            this.cy.destroy();
        }

        this.container.innerHTML = '';
        this.isInitialized = false;
    }
}

// Export for non-module usage
if (typeof window !== 'undefined') {
    window.TopologyManager = TopologyManager;
    window.LAYOUT_OPTIONS = LAYOUT_OPTIONS;
    window.DEVICE_TYPE_INFO = DEVICE_TYPE_INFO;
    window.IMPAIRMENT_COLORS = IMPAIRMENT_COLORS;
}

/**
 * Topology Manager for ATL Interactive Topology Diagram
 * Main orchestrator that brings together all topology components
 */

import { getCytoscapeStyles } from './cytoscape-styles.js';
import { getLayout, LAYOUT_OPTIONS } from './layout-config.js';
import { EventManager } from './event-handlers.js';
import { FilterManager, DEVICE_TYPE_INFO, loadDeviceTypeInfo, getDeviceTypeInfo } from './filter-manager.js';
import { StatusUpdater } from './status-updater.js';

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
        this.isInitialized = false;
        this.topologyData = null;
        this.originalPositions = {};  // Store original positions for reset
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

            // Setup components
            console.log('[TopologyManager] onOpenTerminal option:', this.options.onOpenTerminal ? 'provided' : 'not provided');
            this.eventManager = new EventManager(this.cy, this.container, {
                onOpenTerminal: this.options.onOpenTerminal
            });

            if (this.options.enableFilters) {
                this.filterManager = new FilterManager(this.cy, this.container);
            }

            // Add help button
            this.createHelpButton();

            if (this.options.enableStatus) {
                this.statusUpdater = new StatusUpdater(this.cy, this.options.wsUrl);
                this.statusUpdater.connect();
                // Start polling device status via eAPI for real-time status indicators
                this.statusUpdater.startStatusPolling();
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
        this.cy = cytoscape({
            container: this.container,
            elements: elements,
            style: getCytoscapeStyles(),
            layout: getLayout(this.options.layout),
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
            boxSelectionEnabled: true,      // Enable box/marquee selection by dragging on background
            selectionType: 'additive',      // Allow multi-select with Shift+click or box selection
            autoungrabifyNodes: false,      // Ensure nodes are draggable
            panningEnabled: true,
            userPanningEnabled: true
        });

        // Run layout
        this.cy.layout(getLayout(this.options.layout)).run();
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
                    <div class="help-row"><kbd>Drag Node</kbd> <span>Move node(s)</span></div>
                    <div class="help-row"><kbd>Right-click</kbd> <span>Context menu</span></div>
                    <div class="help-row"><kbd>Hover</kbd> <span>Show details</span></div>
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
     * Destroy the topology manager
     */
    destroy() {
        if (this.eventManager) {
            this.eventManager.destroy();
        }

        if (this.statusUpdater) {
            this.statusUpdater.destroy();
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
}

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
        this.focusMode = false;
        this.focusedNode = null;
        this.terminalWindow = null;  // Reference to terminal window for tab reuse

        // Custom terminal handler (for embedding in terminal page)
        this.customTerminalHandler = options.onOpenTerminal || null;

        // Store bound handler reference for proper cleanup (prevents memory leak)
        this.boundKeyDownHandler = (evt) => this.handleKeyDown(evt);
        this.boundClickHandler = (evt) => this.handleDocumentClick(evt);

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
            this.customTerminalHandler(deviceName, ip);
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
                icon: '⌨',
                action: () => {
                    this.openTerminal(data.label, data.ip);
                    this.hideContextMenu();
                },
                disabled: !data.ip || data.ip === 'N/A'
            },
            {
                label: 'Focus on Device',
                icon: '🎯',
                action: () => {
                    this.enterFocusMode(node);
                    this.hideContextMenu();
                }
            },
            {
                type: 'separator'
            },
            {
                label: 'Copy IP Address',
                icon: '📋',
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

                const icon = document.createElement('span');
                icon.className = 'context-menu-icon';
                icon.textContent = item.icon;

                const label = document.createElement('span');
                label.className = 'context-menu-label';
                label.textContent = item.label;

                menuItem.appendChild(icon);
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
     * Enter focus mode for a node
     */
    enterFocusMode(node) {
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

        // Show focus mode indicator
        this.showFocusIndicator(node.data('label'));
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

        // Build port list
        let portsHtml = '';
        if (data.ports && data.ports.length > 0) {
            const portItems = data.ports.slice(0, 5).map(p =>
                `<li>${p.port} → ${p.neighbor}:${p.neighbor_port}</li>`
            ).join('');
            const moreCount = data.ports.length - 5;
            portsHtml = `
                <div class="tooltip-ports">
                    <strong>Connections:</strong>
                    <ul>${portItems}</ul>
                    ${moreCount > 0 ? `<em>+${moreCount} more</em>` : ''}
                </div>
            `;
        }

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
                    <span class="tooltip-value status-${data.status}">${data.status}</span>
                </div>
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
     * Show tooltip for an edge
     */
    showEdgeTooltip(evt) {
        const edge = evt.target;
        const data = edge.data();

        this.hideTooltip();

        const tooltip = document.createElement('div');
        tooltip.id = 'topo-tooltip';
        tooltip.className = 'topology-tooltip edge-tooltip';

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
        // Escape - close context menu, exit focus mode, clear selection and highlights
        if (evt.key === 'Escape') {
            this.hideContextMenu();
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
     * Destroy event handlers and clean up resources
     */
    destroy() {
        this.hideTooltip();
        this.hideContextMenu();
        this.hideFocusIndicator();

        // Remove global listeners to prevent memory leak
        document.removeEventListener('keydown', this.boundKeyDownHandler);
        document.removeEventListener('click', this.boundClickHandler);

        // Remove all Cytoscape event listeners
        this.cy.removeAllListeners();
    }
}

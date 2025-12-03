/**
 * Event Handlers for ATL Interactive Topology Diagram
 * Handles click-to-SSH, hover tooltips, and path highlighting
 */

export class EventManager {
    constructor(cy, container) {
        this.cy = cy;
        this.container = container;
        this.tooltip = null;

        // Store bound handler reference for proper cleanup (prevents memory leak)
        this.boundKeyDownHandler = (evt) => this.handleKeyDown(evt);

        this.registerHandlers();
    }

    /**
     * Register all event handlers
     */
    registerHandlers() {
        // Node click - open SSH
        this.cy.on('tap', 'node', (evt) => this.handleNodeClick(evt));

        // Node hover - show tooltip
        this.cy.on('mouseover', 'node', (evt) => this.handleNodeMouseOver(evt));
        this.cy.on('mouseout', 'node', (evt) => this.handleNodeMouseOut(evt));

        // Edge hover - highlight path
        this.cy.on('mouseover', 'edge', (evt) => this.handleEdgeMouseOver(evt));
        this.cy.on('mouseout', 'edge', (evt) => this.handleEdgeMouseOut(evt));

        // Background click - clear selections
        this.cy.on('tap', (evt) => {
            if (evt.target === this.cy) {
                this.clearHighlights();
            }
        });

        // Keyboard shortcuts (using stored reference for cleanup)
        document.addEventListener('keydown', this.boundKeyDownHandler);
    }

    /**
     * Handle node click - open SSH session
     */
    handleNodeClick(evt) {
        const node = evt.target;
        const ip = node.data('ip');
        const deviceName = node.data('label');

        if (ip && ip !== 'N/A') {
            // Open SSH in new tab
            const sshUrl = `/ssh/host/${ip}`;
            window.open(sshUrl, `ssh-${deviceName}`, 'noopener,noreferrer');
        }
    }

    /**
     * Handle node mouse over - show tooltip and highlight connections
     */
    handleNodeMouseOver(evt) {
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
                Click to open SSH session
            </div>
        `;

        // Position tooltip
        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        tooltip.style.position = 'absolute';
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

        const renderedPos = evt.renderedPosition;
        const containerRect = this.container.getBoundingClientRect();

        tooltip.style.position = 'absolute';
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
        // Escape - clear selection and highlights
        if (evt.key === 'Escape') {
            this.cy.$(':selected').unselect();
            this.clearHighlights();
            this.hideTooltip();
        }

        // 'f' - fit graph to view
        if (evt.key === 'f' && !evt.ctrlKey && !evt.metaKey) {
            const activeElement = document.activeElement;
            if (activeElement.tagName !== 'INPUT' && activeElement.tagName !== 'TEXTAREA') {
                this.cy.fit(50);
            }
        }

        // 'r' - reset zoom
        if (evt.key === 'r' && !evt.ctrlKey && !evt.metaKey) {
            const activeElement = document.activeElement;
            if (activeElement.tagName !== 'INPUT' && activeElement.tagName !== 'TEXTAREA') {
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

        // Remove global keydown listener to prevent memory leak
        document.removeEventListener('keydown', this.boundKeyDownHandler);

        // Remove all Cytoscape event listeners
        this.cy.removeAllListeners();
    }
}

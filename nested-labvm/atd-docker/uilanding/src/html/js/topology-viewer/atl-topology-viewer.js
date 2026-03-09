/**
 * ATL Topology Viewer - Self-initializing IIFE for Sphinx lab guides
 * Finds all [data-topology] containers and renders interactive diagrams
 *
 * Dependencies: cytoscape.min.js, cytoscape-dagre.js (loaded before this script)
 */

import { ViewerManager } from './viewer-manager.js';
import { ViewerEventHandlers } from './viewer-event-handlers.js';
import { ViewerStatusUpdater } from './viewer-status-updater.js';
import { ViewerZoneRenderer } from './viewer-zone-renderer.js';
import { ViewerAnnotationRenderer } from './viewer-annotation-renderer.js';

class ATLTopologyViewer {
    /**
     * Initialize all topology viewers on the page
     */
    static init() {
        const containers = document.querySelectorAll('[data-topology]');
        if (containers.length === 0) return;

        containers.forEach((container, index) => {
            try {
                const dataAttr = container.getAttribute('data-topology');
                const config = JSON.parse(dataAttr);
                new ATLTopologyViewer(container, config, index);
            } catch (error) {
                console.error(`[ATLTopologyViewer] Failed to initialize viewer ${index}:`, error);
                container.innerHTML = '<div class="atl-topology-loading">Failed to load topology diagram</div>';
            }
        });
    }

    constructor(container, config, viewerIndex) {
        this.container = container;
        this.config = config;
        this.viewerIndex = viewerIndex;

        // Create canvas div
        this.cyContainer = document.createElement('div');
        this.cyContainer.className = 'cy-viewer';
        this.cyContainer.style.width = '100%';
        this.cyContainer.style.height = '100%';
        container.appendChild(this.cyContainer);

        // Initialize components
        this.manager = new ViewerManager(this.cyContainer, config);
        this.cy = this.manager.cy;

        // Zone rendering (compound nodes are handled by Cytoscape styles)
        this.zoneRenderer = new ViewerZoneRenderer(this.cy);

        // Annotation rendering
        this.annotationRenderer = new ViewerAnnotationRenderer(this.cy, container, config.annotations || []);

        // Event handlers (SSH, console, context menu)
        if (config.deviceAccess !== false) {
            this.eventHandlers = new ViewerEventHandlers(this.cy, container);
        }

        // Live status (WebSocket)
        if (config.liveStatus !== false) {
            this.statusUpdater = new ViewerStatusUpdater(this.cy, container);
        }

        // Run layout
        this.manager.runLayout(config.layout || 'dagre');
    }
}

// Self-initialize on DOM ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => ATLTopologyViewer.init());
} else {
    ATLTopologyViewer.init();
}

export { ATLTopologyViewer };

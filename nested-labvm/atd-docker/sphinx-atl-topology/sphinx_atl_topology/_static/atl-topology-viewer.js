/**
 * ATL Topology Viewer - Bundled IIFE for Sphinx lab guides
 * Self-contained viewer with no ES module imports.
 * Dependencies: cytoscape.min.js, dagre.min.js, cytoscape-dagre.js (loaded before this script)
 */
(function() {
'use strict';

// Detect _static/ base path from this script's URL
var STATIC_BASE = (function() {
    var scripts = document.getElementsByTagName('script');
    for (var i = scripts.length - 1; i >= 0; i--) {
        var src = scripts[i].src || '';
        if (src.indexOf('atl-topology-viewer.js') !== -1) {
            return src.substring(0, src.lastIndexOf('/') + 1);
        }
    }
    return '_static/';
})();

// ============================================================
// ViewerManager
// ============================================================
class ViewerManager {
    constructor(container, config) {
        this.container = container;
        this.config = config;
        this.cy = null;
        this.initCytoscape();
    }

    initCytoscape() {
        this.cy = cytoscape({
            container: this.container,
            style: this.getStyles(),
            elements: this.config.elements || [],
            layout: { name: 'preset' },
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
            boxSelectionEnabled: false,
            selectionType: 'single',
            userPanningEnabled: true,
            userZoomingEnabled: true,
        });
    }

    getStyles() {
        var img = STATIC_BASE + 'images/';
        return [
            { selector: 'node', style: { 'label': 'data(label)', 'text-valign': 'bottom', 'text-halign': 'center', 'text-margin-y': 8, 'font-family': '"proxima-nova", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif', 'font-size': 11, 'font-weight': 500, 'color': '#071c35', 'text-outline-color': '#ffffff', 'text-outline-width': 2, 'border-width': 0, 'background-color': 'transparent', 'background-opacity': 0, 'width': 50, 'height': 50, 'transition-property': 'border-color, border-width, opacity', 'transition-duration': '0.2s' } },
            // Spine icon
            { selector: '.device-type-spine', style: { 'background-image': img + 'spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-pe', style: { 'background-image': img + 'spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-p', style: { 'background-image': img + 'spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 62, 'height': 62 } },
            { selector: '.device-type-ce', style: { 'background-image': img + 'spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            // Leaf icon
            { selector: '.device-type-leaf', style: { 'background-image': img + 'leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-borderleaf', style: { 'background-image': img + 'leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-memleaf', style: { 'background-image': img + 'leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 62, 'height': 62 } },
            // Router icon
            { selector: '.device-type-router', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-core', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-dci', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-isp, .device-type-internet', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-rr', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-gw', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-customer', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-oob', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-firewall', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-other', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-velo_orchestrator', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-velo_gateway', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-velo_edge', style: { 'background-image': img + 'router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            // Host icon
            { selector: '.device-type-host, .device-type-linux_host', style: { 'background-image': img + 'hosts.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            // Zone (compound) parent styles
            { selector: ':parent', style: { 'background-color': 'data(zoneBackground)', 'background-opacity': 0.3, 'border-width': 2, 'border-color': 'data(zoneColor)', 'border-style': 'data(zoneBorderStyle)', 'label': 'data(label)', 'text-valign': 'top', 'text-halign': 'left', 'text-margin-x': 10, 'text-margin-y': 10, 'font-size': 14, 'font-weight': 600, 'color': 'data(zoneColor)', 'padding': 20, 'shape': 'roundrectangle', 'corner-radius': 8, 'text-outline-width': 0 } },
            // Status styles
            { selector: '.status-up', style: { 'underlay-color': '#78d82c', 'underlay-padding': 6, 'underlay-opacity': 0.3, 'underlay-shape': 'ellipse' } },
            { selector: '.status-down', style: { 'underlay-color': '#e30909', 'underlay-padding': 8, 'underlay-opacity': 0.4, 'underlay-shape': 'ellipse', 'opacity': 0.85 } },
            { selector: '.status-error', style: { 'underlay-color': '#ff8c00', 'underlay-padding': 6, 'underlay-opacity': 0.35, 'underlay-shape': 'ellipse', 'opacity': 0.9 } },
            { selector: '.status-unknown', style: { 'underlay-color': '#808080', 'underlay-padding': 4, 'underlay-opacity': 0.2, 'underlay-shape': 'ellipse' } },
            // Edge styles
            { selector: 'edge', style: { 'width': 2, 'line-color': '#071c35', 'curve-style': 'bezier', 'opacity': 0.7, 'source-label': 'data(source_port)', 'target-label': 'data(target_port)', 'source-text-offset': 30, 'target-text-offset': 30, 'source-text-rotation': 'autorotate', 'target-text-rotation': 'autorotate', 'font-size': 10, 'font-family': '"proxima-nova", sans-serif', 'color': '#333333', 'text-outline-color': '#ffffff', 'text-outline-width': 2, 'text-background-color': '#ffffff', 'text-background-opacity': 0.8, 'text-background-padding': '2px' } },
            // Interactive states
            { selector: 'node:selected', style: { 'underlay-color': '#fbb500', 'underlay-padding': 10, 'underlay-opacity': 0.4, 'underlay-shape': 'ellipse' } },
            { selector: 'node.highlighted', style: { 'underlay-color': '#fbb500', 'underlay-padding': 8, 'underlay-opacity': 0.35, 'underlay-shape': 'ellipse', 'z-index': 999 } },
            { selector: 'edge.highlighted', style: { 'line-color': '#fbb500', 'width': 4, 'opacity': 1, 'z-index': 998 } },
            { selector: 'node.faded', style: { 'opacity': 0.3 } },
            { selector: 'edge.faded', style: { 'opacity': 0.15 } },
            { selector: 'node.hover', style: { 'underlay-color': '#071c35', 'underlay-padding': 6, 'underlay-opacity': 0.15, 'underlay-shape': 'ellipse', 'z-index': 999 } },
            { selector: 'node.focused', style: { 'underlay-color': '#fbb500', 'underlay-padding': 12, 'underlay-opacity': 0.45, 'underlay-shape': 'ellipse', 'z-index': 9999, 'font-size': 14, 'font-weight': 700 } },
        ];
    }

    runLayout(layoutName) {
        var hasPositions = this.cy.nodes().some(function(n) { return n.position().x !== 0 || n.position().y !== 0; });

        if (layoutName === 'preset' && hasPositions) {
            this.cy.fit(undefined, 30);
            return;
        }

        var name = (layoutName === 'preset' && !hasPositions) ? 'dagre' : layoutName;
        var cy = this.cy;
        var layouts = {
            dagre: { name: 'dagre', rankDir: 'TB', rankSep: 80, nodeSep: 50, edgeSep: 20, padding: 30, animate: false, fit: true, spacingFactor: 1.2 },
            cose: { name: 'cose', idealEdgeLength: 100, nodeOverlap: 20, fit: true, padding: 30, randomize: false, componentSpacing: 100, nodeRepulsion: 400000, animate: false },
            concentric: { name: 'concentric', fit: true, padding: 30, minNodeSpacing: 50, avoidOverlap: true, spacingFactor: 1.5, animate: false },
            grid: { name: 'grid', fit: true, padding: 30, avoidOverlap: true, spacingFactor: 1.5, animate: false },
        };
        var config = layouts[name] || layouts.dagre;
        // Ensure fit after layout completes
        config.stop = function() { cy.fit(undefined, 30); };
        this.cy.layout(config).run();
    }
}

// ============================================================
// ViewerEventHandlers
// ============================================================
class ViewerEventHandlers {
    constructor(cy, container) {
        this.cy = cy;
        this.container = container;
        this.contextMenu = null;
        this.tooltip = null;
        this.focusMode = false;
        this.focusedNode = null;
        this.bindEvents();
    }

    bindEvents() {
        var self = this;
        this.cy.on('cxttap', 'node', function(e) {
            if (e.target.data('isZone')) return;
            self.showContextMenu(e);
        });
        this.cy.on('tap', function() { self.hideContextMenu(); });

        // Hover tooltip with device info
        this.cy.on('mouseover', 'node', function(e) {
            if (e.target.data('isZone')) return;
            e.target.addClass('hover');
            self.container.style.cursor = 'pointer';
            self.showTooltip(e.target);
        });
        this.cy.on('mouseout', 'node', function(e) {
            e.target.removeClass('hover');
            self.container.style.cursor = '';
            self.hideTooltip();
        });

        // Hide tooltip on pan/zoom
        this.cy.on('pan zoom', function() { self.hideTooltip(); });

        document.addEventListener('click', function(e) {
            if (self.contextMenu && !self.contextMenu.contains(e.target)) self.hideContextMenu();
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                self.hideContextMenu();
                self.hideTooltip();
                if (self.focusMode) self.exitFocusMode();
            }
        });
    }

    showTooltip(node) {
        this.hideTooltip();
        var label = node.data('label') || node.id();
        var ip = node.data('ip');
        var type = node.data('device_type') || '';

        var tip = document.createElement('div');
        tip.className = 'atl-topology-tooltip';
        tip.innerHTML = '<strong>' + label + '</strong>';
        if (type) tip.innerHTML += '<br><span style="color:#666;font-size:11px">' + type + '</span>';
        if (ip) tip.innerHTML += '<br><span style="color:#4c5cae;font-size:11px">' + ip + '</span>';

        var renderedPos = node.renderedPosition();
        var rect = this.container.getBoundingClientRect();
        tip.style.left = (rect.left + renderedPos.x + 15) + 'px';
        tip.style.top = (rect.top + renderedPos.y - 10) + 'px';

        document.body.appendChild(tip);
        this.tooltip = tip;

        // Keep tooltip on screen
        var tipRect = tip.getBoundingClientRect();
        if (tipRect.right > window.innerWidth) tip.style.left = (window.innerWidth - tipRect.width - 10) + 'px';
        if (tipRect.top < 0) tip.style.top = (rect.top + renderedPos.y + 20) + 'px';
    }

    hideTooltip() {
        if (this.tooltip) { this.tooltip.remove(); this.tooltip = null; }
    }

    showContextMenu(e) {
        this.hideContextMenu();
        this.hideTooltip();
        var node = e.target;
        var ip = node.data('ip');
        var label = node.data('label') || node.id();
        var self = this;

        var menu = document.createElement('div');
        menu.className = 'atl-topology-context-menu';

        var header = document.createElement('div');
        header.style.cssText = 'padding:6px 14px;font-weight:600;color:#071c35;border-bottom:1px solid #e0e0e0';
        header.textContent = label;
        if (ip) {
            var ipSpan = document.createElement('span');
            ipSpan.style.cssText = 'font-size:11px;color:#999;margin-left:8px';
            ipSpan.textContent = ip;
            header.appendChild(ipSpan);
        }
        menu.appendChild(header);

        if (ip) {
            this._addItem(menu, 'Open Terminal', function() { self.openTerminal(ip, label); });
            this._addItem(menu, 'Open Console', function() { self.openConsole(label); });
            var sep = document.createElement('div');
            sep.className = 'atl-topology-context-menu-separator';
            menu.appendChild(sep);
        }

        var focusLabel = (this.focusMode && this.focusedNode === node) ? 'Exit Focus' : 'Focus Device';
        this._addItem(menu, focusLabel, function() {
            if (self.focusMode && self.focusedNode === node) self.exitFocusMode();
            else self.enterFocusMode(node);
        });

        var renderedPos = node.renderedPosition();
        var rect = this.container.getBoundingClientRect();
        menu.style.left = (rect.left + renderedPos.x + 10) + 'px';
        menu.style.top = (rect.top + renderedPos.y + 10) + 'px';
        document.body.appendChild(menu);
        this.contextMenu = menu;

        var menuRect = menu.getBoundingClientRect();
        if (menuRect.right > window.innerWidth) menu.style.left = (window.innerWidth - menuRect.width - 10) + 'px';
        if (menuRect.bottom > window.innerHeight) menu.style.top = (window.innerHeight - menuRect.height - 10) + 'px';
    }

    _addItem(menu, label, onClick) {
        var item = document.createElement('button');
        item.className = 'atl-topology-context-menu-item';
        item.textContent = label;
        var self = this;
        item.addEventListener('click', function() { onClick(); self.hideContextMenu(); });
        menu.appendChild(item);
    }

    hideContextMenu() {
        if (this.contextMenu) { this.contextMenu.remove(); this.contextMenu = null; }
    }

    openTerminal(ip, label) {
        window.open('/terminal?ip=' + encodeURIComponent(ip) + '&name=' + encodeURIComponent(label), 'terminal-' + label);
    }

    openConsole(label) {
        window.open('/console?name=' + encodeURIComponent(label), 'console-' + label);
    }

    enterFocusMode(node) {
        this.focusMode = true;
        this.focusedNode = node;
        this.cy.elements().addClass('faded');
        var neighborhood = node.neighborhood().add(node);
        neighborhood.removeClass('faded');
        node.addClass('focused');
        neighborhood.edges().addClass('highlighted');
        neighborhood.nodes().filter(function(n) { return n !== node; }).addClass('highlighted');
    }

    exitFocusMode() {
        this.focusMode = false;
        this.focusedNode = null;
        this.cy.elements().removeClass('faded focused highlighted');
    }
}

// ============================================================
// ViewerStatusUpdater
// ============================================================
class ViewerStatusUpdater {
    constructor(cy, container) {
        this.cy = cy;
        this.container = container;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 3000;
        this.statusIndicator = null;
        this.createStatusIndicator();
        this.connect();
    }

    createStatusIndicator() {
        this.statusIndicator = document.createElement('div');
        this.statusIndicator.className = 'atl-topology-status';
        this.container.appendChild(this.statusIndicator);
    }

    connect() {
        var self = this;
        try {
            var protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            this.ws = new WebSocket(protocol + '//' + window.location.host + '/td-ws');
            this.ws.onopen = function() { self.reconnectAttempts = 0; self.statusIndicator.classList.add('connected'); self.requestStatus(); };
            this.ws.onmessage = function(event) { try { self.handleStatusUpdate(JSON.parse(event.data)); } catch(e) {} };
            this.ws.onclose = function() { self.statusIndicator.classList.remove('connected'); self.attemptReconnect(); };
            this.ws.onerror = function() { self.statusIndicator.textContent = ''; };
        } catch(e) {}
    }

    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) return;
        this.reconnectAttempts++;
        var self = this;
        setTimeout(function() { self.connect(); }, this.reconnectDelay);
    }

    requestStatus() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try { this.ws.send(JSON.stringify({ type: 'status_request' })); } catch(e) {}
        }
    }

    handleStatusUpdate(data) {
        if (!data || !data.devices) return;
        var nodeLookup = {};
        this.cy.nodes().forEach(function(node) {
            if (!node.data('isZone')) nodeLookup[node.id().toLowerCase()] = node;
        });
        Object.keys(data.devices).forEach(function(deviceName) {
            var node = nodeLookup[deviceName.toLowerCase()];
            if (!node) return;
            node.removeClass('status-up status-down status-error status-unknown');
            var status = (data.devices[deviceName] || '').toLowerCase();
            if (status === 'up' || status === 'reachable') node.addClass('status-up');
            else if (status === 'down' || status === 'unreachable') node.addClass('status-down');
            else if (status === 'error') node.addClass('status-error');
            else node.addClass('status-unknown');
        });
    }
}

// ============================================================
// ViewerAnnotationRenderer
// ============================================================
class ViewerAnnotationRenderer {
    constructor(cy, container, annotationsData) {
        this.cy = cy;
        this.container = container;
        this.annotations = [];
        this.overlay = document.createElement('div');
        this.overlay.className = 'annotation-overlay';
        this.container.appendChild(this.overlay);
        var self = this;
        if (annotationsData && annotationsData.length) {
            annotationsData.forEach(function(ann) { self.addAnnotation(ann); });
        }
        this.cy.on('pan zoom', function() { self.updatePositions(); });
        this.cy.on('layoutstop', function() { self.updatePositions(); });
    }

    addAnnotation(annData) {
        var el = document.createElement('div');
        el.className = 'atl-annotation';
        if (annData.background !== false) el.classList.add('has-background');
        el.textContent = annData.text || '';
        el.style.color = annData.color || '#4c5cae';
        el.style.fontSize = (annData.font_size || 12) + 'px';
        var position = annData.position || { x: 0, y: 0 };
        this.overlay.appendChild(el);
        this.annotations.push({ el: el, modelPosition: { x: position.x, y: position.y } });
    }

    updatePositions() {
        var pan = this.cy.pan();
        var zoom = this.cy.zoom();
        this.annotations.forEach(function(ann) {
            ann.el.style.left = (ann.modelPosition.x * zoom + pan.x) + 'px';
            ann.el.style.top = (ann.modelPosition.y * zoom + pan.y) + 'px';
            ann.el.style.transform = 'scale(' + Math.min(zoom, 1.5) + ')';
        });
    }
}

// ============================================================
// ATLTopologyViewer - Main entry point
// ============================================================
class ATLTopologyViewer {
    static init() {
        var containers = document.querySelectorAll('[data-topology]');
        if (containers.length === 0) return;
        containers.forEach(function(container, index) {
            try {
                var config = JSON.parse(container.getAttribute('data-topology'));
                new ATLTopologyViewer(container, config, index);
            } catch (error) {
                console.error('[ATLTopologyViewer] Failed to initialize viewer ' + index + ':', error);
                container.innerHTML = '<div class="atl-topology-loading">Failed to load topology diagram</div>';
            }
        });
    }

    constructor(container, config, viewerIndex) {
        this.container = container;
        this.config = config;

        var cyContainer = document.createElement('div');
        cyContainer.className = 'cy-viewer';
        cyContainer.style.width = '100%';
        cyContainer.style.height = '100%';
        container.appendChild(cyContainer);

        this.manager = new ViewerManager(cyContainer, config);
        this.cy = this.manager.cy;
        this.annotationRenderer = new ViewerAnnotationRenderer(this.cy, container, config.annotations || []);

        if (config.deviceAccess !== false) {
            this.eventHandlers = new ViewerEventHandlers(this.cy, container);
        }
        if (config.liveStatus !== false) {
            this.statusUpdater = new ViewerStatusUpdater(this.cy, container);
        }

        this.manager.runLayout(config.layout || 'dagre');

        // Re-fit when user resizes the container (drag handle)
        var self = this;
        var resizeObserver = new ResizeObserver(function() {
            self.cy.resize();
            self.cy.fit(undefined, 30);
        });
        resizeObserver.observe(container);
    }
}

// Self-initialize
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() { ATLTopologyViewer.init(); });
} else {
    ATLTopologyViewer.init();
}

window.ATLTopologyViewer = ATLTopologyViewer;
})();

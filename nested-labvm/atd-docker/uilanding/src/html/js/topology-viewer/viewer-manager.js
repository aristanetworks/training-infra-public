/**
 * Viewer Manager - Stripped-down TopologyManager for embedded diagrams
 * No feature flags, wizards, capture, or NodeBuilder
 */

export class ViewerManager {
    constructor(container, config) {
        this.container = container;
        this.config = config;
        this.cy = null;

        this.initCytoscape();
    }

    initCytoscape() {
        const styles = this.getStyles();

        this.cy = cytoscape({
            container: this.container,
            style: styles,
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
        return [
            // Node base styles
            {
                selector: 'node',
                style: {
                    'label': 'data(label)',
                    'text-valign': 'bottom',
                    'text-halign': 'center',
                    'text-margin-y': 8,
                    'font-family': '"proxima-nova", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                    'font-size': 11,
                    'font-weight': 500,
                    'color': '#071c35',
                    'text-outline-color': '#ffffff',
                    'text-outline-width': 2,
                    'border-width': 0,
                    'background-color': 'transparent',
                    'background-opacity': 0,
                    'width': 50,
                    'height': 50,
                    'transition-property': 'border-color, border-width, opacity',
                    'transition-duration': '0.2s',
                }
            },
            // Device type styles - spine icon
            { selector: '.device-type-spine', style: { 'background-image': 'images/spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-pe', style: { 'background-image': 'images/spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-p', style: { 'background-image': 'images/spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 62, 'height': 62 } },
            { selector: '.device-type-ce', style: { 'background-image': 'images/spine.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            // Device type styles - leaf icon
            { selector: '.device-type-leaf', style: { 'background-image': 'images/leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-borderleaf', style: { 'background-image': 'images/leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-memleaf', style: { 'background-image': 'images/leaf.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 62, 'height': 62 } },
            // Device type styles - router icon
            { selector: '.device-type-router', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-core', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-dci', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-isp, .device-type-internet', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            { selector: '.device-type-rr', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-gw', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-customer', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-oob', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-firewall', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-other', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            { selector: '.device-type-velo_orchestrator', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 68, 'height': 68 } },
            { selector: '.device-type-velo_gateway', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 65, 'height': 65 } },
            { selector: '.device-type-velo_edge', style: { 'background-image': 'images/router.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 60, 'height': 60 } },
            // Device type styles - host icon
            { selector: '.device-type-host, .device-type-linux_host', style: { 'background-image': 'images/hosts.png', 'background-fit': 'contain', 'background-clip': 'none', 'width': 70, 'height': 70 } },
            // Zone (compound) parent styles
            {
                selector: ':parent',
                style: {
                    'background-color': 'data(zoneBackground)',
                    'background-opacity': 0.3,
                    'border-width': 2,
                    'border-color': 'data(zoneColor)',
                    'border-style': 'data(zoneBorderStyle)',
                    'label': 'data(label)',
                    'text-valign': 'top',
                    'text-halign': 'left',
                    'text-margin-x': 10,
                    'text-margin-y': 10,
                    'font-size': 14,
                    'font-weight': 600,
                    'color': 'data(zoneColor)',
                    'padding': 20,
                    'shape': 'roundrectangle',
                    'corner-radius': 8,
                    'text-outline-width': 0,
                }
            },
            // Status styles
            { selector: '.status-up', style: { 'underlay-color': '#78d82c', 'underlay-padding': 6, 'underlay-opacity': 0.3, 'underlay-shape': 'ellipse' } },
            { selector: '.status-down', style: { 'underlay-color': '#e30909', 'underlay-padding': 8, 'underlay-opacity': 0.4, 'underlay-shape': 'ellipse', 'opacity': 0.85 } },
            { selector: '.status-error', style: { 'underlay-color': '#ff8c00', 'underlay-padding': 6, 'underlay-opacity': 0.35, 'underlay-shape': 'ellipse', 'opacity': 0.9 } },
            { selector: '.status-unknown', style: { 'underlay-color': '#808080', 'underlay-padding': 4, 'underlay-opacity': 0.2, 'underlay-shape': 'ellipse' } },
            // Edge styles
            {
                selector: 'edge',
                style: {
                    'width': 2,
                    'line-color': '#071c35',
                    'curve-style': 'bezier',
                    'opacity': 0.7,
                    'source-label': 'data(source_port)',
                    'target-label': 'data(target_port)',
                    'source-text-offset': 30,
                    'target-text-offset': 30,
                    'source-text-rotation': 'autorotate',
                    'target-text-rotation': 'autorotate',
                    'font-size': 10,
                    'font-family': '"proxima-nova", sans-serif',
                    'color': '#333333',
                    'text-outline-color': '#ffffff',
                    'text-outline-width': 2,
                    'text-background-color': '#ffffff',
                    'text-background-opacity': 0.8,
                    'text-background-padding': '2px',
                }
            },
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
        const hasPositions = this.cy.nodes().some(n => n.position().x !== 0 || n.position().y !== 0);

        if (layoutName === 'preset' && hasPositions) {
            this.cy.fit(undefined, 30);
            return;
        }

        // Use dagre as default if no positions
        const name = (layoutName === 'preset' && !hasPositions) ? 'dagre' : layoutName;

        const layouts = {
            dagre: {
                name: 'dagre',
                rankDir: 'TB',
                rankSep: 80,
                nodeSep: 50,
                edgeSep: 20,
                padding: 30,
                animate: false,
                fit: true,
                spacingFactor: 1.2,
            },
            cose: {
                name: 'cose',
                idealEdgeLength: 100,
                nodeOverlap: 20,
                fit: true,
                padding: 30,
                randomize: false,
                componentSpacing: 100,
                nodeRepulsion: 400000,
                animate: false,
            },
            concentric: {
                name: 'concentric',
                fit: true,
                padding: 30,
                minNodeSpacing: 50,
                avoidOverlap: true,
                spacingFactor: 1.5,
                animate: false,
            },
            grid: {
                name: 'grid',
                fit: true,
                padding: 30,
                avoidOverlap: true,
                spacingFactor: 1.5,
                animate: false,
            },
        };

        const config = layouts[name] || layouts.dagre;
        this.cy.layout(config).run();
    }
}

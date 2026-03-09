/**
 * Preview Manager - Preview topology as it would render in a lab guide
 * Shows a read-only Cytoscape viewer in a modal dialog
 */

import { getCytoscapeStyles } from '../topology/cytoscape-styles.js';
import { getLayout } from '../topology/layout-config.js';

export class PreviewManager {
    constructor(options = {}) {
        this.getState = options.getState;
        this.previewCy = null;

        this.bindEvents();
    }

    bindEvents() {
        document.getElementById('preview-close').addEventListener('click', () => this.hide());
        document.getElementById('preview-modal').addEventListener('click', (e) => {
            if (e.target.id === 'preview-modal') this.hide();
        });
    }

    show() {
        const modal = document.getElementById('preview-modal');
        const container = document.getElementById('preview-container');
        modal.style.display = '';

        // Destroy previous preview
        if (this.previewCy) {
            this.previewCy.destroy();
            this.previewCy = null;
        }

        const state = this.getState();
        const elements = this.buildElements(state);

        const styles = getCytoscapeStyles();

        // Add compound node (zone) styles
        styles.push({
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
        });

        this.previewCy = cytoscape({
            container: container,
            style: styles,
            elements: elements,
            layout: { name: 'preset' },
            userPanningEnabled: true,
            userZoomingEnabled: true,
            boxSelectionEnabled: false,
            selectionType: 'single',
            minZoom: 0.2,
            maxZoom: 3,
        });

        // Run layout if needed
        const layoutName = state.settings?.layout || 'dagre';
        if (layoutName !== 'preset') {
            const layoutConfig = getLayout(layoutName);
            this.previewCy.layout(layoutConfig).run();
        } else {
            this.previewCy.fit(undefined, 30);
        }
    }

    hide() {
        document.getElementById('preview-modal').style.display = 'none';
        if (this.previewCy) {
            this.previewCy.destroy();
            this.previewCy = null;
        }
    }

    buildElements(state) {
        const elements = [];

        // Zone parent nodes
        if (state.zones) {
            for (const zone of state.zones) {
                elements.push({
                    group: 'nodes',
                    data: {
                        id: zone.id,
                        label: zone.label || zone.id,
                        isZone: true,
                        zoneColor: zone.color || '#071c35',
                        zoneBackground: zone.background || 'rgba(7, 28, 53, 0.05)',
                        zoneBorderStyle: zone.border_style || 'solid',
                    },
                });
            }
        }

        // Device nodes
        if (state.nodes) {
            for (const node of state.nodes) {
                const nodeData = {
                    id: node.id,
                    label: node.label || node.id,
                    device_type: node.type || 'other',
                    ip: node.ip || '',
                };
                if (node.zone) nodeData.parent = node.zone;

                const elem = {
                    group: 'nodes',
                    data: nodeData,
                    classes: `device-type-${node.type || 'other'}`,
                };
                if (node.position) {
                    elem.position = { x: node.position.x, y: node.position.y };
                }
                elements.push(elem);
            }
        }

        // Edges
        if (state.edges) {
            for (const edge of state.edges) {
                elements.push({
                    group: 'edges',
                    data: {
                        id: `${edge.source}|${edge.target}`,
                        source: edge.source,
                        target: edge.target,
                        source_port: edge.source_port || '',
                        target_port: edge.target_port || '',
                    },
                });
            }
        }

        return elements;
    }
}

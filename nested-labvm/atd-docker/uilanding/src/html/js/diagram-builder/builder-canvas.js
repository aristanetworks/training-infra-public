/**
 * Builder Canvas - Cytoscape.js editing canvas for the diagram builder
 * Handles node placement, drag-and-drop from palette, and layout
 */

import { getCytoscapeStyles } from '../topology/cytoscape-styles.js';
import { getLayout } from '../topology/layout-config.js';

export class BuilderCanvas {
    constructor(containerId, options = {}) {
        this.containerId = containerId;
        this.container = document.getElementById(containerId);
        this.options = options;
        this.cy = null;
        this.dragType = null;
        this.dragGhost = null;

        this.initCytoscape();
        this.bindEvents();
        this.bindDragDrop();
    }

    initCytoscape() {
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

        // Zone parent selection
        styles.push({
            selector: ':parent:selected',
            style: {
                'border-color': '#fbb500',
                'border-width': 3,
            }
        });

        // Edge label styles for builder (show port labels when editing)
        styles.push({
            selector: 'edge',
            style: {
                'source-label': 'data(source_port)',
                'target-label': 'data(target_port)',
                'source-text-offset': 30,
                'target-text-offset': 30,
                'source-text-rotation': 'autorotate',
                'target-text-rotation': 'autorotate',
                'font-size': 9,
                'text-outline-color': '#ffffff',
                'text-outline-width': 2,
            }
        });

        this.cy = cytoscape({
            container: this.container,
            style: styles,
            elements: [],
            layout: { name: 'preset' },
            minZoom: 0.2,
            maxZoom: 3,
            wheelSensitivity: 0.3,
            boxSelectionEnabled: true,
            selectionType: 'single',
        });
    }

    bindEvents() {
        // Selection events
        this.cy.on('select', 'node, edge', () => {
            const selected = this.cy.elements(':selected');
            if (this.options.onSelect) {
                this.options.onSelect(selected.toArray());
            }
        });

        this.cy.on('unselect', () => {
            setTimeout(() => {
                const selected = this.cy.elements(':selected');
                if (this.options.onSelect) {
                    this.options.onSelect(selected.toArray());
                }
            }, 0);
        });

        // Position change (drag end)
        this.cy.on('dragfree', 'node', () => {
            if (this.options.onPositionChange) {
                this.options.onPositionChange();
            }
        });
    }

    bindDragDrop() {
        const canvasWrapper = this.container.parentElement;

        canvasWrapper.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'copy';
        });

        canvasWrapper.addEventListener('drop', (e) => {
            e.preventDefault();
            const deviceType = e.dataTransfer.getData('text/plain');
            if (!deviceType) return;

            // Convert screen position to canvas model position
            const rect = this.container.getBoundingClientRect();
            const pan = this.cy.pan();
            const zoom = this.cy.zoom();

            const position = {
                x: (e.clientX - rect.left - pan.x) / zoom,
                y: (e.clientY - rect.top - pan.y) / zoom,
            };

            if (this.options.onDrop) {
                this.options.onDrop(deviceType, position);
            }
        });
    }

    startDrag(type, event) {
        this.dragType = type;
    }

    runLayout(layoutName) {
        const layoutConfig = getLayout(layoutName);
        this.cy.layout(layoutConfig).run();
    }

    fit() {
        this.cy.fit(undefined, 30);
    }

    clear() {
        this.cy.elements().remove();
    }
}

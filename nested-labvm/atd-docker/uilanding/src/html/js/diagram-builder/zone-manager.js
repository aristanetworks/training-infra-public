/**
 * Zone Manager - Background zone creation and editing
 * Uses Cytoscape.js compound nodes for zone grouping
 * Supports layer ordering: higher layer values render on top
 */

export class ZoneManager {
    constructor(cy, options = {}) {
        this.cy = cy;
        this.options = options;
        this.zoneCounter = 0;
    }

    /**
     * Create a new zone, optionally containing the selected nodes
     */
    createZone(selectedNodes) {
        this.zoneCounter++;
        const id = `zone${this.zoneCounter}`;
        const label = `Zone ${this.zoneCounter}`;

        // Default layer: count existing zones so new ones go on top
        const existingZones = this.cy.nodes().filter(n => n.data('isZone'));
        const maxLayer = existingZones.reduce((max, z) => Math.max(max, z.data('zoneLayer') || 0), 0);

        this.addZone({
            id,
            label,
            color: '#071c35',
            background: 'rgba(7, 28, 53, 0.15)',
            border_style: 'solid',
            layer: maxLayer + 1,
        });

        // Move selected nodes into the zone
        if (selectedNodes && selectedNodes.length) {
            selectedNodes.forEach(node => {
                if (!node.data('isZone')) {
                    node.move({ parent: id });
                }
            });
        }

        if (this.options.onChange) {
            this.options.onChange();
        }

        return id;
    }

    /**
     * Add a zone from data (used during import)
     */
    addZone(zoneData) {
        // Track counter to avoid ID collisions
        const numMatch = zoneData.id.match(/\d+$/);
        if (numMatch) {
            const num = parseInt(numMatch[0]);
            if (num >= this.zoneCounter) {
                this.zoneCounter = num + 1;
            }
        }

        const layer = zoneData.layer !== undefined ? zoneData.layer : 0;

        this.cy.add({
            group: 'nodes',
            data: {
                id: zoneData.id,
                label: zoneData.label || zoneData.id,
                isZone: true,
                zoneColor: zoneData.color || '#071c35',
                zoneBackground: zoneData.background || 'rgba(7, 28, 53, 0.15)',
                zoneBorderStyle: zoneData.border_style || 'solid',
                zoneLayer: layer,
            },
        });

        // Apply z-index based on layer
        this.applyZoneZIndex(zoneData.id, layer);
    }

    /**
     * Apply z-index styling to a zone based on its layer value
     */
    applyZoneZIndex(id, layer) {
        const zone = this.cy.$id(id);
        if (zone.empty()) return;
        // Lower z-index = rendered behind. Offset by 1 so layer 0 gets z-index 1
        zone.style('z-index', layer + 1);
        // Also adjust z-compound-depth so nested zones render correctly
        zone.style('z-compound-depth', 'bottom');
    }

    /**
     * Update zone properties
     */
    updateZone(id, changes) {
        const zone = this.cy.$id(id);
        if (zone.empty()) return;

        if (changes.label !== undefined) zone.data('label', changes.label);
        if (changes.color !== undefined) zone.data('zoneColor', changes.color);
        if (changes.background !== undefined) zone.data('zoneBackground', changes.background);
        if (changes.border_style !== undefined) zone.data('zoneBorderStyle', changes.border_style);
        if (changes.layer !== undefined) {
            const layer = parseInt(changes.layer) || 0;
            zone.data('zoneLayer', layer);
            this.applyZoneZIndex(id, layer);
        }

        if (this.options.onChange) {
            this.options.onChange();
        }
    }

    /**
     * Get all zones as serializable data, sorted by layer (ascending)
     */
    getZones() {
        const zones = [];
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) return;
            zones.push({
                id: node.id(),
                label: node.data('label') || node.id(),
                color: node.data('zoneColor') || '#071c35',
                background: node.data('zoneBackground') || 'rgba(7, 28, 53, 0.15)',
                border_style: node.data('zoneBorderStyle') || 'solid',
                layer: node.data('zoneLayer') || 0,
            });
        });
        // Sort by layer ascending so lower layers are added first (rendered behind)
        zones.sort((a, b) => a.layer - b.layer);
        return zones;
    }

    /**
     * Get zone list for dropdown population
     */
    getZoneList() {
        return this.getZones().map(z => ({ id: z.id, label: z.label }));
    }

    /**
     * Reapply z-index to all zones (call after import/load)
     */
    reapplyAllLayers() {
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) return;
            const layer = node.data('zoneLayer') || 0;
            this.applyZoneZIndex(node.id(), layer);
        });
    }

    /**
     * Remove a zone (moves children out first)
     */
    removeZone(id) {
        const zone = this.cy.$id(id);
        if (zone.empty()) return;

        // Move children out
        zone.children().move({ parent: null });
        zone.remove();

        if (this.options.onChange) {
            this.options.onChange();
        }
    }
}

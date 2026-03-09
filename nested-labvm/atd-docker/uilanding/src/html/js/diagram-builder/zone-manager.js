/**
 * Zone Manager - Background zone creation and editing
 * Uses Cytoscape.js compound nodes for zone grouping
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

        this.addZone({
            id,
            label,
            color: '#071c35',
            background: 'rgba(7, 28, 53, 0.05)',
            border_style: 'solid',
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

        this.cy.add({
            group: 'nodes',
            data: {
                id: zoneData.id,
                label: zoneData.label || zoneData.id,
                isZone: true,
                zoneColor: zoneData.color || '#071c35',
                zoneBackground: zoneData.background || 'rgba(7, 28, 53, 0.05)',
                zoneBorderStyle: zoneData.border_style || 'solid',
            },
        });
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

        if (this.options.onChange) {
            this.options.onChange();
        }
    }

    /**
     * Get all zones as serializable data
     */
    getZones() {
        const zones = [];
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) return;
            zones.push({
                id: node.id(),
                label: node.data('label') || node.id(),
                color: node.data('zoneColor') || '#071c35',
                background: node.data('zoneBackground') || 'rgba(7, 28, 53, 0.05)',
                border_style: node.data('zoneBorderStyle') || 'solid',
            });
        });
        return zones;
    }

    /**
     * Get zone list for dropdown population
     */
    getZoneList() {
        return this.getZones().map(z => ({ id: z.id, label: z.label }));
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

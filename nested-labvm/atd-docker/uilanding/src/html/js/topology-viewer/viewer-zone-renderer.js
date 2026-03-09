/**
 * Viewer Zone Renderer - Compound node rendering for zones
 * Zones are rendered using Cytoscape.js native compound node support.
 * Styling is handled via the :parent selector in viewer-manager.js styles.
 * This module provides helper methods for zone management.
 */

export class ViewerZoneRenderer {
    constructor(cy) {
        this.cy = cy;
    }

    /**
     * Get all zone parent nodes
     */
    getZones() {
        return this.cy.nodes().filter(n => n.data('isZone'));
    }

    /**
     * Get nodes belonging to a specific zone
     */
    getZoneChildren(zoneId) {
        const zone = this.cy.$id(zoneId);
        if (zone.empty()) return this.cy.collection();
        return zone.children();
    }

    /**
     * Check if a node belongs to any zone
     */
    hasZone(nodeId) {
        const node = this.cy.$id(nodeId);
        return !node.empty() && node.parent().length > 0;
    }

    /**
     * Get the zone ID of a node (or null)
     */
    getNodeZone(nodeId) {
        const node = this.cy.$id(nodeId);
        if (node.empty() || node.parent().length === 0) return null;
        return node.parent().id();
    }
}

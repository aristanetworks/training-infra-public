/**
 * Layout Configuration for ATL Topology Diagram
 * Uses preset layout with server-calculated positions for organized display
 */

/**
 * Get Preset layout configuration
 * Uses positions calculated by the server based on device tiers
 * Best for organized, predictable layouts
 */
export function getPresetLayout() {
    return {
        name: 'preset',
        fit: true,
        padding: 50,
        animate: true,
        animationDuration: 500,
        animationEasing: 'ease-out-cubic'
    };
}

/**
 * Get Dagre hierarchical layout configuration
 * Best for spine-leaf and tiered network topologies
 */
export function getDagreLayout() {
    return {
        name: 'dagre',
        rankDir: 'TB',           // Top to bottom
        align: 'UL',             // Align upper-left
        ranker: 'network-simplex',
        rankSep: 80,             // Separation between ranks (vertical spacing)
        nodeSep: 50,             // Separation between nodes in same rank
        edgeSep: 20,             // Separation between edges
        padding: 30,             // Padding around the layout
        animate: true,
        animationDuration: 500,
        animationEasing: 'ease-out-cubic',
        fit: true,               // Fit to container
        spacingFactor: 1.2       // Multiply spacing
    };
}

/**
 * Get CoSE (Compound Spring Embedder) layout configuration
 * Good for general network graphs with clusters
 */
export function getCoseLayout() {
    return {
        name: 'cose',
        idealEdgeLength: 100,
        nodeOverlap: 20,
        refresh: 20,
        fit: true,
        padding: 30,
        randomize: false,
        componentSpacing: 100,
        nodeRepulsion: 400000,
        edgeElasticity: 100,
        nestingFactor: 5,
        gravity: 80,
        numIter: 1000,
        initialTemp: 200,
        coolingFactor: 0.95,
        minTemp: 1.0,
        animate: true,
        animationDuration: 500
    };
}

/**
 * Get Concentric layout configuration
 * Places nodes in concentric circles based on degree
 */
export function getConcentricLayout() {
    return {
        name: 'concentric',
        fit: true,
        padding: 30,
        startAngle: 3 / 2 * Math.PI,
        sweep: undefined,
        clockwise: true,
        equidistant: false,
        minNodeSpacing: 50,
        boundingBox: undefined,
        avoidOverlap: true,
        nodeDimensionsIncludeLabels: true,
        height: undefined,
        width: undefined,
        spacingFactor: 1.5,
        concentric: function(node) {
            // Spines/PE at center, then leaves, then hosts
            const deviceType = node.data('device_type');
            switch (deviceType) {
                case 'spine':
                case 'pe':
                case 'core':
                    return 3;
                case 'leaf':
                case 'borderleaf':
                case 'p':
                    return 2;
                case 'host':
                case 'oob':
                    return 1;
                default:
                    return 1;
            }
        },
        levelWidth: function(nodes) {
            return nodes.maxDegree() / 4;
        },
        animate: true,
        animationDuration: 500
    };
}

/**
 * Get Grid layout configuration
 * Simple grid arrangement
 */
export function getGridLayout() {
    return {
        name: 'grid',
        fit: true,
        padding: 30,
        avoidOverlap: true,
        avoidOverlapPadding: 10,
        nodeDimensionsIncludeLabels: true,
        spacingFactor: 1.5,
        condense: false,
        rows: undefined,
        cols: undefined,
        position: function(node) { return null; },
        sort: function(a, b) {
            // Sort by device type then name
            const typeOrder = {
                'spine': 0, 'pe': 1, 'core': 2,
                'leaf': 3, 'borderleaf': 4, 'p': 5,
                'host': 6, 'oob': 7, 'other': 8
            };
            const typeA = typeOrder[a.data('device_type')] || 99;
            const typeB = typeOrder[b.data('device_type')] || 99;
            if (typeA !== typeB) return typeA - typeB;
            return a.data('label').localeCompare(b.data('label'));
        },
        animate: true,
        animationDuration: 500
    };
}

/**
 * Get layout by name
 * @param {string} layoutName - Name of the layout ('preset', 'dagre', 'cose', 'concentric', 'grid')
 * @returns {Object} Layout configuration
 */
export function getLayout(layoutName) {
    switch (layoutName) {
        case 'preset':
            return getPresetLayout();
        case 'dagre':
            return getDagreLayout();
        case 'cose':
            return getCoseLayout();
        case 'concentric':
            return getConcentricLayout();
        case 'grid':
            return getGridLayout();
        default:
            return getPresetLayout();  // Default to preset (server-calculated positions)
    }
}

/**
 * Available layout options for UI
 */
export const LAYOUT_OPTIONS = [
    { id: 'preset', name: 'Organized', description: 'Tiered layout by device type (default)' },
    { id: 'dagre', name: 'Hierarchical', description: 'Auto-arranged spine-leaf layout' },
    { id: 'cose', name: 'Force-Directed', description: 'Organic clustering layout' },
    { id: 'concentric', name: 'Concentric', description: 'Circles by device importance' },
    { id: 'grid', name: 'Grid', description: 'Simple grid arrangement' }
];

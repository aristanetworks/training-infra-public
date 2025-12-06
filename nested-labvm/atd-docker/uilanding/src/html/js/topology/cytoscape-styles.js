/**
 * Cytoscape.js Styles for ATL Topology Diagram
 * Uses ATL color scheme: Navy #071c35, Yellow #fbb500
 */

export function getCytoscapeStyles() {
    return [
        // ==========================================
        // Node Base Styles
        // ==========================================
        {
            selector: 'node',
            style: {
                'label': 'data(label)',
                'text-valign': 'bottom',
                'text-halign': 'center',
                'text-margin-y': 8,
                'font-family': '"Proxima Nova", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
                'font-size': 11,
                'font-weight': 500,
                'color': '#071c35',
                'text-outline-color': '#ffffff',
                'text-outline-width': 2,
                'border-width': 2,
                'border-color': '#071c35',
                'background-color': '#ffffff',
                'width': 50,
                'height': 50,
                'transition-property': 'border-color, border-width, background-color, opacity',
                'transition-duration': '0.2s'
            }
        },

        // ==========================================
        // Device Type Styles
        // ==========================================
        {
            selector: '.device-type-spine',
            style: {
                'shape': 'diamond',
                'background-color': '#4c5cae',
                'border-color': '#071c35',
                'width': 60,
                'height': 60
            }
        },
        {
            selector: '.device-type-leaf',
            style: {
                'shape': 'round-rectangle',
                'background-color': '#20b2aa',  // Light sea green - distinct from status-up green
                'border-color': '#071c35',
                'width': 50,
                'height': 50
            }
        },
        {
            selector: '.device-type-borderleaf',
            style: {
                'shape': 'hexagon',
                'background-color': '#fbb500',
                'border-color': '#071c35',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-host',
            style: {
                'shape': 'ellipse',
                'background-color': '#dae0fe',
                'border-color': '#4c5cae',
                'width': 45,
                'height': 45
            }
        },
        {
            selector: '.device-type-core',
            style: {
                'shape': 'triangle',
                'background-color': '#071c35',
                'border-color': '#fbb500',
                'color': '#ffffff',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-pe',
            style: {
                'shape': 'diamond',
                'background-color': '#4c5cae',
                'border-color': '#071c35',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-p',
            style: {
                'shape': 'diamond',
                'background-color': '#6b7cc9',
                'border-color': '#071c35',
                'width': 50,
                'height': 50
            }
        },
        {
            selector: '.device-type-ce',
            style: {
                'shape': 'round-rectangle',
                'background-color': '#4c5cae',
                'border-color': '#071c35',
                'width': 45,
                'height': 45
            }
        },
        {
            selector: '.device-type-customer',
            style: {
                'shape': 'round-rectangle',
                'background-color': '#20b2aa',  // Light sea green - matches leaf color
                'border-color': '#071c35',
                'width': 45,
                'height': 45
            }
        },
        {
            selector: '.device-type-router',
            style: {
                'shape': 'diamond',
                'background-color': '#8b4513',  // Saddle brown - distinct for campus routers
                'border-color': '#071c35',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-memleaf',
            style: {
                'shape': 'round-rectangle',
                'background-color': '#32cd32',  // Lime green - between leaf and host
                'border-color': '#071c35',
                'width': 48,
                'height': 48
            }
        },
        {
            selector: '.device-type-dci',
            style: {
                'shape': 'octagon',
                'background-color': '#051431',
                'border-color': '#fbb500',
                'color': '#ffffff',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-isp, .device-type-internet',
            style: {
                'shape': 'star',
                'background-color': '#e30909',
                'border-color': '#071c35',
                'width': 60,
                'height': 60
            }
        },
        {
            selector: '.device-type-oob',
            style: {
                'shape': 'round-rectangle',
                'background-color': '#808080',
                'border-color': '#071c35',
                'width': 45,
                'height': 45
            }
        },
        {
            selector: '.device-type-gw',
            style: {
                'shape': 'pentagon',
                'background-color': '#d4a400',
                'border-color': '#071c35',
                'width': 55,
                'height': 55
            }
        },
        {
            selector: '.device-type-rr',
            style: {
                'shape': 'star',
                'background-color': '#008b8b',
                'border-color': '#071c35',
                'color': '#ffffff',
                'width': 60,
                'height': 60
            }
        },
        {
            selector: '.device-type-other',
            style: {
                'shape': 'rectangle',
                'background-color': '#666666',
                'border-color': '#071c35',
                'width': 45,
                'height': 45
            }
        },

        // ==========================================
        // Status Styles - Visual indicators for device reachability
        // ==========================================
        {
            selector: '.status-up',
            style: {
                'border-color': '#78d82c',
                'border-width': 4,
                'border-opacity': 1
            }
        },
        {
            selector: '.status-down',
            style: {
                'border-color': '#e30909',
                'border-width': 5,
                'border-opacity': 1,
                'opacity': 0.8
            }
        },
        {
            selector: '.status-error',
            style: {
                'border-color': '#ff8c00',
                'border-width': 4,
                'border-style': 'dashed',
                'opacity': 0.9
            }
        },
        {
            selector: '.status-init',
            style: {
                'border-color': '#fbb500',
                'border-width': 3
            }
        },
        {
            selector: '.status-unknown',
            style: {
                'border-color': '#808080',
                'border-style': 'dashed',
                'border-width': 2
            }
        },

        // ==========================================
        // Edge Styles
        // ==========================================
        {
            selector: 'edge',
            style: {
                'width': 2,
                'line-color': '#071c35',
                'curve-style': 'bezier',
                'opacity': 0.7,
                'transition-property': 'line-color, width, opacity',
                'transition-duration': '0.2s',
                // Source and target port labels anchored to respective endpoints
                'source-label': 'data(source_port)',
                'target-label': 'data(target_port)',
                'source-text-offset': 30,
                'target-text-offset': 30,
                'source-text-rotation': 'autorotate',
                'target-text-rotation': 'autorotate',
                'font-size': 10,
                'font-family': '"Proxima Nova", sans-serif',
                'color': '#333333',
                'text-outline-color': '#ffffff',
                'text-outline-width': 2,
                'text-background-color': '#ffffff',
                'text-background-opacity': 0.8,
                'text-background-padding': '2px'
            }
        },

        // ==========================================
        // Interactive States
        // ==========================================
        {
            selector: 'node:selected',
            style: {
                'border-color': '#fbb500',
                'border-width': 4,
                'box-shadow': '0 0 10px #fbb500'
            }
        },
        {
            selector: 'node.highlighted',
            style: {
                'border-color': '#fbb500',
                'border-width': 4,
                'z-index': 999
            }
        },
        {
            selector: 'edge.highlighted',
            style: {
                'line-color': '#fbb500',
                'width': 4,
                'opacity': 1,
                'z-index': 998
            }
        },
        {
            selector: 'node.faded',
            style: {
                'opacity': 0.3
            }
        },
        {
            selector: 'edge.faded',
            style: {
                'opacity': 0.15
            }
        },

        // ==========================================
        // Hover States (for visual feedback)
        // ==========================================
        {
            selector: 'node.hover',
            style: {
                'border-width': 3,
                'z-index': 999
            }
        },
        {
            selector: 'edge.hover',
            style: {
                'width': 3,
                'opacity': 1
            }
        },

        // ==========================================
        // Focus Mode States (right-click to focus)
        // ==========================================
        {
            selector: 'node.focused',
            style: {
                'border-color': '#fbb500',
                'border-width': 5,
                'z-index': 9999,
                'font-size': 14,
                'font-weight': 700
            }
        },

        // ==========================================
        // Edge Utilization States (based on interface stats)
        // ==========================================
        {
            selector: 'edge.utilization-low',
            style: {
                'line-color': '#78d82c',
                'width': 2
            }
        },
        {
            selector: 'edge.utilization-medium',
            style: {
                'line-color': '#fbb500',
                'width': 3
            }
        },
        {
            selector: 'edge.utilization-high',
            style: {
                'line-color': '#ff8c00',
                'width': 3
            }
        },
        {
            selector: 'edge.utilization-critical',
            style: {
                'line-color': '#e30909',
                'width': 4
            }
        },
        {
            selector: 'edge.has-errors',
            style: {
                'line-color': '#e30909',
                'line-style': 'dashed',
                'width': 3
            }
        }
    ];
}

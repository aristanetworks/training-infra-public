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
                'background-color': '#78d82c',
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
                'background-color': '#78d82c',
                'border-color': '#071c35',
                'width': 45,
                'height': 45
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
        // Status Styles
        // ==========================================
        {
            selector: '.status-up',
            style: {
                'border-color': '#78d82c',
                'border-width': 3
            }
        },
        {
            selector: '.status-down',
            style: {
                'border-color': '#e30909',
                'border-width': 4,
                'opacity': 0.7
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
                'border-style': 'dashed'
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
                'transition-duration': '0.2s'
            }
        },
        {
            selector: 'edge[source_port]',
            style: {
                'source-label': 'data(source_port)',
                'source-text-offset': 20,
                'font-size': 9,
                'font-family': '"Proxima Nova", sans-serif',
                'source-text-rotation': 'autorotate',
                'color': '#333333',
                'text-outline-color': '#ffffff',
                'text-outline-width': 2
            }
        },
        {
            selector: 'edge[target_port]',
            style: {
                'target-label': 'data(target_port)',
                'target-text-offset': 20,
                'font-size': 9,
                'font-family': '"Proxima Nova", sans-serif',
                'target-text-rotation': 'autorotate',
                'color': '#333333',
                'text-outline-color': '#ffffff',
                'text-outline-width': 2
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
        }
    ];
}

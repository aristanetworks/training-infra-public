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
                'border-width': 0,
                'border-color': 'transparent',
                'border-opacity': 0,
                'background-color': 'transparent',
                'background-opacity': 0,
                'width': 50,
                'height': 50,
                'transition-property': 'border-color, border-width, background-color, opacity',
                'transition-duration': '0.2s'
            }
        },

        // ==========================================
        // Device Type Styles - Using PNG icons with transparent backgrounds
        // Icons: leaf.png, spine.png, router.png
        // Sizes: 60-70px depending on device importance
        // ==========================================

        // --- Spine icon: spine, pe, p, ce ---
        {
            selector: '.device-type-spine',
            style: {
                'background-image': 'images/spine.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 70,
                'height': 70
            }
        },
        {
            selector: '.device-type-pe',
            style: {
                'background-image': 'images/spine.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 65,
                'height': 65
            }
        },
        {
            selector: '.device-type-p',
            style: {
                'background-image': 'images/spine.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 62,
                'height': 62
            }
        },
        {
            selector: '.device-type-ce',
            style: {
                'background-image': 'images/spine.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },

        // --- Leaf icon: leaf, memleaf, borderleaf ---
        {
            selector: '.device-type-leaf',
            style: {
                'background-image': 'images/leaf.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 70,
                'height': 70
            }
        },
        {
            selector: '.device-type-borderleaf',
            style: {
                'background-image': 'images/leaf.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 68,
                'height': 68
            }
        },
        {
            selector: '.device-type-memleaf',
            style: {
                'background-image': 'images/leaf.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 62,
                'height': 62
            }
        },

        // --- Router icon: router, core, customer, dci, isp, internet, oob, gw, rr, other ---
        {
            selector: '.device-type-router',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 65,
                'height': 65
            }
        },
        {
            selector: '.device-type-core',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 68,
                'height': 68
            }
        },
        {
            selector: '.device-type-customer',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },
        {
            selector: '.device-type-dci',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 68,
                'height': 68
            }
        },
        {
            selector: '.device-type-isp, .device-type-internet',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 70,
                'height': 70
            }
        },
        {
            selector: '.device-type-oob',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },
        {
            selector: '.device-type-gw',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 65,
                'height': 65
            }
        },
        {
            selector: '.device-type-rr',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 68,
                'height': 68
            }
        },
        {
            selector: '.device-type-other',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },

        // --- Host icon ---
        {
            selector: '.device-type-host',
            style: {
                'background-image': 'images/hosts.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 70,
                'height': 70
            }
        },

        // --- Linux Host icon (user-added Linux VMs) ---
        {
            selector: '.device-type-linux_host',
            style: {
                'background-image': 'images/hosts.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 70,
                'height': 70
            }
        },

        // --- Firewall icon (user-added VyOS firewalls) ---
        {
            selector: '.device-type-firewall',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },

        // --- VeloCloud SD-WAN icons (user-added VeloCloud devices) ---
        {
            selector: '.device-type-velo_orchestrator',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 68,
                'height': 68
            }
        },
        {
            selector: '.device-type-velo_gateway',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 65,
                'height': 65
            }
        },
        {
            selector: '.device-type-velo_edge',
            style: {
                'background-image': 'images/router.png',
                'background-fit': 'contain',
                'background-clip': 'none',
                'background-color': 'transparent',
                'border-width': 0,
                'width': 60,
                'height': 60
            }
        },

        // ==========================================
        // Status Styles - Visual indicators for device reachability
        // Uses underlay to show status behind PNG icons
        // ==========================================
        {
            selector: '.status-up',
            style: {
                'underlay-color': '#78d82c',
                'underlay-padding': 6,
                'underlay-opacity': 0.3,
                'underlay-shape': 'ellipse'
            }
        },
        {
            selector: '.status-down',
            style: {
                'underlay-color': '#e30909',
                'underlay-padding': 8,
                'underlay-opacity': 0.4,
                'underlay-shape': 'ellipse',
                'opacity': 0.85
            }
        },
        {
            selector: '.status-error',
            style: {
                'underlay-color': '#ff8c00',
                'underlay-padding': 6,
                'underlay-opacity': 0.35,
                'underlay-shape': 'ellipse',
                'opacity': 0.9
            }
        },
        {
            selector: '.status-init',
            style: {
                'underlay-color': '#fbb500',
                'underlay-padding': 5,
                'underlay-opacity': 0.25,
                'underlay-shape': 'ellipse'
            }
        },
        {
            selector: '.status-unknown',
            style: {
                'underlay-color': '#808080',
                'underlay-padding': 4,
                'underlay-opacity': 0.2,
                'underlay-shape': 'ellipse'
            }
        },
        {
            // Unconfigured: device is reachable but auth failed (needs configuration)
            selector: '.status-unconfigured',
            style: {
                'underlay-color': '#4c5cae',
                'underlay-padding': 6,
                'underlay-opacity': 0.35,
                'underlay-shape': 'ellipse'
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
        // Uses underlay to show highlights behind PNG icons
        // ==========================================
        {
            selector: 'node:selected',
            style: {
                'underlay-color': '#fbb500',
                'underlay-padding': 10,
                'underlay-opacity': 0.4,
                'underlay-shape': 'ellipse'
            }
        },
        {
            selector: 'node.highlighted',
            style: {
                'underlay-color': '#fbb500',
                'underlay-padding': 8,
                'underlay-opacity': 0.35,
                'underlay-shape': 'ellipse',
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
        // ==========================================
        // Impairment Styles
        // Colors: Latency=#fbb500, Loss=#e30909, Dup=#4c5cae, Corrupt=#ff8c00, Reorder=#9b59b6
        // ==========================================

        // Single impairment - solid color with dashed line
        // Latency only (yellow)
        {
            selector: 'edge.has-latency',
            style: {
                'line-color': '#fbb500',
                'line-style': 'dashed',
                'line-dash-pattern': [8, 4],
                'width': 3,
                'opacity': 1,
                'z-index': 997
            }
        },
        // Packet Loss only (red)
        {
            selector: 'edge.has-loss',
            style: {
                'line-color': '#e30909',
                'line-style': 'dashed',
                'line-dash-pattern': [8, 4],
                'width': 3,
                'opacity': 1,
                'z-index': 997
            }
        },
        // Duplication only (blue)
        {
            selector: 'edge.has-duplication',
            style: {
                'line-color': '#4c5cae',
                'line-style': 'dashed',
                'line-dash-pattern': [8, 4],
                'width': 3,
                'opacity': 1,
                'z-index': 997
            }
        },
        // Corruption only (orange)
        {
            selector: 'edge.has-corruption',
            style: {
                'line-color': '#ff8c00',
                'line-style': 'dashed',
                'line-dash-pattern': [8, 4],
                'width': 3,
                'opacity': 1,
                'z-index': 997
            }
        },
        // Reorder only (purple)
        {
            selector: 'edge.has-reorder',
            style: {
                'line-color': '#9b59b6',
                'line-style': 'dashed',
                'line-dash-pattern': [8, 4],
                'width': 3,
                'opacity': 1,
                'z-index': 997
            }
        },
        // Multiple impairments - striped gradient
        // Uses line-gradient with colors stored in edge data
        {
            selector: 'edge.has-impairments',
            style: {
                'line-fill': 'linear-gradient',
                'line-gradient-stop-colors': 'data(gradientColors)',
                'line-gradient-stop-positions': 'data(gradientPositions)',
                'line-style': 'solid',
                'width': 4,
                'opacity': 1,
                'z-index': 997
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
                'underlay-color': '#071c35',
                'underlay-padding': 6,
                'underlay-opacity': 0.15,
                'underlay-shape': 'ellipse',
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
                'underlay-color': '#fbb500',
                'underlay-padding': 12,
                'underlay-opacity': 0.45,
                'underlay-shape': 'ellipse',
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

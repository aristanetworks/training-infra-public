/**
 * Filter Manager for ATL Interactive Topology Diagram
 * Handles device type filtering and search functionality
 */

// Cached device type info from API
let _cachedDeviceTypeInfo = null;

/**
 * Fetch device type metadata from backend API.
 * Falls back to DEFAULT_DEVICE_TYPE_INFO if API fails.
 */
export async function loadDeviceTypeInfo() {
    if (_cachedDeviceTypeInfo) {
        return _cachedDeviceTypeInfo;
    }

    try {
        const response = await fetch('/td-api/device-types');
        if (response.ok) {
            _cachedDeviceTypeInfo = await response.json();
            console.log('[FilterManager] Loaded device types from API');
            return _cachedDeviceTypeInfo;
        }
    } catch (error) {
        console.warn('[FilterManager] Failed to load device types from API, using defaults:', error);
    }

    // Fallback to defaults
    _cachedDeviceTypeInfo = DEFAULT_DEVICE_TYPE_INFO;
    return _cachedDeviceTypeInfo;
}

/**
 * Get device type info (sync version using cached data)
 */
export function getDeviceTypeInfo(deviceType) {
    const info = _cachedDeviceTypeInfo || DEFAULT_DEVICE_TYPE_INFO;
    return info[deviceType] || info['other'] || { label: 'Other', color: '#666666' };
}

/**
 * Get all device type info (sync version using cached data)
 */
export function getAllDeviceTypeInfo() {
    return _cachedDeviceTypeInfo || DEFAULT_DEVICE_TYPE_INFO;
}

export class FilterManager {
    constructor(cy, controlsContainer) {
        this.cy = cy;
        this.controlsContainer = controlsContainer;
        this.filters = {};
        this.searchTerm = '';
        this.init();
    }

    /**
     * Initialize filter manager
     */
    init() {
        // Get all unique device types from the graph
        const deviceTypes = new Set();
        this.cy.nodes().forEach(node => {
            deviceTypes.add(node.data('device_type'));
        });

        // Initialize all filters as enabled
        deviceTypes.forEach(type => {
            this.filters[type] = true;
        });
    }

    /**
     * Set filter state for a device type
     */
    setFilter(deviceType, enabled) {
        this.filters[deviceType] = enabled;
        this.applyFilters();
    }

    /**
     * Toggle filter for a device type
     */
    toggleFilter(deviceType) {
        this.filters[deviceType] = !this.filters[deviceType];
        this.applyFilters();
        return this.filters[deviceType];
    }

    /**
     * Set search term
     */
    setSearch(term) {
        this.searchTerm = term.toLowerCase().trim();
        this.applyFilters();
    }

    /**
     * Apply all filters and search
     */
    applyFilters() {
        const searchTerm = this.searchTerm;
        const filters = this.filters;

        this.cy.batch(() => {
            this.cy.nodes().forEach(node => {
                const deviceType = node.data('device_type');
                const label = node.data('label').toLowerCase();
                const ip = node.data('ip').toLowerCase();

                // Check type filter
                const typeVisible = filters[deviceType] !== false;

                // Check search filter
                let searchVisible = true;
                if (searchTerm) {
                    searchVisible = label.includes(searchTerm) ||
                                   ip.includes(searchTerm) ||
                                   deviceType.includes(searchTerm);
                }

                // Show/hide node
                if (typeVisible && searchVisible) {
                    node.removeClass('filtered-out');
                    node.style('display', 'element');
                } else {
                    node.addClass('filtered-out');
                    node.style('display', 'none');
                }
            });

            // Hide edges connected to hidden nodes
            this.cy.edges().forEach(edge => {
                const source = edge.source();
                const target = edge.target();

                if (source.hasClass('filtered-out') || target.hasClass('filtered-out')) {
                    edge.style('display', 'none');
                } else {
                    edge.style('display', 'element');
                }
            });
        });
    }

    /**
     * Reset all filters
     */
    resetFilters() {
        Object.keys(this.filters).forEach(type => {
            this.filters[type] = true;
        });
        this.searchTerm = '';
        this.applyFilters();
    }

    /**
     * Get current filter state
     */
    getFilters() {
        return { ...this.filters };
    }

    /**
     * Get device type counts
     */
    getDeviceTypeCounts() {
        const counts = {};
        this.cy.nodes().forEach(node => {
            const type = node.data('device_type');
            counts[type] = (counts[type] || 0) + 1;
        });
        return counts;
    }

    /**
     * Show only specific device types
     */
    showOnly(deviceTypes) {
        Object.keys(this.filters).forEach(type => {
            this.filters[type] = deviceTypes.includes(type);
        });
        this.applyFilters();
    }

    /**
     * Hide specific device types
     */
    hide(deviceTypes) {
        deviceTypes.forEach(type => {
            this.filters[type] = false;
        });
        this.applyFilters();
    }

    /**
     * Get visible node count
     */
    getVisibleCount() {
        return this.cy.nodes().filter(node => !node.hasClass('filtered-out')).length;
    }

    /**
     * Get total node count
     */
    getTotalCount() {
        return this.cy.nodes().length;
    }
}

/**
 * Default device type display info (fallback if API unavailable)
 */
const DEFAULT_DEVICE_TYPE_INFO = {
    'spine': { label: 'Spines', color: '#4c5cae' },
    'leaf': { label: 'Leafs', color: '#20b2aa' },
    'borderleaf': { label: 'Borderleafs', color: '#fbb500' },
    'host': { label: 'Hosts', color: '#dae0fe' },
    'linux_host': { label: 'Linux Hosts', color: '#9b59b6' },
    'firewall': { label: 'Firewalls', color: '#e67e22' },
    'core': { label: 'Core', color: '#071c35' },
    'pe': { label: 'PE Routers', color: '#4c5cae' },
    'p': { label: 'P Routers', color: '#6b7cc9' },
    'ce': { label: 'CE Routers', color: '#4c5cae' },
    'gw': { label: 'WAN Gateways', color: '#d4a400' },
    'rr': { label: 'Route Reflectors', color: '#008b8b' },
    'router': { label: 'Routers', color: '#8b4513' },
    'memleaf': { label: 'Member Leafs', color: '#32cd32' },
    'customer': { label: 'Customer', color: '#20b2aa' },
    'dci': { label: 'DCI', color: '#051431' },
    'isp': { label: 'ISP', color: '#e30909' },
    'internet': { label: 'Internet', color: '#e30909' },
    'oob': { label: 'OOB', color: '#808080' },
    'other': { label: 'Other', color: '#666666' }
};

/**
 * Legacy export for backwards compatibility
 * @deprecated Use loadDeviceTypeInfo() or getDeviceTypeInfo() instead
 */
export const DEVICE_TYPE_INFO = DEFAULT_DEVICE_TYPE_INFO;

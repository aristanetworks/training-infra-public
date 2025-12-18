/**
 * NodeBuilder API Service
 *
 * Shared API service for node management operations.
 * Used by AddNodeWizard, AddClusterWizard, and other topology components.
 */

class NodeBuilderAPI {
    // Cache with TTL
    static cache = new Map();
    static CACHE_TTL = 30000; // 30 seconds
    static MAX_CACHE_SIZE = 50; // Prevent unbounded cache growth

    // Request tracking for race condition prevention
    static validationRequestId = 0;

    /**
     * Fetch with caching support
     */
    static async fetchWithCache(key, fetchFn) {
        const cached = this.cache.get(key);
        if (cached && Date.now() - cached.timestamp < this.CACHE_TTL) {
            return cached.data;
        }

        const data = await fetchFn();

        // Enforce max cache size to prevent unbounded growth
        if (this.cache.size >= this.MAX_CACHE_SIZE) {
            this.cleanupCache();
        }

        this.cache.set(key, { data, timestamp: Date.now() });
        return data;
    }

    /**
     * Clean up stale cache entries
     */
    static cleanupCache() {
        const now = Date.now();
        const keysToDelete = [];

        for (const [key, value] of this.cache) {
            if (now - value.timestamp >= this.CACHE_TTL) {
                keysToDelete.push(key);
            }
        }

        // Delete expired entries
        keysToDelete.forEach(key => this.cache.delete(key));

        // If still over limit, delete oldest entries
        if (this.cache.size >= this.MAX_CACHE_SIZE) {
            const entries = [...this.cache.entries()].sort((a, b) => a[1].timestamp - b[1].timestamp);
            const toRemove = entries.slice(0, Math.floor(this.MAX_CACHE_SIZE / 2));
            toRemove.forEach(([key]) => this.cache.delete(key));
        }
    }

    /**
     * Invalidate cache entry
     */
    static invalidateCache(key) {
        if (key) {
            this.cache.delete(key);
        } else {
            this.cache.clear();
        }
    }

    /**
     * Get available IP addresses from DHCP pool
     */
    static async getAvailableIps() {
        return this.fetchWithCache('available-ips', async () => {
            const response = await fetch('/td-api/nodes/available-ips');
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'Failed to fetch available IPs');
            }
            const data = await response.json();
            return data.available_ips || [];
        });
    }

    /**
     * Get target devices available for connections
     */
    static async getTargetDevices() {
        return this.fetchWithCache('target-devices', async () => {
            const response = await fetch('/td-api/nodes/target-devices');
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'Failed to fetch target devices');
            }
            const data = await response.json();
            return data.devices || [];
        });
    }

    /**
     * Get existing nodes (from topo_build + user_nodes)
     */
    static async getExistingNodes() {
        return this.fetchWithCache('existing-nodes', async () => {
            const response = await fetch('/td-api/nodes/existing-nodes');
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'Failed to fetch existing nodes');
            }
            const data = await response.json();
            return data.nodes || [];
        });
    }

    /**
     * Get cluster templates
     */
    static async getClusterTemplates() {
        return this.fetchWithCache('cluster-templates', async () => {
            const response = await fetch('/td-api/nodes/cluster-templates');
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'Failed to fetch cluster templates');
            }
            const data = await response.json();
            return data.templates || [];
        });
    }

    /**
     * Validate a node name
     * Returns requestId for race condition prevention - caller can compare with current expected requestId
     */
    static async validateNode(name) {
        // Increment request ID for tracking
        const requestId = ++this.validationRequestId;

        const response = await fetch('/td-api/nodes/validate-node', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        const result = await response.json();
        return {
            valid: result.valid,
            errors: result.errors || [],
            requestId: requestId
        };
    }

    /**
     * Get current validation request ID
     */
    static getValidationRequestId() {
        return this.validationRequestId;
    }

    /**
     * Create a new node
     */
    static async addNode(config) {
        const response = await fetch('/td-api/nodes/add-node', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to create node');
        }

        // Invalidate caches
        this.invalidateCache('available-ips');
        this.invalidateCache('existing-nodes');
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Create a cluster of nodes
     */
    static async addCluster(config) {
        const response = await fetch('/td-api/nodes/add-cluster', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to create cluster');
        }

        // Invalidate caches
        this.invalidateCache('available-ips');
        this.invalidateCache('existing-nodes');
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Save running config to startup config
     */
    static async saveConfig(device, ip) {
        const response = await fetch('/td-api/nodes/save-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ device, ip })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to save config');
        }
        return result;
    }

    /**
     * Reboot multiple devices
     */
    static async rebootDevices(devices) {
        const response = await fetch('/td-api/nodes/reboot-devices', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ devices })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to reboot devices');
        }
        return result;
    }

    /**
     * Configure impairments on a bridge
     */
    static async configureImpairments(bridgeName, impairments) {
        const response = await fetch('/td-api/impairments/configure', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                bridge: bridgeName,
                latency_ms: impairments.latency_ms || 0,
                loss_percent: impairments.loss_percent || 0,
                duplication_percent: impairments.duplication_percent || 0,
                corruption_percent: impairments.corruption_percent || 0
            })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to configure impairments');
        }
        return result;
    }

    /**
     * Check user nodes status
     */
    static async getUserNodesStatus() {
        const response = await fetch('/td-api/nodes/user-nodes-status');
        if (!response.ok) {
            console.warn('[NodeBuilderAPI] Failed to check user nodes status');
            return { has_user_nodes: false, needs_restore: false };
        }
        return await response.json();
    }

    /**
     * Restore user nodes
     */
    static async restoreUserNodes() {
        const response = await fetch('/td-api/nodes/restore-user-nodes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        });

        const result = await response.json();
        if (result.error) {
            throw new Error(result.error);
        }
        return result;
    }

    /**
     * Delete a user-added node
     */
    static async deleteNode(name) {
        const response = await fetch('/td-api/nodes/delete-node', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete node');
        }

        // Invalidate caches
        this.invalidateCache();

        return result;
    }

    /**
     * Edit node connections
     */
    static async editNode(config) {
        const response = await fetch('/td-api/nodes/edit-node', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to edit node');
        }

        // Invalidate caches
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Get node connections
     */
    static async getNodeConnections(nodeName) {
        const response = await fetch(`/td-api/nodes/node-connections/${encodeURIComponent(nodeName)}`);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to fetch connections');
        }
        const data = await response.json();
        return data.connections || [];
    }

    /**
     * Load all data needed for the add node wizard
     */
    static async loadWizardData() {
        const [availableIps, targetDevices, existingNodes] = await Promise.all([
            this.getAvailableIps(),
            this.getTargetDevices(),
            this.getExistingNodes()
        ]);

        return { availableIps, targetDevices, existingNodes };
    }

    /**
     * Load all data needed for the add cluster wizard
     */
    static async loadClusterData() {
        const [templates, targetDevices, availableIps] = await Promise.all([
            this.getClusterTemplates(),
            this.getTargetDevices(),
            this.getAvailableIps()
        ]);

        return { templates, targetDevices, availableIps };
    }
}

// Export for use in other modules
window.NodeBuilderAPI = NodeBuilderAPI;

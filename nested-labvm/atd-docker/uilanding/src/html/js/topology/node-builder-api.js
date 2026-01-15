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

    // Retry configuration
    static DEFAULT_TIMEOUT_MS = 15000; // 15 second timeout per request
    static MAX_RETRIES = 3;
    static INITIAL_RETRY_DELAY_MS = 500;

    /**
     * Fetch with timeout support
     * Wraps fetch with an AbortController timeout
     */
    static async fetchWithTimeout(url, options = {}, timeoutMs = this.DEFAULT_TIMEOUT_MS) {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            return response;
        } catch (error) {
            if (error.name === 'AbortError') {
                throw new Error(`Request to ${url} timed out after ${timeoutMs}ms`);
            }
            throw error;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    /**
     * Fetch with retry and exponential backoff
     * Retries failed requests with increasing delays
     */
    static async fetchWithRetry(url, options = {}, maxRetries = this.MAX_RETRIES) {
        let lastError;
        let delay = this.INITIAL_RETRY_DELAY_MS;

        for (let attempt = 1; attempt <= maxRetries; attempt++) {
            try {
                const response = await this.fetchWithTimeout(url, options);
                return response;
            } catch (error) {
                lastError = error;
                console.warn(`[NodeBuilderAPI] Attempt ${attempt}/${maxRetries} failed for ${url}:`, error.message);

                if (attempt < maxRetries) {
                    // Wait before retrying with exponential backoff
                    await new Promise(resolve => setTimeout(resolve, delay));
                    delay *= 2; // Double the delay for next retry
                }
            }
        }

        throw new Error(`Failed after ${maxRetries} attempts: ${lastError.message}`);
    }

    /**
     * Fetch with caching support
     * Now uses fetchWithRetry for reliability
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
            const response = await this.fetchWithRetry('/td-api/nodes/available-ips');
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
            const response = await this.fetchWithRetry('/td-api/nodes/target-devices');
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
            const response = await this.fetchWithRetry('/td-api/nodes/existing-nodes');
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
            const response = await this.fetchWithRetry('/td-api/nodes/cluster-templates');
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
     * Returns requestId and validatedName for race condition prevention -
     * caller should compare both with current expected values
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
            requestId: requestId,
            validatedName: name  // Include the name that was validated for race condition check
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
        try {
            const response = await this.fetchWithRetry('/td-api/nodes/user-nodes-status');
            if (!response.ok) {
                console.warn('[NodeBuilderAPI] Failed to check user nodes status');
                return { has_user_nodes: false, needs_restore: false };
            }
            return await response.json();
        } catch (error) {
            console.warn('[NodeBuilderAPI] Failed to check user nodes status:', error.message);
            return { has_user_nodes: false, needs_restore: false };
        }
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

    // =========================================
    // Linux Host API Methods
    // =========================================

    /**
     * Get host status (count and availability)
     */
    static async getHostStatus() {
        const response = await this.fetchWithRetry('/td-api/nodes/host-status');
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to fetch host status');
        }
        return await response.json();
    }

    /**
     * Create a new Linux host
     */
    static async addHost(config) {
        const response = await fetch('/td-api/nodes/add-host', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to create host');
        }

        // Invalidate caches
        this.invalidateCache('available-ips');
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Delete a Linux host
     */
    static async deleteHost(name) {
        const response = await fetch('/td-api/nodes/delete-host', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete host');
        }

        // Invalidate caches
        this.invalidateCache();

        return result;
    }

    /**
     * Get noVNC token for a host
     */
    static async getNoVncToken(hostname) {
        const response = await fetch(`/td-api/nodes/novnc-token/${encodeURIComponent(hostname)}`);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to get VNC token');
        }
        return await response.json();
    }

    /**
     * Load data for add host wizard
     */
    static async loadHostWizardData() {
        const [hostStatus, availableIps, targetDevices] = await Promise.all([
            this.getHostStatus(),
            this.getAvailableIps(),
            this.getTargetDevices()
        ]);

        return { hostStatus, availableIps, targetDevices };
    }

    // =========================================
    // VyOS Firewall API Methods
    // =========================================

    /**
     * Get firewall status (count and availability)
     */
    static async getFirewallStatus() {
        const response = await this.fetchWithRetry('/td-api/nodes/firewall-status');
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to fetch firewall status');
        }
        return await response.json();
    }

    /**
     * Create a new VyOS firewall
     */
    static async addFirewall(config) {
        const response = await fetch('/td-api/nodes/add-firewall', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to create firewall');
        }

        // Invalidate caches
        this.invalidateCache('available-ips');
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Edit firewall interface IPs
     */
    static async editFirewall(config) {
        const response = await fetch('/td-api/nodes/edit-firewall', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to edit firewall');
        }

        return result;
    }

    /**
     * Delete a VyOS firewall
     */
    static async deleteFirewall(name) {
        const response = await fetch('/td-api/nodes/delete-firewall', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete firewall');
        }

        // Invalidate caches
        this.invalidateCache();

        return result;
    }

    /**
     * Load data for add firewall wizard
     */
    static async loadFirewallWizardData() {
        const [firewallStatus, availableIps, targetDevices] = await Promise.all([
            this.getFirewallStatus(),
            this.getAvailableIps(),
            this.getTargetDevices()
        ]);

        return { firewallStatus, availableIps, targetDevices };
    }

    // =========================================
    // VeloCloud SD-WAN API Methods
    // =========================================

    /**
     * Get VeloCloud device status (count and availability)
     */
    static async getVeloStatus() {
        const response = await this.fetchWithRetry('/td-api/nodes/velo-status');
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to fetch VeloCloud status');
        }
        return await response.json();
    }

    /**
     * Get list of VeloCloud devices
     */
    static async getVeloDevices(deviceType = null) {
        const url = deviceType
            ? `/td-api/nodes/velo-devices?device_type=${encodeURIComponent(deviceType)}`
            : '/td-api/nodes/velo-devices';
        const response = await this.fetchWithRetry(url);
        if (!response.ok) {
            const error = await response.json().catch(() => ({}));
            throw new Error(error.error || 'Failed to fetch VeloCloud devices');
        }
        return await response.json();
    }

    /**
     * Create a new VeloCloud device
     */
    static async addVeloDevice(config) {
        const response = await fetch('/td-api/nodes/add-velo-device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to create VeloCloud device');
        }

        // Invalidate caches
        this.invalidateCache('available-ips');
        this.invalidateCache('target-devices');

        return result;
    }

    /**
     * Delete a VeloCloud device
     */
    static async deleteVeloDevice(name) {
        const response = await fetch('/td-api/nodes/delete-velo-device', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name })
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to delete VeloCloud device');
        }

        // Invalidate caches
        this.invalidateCache();

        return result;
    }

    /**
     * Load data for add VeloCloud wizard
     */
    static async loadVeloWizardData() {
        const [veloStatus, availableIps, targetDevices] = await Promise.all([
            this.getVeloStatus(),
            this.getAvailableIps(),
            this.getTargetDevices()
        ]);

        return { veloStatus, availableIps, targetDevices };
    }

    // =========================================
    // Reset Operations
    // =========================================

    /**
     * Reset all user-added nodes (vEOS, hosts, firewalls)
     * This removes all user customizations and restores the original topology.
     */
    static async resetAllUserNodes() {
        const response = await fetch('/td-api/nodes/reset-all-user-nodes', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: '{}'
        });

        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || 'Failed to reset user nodes');
        }

        // Invalidate all caches
        this.invalidateCache();

        return result;
    }
}

// Export for use in other modules
window.NodeBuilderAPI = NodeBuilderAPI;

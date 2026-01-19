/**
 * Feature Flags Client for ATD Frontend
 *
 * Usage:
 *   const ff = new FeatureFlags();
 *   await ff.load();
 *
 *   if (ff.isEnabled('feature-dark-mode')) {
 *     enableDarkMode();
 *   }
 *
 *   // Or with async/await pattern
 *   if (await ff.check('feature-dark-mode')) {
 *     enableDarkMode();
 *   }
 */

class FeatureFlags {
    constructor() {
        this.features = null;
        this.loaded = false;
        this._loadPromise = null;
    }

    /**
     * Load feature flags from the server.
     * Safe to call multiple times - will only fetch once.
     * @returns {Promise<FeatureFlags>} This instance for chaining
     */
    async load() {
        if (this.loaded) return this;

        // If already loading, wait for that request
        if (this._loadPromise) {
            await this._loadPromise;
            return this;
        }

        this._loadPromise = this._fetchFeatures();
        await this._loadPromise;
        return this;
    }

    async _fetchFeatures() {
        try {
            const response = await fetch('/feature-flags');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }
            this.features = await response.json();
            this.loaded = true;
        } catch (error) {
            console.warn('Failed to load feature flags:', error);
            this.features = { enabled_features: [], source: 'error' };
            this.loaded = true;
        }
    }

    /**
     * Check if a feature is enabled (synchronous - requires load() first)
     * @param {string} featureId - The feature identifier
     * @returns {boolean} True if enabled
     */
    isEnabled(featureId) {
        if (!this.features) return false;
        return this.features.enabled_features.includes(featureId);
    }

    /**
     * Check if a feature is enabled (async - auto-loads if needed)
     * @param {string} featureId - The feature identifier
     * @returns {Promise<boolean>} True if enabled
     */
    async check(featureId) {
        await this.load();
        return this.isEnabled(featureId);
    }

    /**
     * Get all feature data
     * @returns {Object} Full feature state object
     */
    getAll() {
        return this.features || { enabled_features: [] };
    }

    /**
     * Get list of enabled feature IDs
     * @returns {string[]} Array of enabled feature IDs
     */
    getEnabled() {
        if (!this.features) return [];
        return this.features.enabled_features || [];
    }

    /**
     * Filter a list of feature IDs to only those enabled
     * @param {string[]} featureIds - Array of feature IDs to check
     * @returns {string[]} Array of enabled feature IDs from input
     */
    filterEnabled(featureIds) {
        if (!this.features) return [];
        const enabledSet = new Set(this.features.enabled_features);
        return featureIds.filter(id => enabledSet.has(id));
    }

    /**
     * Get the current topology
     * @returns {string} Topology identifier
     */
    getTopology() {
        return this.features?.topology || 'unknown';
    }

    /**
     * Get the data source indicator
     * @returns {string} Source: 'firestore', 'cache', 'empty_fallback', or 'error'
     */
    getSource() {
        return this.features?.source || 'unknown';
    }

    /**
     * Clear cache and force reload on next access
     */
    clearCache() {
        this.features = null;
        this.loaded = false;
        this._loadPromise = null;
    }
}

// Global instance - auto-loads when accessed
window.featureFlags = new FeatureFlags();

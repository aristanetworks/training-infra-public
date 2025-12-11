/**
 * Status Updater for ATL Interactive Topology Diagram
 * Handles WebSocket integration for real-time device status updates
 */

export class StatusUpdater {
    constructor(cy, wsUrl) {
        this.cy = cy;
        this.wsUrl = wsUrl;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 2000;
        this.statusCallbacks = [];

        // Device status polling
        this.statusPollInterval = null;
        this.statusPollDelay = 30000; // 30 seconds
        this.isPolling = false;

        // Build case-insensitive node lookup map (lowercase key -> actual node ID)
        this.nodeLookupMap = this.buildNodeLookupMap();
    }

    /**
     * Build a case-insensitive lookup map for node IDs
     * Maps lowercase node ID to actual node ID in Cytoscape
     */
    buildNodeLookupMap() {
        const map = {};
        this.cy.nodes().forEach(node => {
            const actualId = node.id();
            map[actualId.toLowerCase()] = actualId;
        });
        return map;
    }

    /**
     * Find node by ID using case-insensitive matching
     * Returns { node, effectiveNodeId } or { node: empty, effectiveNodeId: null }
     */
    findNodeCaseInsensitive(nodeId) {
        // Try exact match first
        let node = this.cy.$id(nodeId);
        if (!node.empty()) {
            return { node, effectiveNodeId: nodeId };
        }

        // Use lookup map for case-insensitive match
        const lookupKey = nodeId.toLowerCase();
        const actualId = this.nodeLookupMap[lookupKey];
        if (actualId) {
            node = this.cy.$id(actualId);
            if (!node.empty()) {
                return { node, effectiveNodeId: actualId };
            }
        }

        return { node: this.cy.$id('__nonexistent__'), effectiveNodeId: null };
    }

    /**
     * Refresh the node lookup map (call if topology changes)
     */
    refreshNodeLookupMap() {
        this.nodeLookupMap = this.buildNodeLookupMap();
    }

    /**
     * Connect to WebSocket
     */
    connect() {
        if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
            return;
        }

        try {
            this.ws = new WebSocket(this.wsUrl);

            this.ws.onopen = () => {
                console.log('[TopologyStatus] WebSocket connected');
                this.reconnectAttempts = 0;

                // Send hello message to start receiving updates
                this.ws.send(JSON.stringify({
                    type: 'hello',
                    data: {}
                }));
            };

            this.ws.onmessage = (event) => {
                this.handleMessage(event);
            };

            this.ws.onclose = (event) => {
                console.log('[TopologyStatus] WebSocket closed', event.code, event.reason);
                this.scheduleReconnect();
            };

            this.ws.onerror = (error) => {
                console.error('[TopologyStatus] WebSocket error', error);
            };

        } catch (error) {
            console.error('[TopologyStatus] Failed to create WebSocket', error);
            this.scheduleReconnect();
        }
    }

    /**
     * Schedule reconnection attempt
     */
    scheduleReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            console.warn('[TopologyStatus] Max reconnect attempts reached');
            return;
        }

        this.reconnectAttempts++;
        const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);

        console.log(`[TopologyStatus] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

        setTimeout(() => {
            this.connect();
        }, delay);
    }

    /**
     * Handle incoming WebSocket message
     */
    handleMessage(event) {
        try {
            const msg = JSON.parse(event.data);

            if (msg.type === 'status') {
                this.processStatusUpdate(msg.data);
            }
        } catch (error) {
            console.error('[TopologyStatus] Error parsing message', error);
        }
    }

    /**
     * Process status update from WebSocket
     */
    processStatusUpdate(data) {
        // Handle CVP status
        if (data.cvp) {
            const cvpStatus = data.cvp.status === 'UP' ? 'up' : 'down';
            // If there's a CVP node, update its status (case-insensitive)
            const { node: cvpNode, effectiveNodeId } = this.findNodeCaseInsensitive('CVP');
            if (!cvpNode.empty() && effectiveNodeId) {
                this.updateNodeStatus(effectiveNodeId, cvpStatus);
            }
        }

        // Handle per-device status if available
        if (data.devices) {
            Object.entries(data.devices).forEach(([nodeId, status]) => {
                // Use case-insensitive lookup
                const { effectiveNodeId } = this.findNodeCaseInsensitive(nodeId);
                if (effectiveNodeId) {
                    this.updateNodeStatus(effectiveNodeId, status.status || 'unknown');
                }
            });
        }

        // Notify callbacks
        this.statusCallbacks.forEach(callback => {
            try {
                callback(data);
            } catch (error) {
                console.error('[TopologyStatus] Callback error', error);
            }
        });
    }

    /**
     * Update a node's status (expects the correct/resolved node ID)
     */
    updateNodeStatus(nodeId, status) {
        const node = this.cy.$id(nodeId);

        if (node.empty()) {
            return;
        }

        // Update node data
        node.data('status', status);

        // Update CSS classes - remove all status classes first
        node.removeClass('status-up status-down status-init status-unknown status-error');
        node.addClass(`status-${status}`);

        // Update classes string - preserve device type and add status
        const deviceType = node.data('device_type');
        node.classes(`device-type-${deviceType} status-${status}`);
    }

    /**
     * Simulate device status (for testing/demo)
     * In production, this would come from CVP or device monitoring
     */
    simulateStatus() {
        const statuses = ['up', 'up', 'up', 'up', 'down', 'init'];

        this.cy.nodes().forEach(node => {
            const randomStatus = statuses[Math.floor(Math.random() * statuses.length)];
            this.updateNodeStatus(node.id(), randomStatus);
        });
    }

    /**
     * Set all nodes to a specific status
     */
    setAllStatus(status) {
        this.cy.nodes().forEach(node => {
            this.updateNodeStatus(node.id(), status);
        });
    }

    /**
     * Register callback for status updates
     */
    onStatusUpdate(callback) {
        this.statusCallbacks.push(callback);
    }

    /**
     * Remove status update callback
     */
    offStatusUpdate(callback) {
        const index = this.statusCallbacks.indexOf(callback);
        if (index > -1) {
            this.statusCallbacks.splice(index, 1);
        }
    }

    /**
     * Get connection state
     */
    isConnected() {
        return this.ws && this.ws.readyState === WebSocket.OPEN;
    }

    /**
     * Get current status for a node (case-insensitive lookup)
     */
    getNodeStatus(nodeId) {
        const { node } = this.findNodeCaseInsensitive(nodeId);
        if (node.empty()) {
            return null;
        }
        return node.data('status');
    }

    /**
     * Get status summary
     */
    getStatusSummary() {
        const summary = {
            up: 0,
            down: 0,
            init: 0,
            unknown: 0,
            total: 0
        };

        this.cy.nodes().forEach(node => {
            const status = node.data('status') || 'unknown';
            summary[status] = (summary[status] || 0) + 1;
            summary.total++;
        });

        return summary;
    }

    /**
     * Disconnect WebSocket
     */
    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }

    /**
     * Start polling device status via eAPI
     */
    startStatusPolling() {
        if (this.isPolling) {
            return;
        }

        this.isPolling = true;
        console.log('[TopologyStatus] Starting device status polling');

        // Initial check
        this.pollDeviceStatus();

        // Set up interval
        this.statusPollInterval = setInterval(() => {
            this.pollDeviceStatus();
        }, this.statusPollDelay);
    }

    /**
     * Stop polling device status
     */
    stopStatusPolling() {
        if (this.statusPollInterval) {
            clearInterval(this.statusPollInterval);
            this.statusPollInterval = null;
        }
        this.isPolling = false;
        console.log('[TopologyStatus] Stopped device status polling');
    }

    /**
     * Poll all device statuses from API
     */
    async pollDeviceStatus() {
        try {
            const response = await fetch('/td-api/device-status');

            if (!response.ok) {
                console.error('[TopologyStatus] Failed to fetch device status:', response.status);
                return;
            }

            const data = await response.json();

            if (data.devices) {
                let matchedCount = 0;
                let unmatchedDevices = [];

                Object.entries(data.devices).forEach(([nodeId, deviceStatus]) => {
                    // Use case-insensitive lookup to handle any naming variations
                    const { node, effectiveNodeId } = this.findNodeCaseInsensitive(nodeId);

                    if (node.empty() || !effectiveNodeId) {
                        unmatchedDevices.push(nodeId);
                    } else {
                        matchedCount++;
                        this.updateNodeStatus(effectiveNodeId, deviceStatus.status || 'unknown');

                        // Also update the node data with version info if available
                        if (deviceStatus.version) {
                            node.data('version', deviceStatus.version);
                        }
                    }
                });

                // Log any mismatches for debugging
                if (unmatchedDevices.length > 0) {
                    console.warn('[TopologyStatus] Devices not found in topology:', unmatchedDevices);
                    console.log('[TopologyStatus] Available node IDs:', this.cy.nodes().map(n => n.id()));
                }

                // Notify callbacks
                this.statusCallbacks.forEach(callback => {
                    try {
                        callback({ devices: data.devices });
                    } catch (error) {
                        console.error('[TopologyStatus] Callback error', error);
                    }
                });

                console.log('[TopologyStatus] Updated status for', matchedCount, 'of', Object.keys(data.devices).length, 'devices');
            }
        } catch (error) {
            console.error('[TopologyStatus] Error polling device status:', error);
        }
    }

    /**
     * Check status of a single device
     */
    async checkSingleDevice(nodeId) {
        try {
            const response = await fetch(`/td-api/device-status?device=${encodeURIComponent(nodeId)}`);

            if (!response.ok) {
                return null;
            }

            const data = await response.json();
            // Use case-insensitive lookup to find the correct node ID
            const { effectiveNodeId } = this.findNodeCaseInsensitive(nodeId);
            if (effectiveNodeId) {
                this.updateNodeStatus(effectiveNodeId, data.status || 'unknown');
            }

            return data;
        } catch (error) {
            console.error('[TopologyStatus] Error checking device:', nodeId, error);
            return null;
        }
    }

    /**
     * Destroy status updater
     */
    destroy() {
        this.stopStatusPolling();
        this.disconnect();
        this.statusCallbacks = [];
    }
}

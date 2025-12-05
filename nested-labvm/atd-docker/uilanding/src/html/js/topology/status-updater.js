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
            // If there's a CVP node, update its status
            const cvpNode = this.cy.$id('CVP');
            if (!cvpNode.empty()) {
                this.updateNodeStatus('CVP', cvpStatus);
            }
        }

        // Handle per-device status if available
        if (data.devices) {
            Object.entries(data.devices).forEach(([nodeId, status]) => {
                this.updateNodeStatus(nodeId, status.status || 'unknown');
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
     * Update a node's status
     */
    updateNodeStatus(nodeId, status) {
        const node = this.cy.$id(nodeId);

        if (node.empty()) {
            return;
        }

        // Update node data
        node.data('status', status);

        // Update CSS classes
        node.removeClass('status-up status-down status-init status-unknown');
        node.addClass(`status-${status}`);

        // Update classes string
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
     * Get current status for a node
     */
    getNodeStatus(nodeId) {
        const node = this.cy.$id(nodeId);
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
     * Destroy status updater
     */
    destroy() {
        this.disconnect();
        this.statusCallbacks = [];
    }
}

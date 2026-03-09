/**
 * Viewer Status Updater - WebSocket status with graceful degradation
 * If /td-ws is unavailable, status indicators are hidden silently
 */

export class ViewerStatusUpdater {
    constructor(cy, container) {
        this.cy = cy;
        this.container = container;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 3000;
        this.statusIndicator = null;

        this.createStatusIndicator();
        this.connect();
    }

    createStatusIndicator() {
        this.statusIndicator = document.createElement('div');
        this.statusIndicator.className = 'atl-topology-status';
        this.statusIndicator.textContent = '';
        this.container.appendChild(this.statusIndicator);
    }

    connect() {
        try {
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/td-ws`;

            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                this.reconnectAttempts = 0;
                this.statusIndicator.classList.add('connected');
                this.requestStatus();
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    this.handleStatusUpdate(data);
                } catch {
                    // Ignore malformed messages
                }
            };

            this.ws.onclose = () => {
                this.statusIndicator.classList.remove('connected');
                this.attemptReconnect();
            };

            this.ws.onerror = () => {
                // Graceful degradation - don't show errors
                this.statusIndicator.textContent = '';
            };
        } catch {
            // WebSocket not available - silent degradation
        }
    }

    attemptReconnect() {
        if (this.reconnectAttempts >= this.maxReconnectAttempts) {
            // Give up silently - status just won't be shown
            return;
        }

        this.reconnectAttempts++;
        setTimeout(() => this.connect(), this.reconnectDelay);
    }

    requestStatus() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try {
                this.ws.send(JSON.stringify({ type: 'status_request' }));
            } catch {
                // Ignore send errors
            }
        }
    }

    handleStatusUpdate(data) {
        if (!data || !data.devices) return;

        // Build case-insensitive node lookup
        const nodeLookup = {};
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) {
                nodeLookup[node.id().toLowerCase()] = node;
            }
        });

        for (const [deviceName, status] of Object.entries(data.devices)) {
            const node = nodeLookup[deviceName.toLowerCase()];
            if (!node) continue;

            // Remove previous status classes
            node.removeClass('status-up status-down status-error status-unknown');

            // Apply new status
            const statusStr = (status || '').toLowerCase();
            if (statusStr === 'up' || statusStr === 'reachable') {
                node.addClass('status-up');
            } else if (statusStr === 'down' || statusStr === 'unreachable') {
                node.addClass('status-down');
            } else if (statusStr === 'error') {
                node.addClass('status-error');
            } else {
                node.addClass('status-unknown');
            }
        }
    }

    disconnect() {
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}

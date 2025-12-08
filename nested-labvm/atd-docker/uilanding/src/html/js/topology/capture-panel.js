/**
 * Packet Capture Panel for ATL Interactive Topology
 *
 * Provides real-time packet capture and display via WebSocket.
 * Wireshark-inspired UI with packet list and detail views.
 */

export class CapturePanel {
    constructor(options = {}) {
        this.container = null;
        this.ws = null;
        this.isCapturing = false;
        this.currentBridge = null;
        this.sessionId = null;
        this.packets = [];
        this.selectedPacketIndex = -1;
        this.maxPackets = options.maxPackets || 5000;  // Limit for browser memory
        this.onEdgeHighlight = options.onEdgeHighlight || null;  // Callback to highlight edge

        // DOM elements
        this.elements = {};

        // WebSocket reconnection
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 3;
        this.reconnectDelay = 1000;

        // Store bridges data for searching
        this.bridges = [];

        // Bind methods
        this.handlePacket = this.handlePacket.bind(this);
        this.handleWebSocketMessage = this.handleWebSocketMessage.bind(this);
    }

    /**
     * Initialize the capture panel
     */
    init() {
        this.createPanel();
        this.attachEventListeners();
        this.loadBridges();
    }

    /**
     * Create the capture panel DOM structure
     */
    createPanel() {
        // Create main container
        this.container = document.createElement('div');
        this.container.id = 'capture-panel';
        this.container.className = 'capture-panel';

        this.container.innerHTML = `
            <div class="capture-header">
                <div class="capture-title">
                    <span>Packet Capture</span>
                    <span class="capture-link-info" id="capture-link-info"></span>
                </div>

                <div class="capture-bridge-selector">
                    <select id="capture-bridge-select" class="capture-bridge-select">
                        <option value="">Select link...</option>
                    </select>
                </div>

                <div class="capture-controls">
                    <button id="capture-start-btn" class="capture-btn start" disabled>
                        <span>▶</span> Start
                    </button>
                    <button id="capture-stop-btn" class="capture-btn stop" disabled>
                        <span>■</span> Stop
                    </button>
                    <button id="capture-clear-btn" class="capture-btn">
                        Clear
                    </button>
                    <button id="capture-layout-btn" class="capture-btn" title="Toggle side-by-side layout">
                        Side Panel
                    </button>
                </div>

                <div class="capture-status" id="capture-status">
                    <span class="capture-status-dot"></span>
                    <span>Idle</span>
                    <span class="capture-packet-count" id="capture-packet-count">0 packets</span>
                </div>

                <div class="capture-window-controls">
                    <button class="capture-window-btn" id="capture-minimize-btn" title="Minimize">−</button>
                    <button class="capture-window-btn" id="capture-expand-btn" title="Expand">□</button>
                    <button class="capture-window-btn" id="capture-close-btn" title="Close">×</button>
                </div>
            </div>

            <div class="capture-filter-bar">
                <input type="text"
                       id="capture-filter-input"
                       class="capture-filter-input"
                       placeholder="Display filter (e.g., lldp, bgp, ospf, arp)">
                <span class="capture-filter-hint">
                    Examples: <code>lldp</code> <code>bgp</code> <code>ospf</code> <code>arp</code>
                    <code>vxlan</code> <code>icmp</code> <code>tcp.port == 179</code>
                </span>
            </div>

            <div class="capture-body" id="capture-body">
                <div class="capture-packet-list" id="capture-packet-list">
                    <div class="capture-packet-list-header">
                        <span class="packet-col packet-col-num">No.</span>
                        <span class="packet-col packet-col-time">Time</span>
                        <span class="packet-col packet-col-src">Source</span>
                        <span class="packet-col packet-col-dst">Destination</span>
                        <span class="packet-col packet-col-protocol">Protocol</span>
                        <span class="packet-col packet-col-length">Len</span>
                        <span class="packet-col packet-col-info">Info</span>
                    </div>
                    <div class="capture-packet-rows" id="capture-packet-rows">
                        <div class="capture-empty-state">
                            <span class="capture-empty-title">No packets captured</span>
                            <span class="capture-empty-subtitle">Select a link and click Start to begin capturing</span>
                        </div>
                    </div>
                </div>

                <div class="capture-detail-panel" id="capture-detail-panel">
                    <div class="capture-detail-empty">Select a packet to view details</div>
                </div>
            </div>
        `;

        document.body.appendChild(this.container);

        // Cache element references
        this.elements = {
            bridgeSelect: document.getElementById('capture-bridge-select'),
            startBtn: document.getElementById('capture-start-btn'),
            stopBtn: document.getElementById('capture-stop-btn'),
            clearBtn: document.getElementById('capture-clear-btn'),
            layoutBtn: document.getElementById('capture-layout-btn'),
            filterInput: document.getElementById('capture-filter-input'),
            status: document.getElementById('capture-status'),
            packetCount: document.getElementById('capture-packet-count'),
            linkInfo: document.getElementById('capture-link-info'),
            captureBody: document.getElementById('capture-body'),
            packetList: document.getElementById('capture-packet-list'),
            packetRows: document.getElementById('capture-packet-rows'),
            detailPanel: document.getElementById('capture-detail-panel'),
            minimizeBtn: document.getElementById('capture-minimize-btn'),
            expandBtn: document.getElementById('capture-expand-btn'),
            closeBtn: document.getElementById('capture-close-btn')
        };

        // Layout state
        this.sideBySideLayout = false;
    }

    /**
     * Attach event listeners
     */
    attachEventListeners() {
        // Bridge selection
        this.elements.bridgeSelect.addEventListener('change', (e) => {
            this.selectBridge(e.target.value);
        });

        // Control buttons
        this.elements.startBtn.addEventListener('click', () => this.startCapture());
        this.elements.stopBtn.addEventListener('click', () => this.stopCapture());
        this.elements.clearBtn.addEventListener('click', () => this.clearPackets());
        this.elements.layoutBtn.addEventListener('click', () => this.toggleLayout());

        // Filter input - apply on Enter
        this.elements.filterInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                // If capturing, restart with new filter
                if (this.isCapturing) {
                    this.stopCapture();
                    setTimeout(() => this.startCapture(), 100);
                }
            }
        });

        // Window controls
        this.elements.minimizeBtn.addEventListener('click', () => this.toggleMinimize());
        this.elements.expandBtn.addEventListener('click', () => this.toggleExpand());
        this.elements.closeBtn.addEventListener('click', () => this.hide());

        // Keyboard shortcuts
        document.addEventListener('keydown', (e) => {
            if (!this.container.classList.contains('visible')) return;

            if (e.key === 'Escape') {
                this.hide();
            }
        });
    }

    /**
     * Load available bridges from API
     */
    async loadBridges() {
        try {
            const response = await fetch('/td-api/capture/bridges');
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            this.bridges = data.bridges || [];
            this.populateBridgeSelector(this.bridges);
        } catch (error) {
            console.error('[CapturePanel] Failed to load bridges:', error);
            this.bridges = [];
            this.elements.bridgeSelect.innerHTML = '<option value="">Error loading bridges</option>';
        }
    }

    /**
     * Populate bridge selector dropdown
     */
    populateBridgeSelector(bridges) {
        const select = this.elements.bridgeSelect;
        select.innerHTML = '<option value="">Select link...</option>';

        if (!bridges || bridges.length === 0) {
            select.innerHTML += '<option value="" disabled>No bridges available</option>';
            return;
        }

        // Group bridges by readable description
        bridges.forEach(bridge => {
            const option = document.createElement('option');
            option.value = bridge.name;

            // Build descriptive label
            const srcDevice = bridge.source_device_name || bridge.source_device || '?';
            const srcPort = bridge.source_port_name || bridge.source_port || '?';
            const tgtDevice = bridge.target_device_name || bridge.target_device || '?';
            const tgtPort = bridge.target_port_name || bridge.target_port || '?';

            option.textContent = `${srcDevice}:${srcPort} ↔ ${tgtDevice}:${tgtPort}`;
            option.title = bridge.name;

            if (bridge.is_capturing) {
                option.textContent += ' (capturing)';
                option.disabled = true;
            }

            select.appendChild(option);
        });
    }

    /**
     * Select a bridge for capture
     */
    selectBridge(bridgeName) {
        if (!bridgeName) {
            this.currentBridge = null;
            this.elements.startBtn.disabled = true;
            this.elements.linkInfo.textContent = '';
            return;
        }

        this.currentBridge = bridgeName;
        this.elements.startBtn.disabled = false;

        // Update link info display
        const selectedOption = this.elements.bridgeSelect.selectedOptions[0];
        if (selectedOption) {
            this.elements.linkInfo.textContent = selectedOption.textContent;
        }

        // Notify for edge highlighting
        if (this.onEdgeHighlight) {
            this.onEdgeHighlight(bridgeName);
        }
    }

    /**
     * Find bridge by edge data (source/target device and port names)
     * @param {Object} edgeData - Edge data with source, target, source_port, target_port
     * @returns {string|null} Bridge name if found
     */
    findBridgeByEdge(edgeData) {
        if (!edgeData || !this.bridges.length) {
            return null;
        }

        const { source, target, source_port, target_port } = edgeData;

        // Normalize names for comparison (case-insensitive)
        const srcLower = (source || '').toLowerCase();
        const tgtLower = (target || '').toLowerCase();
        const srcPortLower = (source_port || '').toLowerCase();
        const tgtPortLower = (target_port || '').toLowerCase();

        for (const bridge of this.bridges) {
            const bSrcDevice = (bridge.source_device_name || '').toLowerCase();
            const bTgtDevice = (bridge.target_device_name || '').toLowerCase();
            const bSrcPort = (bridge.source_port_name || '').toLowerCase();
            const bTgtPort = (bridge.target_port_name || '').toLowerCase();

            // Check both directions (A-B or B-A)
            const matchForward = (
                bSrcDevice === srcLower &&
                bTgtDevice === tgtLower &&
                bSrcPort === srcPortLower &&
                bTgtPort === tgtPortLower
            );

            const matchReverse = (
                bSrcDevice === tgtLower &&
                bTgtDevice === srcLower &&
                bSrcPort === tgtPortLower &&
                bTgtPort === srcPortLower
            );

            if (matchForward || matchReverse) {
                console.log('[CapturePanel] Found bridge for edge:', bridge.name);
                return bridge.name;
            }
        }

        console.log('[CapturePanel] No bridge found for edge:', edgeData);
        return null;
    }

    /**
     * Show the capture panel
     * @param {string|Object} bridgeNameOrEdgeData - Bridge name string or edge data object
     */
    show(bridgeNameOrEdgeData = null) {
        this.container.classList.add('visible');
        this.container.classList.remove('minimized');

        if (bridgeNameOrEdgeData) {
            let bridgeName = bridgeNameOrEdgeData;

            // If passed edge data object, find matching bridge
            if (typeof bridgeNameOrEdgeData === 'object') {
                bridgeName = this.findBridgeByEdge(bridgeNameOrEdgeData);
            }

            if (bridgeName) {
                // Pre-select the bridge
                this.elements.bridgeSelect.value = bridgeName;
                this.selectBridge(bridgeName);
            }
        }
    }

    /**
     * Hide the capture panel
     */
    hide() {
        // Stop any active capture
        if (this.isCapturing) {
            this.stopCapture();
        }

        this.container.classList.remove('visible', 'minimized', 'expanded');

        // Disconnect WebSocket
        this.disconnectWebSocket();
    }

    /**
     * Toggle minimized state
     */
    toggleMinimize() {
        this.container.classList.toggle('minimized');
        this.container.classList.remove('expanded');
    }

    /**
     * Toggle expanded state
     */
    toggleExpand() {
        this.container.classList.toggle('expanded');
        this.container.classList.remove('minimized');
    }

    /**
     * Toggle side-by-side layout (detail panel to the right of packet list)
     */
    toggleLayout() {
        this.sideBySideLayout = !this.sideBySideLayout;
        this.elements.captureBody.classList.toggle('side-by-side', this.sideBySideLayout);
        this.elements.layoutBtn.classList.toggle('active', this.sideBySideLayout);
    }

    /**
     * Start packet capture
     */
    startCapture() {
        if (!this.currentBridge) {
            console.warn('[CapturePanel] No bridge selected');
            return;
        }

        if (this.isCapturing) {
            console.warn('[CapturePanel] Already capturing');
            return;
        }

        // Connect WebSocket and start
        this.connectWebSocket().then(() => {
            const filter = this.elements.filterInput.value.trim();

            this.ws.send(JSON.stringify({
                type: 'start',
                bridge: this.currentBridge,
                filter: filter
            }));

            this.updateUICapturing(true);
        }).catch(error => {
            console.error('[CapturePanel] WebSocket connection failed:', error);
            this.showError('Failed to connect for capture');
        });
    }

    /**
     * Stop packet capture
     */
    stopCapture() {
        if (!this.isCapturing) return;

        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type: 'stop' }));
        }

        this.updateUICapturing(false);
    }

    /**
     * Clear captured packets
     */
    clearPackets() {
        this.packets = [];
        this.selectedPacketIndex = -1;
        this.updatePacketCount();
        this.renderPackets();
        this.clearDetailPanel();
    }

    /**
     * Connect to capture WebSocket
     */
    connectWebSocket() {
        return new Promise((resolve, reject) => {
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                resolve();
                return;
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const wsUrl = `${protocol}//${window.location.host}/capture-ws`;

            console.log('[CapturePanel] Connecting to', wsUrl);
            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('[CapturePanel] WebSocket connected');
                this.reconnectAttempts = 0;
                resolve();
            };

            this.ws.onmessage = this.handleWebSocketMessage;

            this.ws.onerror = (error) => {
                console.error('[CapturePanel] WebSocket error:', error);
                reject(error);
            };

            this.ws.onclose = (event) => {
                console.log('[CapturePanel] WebSocket closed:', event.code, event.reason);

                if (this.isCapturing) {
                    this.updateUICapturing(false);

                    // Attempt reconnection
                    if (this.reconnectAttempts < this.maxReconnectAttempts) {
                        this.reconnectAttempts++;
                        console.log(`[CapturePanel] Reconnecting (${this.reconnectAttempts}/${this.maxReconnectAttempts})...`);
                        setTimeout(() => {
                            this.startCapture();
                        }, this.reconnectDelay * this.reconnectAttempts);
                    }
                }
            };
        });
    }

    /**
     * Disconnect WebSocket
     */
    disconnectWebSocket() {
        if (this.ws) {
            // Clear handlers before closing to prevent reconnection attempts
            this.ws.onclose = null;
            this.ws.onerror = null;
            this.ws.onmessage = null;
            this.ws.close();
            this.ws = null;
        }
    }

    /**
     * Handle WebSocket messages
     */
    handleWebSocketMessage(event) {
        try {
            const message = JSON.parse(event.data);

            switch (message.type) {
                case 'started':
                    this.sessionId = message.session_id;
                    console.log('[CapturePanel] Capture started:', message);
                    break;

                case 'packet':
                    this.handlePacket(message.data);
                    break;

                case 'stopped':
                    console.log('[CapturePanel] Capture stopped:', message);
                    this.updateUICapturing(false);
                    // Disconnect WebSocket after capture stops to ensure clean state
                    this.disconnectWebSocket();
                    break;

                case 'error':
                    console.error('[CapturePanel] Server error:', message.message);
                    this.showError(message.message);
                    this.updateUICapturing(false);
                    // Disconnect WebSocket on error to ensure clean state for next attempt
                    this.disconnectWebSocket();
                    break;

                case 'pong':
                    // Keepalive response
                    break;

                default:
                    console.log('[CapturePanel] Unknown message:', message);
            }
        } catch (error) {
            console.error('[CapturePanel] Failed to parse message:', error);
        }
    }

    /**
     * Handle incoming packet
     */
    handlePacket(packet) {
        // Limit packet buffer size
        if (this.packets.length >= this.maxPackets) {
            this.packets.shift();  // Remove oldest packet
        }

        this.packets.push(packet);
        this.updatePacketCount();

        // Append new packet row (more efficient than full re-render)
        this.appendPacketRow(packet, this.packets.length - 1);
    }

    /**
     * Render all packets (for initial display or after clear)
     */
    renderPackets() {
        const container = this.elements.packetRows;

        if (this.packets.length === 0) {
            container.innerHTML = `
                <div class="capture-empty-state">
                    <span class="capture-empty-title">No packets captured</span>
                    <span class="capture-empty-subtitle">Select a link and click Start to begin capturing</span>
                </div>
            `;
            return;
        }

        container.innerHTML = '';

        this.packets.forEach((packet, index) => {
            this.appendPacketRow(packet, index);
        });
    }

    /**
     * Append a single packet row to the list
     */
    appendPacketRow(packet, index) {
        const container = this.elements.packetRows;

        // Remove empty state if present
        const emptyState = container.querySelector('.capture-empty-state');
        if (emptyState) {
            emptyState.remove();
        }

        const row = document.createElement('div');
        row.className = `capture-packet-row protocol-${(packet.protocol || 'unknown').toLowerCase()}`;
        row.dataset.index = index;

        // Format source/destination display
        const source = packet.src_ip || packet.src_mac || 'N/A';
        const dest = packet.dst_ip || packet.dst_mac || 'N/A';

        // Format timestamp - show relative time or just time portion
        const timeDisplay = this.formatTime(packet.timestamp);

        row.innerHTML = `
            <span class="packet-col packet-col-num">${packet.number}</span>
            <span class="packet-col packet-col-time">${this.escapeHtml(timeDisplay)}</span>
            <span class="packet-col packet-col-src">${this.escapeHtml(source)}</span>
            <span class="packet-col packet-col-dst">${this.escapeHtml(dest)}</span>
            <span class="packet-col packet-col-protocol">${this.escapeHtml(packet.protocol || 'Unknown')}</span>
            <span class="packet-col packet-col-length">${packet.length || 0}</span>
            <span class="packet-col packet-col-info">${this.escapeHtml(packet.info || '')}</span>
        `;

        row.addEventListener('click', () => {
            this.selectPacket(index);
        });

        container.appendChild(row);

        // Auto-scroll to bottom if near bottom
        const list = this.elements.packetList;
        const isNearBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 100;
        if (isNearBottom) {
            list.scrollTop = list.scrollHeight;
        }
    }

    /**
     * Select a packet and show details
     */
    selectPacket(index) {
        // Update selection styling
        const rows = this.elements.packetRows.querySelectorAll('.capture-packet-row');
        rows.forEach((row, i) => {
            row.classList.toggle('selected', i === index);
        });

        this.selectedPacketIndex = index;
        const packet = this.packets[index];

        if (packet) {
            this.showPacketDetail(packet);
        }
    }

    /**
     * Protocol display names and ordering for detail panel
     */
    static PROTOCOL_DISPLAY = {
        'eth': { name: 'Ethernet II', order: 1 },
        'ip': { name: 'Internet Protocol Version 4', order: 2 },
        'ipv6': { name: 'Internet Protocol Version 6', order: 2 },
        'tcp': { name: 'Transmission Control Protocol', order: 3 },
        'udp': { name: 'User Datagram Protocol', order: 3 },
        'icmp': { name: 'Internet Control Message Protocol', order: 4 },
        'icmpv6': { name: 'Internet Control Message Protocol v6', order: 4 },
        'arp': { name: 'Address Resolution Protocol', order: 4 },
        'bgp': { name: 'Border Gateway Protocol', order: 5 },
        'ospf': { name: 'Open Shortest Path First', order: 5 },
        'isis': { name: 'Intermediate System to Intermediate System', order: 5 },
        'bfd': { name: 'Bidirectional Forwarding Detection', order: 5 },
        'lldp': { name: 'Link Layer Discovery Protocol', order: 5 },
        'lacp': { name: 'Link Aggregation Control Protocol', order: 5 },
        'stp': { name: 'Spanning Tree Protocol', order: 5 },
        'rstp': { name: 'Rapid Spanning Tree Protocol', order: 5 },
        'vxlan': { name: 'Virtual eXtensible LAN', order: 6 },
        'evpn': { name: 'Ethernet VPN', order: 6 },
        'mpls': { name: 'Multiprotocol Label Switching', order: 6 },
        'gre': { name: 'Generic Routing Encapsulation', order: 6 },
        'dhcp': { name: 'Dynamic Host Configuration Protocol', order: 7 },
        'dhcpv6': { name: 'DHCPv6', order: 7 },
        'dns': { name: 'Domain Name System', order: 7 },
        'ntp': { name: 'Network Time Protocol', order: 7 }
    };

    /**
     * Show packet detail in the detail panel
     */
    showPacketDetail(packet) {
        const panel = this.elements.detailPanel;

        let html = '<div class="packet-detail-tree">';

        // Frame info summary
        html += `
            <div class="packet-detail-section">
                <div class="packet-detail-header">
                    <span class="packet-detail-toggle">▼</span>
                    <span class="packet-detail-title">Frame ${packet.number}</span>
                    <span class="packet-detail-summary">${packet.length} bytes</span>
                </div>
                <div class="packet-detail-fields">
                    <div class="packet-detail-field">
                        <span class="packet-detail-field-name">Arrival Time:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(packet.timestamp || '')}</span>
                    </div>
                    <div class="packet-detail-field">
                        <span class="packet-detail-field-name">Frame Length:</span>
                        <span class="packet-detail-field-value">${packet.length} bytes</span>
                    </div>
                    <div class="packet-detail-field">
                        <span class="packet-detail-field-name">Protocol:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(packet.protocol || 'Unknown')}</span>
                    </div>
                    <div class="packet-detail-field">
                        <span class="packet-detail-field-name">Info:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(packet.info || '')}</span>
                    </div>
                </div>
            </div>
        `;

        // Render protocol layers from tshark data
        if (packet.layers && typeof packet.layers === 'object') {
            // Sort layers by protocol order
            const sortedLayers = Object.entries(packet.layers).sort((a, b) => {
                const orderA = (CapturePanel.PROTOCOL_DISPLAY[a[0]] || {}).order || 99;
                const orderB = (CapturePanel.PROTOCOL_DISPLAY[b[0]] || {}).order || 99;
                return orderA - orderB;
            });

            for (const [proto, fields] of sortedLayers) {
                const displayInfo = CapturePanel.PROTOCOL_DISPLAY[proto] || { name: proto.toUpperCase(), order: 99 };
                html += this.renderProtocolLayer(proto, displayInfo.name, fields, packet);
            }
        } else {
            // Fallback for legacy packet format without layers object
            html += this.renderLegacyPacketDetail(packet);
        }

        html += '</div>';
        panel.innerHTML = html;

        // Add collapse/expand handlers
        panel.querySelectorAll('.packet-detail-header').forEach(header => {
            header.addEventListener('click', () => {
                header.parentElement.classList.toggle('collapsed');
            });
        });
    }

    /**
     * Render a single protocol layer section
     */
    renderProtocolLayer(proto, displayName, fields, packet) {
        if (!fields || typeof fields !== 'object') {
            return '';
        }

        // Build summary based on protocol type
        let summary = '';
        switch (proto) {
            case 'eth':
                summary = `${packet.src_mac || ''} → ${packet.dst_mac || ''}`;
                break;
            case 'ip':
            case 'ipv6':
                summary = `${packet.src_ip || ''} → ${packet.dst_ip || ''}`;
                break;
            case 'tcp':
            case 'udp':
                summary = `${packet.src_port || ''} → ${packet.dst_port || ''}`;
                break;
            case 'bgp':
                summary = fields.type ? `${fields.type}` : '';
                break;
            case 'ospf':
                summary = fields.msg ? `${fields.msg}` : '';
                break;
            case 'vxlan':
                summary = fields.vni ? `VNI: ${fields.vni}` : '';
                break;
            case 'lldp':
                summary = fields.chassis_id ? `Chassis: ${fields.chassis_id}` : '';
                break;
        }

        let html = `
            <div class="packet-detail-section protocol-${proto}">
                <div class="packet-detail-header">
                    <span class="packet-detail-toggle">▼</span>
                    <span class="packet-detail-title">${this.escapeHtml(displayName)}</span>
                    ${summary ? `<span class="packet-detail-summary">${this.escapeHtml(summary)}</span>` : ''}
                </div>
                <div class="packet-detail-fields">
        `;

        // Render each field in the layer
        for (const [fieldName, fieldValue] of Object.entries(fields)) {
            // Skip complex nested objects for now (could be expanded in future)
            if (typeof fieldValue === 'object' && fieldValue !== null) {
                // Render nested object as indented fields
                html += this.renderNestedFields(fieldName, fieldValue);
            } else {
                // Format field name for display (convert underscores to spaces, capitalize)
                const displayFieldName = this.formatFieldName(fieldName);
                html += `
                    <div class="packet-detail-field">
                        <span class="packet-detail-field-name">${this.escapeHtml(displayFieldName)}:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(String(fieldValue))}</span>
                    </div>
                `;
            }
        }

        html += `
                </div>
            </div>
        `;

        return html;
    }

    /**
     * Render nested fields (for complex protocol data)
     */
    renderNestedFields(parentName, obj, depth = 1) {
        if (!obj || typeof obj !== 'object') return '';
        if (depth > 3) return ''; // Prevent infinite recursion

        let html = '';
        const indent = depth * 12; // pixels

        for (const [key, value] of Object.entries(obj)) {
            const displayName = this.formatFieldName(key);

            if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
                // Recurse for nested objects
                html += `
                    <div class="packet-detail-field nested" style="padding-left: ${indent}px;">
                        <span class="packet-detail-field-name">${this.escapeHtml(displayName)}:</span>
                    </div>
                `;
                html += this.renderNestedFields(key, value, depth + 1);
            } else if (Array.isArray(value)) {
                // Render array as comma-separated values
                html += `
                    <div class="packet-detail-field nested" style="padding-left: ${indent}px;">
                        <span class="packet-detail-field-name">${this.escapeHtml(displayName)}:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(value.join(', '))}</span>
                    </div>
                `;
            } else {
                html += `
                    <div class="packet-detail-field nested" style="padding-left: ${indent}px;">
                        <span class="packet-detail-field-name">${this.escapeHtml(displayName)}:</span>
                        <span class="packet-detail-field-value">${this.escapeHtml(String(value))}</span>
                    </div>
                `;
            }
        }

        return html;
    }

    /**
     * Format field name for display
     * Converts snake_case to Title Case with spaces
     */
    formatFieldName(name) {
        if (!name) return '';
        return name
            .replace(/_/g, ' ')
            .replace(/\b\w/g, c => c.toUpperCase());
    }

    /**
     * Render legacy packet detail (fallback for packets without layers object)
     */
    renderLegacyPacketDetail(packet) {
        let html = '';

        // Ethernet section
        if (packet.src_mac || packet.dst_mac) {
            html += `
                <div class="packet-detail-section">
                    <div class="packet-detail-header">
                        <span class="packet-detail-toggle">▼</span>
                        <span class="packet-detail-title">Ethernet II</span>
                        <span class="packet-detail-summary">${packet.src_mac} → ${packet.dst_mac}</span>
                    </div>
                    <div class="packet-detail-fields">
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Source:</span>
                            <span class="packet-detail-field-value">${packet.src_mac}</span>
                        </div>
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Destination:</span>
                            <span class="packet-detail-field-value">${packet.dst_mac}</span>
                        </div>
                    </div>
                </div>
            `;
        }

        // IP section
        if (packet.src_ip || packet.dst_ip) {
            html += `
                <div class="packet-detail-section">
                    <div class="packet-detail-header">
                        <span class="packet-detail-toggle">▼</span>
                        <span class="packet-detail-title">Internet Protocol</span>
                        <span class="packet-detail-summary">${packet.src_ip} → ${packet.dst_ip}</span>
                    </div>
                    <div class="packet-detail-fields">
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Source:</span>
                            <span class="packet-detail-field-value">${packet.src_ip}</span>
                        </div>
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Destination:</span>
                            <span class="packet-detail-field-value">${packet.dst_ip}</span>
                        </div>
                    </div>
                </div>
            `;
        }

        // Transport/Protocol section
        if (packet.protocol && !['IPv4', 'IPv6', 'Ethernet'].includes(packet.protocol)) {
            html += `
                <div class="packet-detail-section">
                    <div class="packet-detail-header">
                        <span class="packet-detail-toggle">▼</span>
                        <span class="packet-detail-title">${packet.protocol}</span>
                        ${packet.src_port ? `<span class="packet-detail-summary">${packet.src_port} → ${packet.dst_port}</span>` : ''}
                    </div>
                    <div class="packet-detail-fields">
                        ${packet.src_port ? `
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Source Port:</span>
                            <span class="packet-detail-field-value">${packet.src_port}</span>
                        </div>
                        <div class="packet-detail-field">
                            <span class="packet-detail-field-name">Destination Port:</span>
                            <span class="packet-detail-field-value">${packet.dst_port}</span>
                        </div>
                        ` : ''}
                    </div>
                </div>
            `;
        }

        return html;
    }

    /**
     * Clear the detail panel
     */
    clearDetailPanel() {
        this.elements.detailPanel.innerHTML = '<div class="capture-detail-empty">Select a packet to view details</div>';
    }

    /**
     * Update UI for capturing state
     */
    updateUICapturing(capturing) {
        this.isCapturing = capturing;

        this.elements.startBtn.disabled = capturing || !this.currentBridge;
        this.elements.stopBtn.disabled = !capturing;
        this.elements.bridgeSelect.disabled = capturing;
        this.elements.filterInput.disabled = capturing;

        if (capturing) {
            this.elements.status.classList.add('capturing');
            this.elements.status.querySelector('span:nth-child(2)').textContent = 'Capturing';
            this.elements.startBtn.classList.add('active');
        } else {
            this.elements.status.classList.remove('capturing');
            this.elements.status.querySelector('span:nth-child(2)').textContent = 'Idle';
            this.elements.startBtn.classList.remove('active');
        }
    }

    /**
     * Update packet count display
     */
    updatePacketCount() {
        const count = this.packets.length;
        this.elements.packetCount.textContent = `${count} packet${count !== 1 ? 's' : ''}`;
    }

    /**
     * Format timestamp for display
     */
    formatTime(timestamp) {
        if (!timestamp) return '';

        // Extract just the time portion if full timestamp
        const match = timestamp.match(/(\d{2}:\d{2}:\d{2}\.\d+)/);
        if (match) {
            // Truncate to milliseconds
            return match[1].substring(0, 12);
        }
        return timestamp;
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Show error message to user
     */
    showError(message) {
        console.error('[CapturePanel] Error:', message);
        // Update status to show error briefly
        const statusText = this.elements.status.querySelector('span:nth-child(2)');
        if (statusText) {
            statusText.textContent = 'Error';
            statusText.style.color = '#e30909';
            // Show error in packet count area
            this.elements.packetCount.textContent = message;
            this.elements.packetCount.style.color = '#e30909';
            // Reset after 5 seconds
            setTimeout(() => {
                statusText.textContent = 'Idle';
                statusText.style.color = '';
                this.elements.packetCount.textContent = `${this.packets.length} packets`;
                this.elements.packetCount.style.color = '';
            }, 5000);
        }
    }

    /**
     * Destroy the panel
     */
    destroy() {
        this.disconnectWebSocket();
        if (this.container) {
            this.container.remove();
            this.container = null;
        }
    }
}

/**
 * Connectivity Monitor for UILanding
 * Monitors WebSocket and CVP gRPC connectivity
 * Provides visual status indicators to help users identify firewall/VPN blocking issues
 */

(function() {
    'use strict';

    // Configuration
    const CONNECTIVITY_CONFIG = {
        grpc: {
            timeout: 5000,          // 5s timeout for CVP checks
            retryInterval: 30000,   // 30s between checks
            maxFailures: 3          // Show warning after 3 failures
        },
        websocket: {
            stalePingThreshold: 60000,  // 60s without ping = stale
            maxFailures: 5               // Show warning after 5 failures
        }
    };

    // WebSocket connection status
    var wsConnectionStatus = {
        connected: null,  // null = unknown/initializing, true = connected, false = disconnected
        lastSuccessfulPing: null,
        failureCount: 0
    };

    // CVP gRPC connection status
    var grpcConnectionStatus = {
        connected: false,
        lastCheck: null,
        failureCount: 0,
        errorMessage: ''
    };

    // Connectivity messages for different scenarios
    const CONNECTIVITY_MESSAGES = {
        all_ok: "All systems operational. WebSocket and CVP connections are healthy.",
        ws_warning: "WebSocket connection unstable. Live updates may be delayed.",
        ws_critical: "WebSocket disconnected. Attempting to reconnect. Real-time updates unavailable.",
        grpc_warning: "CVP connectivity issue detected. Some lab features may be limited.",
        grpc_critical: "Unable to reach CVP. CVP-dependent features unavailable.",
        both_warning: "Both WebSocket and CVP connections have issues. Some features may be limited.",
        both_critical: "Critical connectivity issues detected. This may be caused by VPN or firewall blocking WebSocket/gRPC traffic.",
        firewall_hint: "If using a corporate network or VPN, WebSocket and gRPC traffic may be blocked. Contact your network administrator or try disconnecting your VPN."
    };

    /**
     * Test CVP gRPC connectivity via actual gRPC endpoint
     * This tests if gRPC traffic can reach CVP (not just HTTP)
     */
    function testCVPConnectivity() {
        // Test CVP gRPC endpoint through the /cv/ proxy
        // This tests if CVP is reachable (both HTTP and gRPC use same backend)
        // Using CVP Studio service which is always available
        const grpcEndpoint = '/cv/arista.studio.v1.services.StudioService/GetAll';
        const timeout = CONNECTIVITY_CONFIG.grpc.timeout;

        if (window.ConnectivityDebug) {
            console.log('[Connectivity Monitor] Testing CVP gRPC connectivity...');
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        fetch(grpcEndpoint, {
            method: 'POST',
            signal: controller.signal,
            headers: {
                'Content-Type': 'application/grpc-web+proto',
                'Accept': 'application/grpc-web+proto'
            },
            cache: 'no-cache'
        })
        .then(response => {
            clearTimeout(timeoutId);
            grpcConnectionStatus.lastCheck = Date.now();

            // Status code interpretation for CVP connectivity:
            // 200 = success, CVP fully working
            // 401/403 = auth required, but CVP is reachable (working)
            // 405 = method not allowed, but CVP responded (reachable)
            // 502/503/504 = backend unreachable (CVP down or blocked)

            if (response.status === 200 || response.status === 401 || response.status === 403 || response.status === 405) {
                // These statuses mean CVP responded, so it's reachable
                // 405 is OK - it means CVP received our request and rejected the method
                grpcConnectionStatus.connected = true;
                grpcConnectionStatus.failureCount = 0;
                grpcConnectionStatus.errorMessage = '';
                logConnectivityEvent('GRPC_TEST_SUCCESS', { status: response.status });
            } else if (response.status === 404) {
                // 404 could mean endpoint doesn't exist, treat as degraded but not blocked
                grpcConnectionStatus.connected = true;
                grpcConnectionStatus.failureCount = 0;
                grpcConnectionStatus.errorMessage = '';
                logConnectivityEvent('GRPC_TEST_SUCCESS', {
                    status: response.status,
                    note: 'CVP reachable but endpoint not found'
                });
            } else {
                // 502/503/504 = backend issues, CVP unreachable
                grpcConnectionStatus.connected = false;
                grpcConnectionStatus.failureCount++;
                grpcConnectionStatus.errorMessage = `CVP unreachable (${response.status})`;
                logConnectivityEvent('GRPC_TEST_FAILED', {
                    status: response.status,
                    failureCount: grpcConnectionStatus.failureCount
                });
            }
            updateConnectivityBadge();
        })
        .catch(error => {
            clearTimeout(timeoutId);
            grpcConnectionStatus.connected = false;
            grpcConnectionStatus.failureCount++;
            grpcConnectionStatus.lastCheck = Date.now();

            if (error.name === 'AbortError') {
                grpcConnectionStatus.errorMessage = 'gRPC timeout (may be blocked)';
            } else if (error.message.includes('NetworkError') || error.message.includes('Failed to fetch')) {
                grpcConnectionStatus.errorMessage = 'gRPC blocked';
            } else {
                grpcConnectionStatus.errorMessage = error.message.substring(0, 50);
            }

            logConnectivityEvent('GRPC_TEST_ERROR', {
                error: grpcConnectionStatus.errorMessage,
                failureCount: grpcConnectionStatus.failureCount
            });
            updateConnectivityBadge();
        });
    }

    /**
     * Determine overall health status based on connection states
     */
    function getHealthStatus() {
        // Don't fail WebSocket check if we haven't initialized yet (null state)
        const wsHealthy = (wsConnectionStatus.connected === null) ||
                          (wsConnectionStatus.connected &&
                           wsConnectionStatus.failureCount < CONNECTIVITY_CONFIG.websocket.maxFailures);

        const grpcHealthy = grpcConnectionStatus.connected ||
                            grpcConnectionStatus.failureCount < CONNECTIVITY_CONFIG.grpc.maxFailures;

        let level, color, message, detailMessage;

        if (wsHealthy && grpcHealthy) {
            level = 'healthy';
            color = 'green';
            message = 'Connected';
            detailMessage = CONNECTIVITY_MESSAGES.all_ok;
        } else if (!wsHealthy && !grpcHealthy) {
            level = 'critical';
            color = 'red';
            message = 'Disconnected';
            detailMessage = CONNECTIVITY_MESSAGES.both_critical + ' ' + CONNECTIVITY_MESSAGES.firewall_hint;
        } else if (!wsHealthy) {
            level = 'warning';
            color = 'orange';
            message = 'Connection Issues';
            detailMessage = wsConnectionStatus.failureCount >= CONNECTIVITY_CONFIG.websocket.maxFailures
                ? CONNECTIVITY_MESSAGES.ws_critical
                : CONNECTIVITY_MESSAGES.ws_warning;
        } else {
            level = 'warning';
            color = 'orange';
            message = 'Connection Issues';
            detailMessage = grpcConnectionStatus.failureCount >= CONNECTIVITY_CONFIG.grpc.maxFailures
                ? CONNECTIVITY_MESSAGES.grpc_critical + ' ' + CONNECTIVITY_MESSAGES.firewall_hint
                : CONNECTIVITY_MESSAGES.grpc_warning;
        }

        return { level, color, message, detailMessage };
    }

    /**
     * Update the connectivity badge visual state
     */
    function updateConnectivityBadge() {
        const badge = document.getElementById('connectivity-badge');
        if (!badge) return;

        const health = getHealthStatus();

        // Update badge classes
        badge.className = 'connectivity-badge ' + health.level;

        // Update icon
        const iconMap = {
            'healthy': 'fa-circle-check',
            'warning': 'fa-circle-exclamation',
            'critical': 'fa-circle-xmark'
        };
        const icon = badge.querySelector('.connectivity-icon i');
        if (icon) {
            icon.className = 'fa-solid ' + iconMap[health.level];
        }

        // Update text
        const textSpan = badge.querySelector('.connectivity-text');
        if (textSpan) {
            textSpan.textContent = health.message;
        }

        // Update tooltip
        badge.setAttribute('title', health.detailMessage);

        // Update detail popup if it exists
        updateDetailPopup(health);

        if (window.ConnectivityDebug) {
            console.log('[Connectivity Monitor] Badge updated', {
                level: health.level,
                ws: wsConnectionStatus,
                grpc: grpcConnectionStatus
            });
        }
    }

    /**
     * Update the detailed status popup
     */
    function updateDetailPopup(health) {
        const wsStatusText = document.getElementById('ws-status-text');
        const wsStatusIcon = document.getElementById('ws-status-icon');
        const grpcStatusText = document.getElementById('grpc-status-text');
        const grpcStatusIcon = document.getElementById('grpc-status-icon');
        const message = document.getElementById('connectivity-message');

        if (!wsStatusText) return; // Popup not in DOM

        // Update WebSocket status
        if (wsConnectionStatus.connected === null) {
            wsStatusText.textContent = 'Initializing...';
            wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-notch fa-spin status-icon warning"></i>';
        } else if (wsConnectionStatus.connected) {
            wsStatusText.textContent = 'Connected';
            wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check status-icon ok"></i>';
        } else if (wsConnectionStatus.failureCount > 0 && wsConnectionStatus.failureCount < CONNECTIVITY_CONFIG.websocket.maxFailures) {
            wsStatusText.textContent = 'Reconnecting...';
            wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-exclamation status-icon warning"></i>';
        } else {
            wsStatusText.textContent = 'Disconnected';
            wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-xmark status-icon error"></i>';
        }

        // Update gRPC status
        if (grpcConnectionStatus.connected) {
            grpcStatusText.textContent = 'Connected';
            grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check status-icon ok"></i>';
        } else {
            const failCount = grpcConnectionStatus.failureCount;
            if (failCount === 0) {
                grpcStatusText.textContent = 'Checking...';
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-question status-icon warning"></i>';
            } else if (failCount < CONNECTIVITY_CONFIG.grpc.maxFailures) {
                grpcStatusText.textContent = 'Checking... (' + failCount + ' failures)';
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-exclamation status-icon warning"></i>';
            } else {
                grpcStatusText.textContent = 'Disconnected (' + grpcConnectionStatus.errorMessage + ')';
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-xmark status-icon error"></i>';
            }
        }

        // Update message
        if (message) {
            message.textContent = health.detailMessage;
        }
    }

    /**
     * Show the connectivity details popup
     */
    function showConnectivityDetails() {
        const details = document.getElementById('connectivity-details');
        if (details) {
            details.style.display = 'block';
            updateConnectivityBadge(); // Refresh popup content
        }
    }

    /**
     * Hide the connectivity details popup
     */
    function hideConnectivityDetails() {
        const details = document.getElementById('connectivity-details');
        if (details) {
            details.style.display = 'none';
        }
    }

    /**
     * Log connectivity events to console
     */
    function logConnectivityEvent(type, data) {
        const timestamp = new Date().toISOString();
        console.log(`[Connectivity Monitor] [${timestamp}] ${type}:`, data);
    }

    /**
     * Initialize the connectivity badge
     */
    function initConnectivityBadge() {
        console.log('[Connectivity Monitor] Initializing...');

        // Verify badge exists in DOM
        const badge = document.getElementById('connectivity-badge');
        if (!badge) {
            console.error('[Connectivity Monitor] Badge element not found in DOM');
            return;
        }

        // Add click handler for detail popup
        badge.addEventListener('click', function() {
            const details = document.getElementById('connectivity-details');
            if (details && details.style.display === 'none') {
                showConnectivityDetails();
            } else if (details) {
                hideConnectivityDetails();
            }
        });

        // Add close button handler if popup exists
        const closeBtn = document.querySelector('.connectivity-close-btn');
        if (closeBtn) {
            closeBtn.addEventListener('click', hideConnectivityDetails);
        }

        // Check if WebSocket is already connected (from atd-ws.js)
        // The global 'ws' variable is created by atd-ws.js
        if (typeof ws !== 'undefined' && ws.readyState === WebSocket.OPEN) {
            console.log('[Connectivity Monitor] WebSocket already connected on init');
            wsConnectionStatus.connected = true;
            wsConnectionStatus.lastSuccessfulPing = Date.now();
            wsConnectionStatus.failureCount = 0;
        }

        // Initial state
        updateConnectivityBadge();

        // Start CVP monitoring after brief delay
        setTimeout(function() {
            console.log('[Connectivity Monitor] Starting CVP monitoring...');
            testCVPConnectivity();
        }, 2000);

        // Periodic CVP checks
        setInterval(function() {
            testCVPConnectivity();
        }, CONNECTIVITY_CONFIG.grpc.retryInterval);

        console.log('[Connectivity Monitor] Initialized successfully');
    }

    // Export to global scope
    window.ConnectivityMonitor = {
        /**
         * Update WebSocket connection status
         * Called from atd-ws.js
         */
        updateWSStatus: function(connected) {
            wsConnectionStatus.connected = connected;

            if (connected) {
                wsConnectionStatus.lastSuccessfulPing = Date.now();
                wsConnectionStatus.failureCount = 0;
                logConnectivityEvent('WS_CONNECTED', { uptime: performance.now() });
            } else {
                wsConnectionStatus.failureCount++;
                logConnectivityEvent('WS_DISCONNECTED', {
                    failureCount: wsConnectionStatus.failureCount
                });
            }

            updateConnectivityBadge();
        },

        /**
         * Get current health status
         */
        getStatus: getHealthStatus,

        /**
         * Show/hide details popup
         */
        showDetails: showConnectivityDetails,
        hideDetails: hideConnectivityDetails
    };

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initConnectivityBadge);
    } else {
        // DOM already loaded
        initConnectivityBadge();
    }
})();

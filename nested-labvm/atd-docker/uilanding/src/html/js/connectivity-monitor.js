/**
 * Connectivity Monitor for UILanding
 * Unified system status monitoring for WebSocket, CVP, and gRPC connectivity
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

    // CVP status (from WebSocket updates)
    var cvpStatus = {
        version: null,
        status: null,      // 'UP', 'DOWN', or null
        tasks: null,       // Summary string for display
        tasksData: null,   // Raw tasks object with counts by status
        lastUpdate: null
    };

    // CVP gRPC connection status
    var grpcConnectionStatus = {
        connected: false,
        lastCheck: null,
        failureCount: 0,
        errorMessage: '',
        monitoringStarted: false  // Only start after CVP is UP
    };

    // gRPC monitoring interval reference
    var grpcMonitorInterval = null;

    // Connectivity messages for different scenarios
    const CONNECTIVITY_MESSAGES = {
        all_ok: "All systems operational. WebSocket and CVP connections are healthy.",
        cvp_starting: "CVP is starting up. gRPC monitoring will begin once CVP is ready.",
        cvp_tasks_failed: "CVP has failed tasks that need attention. Check CVP for details.",
        ws_warning: "WebSocket connection unstable. Live updates may be delayed.",
        ws_critical: "WebSocket disconnected. Attempting to reconnect. Real-time updates unavailable.",
        grpc_warning: "CVP gRPC connectivity issue detected. Some lab features may be limited.",
        grpc_critical: "Unable to reach CVP gRPC. CVP-dependent features unavailable.",
        both_warning: "Both WebSocket and CVP connections have issues. Some features may be limited.",
        both_critical: "Critical connectivity issues detected. This may be caused by VPN or firewall blocking WebSocket/gRPC traffic.",
        firewall_hint: "If using a corporate network or VPN, WebSocket and gRPC traffic may be blocked. Contact your network administrator or try disconnecting your VPN."
    };

    /**
     * Test CVP gRPC connectivity via actual gRPC endpoint
     * Only runs after CVP status is UP
     */
    function testCVPConnectivity() {
        // Don't test gRPC if CVP is not UP yet
        if (cvpStatus.status !== 'UP') {
            if (window.ConnectivityDebug) {
                console.log('[Connectivity Monitor] Skipping gRPC test - CVP not UP yet');
            }
            return;
        }

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
                grpcConnectionStatus.connected = true;
                grpcConnectionStatus.failureCount = 0;
                grpcConnectionStatus.errorMessage = '';
                logConnectivityEvent('GRPC_TEST_SUCCESS', { status: response.status });
            } else if (response.status === 404) {
                grpcConnectionStatus.connected = true;
                grpcConnectionStatus.failureCount = 0;
                grpcConnectionStatus.errorMessage = '';
                logConnectivityEvent('GRPC_TEST_SUCCESS', {
                    status: response.status,
                    note: 'CVP reachable but endpoint not found'
                });
            } else {
                grpcConnectionStatus.connected = false;
                grpcConnectionStatus.failureCount++;
                grpcConnectionStatus.errorMessage = `CVP unreachable (${response.status})`;
                logConnectivityEvent('GRPC_TEST_FAILED', {
                    status: response.status,
                    failureCount: grpcConnectionStatus.failureCount
                });
            }
            updateStatusUI();
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
            cloudLog('error', 'gRPC connectivity test failed: ' + grpcConnectionStatus.errorMessage, { source: 'connectivity-monitor', action: 'grpc_test_error' });
            updateStatusUI();
        });
    }

    /**
     * Start gRPC monitoring (called when CVP becomes UP)
     */
    function startGRPCMonitoring() {
        if (grpcConnectionStatus.monitoringStarted) {
            return; // Already started
        }

        console.log('[Connectivity Monitor] CVP is UP - starting gRPC monitoring');
        grpcConnectionStatus.monitoringStarted = true;

        // Initial test after short delay
        setTimeout(function() {
            testCVPConnectivity();
        }, 1000);

        // Periodic checks
        grpcMonitorInterval = setInterval(function() {
            testCVPConnectivity();
        }, CONNECTIVITY_CONFIG.grpc.retryInterval);
    }

    /**
     * Check if there are failed CVP tasks
     */
    function hasFailedTasks() {
        if (!cvpStatus.tasksData) return false;
        for (var status in cvpStatus.tasksData) {
            if (status.toLowerCase() === 'failed' && cvpStatus.tasksData[status] > 0) {
                return true;
            }
        }
        return false;
    }

    /**
     * Determine overall health status based on connection states
     */
    function getHealthStatus() {
        const wsHealthy = (wsConnectionStatus.connected === null) ||
                          (wsConnectionStatus.connected &&
                           wsConnectionStatus.failureCount < CONNECTIVITY_CONFIG.websocket.maxFailures);

        const cvpUp = cvpStatus.status === 'UP';

        // gRPC is considered healthy if: CVP not up yet, or gRPC connected, or below failure threshold
        const grpcHealthy = !cvpUp ||
                            grpcConnectionStatus.connected ||
                            grpcConnectionStatus.failureCount < CONNECTIVITY_CONFIG.grpc.maxFailures;

        // Check for failed CVP tasks
        const hasCvpFailedTasks = hasFailedTasks();

        let level, message, detailMessage;

        if (wsHealthy && cvpUp && grpcHealthy) {
            // All connected - but check for failed tasks
            if (hasCvpFailedTasks) {
                level = 'warning';
                message = 'CVP Tasks Failed';
                detailMessage = CONNECTIVITY_MESSAGES.cvp_tasks_failed;
            } else {
                level = 'healthy';
                message = 'All systems operational';
                detailMessage = CONNECTIVITY_MESSAGES.all_ok;
            }
        } else if (wsHealthy && !cvpUp) {
            level = 'warning';
            message = 'CVP starting...';
            detailMessage = CONNECTIVITY_MESSAGES.cvp_starting;
        } else if (!wsHealthy && (!cvpUp || !grpcHealthy)) {
            level = 'critical';
            message = 'Connection issues';
            detailMessage = CONNECTIVITY_MESSAGES.both_critical + ' ' + CONNECTIVITY_MESSAGES.firewall_hint;
        } else if (!wsHealthy) {
            level = 'warning';
            message = 'WebSocket issues';
            detailMessage = wsConnectionStatus.failureCount >= CONNECTIVITY_CONFIG.websocket.maxFailures
                ? CONNECTIVITY_MESSAGES.ws_critical
                : CONNECTIVITY_MESSAGES.ws_warning;
        } else if (!grpcHealthy) {
            level = 'warning';
            message = 'gRPC issues';
            detailMessage = grpcConnectionStatus.failureCount >= CONNECTIVITY_CONFIG.grpc.maxFailures
                ? CONNECTIVITY_MESSAGES.grpc_critical + ' ' + CONNECTIVITY_MESSAGES.firewall_hint
                : CONNECTIVITY_MESSAGES.grpc_warning;
        } else {
            level = 'healthy';
            message = 'Connected';
            detailMessage = CONNECTIVITY_MESSAGES.all_ok;
        }

        return { level, message, detailMessage };
    }

    /**
     * Update all status UI elements
     */
    function updateStatusUI() {
        updateBadge();
        updatePopup();
    }

    /**
     * Update the main badge (single icon + text)
     */
    function updateBadge() {
        var badge = document.getElementById('system-status-badge');
        var icon = document.getElementById('system-status-icon');
        var text = document.getElementById('system-status-text');
        var popup = document.getElementById('system-status-popup');

        if (!badge) return;

        var health = getHealthStatus();

        // Update badge class for styling
        badge.className = 'system-status-badge ' + health.level;
        if (popup) {
            popup.className = 'system-status-popup ' + health.level;
        }

        // Update icon based on health level
        if (icon) {
            if (health.level === 'healthy') {
                icon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (health.level === 'warning') {
                icon.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i>';
            } else if (health.level === 'critical') {
                icon.innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
            } else {
                icon.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            }
        }

        // Update text
        if (text) {
            if (health.level === 'healthy') {
                text.textContent = 'Connected';
            } else if (health.level === 'warning') {
                // Show more specific text for warnings
                if (hasFailedTasks()) {
                    text.textContent = 'CVP Tasks Failed';
                } else if (cvpStatus.status !== 'UP') {
                    text.textContent = 'CVP Starting...';
                } else if (!wsConnectionStatus.connected) {
                    text.textContent = 'Reconnecting...';
                } else {
                    text.textContent = 'Connecting...';
                }
            } else if (health.level === 'critical') {
                text.textContent = 'Disconnected';
            } else {
                text.textContent = 'Connecting...';
            }
        }
    }

    /**
     * Update the detailed status popup
     */
    function updatePopup() {
        var health = getHealthStatus();

        // Update CVP row
        var cvpVersionText = document.getElementById('cvp-version-text');
        var cvpTasksText = document.getElementById('cvp-tasks-text');
        var cvpStatusText = document.getElementById('cvp-status-text');
        var cvpStatusIcon = document.getElementById('cvp-status-row-icon');

        if (cvpVersionText) {
            cvpVersionText.textContent = cvpStatus.version || '--';
        }

        if (cvpTasksText) {
            // Build tasks display with highlighting for failed tasks
            if (cvpStatus.tasksData) {
                var tasksHtml = '(';
                var first = true;
                for (var status in cvpStatus.tasksData) {
                    if (!first) tasksHtml += ', ';
                    first = false;
                    var count = cvpStatus.tasksData[status];
                    // Highlight Failed tasks in red
                    if (status.toLowerCase() === 'failed') {
                        tasksHtml += '<span class="task-failed">' + count + ' ' + status + '</span>';
                    } else {
                        tasksHtml += count + ' ' + status;
                    }
                }
                tasksHtml += ' tasks)';
                cvpTasksText.innerHTML = tasksHtml;
            } else if (cvpStatus.tasks && cvpStatus.tasks !== 'No pending tasks in CVP.') {
                cvpTasksText.textContent = '(' + cvpStatus.tasks + ')';
            } else {
                cvpTasksText.textContent = '';
            }
        }

        if (cvpStatusText) {
            if (cvpStatus.status === 'UP') {
                cvpStatusText.textContent = 'UP';
            } else if (cvpStatus.status === null) {
                cvpStatusText.textContent = 'Starting...';
            } else {
                cvpStatusText.textContent = cvpStatus.status || 'Starting...';
            }
        }

        if (cvpStatusIcon) {
            if (cvpStatus.status === 'UP') {
                cvpStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (cvpStatus.status === null) {
                cvpStatusIcon.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            } else {
                cvpStatusIcon.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i>';
            }
        }

        // Update WebSocket row
        var wsStatusText = document.getElementById('ws-status-text');
        var wsStatusIcon = document.getElementById('ws-status-row-icon');

        if (wsStatusText) {
            if (wsConnectionStatus.connected === null) {
                wsStatusText.textContent = 'Connecting...';
            } else if (wsConnectionStatus.connected) {
                wsStatusText.textContent = 'Connected';
            } else if (wsConnectionStatus.failureCount > 0 && wsConnectionStatus.failureCount < CONNECTIVITY_CONFIG.websocket.maxFailures) {
                wsStatusText.textContent = 'Reconnecting...';
            } else {
                wsStatusText.textContent = 'Disconnected';
            }
        }

        if (wsStatusIcon) {
            if (wsConnectionStatus.connected === null) {
                wsStatusIcon.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            } else if (wsConnectionStatus.connected) {
                wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (wsConnectionStatus.failureCount > 0 && wsConnectionStatus.failureCount < CONNECTIVITY_CONFIG.websocket.maxFailures) {
                wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i>';
            } else {
                wsStatusIcon.innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
            }
        }

        // Update gRPC row
        var grpcStatusText = document.getElementById('grpc-status-text');
        var grpcStatusIcon = document.getElementById('grpc-status-row-icon');

        if (grpcStatusText) {
            if (cvpStatus.status !== 'UP') {
                grpcStatusText.textContent = 'Waiting for CVP...';
            } else if (grpcConnectionStatus.connected) {
                grpcStatusText.textContent = 'Connected';
            } else if (grpcConnectionStatus.failureCount === 0) {
                grpcStatusText.textContent = 'Checking...';
            } else if (grpcConnectionStatus.failureCount < CONNECTIVITY_CONFIG.grpc.maxFailures) {
                grpcStatusText.textContent = 'Checking... (' + grpcConnectionStatus.failureCount + ' failures)';
            } else {
                grpcStatusText.textContent = 'Disconnected';
            }
        }

        if (grpcStatusIcon) {
            if (cvpStatus.status !== 'UP') {
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-clock"></i>';
            } else if (grpcConnectionStatus.connected) {
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-check"></i>';
            } else if (grpcConnectionStatus.failureCount === 0) {
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            } else if (grpcConnectionStatus.failureCount < CONNECTIVITY_CONFIG.grpc.maxFailures) {
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i>';
            } else {
                grpcStatusIcon.innerHTML = '<i class="fa-solid fa-circle-xmark"></i>';
            }
        }

        // Update message
        var message = document.getElementById('connectivity-message');
        if (message) {
            message.textContent = health.detailMessage;
        }

        if (window.ConnectivityDebug) {
            console.log('[Connectivity Monitor] UI updated', {
                level: health.level,
                ws: wsConnectionStatus,
                cvp: cvpStatus,
                grpc: grpcConnectionStatus
            });
        }
    }

    /**
     * Show the status popup
     */
    function showStatusPopup() {
        var popup = document.getElementById('system-status-popup');
        if (popup) {
            popup.style.display = 'block';
            updateStatusUI();
        }
    }

    /**
     * Hide the status popup
     */
    function hideStatusPopup() {
        var popup = document.getElementById('system-status-popup');
        if (popup) {
            popup.style.display = 'none';
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
        console.log('[Connectivity Monitor] Initializing unified status badge...');

        // Verify badge exists in DOM
        var badge = document.getElementById('system-status-badge');
        if (!badge) {
            console.error('[Connectivity Monitor] Badge element not found in DOM');
            return;
        }

        var popup = document.getElementById('system-status-popup');

        // Add hover handlers for popup
        badge.addEventListener('mouseenter', function() {
            if (popup) {
                showStatusPopup();
            }
        });

        badge.addEventListener('mouseleave', function() {
            if (popup) {
                setTimeout(function() {
                    if (!popup.matches(':hover') && !badge.matches(':hover')) {
                        hideStatusPopup();
                    }
                }, 100);
            }
        });

        if (popup) {
            popup.addEventListener('mouseleave', function() {
                hideStatusPopup();
            });
        }

        // Add close button handler
        var closeBtn = document.querySelector('.status-popup-close');
        if (closeBtn) {
            closeBtn.addEventListener('click', hideStatusPopup);
        }

        // Check if WebSocket is already connected
        if (typeof ws !== 'undefined' && ws.readyState === WebSocket.OPEN) {
            console.log('[Connectivity Monitor] WebSocket already connected on init');
            wsConnectionStatus.connected = true;
            wsConnectionStatus.lastSuccessfulPing = Date.now();
            wsConnectionStatus.failureCount = 0;
        }

        // Initial UI update
        updateStatusUI();

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

            updateStatusUI();
        },

        /**
         * Update CVP status from WebSocket messages
         * Called from atd-ws.js when CVP status updates arrive
         * @param {string} version - CVP version
         * @param {string} status - CVP status ('UP', 'DOWN', etc.)
         * @param {string} tasksInfo - Summary string of tasks
         * @param {object} tasksData - Raw tasks object with counts by status (e.g., {Pending: 3, Failed: 1})
         */
        updateCVPStatus: function(version, status, tasksInfo, tasksData) {
            var wasNotUp = cvpStatus.status !== 'UP';

            cvpStatus.version = version;
            cvpStatus.status = status;
            cvpStatus.tasks = tasksInfo;
            cvpStatus.tasksData = tasksData || null;
            cvpStatus.lastUpdate = Date.now();

            logConnectivityEvent('CVP_STATUS_UPDATE', {
                version: version,
                status: status,
                tasks: tasksInfo,
                tasksData: tasksData
            });

            // Start gRPC monitoring when CVP becomes UP for the first time
            if (wasNotUp && status === 'UP') {
                startGRPCMonitoring();
            }

            updateStatusUI();
        },

        /**
         * Get current health status
         */
        getStatus: getHealthStatus,

        /**
         * Show/hide popup
         */
        showPopup: showStatusPopup,
        hidePopup: hideStatusPopup
    };

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initConnectivityBadge);
    } else {
        initConnectivityBadge();
    }
})();

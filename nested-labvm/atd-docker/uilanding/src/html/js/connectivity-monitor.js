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

    // CVP authentication token for gRPC-Web requests
    var cvpToken = null;

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

    // ============================================
    // Connection Tracker - Event Buffer & Sync
    // ============================================

    var TRACKER_CONFIG = {
        maxEvents: 500,
        summaryInterval: 300000,
        localStorageKey: 'atl_connectivity_events',
        localStorageMaxAge: 86400000
    };

    var sessionInfo = {
        id: null,
        reconnectCount: 0,
        debugMode: false
    };

    var eventBuffer = [];
    var latencyHistory = [];
    var MAX_LATENCY_SAMPLES = 60;
    var sessionStartTime = Date.now();
    var lastDisconnectTime = null;
    var summaryTimerRef = null;
    var saveDebounceTimer = null;
    var pageVisible = true;

    function debouncedSaveToLocalStorage() {
        if (saveDebounceTimer) return;
        saveDebounceTimer = setTimeout(function() {
            saveToLocalStorage();
            saveDebounceTimer = null;
        }, 5000);
    }

    function trackEvent(type, data) {
        var entry = { ts: Date.now(), type: type, data: data || {} };
        eventBuffer.push(entry);
        if (eventBuffer.length > TRACKER_CONFIG.maxEvents) {
            eventBuffer = eventBuffer.slice(eventBuffer.length - TRACKER_CONFIG.maxEvents);
        }
        debouncedSaveToLocalStorage();
    }

    function recordLatency(rttMs) {
        if (typeof rttMs !== 'number' || rttMs < 0) return;
        latencyHistory.push({ ts: Date.now(), rtt: rttMs });
        if (latencyHistory.length > MAX_LATENCY_SAMPLES) {
            latencyHistory = latencyHistory.slice(latencyHistory.length - MAX_LATENCY_SAMPLES);
        }
        if (latencyHistory.length >= 5) {
            var avg = getAverageLatency();
            if (rttMs > avg * 2 && rttMs > 200) {
                trackEvent('latency_spike', { rtt: rttMs, avg: Math.round(avg) });
            }
        }
    }

    function getAverageLatency() {
        if (latencyHistory.length === 0) return 0;
        var sum = 0;
        for (var i = 0; i < latencyHistory.length; i++) {
            sum += latencyHistory[i].rtt;
        }
        return sum / latencyHistory.length;
    }

    function getLatestLatency() {
        if (latencyHistory.length === 0) return null;
        return latencyHistory[latencyHistory.length - 1].rtt;
    }

    function getUptimePercent() {
        var totalTime = Date.now() - sessionStartTime;
        if (totalTime === 0) return 100;
        var downtime = 0;
        var disconnectStart = null;
        for (var i = 0; i < eventBuffer.length; i++) {
            var evt = eventBuffer[i];
            if (evt.type === 'ws_disconnect' && disconnectStart === null) {
                disconnectStart = evt.ts;
            } else if (evt.type === 'ws_reconnect' && disconnectStart !== null) {
                downtime += evt.ts - disconnectStart;
                disconnectStart = null;
            }
        }
        if (disconnectStart !== null) {
            downtime += Date.now() - disconnectStart;
        }
        return Math.round(((totalTime - downtime) / totalTime) * 1000) / 10;
    }

    function saveToLocalStorage() {
        try {
            var payload = {
                savedAt: Date.now(),
                sessionId: sessionInfo.id,
                events: eventBuffer.slice(-100)
            };
            localStorage.setItem(TRACKER_CONFIG.localStorageKey, JSON.stringify(payload));
        } catch (e) {}
    }

    function loadFromLocalStorage() {
        try {
            var stored = localStorage.getItem(TRACKER_CONFIG.localStorageKey);
            if (!stored) return [];
            var payload = JSON.parse(stored);
            if (Date.now() - payload.savedAt > TRACKER_CONFIG.localStorageMaxAge) {
                localStorage.removeItem(TRACKER_CONFIG.localStorageKey);
                return [];
            }
            return payload.events || [];
        } catch (e) {
            return [];
        }
    }

    function clearLocalStorage() {
        try {
            localStorage.removeItem(TRACKER_CONFIG.localStorageKey);
        } catch (e) {}
    }

    function sendPeriodicSummary() {
        if (typeof ws === 'undefined' || ws.readyState !== WebSocket.OPEN) return;

        // Note: frontend latency is server-to-client one-way + clock skew, not true RTT.
        // The authoritative RTT is calculated server-side from the ping/pong round-trip.
        var summaryData = {
            event: 'periodic_summary',
            wsRoundTrip: getLatestLatency(),
            avgLatency: Math.round(getAverageLatency()),
            grpcStatus: grpcConnectionStatus.connected ? 'connected' : 'disconnected',
            grpcFailures: grpcConnectionStatus.failureCount,
            eventCount: eventBuffer.length,
            sessionUptime: Math.round((Date.now() - sessionStartTime) / 1000),
            uptimePercent: getUptimePercent(),
            externalCheck: externalCheckResult.arista,
            externalRttMs: externalCheckResult.aristaRttMs
        };

        // Add network info if available (Chrome/Edge only)
        var netInfo = getNetworkInfo();
        if (netInfo) {
            summaryData.networkType = netInfo.type;
            summaryData.effectiveType = netInfo.effectiveType;
            summaryData.downlinkMbps = netInfo.downlinkMbps;
            summaryData.browserRttMs = netInfo.rttMs;
        }

        try {
            ws.send(JSON.stringify({ type: 'connectivity', data: summaryData }));
        } catch (e) {}
    }

    function sendReconnectReport(offlineFrom, offlineTo, bufferedEvents) {
        if (typeof ws === 'undefined' || ws.readyState !== WebSocket.OPEN) return;
        var report = {
            type: 'connectivity',
            data: {
                event: 'reconnect_report',
                offlineFrom: offlineFrom,
                offlineTo: offlineTo,
                offlineDuration: offlineTo - offlineFrom,
                bufferedEvents: bufferedEvents
            }
        };
        try {
            ws.send(JSON.stringify(report));
            clearLocalStorage();
        } catch (e) {}
    }

    /**
     * Send individual gRPC check result to backend for Cloud Logging
     * @param {string} status - 'ok', 'failed', or 'error'
     * @param {object} detail - Additional details (reason, grpcStatus, etc.)
     */
    function sendGRPCCheckResult(status, detail) {
        if (typeof ws === 'undefined' || ws.readyState !== WebSocket.OPEN) return;
        try {
            ws.send(JSON.stringify({
                type: 'connectivity',
                data: {
                    event: 'grpc_check',
                    status: status,
                    detail: JSON.stringify(detail || {})
                }
            }));
        } catch (e) {}
    }

    // External check timer reference
    var externalCheckTimerRef = null;

    function startSummaryTimer() {
        if (summaryTimerRef) clearInterval(summaryTimerRef);
        summaryTimerRef = setInterval(sendPeriodicSummary, TRACKER_CONFIG.summaryInterval);
        // Run external check every 60 seconds on its own timer
        if (externalCheckTimerRef) clearInterval(externalCheckTimerRef);
        checkExternalConnectivity();  // Initial check immediately
        externalCheckTimerRef = setInterval(checkExternalConnectivity, 60000);
    }

    function stopSummaryTimer() {
        if (summaryTimerRef) {
            clearInterval(summaryTimerRef);
            summaryTimerRef = null;
        }
        if (externalCheckTimerRef) {
            clearInterval(externalCheckTimerRef);
            externalCheckTimerRef = null;
        }
    }

    // ============================================
    // External Connectivity Checks
    // ============================================

    // Last external check results
    var externalCheckResult = {
        arista: null,      // 'ok', 'failed', 'timeout', null (not checked yet)
        aristaRttMs: null,  // round-trip time to arista.com
        lastCheck: null
    };

    /**
     * Check external connectivity by fetching arista.com favicon
     * Uses no-cors mode so we only measure success/failure + timing, not response body
     */
    function checkExternalConnectivity() {
        var startTime = Date.now();
        var controller = new AbortController();
        var timeoutId = setTimeout(function() { controller.abort(); }, 5000);

        fetch('https://www.arista.com/favicon.ico', {
            mode: 'no-cors',
            cache: 'no-cache',
            signal: controller.signal
        })
        .then(function() {
            clearTimeout(timeoutId);
            var rtt = Date.now() - startTime;
            externalCheckResult.arista = 'ok';
            externalCheckResult.aristaRttMs = rtt;
            externalCheckResult.lastCheck = Date.now();
            trackEvent('external_check_ok', { rttMs: rtt });
        })
        .catch(function(error) {
            clearTimeout(timeoutId);
            externalCheckResult.aristaRttMs = null;
            externalCheckResult.lastCheck = Date.now();
            if (error.name === 'AbortError') {
                externalCheckResult.arista = 'timeout';
                trackEvent('external_check_fail', { reason: 'timeout' });
            } else {
                externalCheckResult.arista = 'failed';
                trackEvent('external_check_fail', { reason: 'network' });
            }
        });
    }

    /**
     * Get browser network information from the Network Information API
     * Only available in Chromium-based browsers
     * @returns {object|null} Network info or null if not supported
     */
    function getNetworkInfo() {
        var conn = navigator.connection || navigator.mozConnection || navigator.webkitConnection;
        if (!conn) return null;
        return {
            type: conn.type || null,              // 'wifi', 'cellular', 'ethernet', etc.
            effectiveType: conn.effectiveType || null,  // '4g', '3g', '2g', 'slow-2g'
            downlinkMbps: conn.downlink || null,  // estimated downlink in Mbps
            rttMs: conn.rtt || null               // estimated RTT in ms
        };
    }

    // ============================================
    // Diagnostics Panel Controller
    // ============================================

    var diagRefreshInterval = null;

    function showDiagnosticsPanel() {
        var panel = document.getElementById('diagnostics-panel');
        if (!panel) return;
        panel.style.display = 'flex';
        refreshDiagnosticsPanel();
        if (diagRefreshInterval) clearInterval(diagRefreshInterval);
        diagRefreshInterval = setInterval(refreshDiagnosticsPanel, 2000);
    }

    function hideDiagnosticsPanel() {
        var panel = document.getElementById('diagnostics-panel');
        if (panel) panel.style.display = 'none';
        if (diagRefreshInterval) {
            clearInterval(diagRefreshInterval);
            diagRefreshInterval = null;
        }
    }

    function toggleDiagnosticsPanel() {
        var panel = document.getElementById('diagnostics-panel');
        if (!panel) return;
        if (panel.style.display === 'none' || panel.style.display === '') {
            showDiagnosticsPanel();
        } else {
            hideDiagnosticsPanel();
        }
    }

    function formatDuration(ms) {
        var seconds = Math.floor(ms / 1000);
        var hours = Math.floor(seconds / 3600);
        var minutes = Math.floor((seconds % 3600) / 60);
        var secs = seconds % 60;
        if (hours > 0) return hours + 'h ' + minutes + 'm';
        if (minutes > 0) return minutes + 'm ' + secs + 's';
        return secs + 's';
    }

    function getEventClass(type) {
        if (type === 'ws_reconnect' || type === 'grpc_ok' || type === 'external_check_ok' || type === 'browser_online') return 'evt-connect';
        if (type === 'ws_disconnect' || type === 'grpc_fail' || type === 'external_check_fail' || type === 'browser_offline') return 'evt-disconnect';
        if (type === 'latency_spike' || type === 'state_change' || type === 'grpc_sync_check') return 'evt-warning';
        return 'evt-info';
    }

    function formatTimelineTs(ts) {
        var d = new Date(ts);
        return d.getHours().toString().padStart(2, '0') + ':' +
               d.getMinutes().toString().padStart(2, '0') + ':' +
               d.getSeconds().toString().padStart(2, '0');
    }

    function refreshDiagnosticsPanel() {
        var panel = document.getElementById('diagnostics-panel');
        if (!panel || panel.style.display === 'none') return;

        // Session Info
        var sessionIdEl = document.getElementById('diag-session-id');
        if (sessionIdEl) sessionIdEl.textContent = sessionInfo.id ? sessionInfo.id.substring(0, 8) + '...' : '--';

        var durationEl = document.getElementById('diag-duration');
        if (durationEl) durationEl.textContent = formatDuration(Date.now() - sessionStartTime);

        var reconnectsEl = document.getElementById('diag-reconnects');
        if (reconnectsEl) reconnectsEl.textContent = sessionInfo.reconnectCount.toString();

        var debugToggle = document.getElementById('diag-debug-toggle');
        if (debugToggle) {
            debugToggle.textContent = sessionInfo.debugMode ? 'ON' : 'OFF';
            if (sessionInfo.debugMode) {
                debugToggle.classList.add('active');
            } else {
                debugToggle.classList.remove('active');
            }
        }

        var verdictEl = document.getElementById('diag-verdict');
        if (verdictEl) {
            var verdict = generateVerdict();
            verdictEl.textContent = verdict.summary;
        }

        // Live Metrics
        var latencyEl = document.getElementById('diag-latency');
        if (latencyEl) {
            var latest = getLatestLatency();
            var avg = Math.round(getAverageLatency());
            latencyEl.textContent = latest !== null ? latest + 'ms (avg: ' + avg + 'ms)' : '--';
        }

        var uptimeEl = document.getElementById('diag-uptime');
        if (uptimeEl) uptimeEl.textContent = getUptimePercent() + '%';

        var grpcEl = document.getElementById('diag-grpc');
        if (grpcEl) {
            if (cvpStatus.status !== 'UP') {
                grpcEl.textContent = 'Waiting for CVP';
            } else {
                grpcEl.textContent = grpcConnectionStatus.connected
                    ? 'Connected'
                    : 'Disconnected (' + grpcConnectionStatus.failureCount + ' failures)';
            }
        }

        var missedPongsEl = document.getElementById('diag-missed-pongs');
        if (missedPongsEl) missedPongsEl.textContent = wsConnectionStatus.failureCount.toString();

        // External Connectivity
        var extCheckEl = document.getElementById('diag-external');
        if (extCheckEl) {
            if (externalCheckResult.arista === 'ok') {
                extCheckEl.textContent = 'OK (' + externalCheckResult.aristaRttMs + 'ms)';
            } else if (externalCheckResult.arista) {
                extCheckEl.textContent = externalCheckResult.arista;
            } else {
                extCheckEl.textContent = '--';
            }
        }

        var netInfoEl = document.getElementById('diag-network');
        if (netInfoEl) {
            var netInfo = getNetworkInfo();
            if (netInfo) {
                var parts = [];
                if (netInfo.type) parts.push(netInfo.type);
                if (netInfo.effectiveType) parts.push(netInfo.effectiveType);
                if (netInfo.downlinkMbps) parts.push(netInfo.downlinkMbps + ' Mbps');
                if (netInfo.rttMs) parts.push('RTT: ' + netInfo.rttMs + 'ms');
                netInfoEl.textContent = parts.join(' / ') || 'Available but empty';
            } else {
                netInfoEl.textContent = 'Not supported';
            }
        }

        // Event Timeline - use DOM methods for security (no innerHTML with data)
        var timeline = document.getElementById('diag-timeline');
        if (timeline) {
            while (timeline.firstChild) {
                timeline.removeChild(timeline.firstChild);
            }

            if (eventBuffer.length === 0) {
                var emptyDiv = document.createElement('div');
                emptyDiv.className = 'diag-timeline-empty';
                emptyDiv.textContent = 'No events recorded yet';
                timeline.appendChild(emptyDiv);
            } else {
                var startIdx = Math.max(0, eventBuffer.length - 50);
                for (var i = startIdx; i < eventBuffer.length; i++) {
                    var evt = eventBuffer[i];
                    var entry = document.createElement('div');
                    entry.className = 'diag-timeline-entry';

                    var tsSpan = document.createElement('span');
                    tsSpan.className = 'diag-timeline-ts';
                    tsSpan.textContent = formatTimelineTs(evt.ts);

                    var typeSpan = document.createElement('span');
                    typeSpan.className = 'diag-timeline-type ' + getEventClass(evt.type);
                    typeSpan.textContent = evt.type;

                    var dataSpan = document.createElement('span');
                    dataSpan.className = 'diag-timeline-data';
                    if (evt.data) {
                        var keys = Object.keys(evt.data);
                        var parts = [];
                        for (var j = 0; j < keys.length && j < 3; j++) {
                            parts.push(keys[j] + '=' + evt.data[keys[j]]);
                        }
                        dataSpan.textContent = parts.join(' ');
                    }

                    entry.appendChild(tsSpan);
                    entry.appendChild(typeSpan);
                    entry.appendChild(dataSpan);
                    timeline.appendChild(entry);
                }
                timeline.scrollTop = timeline.scrollHeight;
            }
        }
    }

    /**
     * Generate an auto-verdict summarizing connectivity issues for diagnostics export
     * @returns {object} {summary: string, details: string[]}
     */
    function generateVerdict() {
        var issues = [];
        var uptime = getUptimePercent();

        if (uptime >= 99) {
            issues.push('No significant connectivity issues (' + uptime + '% uptime)');
        } else if (uptime >= 95) {
            issues.push('Minor connectivity issues (' + uptime + '% uptime)');
        } else {
            issues.push('Significant connectivity issues (' + uptime + '% uptime)');
        }

        if (grpcConnectionStatus.connected === false && grpcConnectionStatus.failureCount > 0) {
            issues.push('gRPC to CVP is failing (' + grpcConnectionStatus.failureCount + ' failures)');
        }

        if (externalCheckResult.arista === 'failed' || externalCheckResult.arista === 'timeout') {
            issues.push('External connectivity check failed (arista.com unreachable) - likely client-side network issue');
        } else if (externalCheckResult.arista === 'ok' && grpcConnectionStatus.connected === false) {
            issues.push('Internet works but gRPC fails - likely firewall/VPN blocking gRPC traffic');
        }

        var netInfo = getNetworkInfo();
        if (netInfo && netInfo.effectiveType && netInfo.effectiveType !== '4g') {
            issues.push('Degraded network quality: ' + netInfo.effectiveType + ' connection');
        }

        // Check for visibility-triggered reconnects
        var visTriggered = 0;
        var totalReconnects = 0;
        for (var i = 0; i < eventBuffer.length; i++) {
            if (eventBuffer[i].type === 'ws_reconnect') {
                totalReconnects++;
                if (eventBuffer[i].data && eventBuffer[i].data.visibilityTriggered) {
                    visTriggered++;
                }
            }
        }
        if (visTriggered > 0) {
            issues.push(visTriggered + ' of ' + totalReconnects + ' reconnects were triggered by tab visibility change (browser throttling, not real network issues)');
        }

        return {
            summary: issues[0] || 'No data available',
            details: issues
        };
    }

    /**
     * Test CVP gRPC connectivity via proper gRPC-Web framed request
     * Sends a minimal gRPC-Web unary call and validates response framing/trailers
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

        var grpcEndpoint = '/arista.studio.v1.StudioService/GetAll';
        var timeout = CONNECTIVITY_CONFIG.grpc.timeout;

        if (window.ConnectivityDebug) {
            console.log('[Connectivity Monitor] Testing CVP gRPC connectivity (framed request)...');
        }

        // Build minimal gRPC-Web frame: flag(1 byte) + length(4 bytes) + empty protobuf body
        // flag=0x00 (data frame, not compressed), length=0x00000000 (empty body)
        var grpcFrame = new Uint8Array([0x00, 0x00, 0x00, 0x00, 0x00]);

        var controller = new AbortController();
        var timeoutId = setTimeout(function() { controller.abort(); }, timeout);

        // Build headers with auth token if available
        var grpcHeaders = {
            'Content-Type': 'application/grpc-web+proto',
            'Accept': 'application/grpc-web+proto',
            'x-grpc-web': '1'
        };
        if (cvpToken) {
            grpcHeaders['Authorization'] = 'Bearer ' + cvpToken;
        }

        fetch(grpcEndpoint, {
            method: 'POST',
            signal: controller.signal,
            headers: grpcHeaders,
            body: grpcFrame,
            cache: 'no-cache'
        })
        .then(function(response) {
            clearTimeout(timeoutId);
            grpcConnectionStatus.lastCheck = Date.now();

            // Auth failure - refresh token and retry next cycle
            if (response.status === 401 || response.status === 403 || response.status === 405) {
                logConnectivityEvent('GRPC_AUTH_REFRESH', { status: response.status });
                fetchCVPToken(null);
                // Don't count as failure - token may have expired
                return;
            }

            // HTTP-level failures mean the backend is unreachable
            if (response.status === 502 || response.status === 503 || response.status === 504) {
                grpcConnectionStatus.connected = false;
                grpcConnectionStatus.failureCount++;
                grpcConnectionStatus.errorMessage = 'CVP backend unreachable (' + response.status + ')';
                logConnectivityEvent('GRPC_TEST_FAILED', {
                    reason: 'http_error',
                    status: response.status,
                    failureCount: grpcConnectionStatus.failureCount
                });
                updateStatusUI();
                return;
            }

            // Read the response body to validate gRPC framing
            return response.arrayBuffer().then(function(buffer) {
                var bytes = new Uint8Array(buffer);
                var grpcResult = parseGRPCWebResponse(bytes, response.headers);

                if (grpcResult.valid) {
                    grpcConnectionStatus.connected = true;
                    grpcConnectionStatus.failureCount = 0;
                    grpcConnectionStatus.errorMessage = '';
                    logConnectivityEvent('GRPC_TEST_SUCCESS', {
                        grpcStatus: grpcResult.grpcStatus,
                        httpStatus: response.status,
                        hasTrailers: grpcResult.hasTrailers
                    });
                    trackEvent('grpc_ok', { grpcStatus: grpcResult.grpcStatus });
                    sendGRPCCheckResult('ok', { grpcStatus: grpcResult.grpcStatus, httpStatus: response.status });
                } else {
                    grpcConnectionStatus.connected = false;
                    grpcConnectionStatus.failureCount++;
                    grpcConnectionStatus.errorMessage = grpcResult.reason || 'Invalid gRPC response';
                    logConnectivityEvent('GRPC_TEST_FAILED', {
                        reason: grpcResult.reason,
                        httpStatus: response.status,
                        failureCount: grpcConnectionStatus.failureCount
                    });
                    trackEvent('grpc_fail', { reason: grpcResult.reason, failures: grpcConnectionStatus.failureCount });
                    sendGRPCCheckResult('failed', { reason: grpcResult.reason, httpStatus: response.status });
                }
                updateStatusUI();
            });
        })
        .catch(function(error) {
            clearTimeout(timeoutId);
            grpcConnectionStatus.connected = false;
            grpcConnectionStatus.failureCount++;
            grpcConnectionStatus.lastCheck = Date.now();

            if (error.name === 'AbortError') {
                grpcConnectionStatus.errorMessage = 'gRPC timeout (may be blocked)';
            } else if (error.message && (error.message.indexOf('NetworkError') !== -1 ||
                       error.message.indexOf('Failed to fetch') !== -1)) {
                grpcConnectionStatus.errorMessage = 'gRPC blocked';
            } else {
                grpcConnectionStatus.errorMessage = (error.message || 'Unknown error').substring(0, 50);
            }

            logConnectivityEvent('GRPC_TEST_ERROR', {
                error: grpcConnectionStatus.errorMessage,
                failureCount: grpcConnectionStatus.failureCount
            });
            trackEvent('grpc_fail', { reason: grpcConnectionStatus.errorMessage, failures: grpcConnectionStatus.failureCount });
            sendGRPCCheckResult('error', { reason: grpcConnectionStatus.errorMessage });
            updateStatusUI();
        });
    }

    /**
     * Parse a gRPC-Web response to validate framing and extract grpc-status
     * gRPC-Web frames: 1-byte flag + 4-byte big-endian length + payload
     * Flag 0x00 = data frame, 0x80 = trailers frame
     * @param {Uint8Array} bytes - Response body bytes
     * @param {Headers} headers - Response headers
     * @returns {object} {valid: boolean, grpcStatus: number|null, hasTrailers: boolean, reason: string}
     */
    function parseGRPCWebResponse(bytes, headers) {
        // Empty response with grpc-status in HTTP headers is valid
        var headerGrpcStatus = headers.get('grpc-status');
        if (headerGrpcStatus !== null) {
            var statusCode = parseInt(headerGrpcStatus, 10);
            // 0=OK, 7=PERMISSION_DENIED, 16=UNAUTHENTICATED all prove gRPC works
            if (statusCode === 0 || statusCode === 7 || statusCode === 16) {
                return { valid: true, grpcStatus: statusCode, hasTrailers: false, reason: '' };
            }
            // 14=UNAVAILABLE means gRPC server is down
            if (statusCode === 14) {
                return { valid: false, grpcStatus: statusCode, hasTrailers: false, reason: 'gRPC server unavailable (status 14)' };
            }
            // Other valid gRPC status codes still prove the stack works
            return { valid: true, grpcStatus: statusCode, hasTrailers: false, reason: '' };
        }

        // Need at least 5 bytes for a gRPC frame header
        if (bytes.length < 5) {
            return { valid: false, grpcStatus: null, hasTrailers: false, reason: 'Response too short for gRPC frame' };
        }

        // Check first byte is a valid gRPC frame flag
        var flag = bytes[0];
        if (flag !== 0x00 && flag !== 0x80) {
            return { valid: false, grpcStatus: null, hasTrailers: false, reason: 'Invalid gRPC frame flag: 0x' + flag.toString(16) };
        }

        var hasTrailers = false;

        // Walk through gRPC frames looking for trailers
        var offset = 0;
        while (offset + 5 <= bytes.length) {
            var frameFlag = bytes[offset];
            var frameLen = (bytes[offset + 1] << 24) | (bytes[offset + 2] << 16) |
                           (bytes[offset + 3] << 8) | bytes[offset + 4];

            if (frameFlag === 0x80) {
                hasTrailers = true;
                // Parse trailers text to find grpc-status
                var trailerBytes = bytes.slice(offset + 5, offset + 5 + frameLen);
                var trailerText = '';
                for (var i = 0; i < trailerBytes.length; i++) {
                    trailerText += String.fromCharCode(trailerBytes[i]);
                }
                var statusMatch = trailerText.match(/grpc-status:\s*(\d+)/);
                if (statusMatch) {
                    var trailStatus = parseInt(statusMatch[1], 10);
                    if (trailStatus === 0 || trailStatus === 7 || trailStatus === 16) {
                        return { valid: true, grpcStatus: trailStatus, hasTrailers: true, reason: '' };
                    }
                    if (trailStatus === 14) {
                        return { valid: false, grpcStatus: trailStatus, hasTrailers: true, reason: 'gRPC server unavailable (status 14)' };
                    }
                    return { valid: true, grpcStatus: trailStatus, hasTrailers: true, reason: '' };
                }
            }

            offset += 5 + frameLen;
            if (frameLen === 0 && offset >= bytes.length) break;
        }

        // Valid gRPC frame structure even without grpc-status trailer
        return { valid: true, grpcStatus: null, hasTrailers: hasTrailers, reason: '' };
    }

    /**
     * Fetch a CVP authentication token for gRPC-Web requests
     * Instead of scraping DOM for password, use token from backend session_info
     * The backend now sends cvp_token in session_info and token_refresh messages
     * @param {function} callback - Called with true on success, false on failure
     */
    function fetchCVPToken(callback) {
        // Use token from backend session_info (sent via session_info and token_refresh messages)
        if (sessionInfo.cvpToken) {
            cvpToken = sessionInfo.cvpToken;
            if (callback) callback(true);
            return;
        }
        // Fallback: if no token from backend yet, skip
        console.warn('[Connectivity Monitor] No CVP token available from backend');
        if (callback) callback(false);
    }

    /**
     * Start gRPC monitoring (called when CVP becomes UP)
     * Fetches a CVP token first, then begins periodic gRPC checks
     */
    function startGRPCMonitoring() {
        if (grpcConnectionStatus.monitoringStarted) {
            return; // Already started
        }

        console.log('[Connectivity Monitor] CVP is UP - fetching token and starting gRPC monitoring');
        grpcConnectionStatus.monitoringStarted = true;

        // Fetch CVP token, then start checks
        fetchCVPToken(function(success) {
            if (!success) {
                console.warn('[Connectivity Monitor] Starting gRPC checks without token (may fail auth)');
            }

            // Initial test after short delay
            setTimeout(function() {
                testCVPConnectivity();
            }, 1000);

            // Periodic checks
            grpcMonitorInterval = setInterval(function() {
                testCVPConnectivity();
            }, CONNECTIVITY_CONFIG.grpc.retryInterval);
        });

        // Token refresh is now handled by the backend via token_refresh WebSocket messages
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

        // Recover any events from localStorage (from previous offline session)
        var recovered = loadFromLocalStorage();
        if (recovered.length > 0) {
            console.log('[Connectivity Monitor] Recovered ' + recovered.length + ' events from localStorage');
            eventBuffer = recovered.concat(eventBuffer);
        }

        // Track page visibility changes
        document.addEventListener('visibilitychange', function() {
            if (document.hidden) {
                pageVisible = false;
                trackEvent('page_hidden', {});
            } else {
                pageVisible = true;
                trackEvent('page_visible', {});
            }
        });

        // Track browser online/offline events — fires instantly when WiFi is disabled
        window.addEventListener('offline', function() {
            trackEvent('browser_offline', {});
            saveToLocalStorage();  // Immediate save — critical event
        });
        window.addEventListener('online', function() {
            trackEvent('browser_online', {});
        });

        // Keyboard shortcut: Ctrl+Shift+D to toggle diagnostics panel
        document.addEventListener('keydown', function(e) {
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                e.preventDefault();
                toggleDiagnosticsPanel();
            }
        });

        // URL parameter: ?debug=connectivity opens panel on load
        var urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('debug') === 'connectivity') {
            setTimeout(showDiagnosticsPanel, 500);
        }

        // Diagnostics panel button handlers
        var diagCloseBtn = document.getElementById('diag-close-btn');
        if (diagCloseBtn) {
            diagCloseBtn.addEventListener('click', hideDiagnosticsPanel);
        }

        var diagExportBtn = document.getElementById('diag-export-btn');
        if (diagExportBtn) {
            diagExportBtn.addEventListener('click', function() {
                if (window.ConnectivityMonitor && typeof window.ConnectivityMonitor.exportDiagnostics === 'function') {
                    window.ConnectivityMonitor.exportDiagnostics();
                }
            });
        }

        var diagDebugToggle = document.getElementById('diag-debug-toggle');
        if (diagDebugToggle) {
            diagDebugToggle.addEventListener('click', function() {
                if (window.ConnectivityMonitor && typeof window.ConnectivityMonitor.toggleDebug === 'function') {
                    window.ConnectivityMonitor.toggleDebug();
                }
            });
        }

        var diagClearBtn = document.getElementById('diag-clear-btn');
        if (diagClearBtn) {
            diagClearBtn.addEventListener('click', function() {
                eventBuffer = [];
                clearLocalStorage();
                refreshDiagnosticsPanel();
            });
        }

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
            var wasConnected = wsConnectionStatus.connected;
            wsConnectionStatus.connected = connected;

            if (connected) {
                wsConnectionStatus.lastSuccessfulPing = Date.now();
                wsConnectionStatus.failureCount = 0;
                logConnectivityEvent('WS_CONNECTED', { uptime: performance.now() });

                if (wasConnected === false) {
                    var now = Date.now();

                    // Check if reconnect was triggered by page becoming visible
                    var visibilityTriggered = false;
                    for (var k = eventBuffer.length - 1; k >= Math.max(0, eventBuffer.length - 5); k--) {
                        if (eventBuffer[k].type === 'page_visible' && (now - eventBuffer[k].ts) < 5000) {
                            visibilityTriggered = true;
                            break;
                        }
                    }
                    trackEvent('ws_reconnect', { reconnectTime: now, visibilityTriggered: visibilityTriggered });

                    if (lastDisconnectTime !== null) {
                        var offlineEvents = loadFromLocalStorage();
                        var offlineDuration = now - lastDisconnectTime;
                        // Send explicit disconnect event to backend for GCP logging
                        setTimeout(function() {
                            if (typeof ws !== 'undefined' && ws.readyState === WebSocket.OPEN) {
                                try {
                                    ws.send(JSON.stringify({
                                        type: 'connectivity',
                                        data: {
                                            event: 'state_change',
                                            changeType: 'disconnect_recovered',
                                            detail: 'offline=' + offlineDuration + 'ms browserOnline=' + navigator.onLine
                                        }
                                    }));
                                } catch (e) {}
                            }
                            sendReconnectReport(lastDisconnectTime, now, offlineEvents);
                        }, 2000);
                        lastDisconnectTime = null;
                    }
                    startSummaryTimer();

                    // Restart gRPC monitoring if it was running
                    if (grpcConnectionStatus.monitoringStarted && !grpcMonitorInterval) {
                        grpcMonitorInterval = setInterval(function() {
                            testCVPConnectivity();
                        }, CONNECTIVITY_CONFIG.grpc.retryInterval);
                    }
                } else if (wasConnected === null) {
                    startSummaryTimer();
                }
            } else {
                wsConnectionStatus.failureCount++;
                logConnectivityEvent('WS_DISCONNECTED', {
                    failureCount: wsConnectionStatus.failureCount
                });
                trackEvent('ws_disconnect', {
                    failureCount: wsConnectionStatus.failureCount,
                    browserOnline: navigator.onLine
                });
                lastDisconnectTime = Date.now();
                // Save immediately on disconnect — don't debounce critical events
                saveToLocalStorage();
                stopSummaryTimer();

                // Pause gRPC checks while WebSocket is down
                if (grpcMonitorInterval) {
                    clearInterval(grpcMonitorInterval);
                    grpcMonitorInterval = null;
                }
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
        hidePopup: hideStatusPopup,

        updateSessionInfo: function(data) {
            sessionInfo.id = data.session_id || null;
            sessionInfo.reconnectCount = data.reconnect_count || 0;
            sessionInfo.debugMode = data.debug_mode || false;
            sessionInfo.cvpToken = data.cvp_token || null;
            if (data.cvp_token) {
                cvpToken = data.cvp_token;
            }
            logConnectivityEvent('SESSION_INFO', data);
        },

        updateDebugMode: function(debugMode) {
            sessionInfo.debugMode = debugMode;
        },

        updateCVPToken: function(token) {
            cvpToken = token;
            sessionInfo.cvpToken = token;
            logConnectivityEvent('CVP_TOKEN_REFRESHED', {});
            trackEvent('cvp_token_refresh', {});
        },

        recordLatency: recordLatency,

        /**
         * Handle ping data from backend, triggering synchronized gRPC checks
         * When the backend includes its internal gRPC status in a ping,
         * run an immediate client-side gRPC check for comparison.
         * @param {Object} data - Ping data object from backend
         */
        handlePingData: function(data) {
            if (data && data.internal_grpc) {
                // Run immediate client gRPC check for comparison
                testCVPConnectivity();
                trackEvent('grpc_sync_check', {
                    internal: data.internal_grpc,
                    clientConnected: grpcConnectionStatus.connected
                });
            }
        },

        getTrackerData: function() {
            return {
                sessionInfo: sessionInfo,
                eventBuffer: eventBuffer,
                latencyHistory: latencyHistory,
                sessionStartTime: sessionStartTime,
                uptimePercent: getUptimePercent(),
                latestLatency: getLatestLatency(),
                averageLatency: Math.round(getAverageLatency())
            };
        },

        toggleDebug: function() {
            if (typeof ws === 'undefined' || ws.readyState !== WebSocket.OPEN) return;
            try {
                ws.send(JSON.stringify({ type: 'debug_toggle', data: {} }));
            } catch (e) {}
        },

        exportDiagnostics: function() {
            var data = {
                exportedAt: new Date().toISOString(),
                sessionInfo: sessionInfo,
                sessionStartTime: new Date(sessionStartTime).toISOString(),
                uptimePercent: getUptimePercent(),
                latencyStats: {
                    latest: getLatestLatency(),
                    average: Math.round(getAverageLatency()),
                    samples: latencyHistory.length
                },
                wsStatus: {
                    connected: wsConnectionStatus.connected,
                    failureCount: wsConnectionStatus.failureCount
                },
                grpcStatus: {
                    connected: grpcConnectionStatus.connected,
                    failureCount: grpcConnectionStatus.failureCount,
                    errorMessage: grpcConnectionStatus.errorMessage
                },
                cvpStatus: {
                    status: cvpStatus.status,
                    version: cvpStatus.version
                },
                externalConnectivity: {
                    status: externalCheckResult.arista,
                    rttMs: externalCheckResult.aristaRttMs,
                    lastCheck: externalCheckResult.lastCheck ? new Date(externalCheckResult.lastCheck).toISOString() : null
                },
                networkInfo: getNetworkInfo(),
                verdict: generateVerdict(),
                events: eventBuffer,
                latencyHistory: latencyHistory
            };
            var blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
            var url = URL.createObjectURL(blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = 'connectivity-report-' + (sessionInfo.id || 'unknown') + '-' + Date.now() + '.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            URL.revokeObjectURL(url);
        }
    };

    // Auto-initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initConnectivityBadge);
    } else {
        initConnectivityBadge();
    }
})();

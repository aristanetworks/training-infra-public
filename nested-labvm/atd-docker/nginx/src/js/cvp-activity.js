/**
 * CVP Activity Monitor - "Are you still there?" prompt for CVP pages
 *
 * CVP continuously streams data via gRPC/WebSocket, preventing the system's
 * idle timer from detecting inactive users. This module tracks actual user
 * activity and prompts after inactivity.
 *
 * Configuration:
 *   - Inactivity timeout: 60 minutes
 *   - Response timeout: 3 minutes
 *   - On timeout: Redirect to homepage
 *   - On confirm: Close prompt, reset timer
 *
 * Note: Uses a Web Worker for reliable timing in background tabs. Browsers
 * throttle setTimeout/setInterval in background tabs, but Web Workers are
 * less affected.
 */
(function() {
    'use strict';

    // Prevent duplicate initialization
    if (window.__cvpActivityMonitorInjected) {
        console.log('[CVP Activity] Already initialized, skipping');
        return;
    }
    window.__cvpActivityMonitorInjected = true;

    // Skip if not in top-level window
    if (window.self !== window.top) {
        console.log('[CVP Activity] Running in iframe, skipping');
        return;
    }

    // Debug mode: add ?cvp-test=1 to URL for 10-second timeouts
    const DEBUG_MODE = new URLSearchParams(window.location.search).has('cvp-test');

    // Configuration
    const INACTIVITY_TIMEOUT_MS = DEBUG_MODE ? 10 * 1000 : 60 * 60 * 1000;  // 10s debug, 60min prod
    const RESPONSE_TIMEOUT_MS = DEBUG_MODE ? 10 * 1000 : 3 * 60 * 1000;     // 10s debug, 3min prod
    const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'];
    const ACTIVITY_THROTTLE_MS = 1000;             // Throttle activity detection
    const WORKER_CHECK_INTERVAL_MS = 1000;         // Worker checks every second

    // State
    let worker = null;
    let inactivityDeadline = null;
    let responseDeadline = null;
    let modalVisible = false;

    /**
     * Create an inline Web Worker for reliable background timing
     * Web Workers are less throttled than main thread in background tabs
     */
    function createTimerWorker() {
        const workerCode = `
            let checkInterval = null;
            let inactivityDeadline = null;
            let responseDeadline = null;

            self.onmessage = function(e) {
                const { type, deadline, interval } = e.data;

                if (type === 'setInactivityDeadline') {
                    inactivityDeadline = deadline;
                } else if (type === 'setResponseDeadline') {
                    responseDeadline = deadline;
                } else if (type === 'clearResponseDeadline') {
                    responseDeadline = null;
                } else if (type === 'start') {
                    if (checkInterval) clearInterval(checkInterval);
                    checkInterval = setInterval(function() {
                        const now = Date.now();

                        // Check response deadline first (modal countdown)
                        if (responseDeadline && now >= responseDeadline) {
                            self.postMessage({ type: 'responseTimeout' });
                            responseDeadline = null;
                        } else if (responseDeadline) {
                            self.postMessage({
                                type: 'responseCountdown',
                                remaining: responseDeadline - now
                            });
                        }
                        // Check inactivity deadline (show modal)
                        else if (inactivityDeadline && now >= inactivityDeadline) {
                            self.postMessage({ type: 'inactivityTimeout' });
                            inactivityDeadline = null;
                        }
                    }, interval || 1000);
                } else if (type === 'stop') {
                    if (checkInterval) {
                        clearInterval(checkInterval);
                        checkInterval = null;
                    }
                }
            };
        `;

        const blob = new Blob([workerCode], { type: 'application/javascript' });
        const workerUrl = URL.createObjectURL(blob);
        const timerWorker = new Worker(workerUrl);
        URL.revokeObjectURL(workerUrl);

        return timerWorker;
    }

    /**
     * Inject CSS for the modal
     */
    function injectCSS() {
        const css = `
            /* CVP Activity Monitor Modal Overlay */
            .cvp-activity-overlay {
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(0, 0, 0, 0.7);
                z-index: 200000;
                display: flex;
                align-items: center;
                justify-content: center;
                opacity: 0;
                visibility: hidden;
                transition: opacity 0.3s ease, visibility 0.3s ease;
            }

            .cvp-activity-overlay.visible {
                opacity: 1;
                visibility: visible;
            }

            /* Modal Container */
            .cvp-activity-modal {
                background: linear-gradient(135deg, #04152a 0%, #071c35 100%);
                border: 2px solid #fbb500;
                border-radius: 8px;
                padding: 32px 40px;
                max-width: 420px;
                width: 90%;
                text-align: center;
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.5);
                transform: scale(0.9);
                transition: transform 0.3s ease;
            }

            .cvp-activity-overlay.visible .cvp-activity-modal {
                transform: scale(1);
            }

            /* Modal Icon */
            .cvp-activity-icon {
                font-size: 48px;
                margin-bottom: 16px;
            }

            /* Modal Title */
            .cvp-activity-title {
                color: #fbb500;
                font-size: 22px;
                font-weight: 600;
                margin-bottom: 12px;
            }

            /* Modal Message */
            .cvp-activity-message {
                color: rgba(255, 255, 255, 0.85);
                font-size: 14px;
                line-height: 1.5;
                margin-bottom: 8px;
            }

            /* Countdown Display */
            .cvp-activity-countdown {
                color: #ff6b6b;
                font-size: 13px;
                font-weight: 600;
                margin-bottom: 24px;
            }

            .cvp-activity-countdown-value {
                font-family: 'Courier New', monospace;
                font-size: 16px;
            }

            /* Confirm Button */
            .cvp-activity-button {
                background: #fbb500;
                color: #04152a;
                border: none;
                border-radius: 4px;
                padding: 14px 32px;
                font-size: 15px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s ease;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }

            .cvp-activity-button:hover {
                background: #ffc72c;
                transform: translateY(-1px);
                box-shadow: 0 4px 12px rgba(251, 181, 0, 0.4);
            }

            .cvp-activity-button:active {
                transform: translateY(0);
            }
        `;

        const style = document.createElement('style');
        style.id = 'cvp-activity-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    /**
     * Create and inject the modal HTML
     */
    function createModal() {
        const overlay = document.createElement('div');
        overlay.className = 'cvp-activity-overlay';
        overlay.id = 'cvpActivityOverlay';

        overlay.innerHTML = `
            <div class="cvp-activity-modal">
                <div class="cvp-activity-icon">👋</div>
                <div class="cvp-activity-title">Are you still there?</div>
                <div class="cvp-activity-message">
                    Your session has been inactive. Click below to continue using CloudVision.
                </div>
                <div class="cvp-activity-countdown">
                    Redirecting to homepage in <span class="cvp-activity-countdown-value" id="cvpCountdown">3:00</span>
                </div>
                <button class="cvp-activity-button" id="cvpStillHereBtn">
                    Yes, I'm still here
                </button>
            </div>
        `;

        document.body.appendChild(overlay);

        // Attach button handler
        document.getElementById('cvpStillHereBtn').addEventListener('click', handleConfirm);
    }

    /**
     * Format milliseconds as M:SS
     */
    function formatCountdown(ms) {
        const totalSeconds = Math.max(0, Math.ceil(ms / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${minutes}:${String(seconds).padStart(2, '0')}`;
    }

    /**
     * Handle messages from the Web Worker
     */
    function handleWorkerMessage(e) {
        const { type, remaining } = e.data;

        switch (type) {
            case 'inactivityTimeout':
                console.log('[CVP Activity] Inactivity timeout from worker');
                showModal();
                break;

            case 'responseTimeout':
                console.log('[CVP Activity] Response timeout from worker');
                handleTimeout();
                break;

            case 'responseCountdown':
                // Update countdown display
                const countdown = document.getElementById('cvpCountdown');
                if (countdown) {
                    countdown.textContent = formatCountdown(remaining);
                }
                break;
        }
    }

    /**
     * Show the "Are you still there?" modal
     */
    function showModal() {
        if (modalVisible) return;
        modalVisible = true;

        console.log('[CVP Activity] Showing inactivity prompt');

        const overlay = document.getElementById('cvpActivityOverlay');
        const countdown = document.getElementById('cvpCountdown');

        if (!overlay || !countdown) return;

        // Set response deadline and tell worker
        responseDeadline = Date.now() + RESPONSE_TIMEOUT_MS;
        countdown.textContent = formatCountdown(RESPONSE_TIMEOUT_MS);

        if (worker) {
            worker.postMessage({ type: 'setResponseDeadline', deadline: responseDeadline });
        }

        overlay.classList.add('visible');
    }

    /**
     * Hide the modal
     */
    function hideModal() {
        modalVisible = false;
        responseDeadline = null;

        if (worker) {
            worker.postMessage({ type: 'clearResponseDeadline' });
        }

        const overlay = document.getElementById('cvpActivityOverlay');
        if (overlay) {
            overlay.classList.remove('visible');
        }
    }

    /**
     * Handle user confirming they're still there
     */
    function handleConfirm() {
        console.log('[CVP Activity] User confirmed presence');
        hideModal();
        resetActivityTimer();
    }

    /**
     * Handle timeout - user didn't respond
     */
    function handleTimeout() {
        console.log('[CVP Activity] Response timeout - redirecting to homepage');
        hideModal();

        // Stop the worker
        if (worker) {
            worker.postMessage({ type: 'stop' });
            worker.terminate();
            worker = null;
        }

        // Redirect to homepage
        window.location.href = '/';
    }

    /**
     * Reset the inactivity timer
     */
    function resetActivityTimer() {
        inactivityDeadline = Date.now() + INACTIVITY_TIMEOUT_MS;

        if (worker) {
            worker.postMessage({ type: 'setInactivityDeadline', deadline: inactivityDeadline });
        }
    }

    /**
     * Handle user activity (throttled)
     */
    let activityThrottled = false;
    function handleActivity() {
        // Don't track activity while modal is visible
        if (modalVisible) return;

        // Throttle activity detection
        if (activityThrottled) return;
        activityThrottled = true;

        setTimeout(() => {
            activityThrottled = false;
        }, ACTIVITY_THROTTLE_MS);

        resetActivityTimer();
    }

    /**
     * Initialize the activity monitor
     */
    async function init() {
        // Check if cvpactivity feature is enabled
        if (window.featureFlags && !await window.featureFlags.check('cvpactivity')) {
            console.log('[CVP Activity] Feature is disabled via feature flags');
            return;
        }

        if (DEBUG_MODE) {
            console.log('[CVP Activity] DEBUG MODE ENABLED - using 10 second timeouts');
        }
        console.log('[CVP Activity] Initializing activity monitor');
        console.log('[CVP Activity] Inactivity timeout:', INACTIVITY_TIMEOUT_MS / 1000, 'seconds');
        console.log('[CVP Activity] Response timeout:', RESPONSE_TIMEOUT_MS / 1000, 'seconds');

        // Inject styles
        injectCSS();

        // Create modal
        createModal();

        // Create Web Worker for reliable background timing
        try {
            worker = createTimerWorker();
            worker.onmessage = handleWorkerMessage;
            worker.onerror = (err) => {
                console.error('[CVP Activity] Worker error:', err);
            };
            console.log('[CVP Activity] Web Worker created for background timing');
        } catch (err) {
            console.error('[CVP Activity] Failed to create Web Worker:', err);
            // Fallback: will rely on visibilitychange, less reliable but better than nothing
        }

        // Attach activity listeners
        ACTIVITY_EVENTS.forEach(eventType => {
            document.addEventListener(eventType, handleActivity, { passive: true });
        });

        // Start the inactivity timer
        resetActivityTimer();

        // Start the worker
        if (worker) {
            worker.postMessage({ type: 'start', interval: WORKER_CHECK_INTERVAL_MS });
        }

        // Expose debug functions on window for console testing
        window.cvpActivityDebug = {
            showModal: showModal,
            hideModal: hideModal,
            resetTimer: resetActivityTimer,
            getDeadline: function() {
                return {
                    inactivityDeadline: inactivityDeadline,
                    responseDeadline: responseDeadline,
                    remainingInactivity: inactivityDeadline ? inactivityDeadline - Date.now() : null,
                    remainingResponse: responseDeadline ? responseDeadline - Date.now() : null
                };
            },
            trigger: function() {
                console.log('[CVP Activity] Manually triggered via console');
                showModal();
            }
        };

        console.log('[CVP Activity] Activity monitor started');
        console.log('[CVP Activity] Debug: use window.cvpActivityDebug.trigger() to test modal');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

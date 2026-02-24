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

    // Configuration
    const INACTIVITY_TIMEOUT_MS = 60 * 60 * 1000;  // 60 minutes
    const RESPONSE_TIMEOUT_MS = 3 * 60 * 1000;     // 3 minutes
    const ACTIVITY_EVENTS = ['mousemove', 'keydown', 'click', 'scroll', 'touchstart'];
    const ACTIVITY_THROTTLE_MS = 1000;             // Throttle activity detection

    // State
    let inactivityTimer = null;
    let responseTimer = null;
    let modalVisible = false;

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
     * Show the "Are you still there?" modal
     */
    function showModal() {
        if (modalVisible) return;
        modalVisible = true;

        console.log('[CVP Activity] Showing inactivity prompt');

        const overlay = document.getElementById('cvpActivityOverlay');
        const countdown = document.getElementById('cvpCountdown');

        if (!overlay || !countdown) return;

        overlay.classList.add('visible');

        // Start countdown
        let remaining = RESPONSE_TIMEOUT_MS;
        countdown.textContent = formatCountdown(remaining);

        responseTimer = setInterval(() => {
            remaining -= 1000;
            countdown.textContent = formatCountdown(remaining);

            if (remaining <= 0) {
                handleTimeout();
            }
        }, 1000);
    }

    /**
     * Hide the modal
     */
    function hideModal() {
        modalVisible = false;

        const overlay = document.getElementById('cvpActivityOverlay');
        if (overlay) {
            overlay.classList.remove('visible');
        }

        if (responseTimer) {
            clearInterval(responseTimer);
            responseTimer = null;
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

        // Clear all timers
        if (inactivityTimer) {
            clearTimeout(inactivityTimer);
            inactivityTimer = null;
        }

        // Redirect to homepage
        window.location.href = '/';
    }

    /**
     * Reset the inactivity timer
     */
    function resetActivityTimer() {
        if (inactivityTimer) {
            clearTimeout(inactivityTimer);
        }

        inactivityTimer = setTimeout(() => {
            showModal();
        }, INACTIVITY_TIMEOUT_MS);
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
    function init() {
        console.log('[CVP Activity] Initializing activity monitor');
        console.log('[CVP Activity] Inactivity timeout: 60 minutes');
        console.log('[CVP Activity] Response timeout: 3 minutes');

        // Inject styles
        injectCSS();

        // Create modal
        createModal();

        // Attach activity listeners
        ACTIVITY_EVENTS.forEach(eventType => {
            document.addEventListener(eventType, handleActivity, { passive: true });
        });

        // Start the inactivity timer
        resetActivityTimer();

        console.log('[CVP Activity] Activity monitor started');
    }

    // Initialize when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

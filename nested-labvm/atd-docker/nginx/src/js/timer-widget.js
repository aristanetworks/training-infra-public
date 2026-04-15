(function() {
    'use strict';

    // Inject timer widget HTML
    const timerHTML = `
        <div id="atd-countdown-timer" draggable="true">
            <div class="timer-header">
                <div class="timer-label">Time Remaining</div>
                <div class="drag-hint">⋮⋮</div>
            </div>
            <div class="timer-value">--:--:--</div>
        </div>
    `;

    // Inject CSS - matching uilanding color scheme
    const timerCSS = `
        #atd-countdown-timer {
            position: fixed;
            top: 10px;
            right: 10px;
            background: linear-gradient(135deg, #04152a 0%, #071a33 100%);
            border: 2px solid #fbb500;
            color: white;
            padding: 12px 18px;
            border-radius: 8px;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            font-size: 14px;
            font-weight: 600;
            z-index: 999999;
            box-shadow: 0 4px 16px rgba(251, 181, 0, 0.3);
            min-width: 140px;
            text-align: center;
            cursor: move;
            transition: box-shadow 0.3s ease;
        }

        #atd-countdown-timer:hover {
            box-shadow: 0 6px 20px rgba(251, 181, 0, 0.5);
        }

        #atd-countdown-timer.dragging {
            opacity: 0.8;
            box-shadow: 0 8px 24px rgba(251, 181, 0, 0.6);
        }

        #atd-countdown-timer.exam-mode {
            border-color: #78d82c;
            box-shadow: 0 4px 16px rgba(120, 216, 44, 0.3);
        }

        #atd-countdown-timer.exam-mode:hover {
            box-shadow: 0 6px 20px rgba(120, 216, 44, 0.5);
        }

        #atd-countdown-timer.warning {
            border-color: #ff6b6b;
            animation: pulse 2s infinite;
        }

        #atd-countdown-timer.expired {
            background: linear-gradient(135deg, #1a0a0a 0%, #2a0505 100%);
            border-color: #ff6b6b;
        }

        @keyframes pulse {
            0%, 100% {
                box-shadow: 0 4px 16px rgba(255, 107, 107, 0.3);
            }
            50% {
                box-shadow: 0 6px 20px rgba(255, 107, 107, 0.6);
            }
        }

        .timer-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 4px;
        }

        .timer-label {
            font-size: 10px;
            opacity: 0.9;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #fbb500;
        }

        .drag-hint {
            font-size: 14px;
            opacity: 0.5;
            color: #fbb500;
            cursor: move;
        }

        .timer-value {
            font-size: 22px;
            font-weight: 700;
            letter-spacing: 1.5px;
            font-family: 'Courier New', monospace;
            color: #ffffff;
        }

        .timer-error {
            font-size: 11px;
            opacity: 0.9;
            color: #ff6b6b;
        }
    `;

    const UPTIME_ENDPOINT = '/uptimeWithRuntime';
    const UPDATE_INTERVAL = 1000; // 1 second
    const RETRY_DELAY = 5000; // 5 seconds on error
    const WARNING_THRESHOLD = 300; // 5 minutes in seconds

    let timerElement = null;
    let timerValue = null;
    let timerLabel = null;
    let updateInterval = null;
    let isExamMode = false;
    let examEndTime = null;
    let labRuntime = null; // in hours

    // Dragging state
    let isDragging = false;
    let currentX = 0;
    let currentY = 0;
    let initialX = 0;
    let initialY = 0;
    let xOffset = 0;
    let yOffset = 0;

    // Format seconds to HH:MM:SS
    function formatTime(seconds) {
        if (seconds < 0) return '00:00:00';

        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        return [
            String(hours).padStart(2, '0'),
            String(minutes).padStart(2, '0'),
            String(secs).padStart(2, '0')
        ].join(':');
    }

    // Fetch timer data from uptime endpoint
    async function fetchTimerData() {
        try {
            const response = await fetch(UPTIME_ENDPOINT, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json'
                },
                cache: 'no-cache'
            });

            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();
            return data;
        } catch (error) {
            console.error('[ATD Timer] Failed to fetch timer data:', error);
            throw error;
        }
    }

    // Calculate time remaining
    function calculateTimeRemaining(data) {
        const currentTime = Math.floor(Date.now() / 1000);
        let timeRemaining = 0;

        // Check if exam mode is active (exam_end_time > 0 means exam has started)
        if (data.exam_end_time && data.exam_end_time > 0) {
            // Exam mode: Use absolute end time from server
            if (!isExamMode) {
                // First time detecting exam mode
                isExamMode = true;
                timerElement.classList.add('exam-mode');
                console.log('[ATD Timer] Exam mode detected');
            }
            examEndTime = data.exam_end_time;
            timeRemaining = examEndTime - currentTime;
        } else if (data.boottime && data.boottime > 0) {
            // Normal lab mode: Use boottime + runtime
            // Use runtime from data if available, otherwise use stored value or default to 12 hours
            const runtime = data.runtime || labRuntime || 12;

            // Store runtime for future use if we got it from the server
            if (data.runtime && data.runtime !== labRuntime) {
                labRuntime = data.runtime;
            }

            const expirationTime = data.boottime + (runtime * 60 * 60);
            timeRemaining = expirationTime - currentTime;
        } else {
            // Boottime is 0 or missing - lab is still initializing
            return null; // Return null to indicate loading state
        }

        return timeRemaining;
    }

    // Update timer display
    function updateTimer(timeRemaining) {
        if (!timerValue) return;

        const formattedTime = formatTime(timeRemaining);
        timerValue.textContent = formattedTime;
        timerValue.classList.remove('timer-error');

        // Update visual state based on time remaining
        if (timeRemaining <= 0) {
            timerElement.classList.add('expired');
            timerElement.classList.remove('warning');
            timerLabel.textContent = isExamMode ? 'Exam Ended' : 'Lab Expired';
        } else if (timeRemaining <= WARNING_THRESHOLD) {
            timerElement.classList.add('warning');
            timerElement.classList.remove('expired');
            timerLabel.textContent = 'Time Remaining';
        } else {
            timerElement.classList.remove('warning', 'expired');
            timerLabel.textContent = 'Time Remaining';
        }
    }

    // Show error state
    function showError(message) {
        if (!timerValue || !timerLabel) return;

        timerValue.textContent = 'Error';
        timerValue.classList.add('timer-error');
        timerLabel.textContent = message || 'Connection Error';
        console.error('[ATD Timer]', message);
    }

    // Main update loop
    async function update() {
        try {
            const data = await fetchTimerData();

            // Calculate and display time remaining
            const timeRemaining = calculateTimeRemaining(data);

            if (timeRemaining === null) {
                // Lab is still initializing, show loading state
                if (timerValue) {
                    timerValue.textContent = 'Loading...';
                    timerValue.classList.remove('timer-error');
                    timerLabel.textContent = 'Initializing';
                }
            } else {
                updateTimer(timeRemaining);
            }

        } catch (error) {
            showError('Failed to fetch');

            // Retry after delay
            if (updateInterval) {
                clearInterval(updateInterval);
            }
            setTimeout(() => {
                startTimer();
            }, RETRY_DELAY);
        }
    }

    // Start timer updates
    function startTimer() {
        // Initial update
        update();

        // Set up interval for regular updates
        if (updateInterval) {
            clearInterval(updateInterval);
        }
        updateInterval = setInterval(update, UPDATE_INTERVAL);
    }

    // Dragging functionality
    function dragStart(e) {
        if (e.type === "touchstart") {
            initialX = e.touches[0].clientX - xOffset;
            initialY = e.touches[0].clientY - yOffset;
        } else {
            initialX = e.clientX - xOffset;
            initialY = e.clientY - yOffset;
        }

        if (e.target === timerElement || e.target.closest('#atd-countdown-timer')) {
            isDragging = true;
            timerElement.classList.add('dragging');
        }
    }

    function drag(e) {
        if (isDragging) {
            e.preventDefault();

            if (e.type === "touchmove") {
                currentX = e.touches[0].clientX - initialX;
                currentY = e.touches[0].clientY - initialY;
            } else {
                currentX = e.clientX - initialX;
                currentY = e.clientY - initialY;
            }

            xOffset = currentX;
            yOffset = currentY;

            setTranslate(currentX, currentY, timerElement);
        }
    }

    function dragEnd() {
        initialX = currentX;
        initialY = currentY;
        isDragging = false;
        if (timerElement) {
            timerElement.classList.remove('dragging');
        }
    }

    function setTranslate(xPos, yPos, el) {
        el.style.transform = `translate3d(${xPos}px, ${yPos}px, 0)`;
    }

    // Terminal page inline timer references
    let inlineTimerValue = null;
    let inlineTimerWidget = null;
    let isTerminalMode = false;

    // Update terminal page's inline timer widget
    function updateInlineTimer(timeRemaining) {
        if (!inlineTimerValue) return;

        const formattedTime = formatTime(timeRemaining);
        inlineTimerValue.textContent = formattedTime;

        if (timeRemaining <= 0) {
            inlineTimerWidget.classList.add('expired');
            inlineTimerWidget.classList.remove('warning');
        } else if (timeRemaining <= WARNING_THRESHOLD) {
            inlineTimerWidget.classList.add('warning');
            inlineTimerWidget.classList.remove('expired');
        } else {
            inlineTimerWidget.classList.remove('warning', 'expired');
        }
    }

    // Initialize timer
    function init() {
        // Only show timer in top-level window, not in iframes
        if (window !== window.top) {
            console.log('[ATD Timer] Skipping timer in iframe');
            return;
        }

        // Check if terminal page has an inline timer widget
        inlineTimerValue = document.getElementById('timeRemainingValue');
        inlineTimerWidget = document.getElementById('timeRemainingWidget');

        if (inlineTimerValue && inlineTimerWidget) {
            // Terminal page: use the inline timer, skip floating widget
            isTerminalMode = true;
            console.log('[ATD Timer] Terminal mode: updating inline timer widget');

            // Override updateTimer to use inline timer
            const originalUpdateTimer = updateTimer;
            updateTimer = function(timeRemaining) {
                updateInlineTimer(timeRemaining);
            };

            // Start timer updates
            startTimer();

            // Clean up on page unload
            window.addEventListener('beforeunload', () => {
                if (updateInterval) {
                    clearInterval(updateInterval);
                }
            });
            return;
        }

        // Prevent duplicate timers
        if (document.getElementById('atd-countdown-timer')) {
            console.log('[ATD Timer] Timer already exists, skipping initialization');
            return;
        }

        console.log('[ATD Timer] Initializing countdown timer widget');

        // Inject CSS
        const style = document.createElement('style');
        style.textContent = timerCSS;
        document.head.appendChild(style);

        // Inject HTML
        const div = document.createElement('div');
        div.innerHTML = timerHTML;
        document.body.appendChild(div.firstElementChild);

        // Get references to elements
        timerElement = document.getElementById('atd-countdown-timer');
        timerValue = timerElement.querySelector('.timer-value');
        timerLabel = timerElement.querySelector('.timer-label');

        // Load saved position from localStorage
        const savedPos = localStorage.getItem('atd-timer-position');
        if (savedPos) {
            try {
                const pos = JSON.parse(savedPos);
                xOffset = pos.x || 0;
                yOffset = pos.y || 0;
                setTranslate(xOffset, yOffset, timerElement);
            } catch (e) {
                console.error('[ATD Timer] Failed to load saved position:', e);
            }
        }

        // Add drag event listeners
        timerElement.addEventListener('mousedown', dragStart, false);
        document.addEventListener('mousemove', drag, false);
        document.addEventListener('mouseup', dragEnd, false);

        // Touch events for mobile
        timerElement.addEventListener('touchstart', dragStart, false);
        document.addEventListener('touchmove', drag, false);
        document.addEventListener('touchend', dragEnd, false);

        // Start timer updates
        startTimer();

        // Clean up on page unload
        window.addEventListener('beforeunload', () => {
            // Save position
            localStorage.setItem('atd-timer-position', JSON.stringify({
                x: xOffset,
                y: yOffset
            }));

            if (updateInterval) {
                clearInterval(updateInterval);
            }
        });
    }

    // Start when DOM is ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();

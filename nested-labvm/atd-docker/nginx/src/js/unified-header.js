/**
 * Unified Header - Injects a consistent header across all ATD pages
 */

// Prevent duplicate injection
if (window.__unifiedHeaderInjected) {
    console.log('[UnifiedHeader] Already injected, skipping');
} else {
    window.__unifiedHeaderInjected = true;

    if (document.querySelector('.unified-header')) {
        console.log('[UnifiedHeader] Header element already exists, skipping');
    } else if (window.self !== window.top) {
        console.log('[UnifiedHeader] Running in iframe, skipping');
    } else {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', init);
        } else {
            init();
        }
    }
}

function init() {
    if (document.querySelector('.unified-header')) {
        console.log('[UnifiedHeader] Header appeared during load, skipping');
        return;
    }
    injectCSS();
    // Detect CVP pages
    if (document.getElementById("main")) {
        document.body.classList.add("has-main");
    }
    injectHTML();
    initializeHeader();
    console.log('[UnifiedHeader] Injection complete');
}

function injectCSS() {
    const css = `
        /* CSS Reset for unified header elements - prevents host page styles from leaking in */
        .unified-header,
        .unified-header *,
        .announcement-banner,
        .announcement-banner *,
        .notifications-panel,
        .notifications-panel * {
            list-style: none !important;
            list-style-type: none !important;
        }
        .unified-header *::before,
        .announcement-banner *::before,
        .notifications-panel *::before {
            content: none !important;
        }

        /* Unified Header */
        .unified-header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            height: 44px;
            background: #071c35;
            border-bottom: 1px solid #fbb500;
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 0 16px;
            z-index: 100000;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
        }

        body {
            margin-top: 44px !important;
        }

        /* CVP spacing fix */
        body.has-main {
            margin-top: 0 !important;
        }

        #main {
            padding-top: 44px !important;
        }

        /* Collapse feature */
        .header-collapse-btn {
            padding: 4px 8px;
            height: 32px;
            box-sizing: border-box;
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            color: #fbb500;
            font-size: 16px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .header-expand-btn {
            position: fixed;
            top: 0;
            right: 16px;
            background: #071c35;
            border: 1px solid #fbb500;
            border-top: none;
            border-radius: 0 0 4px 4px;
            color: #fbb500;
            padding: 4px 12px;
            font-size: 11px;
            cursor: pointer;
            z-index: 100001;
            opacity: 0;
            pointer-events: none;
            transition: all 0.3s ease;
        }

        body.header-collapsed {
            margin-top: 0 !important;
            transition: margin-top 0.3s ease;
        }

        body.header-collapsed .unified-header {
            transform: translateY(-44px);
            transition: transform 0.3s ease;
        }

        body.header-collapsed .header-expand-btn {
            transform: translateY(0);
            opacity: 1;
            pointer-events: auto;
        }

        body.header-collapsed.has-main {
            margin-top: 0 !important;
        }

        body.header-collapsed #main {
            padding-top: 0 !important;
        }
        /* CVP-specific: Ensure #main div does not overlap header */
        #main {
            padding-top: 44px !important;
            margin-top: 0 !important;
            box-sizing: border-box;
        }


        /* Announcement Banner */
        .announcement-banner {
            position: fixed;
            top: 44px;
            left: 0;
            right: 0;
            padding: 12px 16px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            z-index: 99998;
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            font-size: 13px;
            line-height: 1.4;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.15);
        }

        .announcement-banner.info {
            background: #1e3a5f;
            border-bottom: 2px solid #3498db;
            color: #fff;
        }

        .announcement-banner.warning {
            background: #5a4a1a;
            border-bottom: 2px solid #f39c12;
            color: #fff;
        }

        .announcement-banner.alert {
            background: #4a1a1a;
            border-bottom: 2px solid #e74c3c;
            color: #fff;
        }

        .announcement-banner.success {
            background: #1a4a2e;
            border-bottom: 2px solid #27ae60;
            color: #fff;
        }

        .announcement-content {
            flex: 1;
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .announcement-icon {
            font-size: 18px;
            flex-shrink: 0;
        }

        .announcement-text {
            flex: 1;
        }

        .announcement-title {
            font-weight: 600;
            margin-bottom: 2px;
        }

        .announcement-message {
            font-size: 12px;
            opacity: 0.9;
        }

        .announcement-close {
            background: transparent;
            border: 1px solid rgba(255, 255, 255, 0.3);
            color: #fff;
            font-size: 18px;
            cursor: pointer;
            padding: 4px 8px;
            border-radius: 2px;
            transition: all 0.2s;
            flex-shrink: 0;
        }

        .announcement-close:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.5);
        }

        /* Adjust body margin when announcement is shown */
        body.has-announcement {
            padding-top: 60px !important;
        }

        body.has-announcement.has-main {
            padding-top: 0 !important;
        }

        body.has-announcement.has-main #main {
            padding-top: 104px !important;
        }

        body.has-announcement.header-collapsed {
            padding-top: 16px !important;
        }

        body.has-announcement.header-collapsed .announcement-banner {
            top: 0;
        }

        .header-left {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            min-width: 0;
        }

        .arista-logo {
            height: 24px;
            margin-right: 12px;
            flex-shrink: 0;
        }

        .arista-logo-img {
            height: 24px;
            width: auto;
        }

        .header-center {
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            margin: 0 15px;
            gap: 10px;
        }

        .credentials-widget {
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            padding: 6px 14px;
            display: flex;
            align-items: center;
            gap: 12px;
            height: 32px;
            box-sizing: border-box;
            font-size: 11px;
        }

        .credential-item {
            display: flex;
            align-items: center;
            gap: 4px;
        }

        .credential-label {
            color: #fbb500;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 10px;
        }

        .credential-value {
            color: #fff;
            font-family: 'Courier New', monospace;
            font-weight: 600;
        }

        .credential-divider {
            width: 1px;
            height: 20px;
            background: rgba(251, 181, 0, 0.4);
        }

        .timer-widget {
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            padding: 6px 14px;
            display: flex;
            align-items: center;
            gap: 8px;
            height: 32px;
            box-sizing: border-box;
        }

        .timer-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #fbb500;
            font-weight: 600;
        }

        .timer-value {
            font-family: 'Courier New', monospace;
            font-size: 16px;
            font-weight: 700;
            color: #fff;
            letter-spacing: 1px;
        }

        .timer-widget.warning {
            border-color: #ff6b6b;
            background: rgba(255, 107, 107, 0.1);
            animation: pulse 2s infinite;
        }

        .timer-widget.warning .timer-label {
            color: #ff6b6b;
        }

        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(255, 107, 107, 0.4); }
            50% { box-shadow: 0 0 0 8px rgba(255, 107, 107, 0); }
        }

        .header-right {
            display: flex;
            align-items: center;
            gap: 10px;
            flex-shrink: 0;
        }

        /* Notification Bell */
        .notification-bell {
            position: relative;
            padding: 6px 10px;
            height: 32px;
            box-sizing: border-box;
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            color: #fbb500;
            font-size: 16px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.2s;
            display: flex;
            align-items: center;
        }

        .notification-bell:hover {
            background: rgba(251, 181, 0, 0.15);
        }

        .notification-bell.active {
            background: #fbb500;
            color: #04152a;
        }

        .notification-badge {
            position: absolute;
            top: -4px;
            right: -4px;
            background: #e74c3c;
            color: #fff;
            font-size: 10px;
            font-weight: 600;
            padding: 2px 5px;
            border-radius: 10px;
            min-width: 16px;
            text-align: center;
        }

        /* Notifications Panel */
        .notifications-panel {
            position: fixed;
            top: 44px;
            right: 0;
            width: 400px;
            min-width: 300px;
            max-width: 90vw;
            max-height: calc(100vh - 44px);
            background: #04152a;
            border-left: 4px solid #fbb500;
            transition: transform 0.3s ease;
            transform: translateX(100%);
            z-index: 99997;
            display: flex;
            flex-direction: column;
            box-shadow: -4px 0 20px rgba(0, 0, 0, 0.5);
        }

        .notifications-panel.visible {
            transform: translateX(0);
        }

        .notifications-panel-header {
            padding: 12px 16px;
            background: rgba(251, 181, 0, 0.1);
            border-bottom: 1px solid rgba(251, 181, 0, 0.3);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .notifications-panel-header h3 {
            font-size: 14px;
            font-weight: 600;
            color: #fbb500;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin: 0;
        }

        .notifications-close {
            background: transparent;
            border: none;
            color: #fbb500;
            font-size: 24px;
            cursor: pointer;
            padding: 0;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 0;
            transition: all 0.2s;
        }

        .notifications-close:hover {
            background: rgba(251, 181, 0, 0.2);
        }

        .notifications-content {
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }

        .notification-item {
            background: rgba(255, 255, 255, 0.05);
            border-left: 3px solid;
            padding: 12px;
            margin-bottom: 8px;
            border-radius: 4px;
            cursor: pointer;
            transition: all 0.2s;
        }

        .notification-item:hover {
            background: rgba(255, 255, 255, 0.1);
        }

        .notification-item.info {
            border-left-color: #3498db;
        }

        .notification-item.warning {
            border-left-color: #f39c12;
        }

        .notification-item.alert {
            border-left-color: #e74c3c;
        }

        .notification-item.success {
            border-left-color: #27ae60;
        }

        .notification-item-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 6px;
        }

        .notification-item-title {
            font-weight: 600;
            color: #fff;
            font-size: 13px;
            flex: 1;
        }

        .notification-item-time {
            font-size: 10px;
            color: rgba(255, 255, 255, 0.5);
            white-space: nowrap;
            margin-left: 8px;
        }

        .notification-item-message {
            color: rgba(255, 255, 255, 0.8);
            font-size: 12px;
            line-height: 1.4;
        }

        .notification-item-icon {
            font-size: 14px;
            margin-right: 8px;
        }

        .notifications-empty {
            text-align: center;
            padding: 40px 20px;
            color: rgba(255, 255, 255, 0.5);
        }

        .notifications-empty-icon {
            font-size: 48px;
            margin-bottom: 16px;
            opacity: 0.3;
        }

        .labguides-toggle {
            padding: 6px 14px;
            height: 32px;
            box-sizing: border-box;
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            color: #fbb500;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.2s;
            display: flex;
            align-items: center;
        }

        .labguides-toggle:hover {
            background: rgba(251, 181, 0, 0.15);
        }

        .labguides-toggle.active {
            background: #fbb500;
            color: #04152a;
        }

        /* Lab Guides Side Panel */
        .labguides-panel {
            position: fixed;
            top: 44px;
            right: 0;
            width: 500px;
            min-width: 300px;
            max-width: 80vw;
            height: calc(100vh - 44px);
            background: #04152a;
            border-left: 4px solid #fbb500;
            transition: transform 0.3s ease;
            transform: translateX(100%);
            z-index: 99999;
            display: flex;
            flex-direction: column;
            box-shadow: -4px 0 20px rgba(0, 0, 0, 0.5);
        }

        .labguides-panel:hover {
            border-left-color: #ffc52f;
        }

        .labguides-panel.visible {
            transform: translateX(0);
        }

        body.labguides-open {
            margin-right: 500px !important;
            transition: margin-right 0.3s ease;
        }

        .labguides-panel.resizing {
            transition: none;
        }

        .resize-handle {
            position: absolute;
            left: 0;
            top: 0;
            width: 16px;
            height: 100%;
            cursor: ew-resize;
            background: transparent;
            z-index: 10;
            transition: background 0.2s;
        }

        .resize-handle:hover,
        .resize-handle.dragging {
            background: rgba(251, 181, 0, 0.2);
        }

        .resize-handle::before {
            content: '';
            position: absolute;
            left: 7px;
            top: 50%;
            transform: translateY(-50%);
            width: 2px;
            height: 40px;
            background: rgba(251, 181, 0, 0.5);
            border-radius: 1px;
        }

        .resize-handle:hover::before,
        .resize-handle.dragging::before {
            background: #fbb500;
            height: 60px;
        }

        .labguides-panel.resizing iframe {
            pointer-events: none;
        }

        .labguides-panel-header {
            padding: 12px 16px;
            background: rgba(251, 181, 0, 0.1);
            border-bottom: 1px solid rgba(251, 181, 0, 0.3);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .labguides-panel-header h3 {
            font-size: 14px;
            font-weight: 600;
            color: #fbb500;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin: 0;
        }

        .labguides-close {
            background: transparent;
            border: none;
            color: #fbb500;
            font-size: 24px;
            cursor: pointer;
            padding: 0;
            width: 30px;
            height: 30px;
            display: flex;
            align-items: center;
            justify-content: center;
            border-radius: 0;
            transition: all 0.2s;
        }

        .labguides-close:hover {
            background: rgba(251, 181, 0, 0.2);
        }

        .labguides-panel-content {
            flex: 1;
            overflow: auto;
        }

        .labguides-panel-content iframe {
            width: 100%;
            height: 100%;
            border: none;
        }

        @media (max-width: 768px) {
            .unified-header {
                height: auto;
                flex-direction: column;
                padding: 10px;
                gap: 10px;
            }

            body {
                margin-top: 44px !important;
            }

        /* CVP spacing fix */
        body.has-main {
            margin-top: 0 !important;
        }

        #main {
            padding-top: 44px !important;
        }

        /* Collapse feature */
        .header-collapse-btn {
            padding: 4px 8px;
            height: 32px;
            box-sizing: border-box;
            background: transparent;
            border: 1px solid #fbb500;
            border-radius: 0;
            color: #fbb500;
            font-size: 16px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
        }

        .header-expand-btn {
            position: fixed;
            top: 0;
            right: 16px;
            background: #071c35;
            border: 1px solid #fbb500;
            border-top: none;
            border-radius: 0 0 4px 4px;
            color: #fbb500;
            padding: 4px 12px;
            font-size: 11px;
            cursor: pointer;
            z-index: 100001;
            opacity: 0;
            pointer-events: none;
            transition: all 0.3s ease;
        }

        body.header-collapsed {
            margin-top: 0 !important;
        }

        body.header-collapsed .unified-header {
            transform: translateY(-44px);
            transition: transform 0.3s ease;
        }

        body.header-collapsed .header-expand-btn {
            transform: translateY(0);
            opacity: 1;
            pointer-events: auto;
        }

        body.header-collapsed.has-main {
            margin-top: 0 !important;
        }

        body.header-collapsed #main {
            padding-top: 0 !important;
        }

            .header-left,
            .header-center,
            .header-right {
                width: 100%;
                justify-content: center;
            }

            .labguides-panel {
                width: 100%;
            }

            .notifications-panel {
                width: 100%;
                max-width: 100vw;
            }
        }
    `;

    const style = document.createElement('style');
    style.textContent = css;
    document.head.appendChild(style);
}

function injectHTML() {
    const html = `
        <button class="header-expand-btn" id="headerExpandBtn">▼ Show</button>
        <div class="unified-header">
            <div class="header-left">
                <a href="/" class="arista-logo">
                    <img src="/images/arista.svg" alt="Arista" class="arista-logo-img">
                </a>
            </div>

            <div class="header-center">
                <div class="credentials-widget">
                    <div class="credential-item">
                        <span class="credential-label">User:</span>
                        <span class="credential-value" id="credUsername">arista</span>
                    </div>
                    <div class="credential-divider"></div>
                    <div class="credential-item">
                        <span class="credential-label">Pass:</span>
                        <span class="credential-value" id="credPassword">arista</span>
                    </div>
                </div>
                <div class="timer-widget" id="timerWidget">
                    <div class="timer-label">Time Remaining</div>
                    <div class="timer-value" id="timerValue">--:--:--</div>
                </div>
            </div>

            <div class="header-right">
                <button class="notification-bell" id="notificationBell" title="View Notifications">
                    🔔
                    <span class="notification-badge" id="notificationBadge" style="display: none;">0</span>
                </button>
                <button class="labguides-toggle" id="labguidesToggle">Lab Guides</button>
                <button class="header-collapse-btn" id="headerCollapseBtn" title="Hide Header">▲</button>
            </div>
        </div>

        <div class="notifications-panel" id="notificationsPanel">
            <div class="notifications-panel-header">
                <h3>Notifications</h3>
                <button class="notifications-close" id="notificationsClose">&times;</button>
            </div>
            <div class="notifications-content" id="notificationsContent">
                <div class="notifications-empty">
                    <div class="notifications-empty-icon">🔔</div>
                    <div>No notifications</div>
                </div>
            </div>
        </div>

        <div class="labguides-panel" id="labguidesPanel">
            <div class="resize-handle" id="resizeHandle"></div>
            <div class="labguides-panel-header">
                <h3>Lab Guides</h3>
                <button class="labguides-close" id="labguidesClose">&times;</button>
            </div>
            <div class="labguides-panel-content">
                <iframe src="/labguides/index.html" title="Lab Guides"></iframe>
            </div>
        </div>
    `;

    document.body.insertAdjacentHTML('afterbegin', html);
}

function initializeHeader() {
    loadCredentials();
    initializeTimer();
    initializeLabGuides();
    initializeCollapse();
    applyCollapsedState();
    initializeAnnouncements();
    initializeNotifications();

    // Hide Lab Guides button on labguides page
    if (window.location.pathname.startsWith('/labguides')) {
        const labguidesToggle = document.getElementById('labguidesToggle');
        if (labguidesToggle) {
            labguidesToggle.style.display = 'none';
        }
    }

    // Load CVP activity monitor for CVP pages
    // CVP pages have a #main div and constant data transmission that bypasses idle detection
    if (document.getElementById('main')) {
        loadCVPActivityMonitor();
    }
}

function loadCVPActivityMonitor() {
    // Prevent duplicate loading
    if (window.__cvpActivityMonitorInjected || document.getElementById('cvp-activity-script')) {
        return;
    }

    console.log('[UnifiedHeader] Loading CVP activity monitor');

    const script = document.createElement('script');
    script.id = 'cvp-activity-script';
    script.src = '/cvp-activity.js';
    script.async = true;
    script.onerror = () => {
        console.error('[UnifiedHeader] Failed to load CVP activity monitor');
    };
    document.head.appendChild(script);
}

async function loadCredentials() {
    try {
        const response = await fetch('/baseUrl');
        const data = await response.json();
        const decoded = JSON.parse(atob(data.response));

        const usernameEl = document.getElementById('credUsername');
        const passwordEl = document.getElementById('credPassword');

        if (usernameEl && decoded.user) {
            usernameEl.textContent = decoded.user;
        }
        if (passwordEl && decoded.pwd) {
            passwordEl.textContent = decoded.pwd;
        }
    } catch (error) {
        console.error('[UnifiedHeader] Failed to load credentials:', error);
    }
}

function initializeTimer() {
    const timerValue = document.getElementById('timerValue');
    const timerWidget = document.getElementById('timerWidget');

    if (!timerValue || !timerWidget) return;

    updateTimer();
    setInterval(updateTimer, 1000);

    async function updateTimer() {
        try {
            const response = await fetch('/uptimeWithRuntime');
            const data = await response.json();

            let remainingSeconds = 0;
            const currentTime = Math.floor(Date.now() / 1000);

            if (data.exam_end_time && data.exam_end_time > 0) {
                remainingSeconds = data.exam_end_time - currentTime;
            } else if (data.boottime && data.runtime) {
                const expirationTime = data.boottime + (data.runtime * 60 * 60);
                remainingSeconds = expirationTime - currentTime;
            }

            if (remainingSeconds <= 0) {
                timerValue.textContent = '00:00:00';
                timerWidget.classList.add('warning');
                return;
            }

            const hours = Math.floor(remainingSeconds / 3600);
            const minutes = Math.floor((remainingSeconds % 3600) / 60);
            const seconds = Math.floor(remainingSeconds % 60);

            timerValue.textContent =
                String(hours).padStart(2, '0') + ':' +
                String(minutes).padStart(2, '0') + ':' +
                String(seconds).padStart(2, '0');

            if (remainingSeconds < 300) {
                timerWidget.classList.add('warning');
            } else {
                timerWidget.classList.remove('warning');
            }
        } catch (error) {
            console.error('Failed to fetch timer data:', error);
            timerValue.textContent = '--:--:--';
        }
    }
}

function initializeLabGuides() {
    const labguidesToggle = document.getElementById('labguidesToggle');
    const labguidesPanel = document.getElementById('labguidesPanel');
    const labguidesClose = document.getElementById('labguidesClose');
    const resizeHandle = document.getElementById('resizeHandle');

    if (!labguidesToggle || !labguidesPanel || !labguidesClose || !resizeHandle) return;

    function getPanelWidth() {
        return labguidesPanel.offsetWidth || 500;
    }

    function updateBodyMargin(width) {
        document.body.style.marginRight = width + 'px';
    }

    function openLabGuides() {
        labguidesPanel.classList.add('visible');
        labguidesToggle.classList.add('active');
        document.body.classList.add('labguides-open');
        updateBodyMargin(getPanelWidth());
    }

    function closeLabGuides() {
        labguidesPanel.classList.remove('visible');
        labguidesToggle.classList.remove('active');
        document.body.classList.remove('labguides-open');
        document.body.style.marginRight = '';
    }

    function toggleLabGuides() {
        if (labguidesPanel.classList.contains('visible')) {
            closeLabGuides();
        } else {
            openLabGuides();
        }
    }

    labguidesToggle.addEventListener('click', toggleLabGuides);
    labguidesClose.addEventListener('click', closeLabGuides);

    // Resizable Panel Logic
    let isResizing = false;
    let startX = 0;
    let startWidth = 0;
    const minWidth = 300;
    let maxWidth = window.innerWidth * 0.8;

    resizeHandle.addEventListener('mousedown', (e) => {
        isResizing = true;
        startX = e.clientX;
        startWidth = labguidesPanel.offsetWidth;
        labguidesPanel.classList.add('resizing');
        resizeHandle.classList.add('dragging');
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        e.preventDefault();
    });

    const handleMouseMove = (e) => {
        if (!isResizing) return;
        const delta = startX - e.clientX;
        const newWidth = Math.max(minWidth, Math.min(startWidth + delta, maxWidth));
        labguidesPanel.style.width = newWidth + 'px';
        if (labguidesPanel.classList.contains('visible')) {
            updateBodyMargin(newWidth);
        }
    };

    const handleMouseUp = () => {
        if (isResizing) {
            isResizing = false;
            labguidesPanel.classList.remove('resizing');
            resizeHandle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            const finalWidth = labguidesPanel.offsetWidth;
            localStorage.setItem('unified-header-panel-width', finalWidth + 'px');
            if (labguidesPanel.classList.contains('visible')) {
                updateBodyMargin(finalWidth);
            }
        }
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
    window.addEventListener('mouseup', handleMouseUp);
    window.addEventListener('blur', handleMouseUp);

    // Restore saved width
    const savedWidth = localStorage.getItem('unified-header-panel-width');
    if (savedWidth) {
        labguidesPanel.style.width = savedWidth;
    }

    // Update max width on window resize
    window.addEventListener('resize', () => {
        maxWidth = window.innerWidth * 0.8;
        const currentWidth = labguidesPanel.offsetWidth;
        if (currentWidth > maxWidth) {
            labguidesPanel.style.width = maxWidth + 'px';
            if (labguidesPanel.classList.contains('visible')) {
                updateBodyMargin(maxWidth);
            }
        }
    });
}

function initializeCollapse() {
    const collapseBtn = document.getElementById("headerCollapseBtn");
    const expandBtn = document.getElementById("headerExpandBtn");
    if (collapseBtn) {
        collapseBtn.onclick = () => {
            document.body.classList.add("header-collapsed");
            localStorage.setItem("headerCollapsed", "true");
        };
    }
    if (expandBtn) {
        expandBtn.onclick = () => {
            document.body.classList.remove("header-collapsed");
            localStorage.setItem("headerCollapsed", "false");
        };
    }
}

function applyCollapsedState() {
    if (localStorage.getItem("headerCollapsed") === "true") {
        document.body.classList.add("header-collapsed");
    }
}

async function fetchAnnouncements() {
    try {
        const response = await fetch('/announcements');
        if (!response.ok) {
            console.error('[UnifiedHeader] Failed to fetch announcements:', response.status);
            return null;
        }
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('[UnifiedHeader] Error fetching announcements:', error);
        return null;
    }
}

function renderAnnouncements(announcementsData) {
    if (!announcementsData || !announcementsData.active_announcements || announcementsData.active_announcements.length === 0) {
        return;
    }

    // Get dismissed announcements from localStorage
    const dismissed = JSON.parse(localStorage.getItem('dismissedAnnouncements') || '[]');

    // Filter out dismissed announcements and get the highest priority one
    const activeAnnouncements = announcementsData.active_announcements
        .filter(ann => !dismissed.includes(ann.id))
        .sort((a, b) => (b.priority || 0) - (a.priority || 0));

    if (activeAnnouncements.length === 0) {
        return;
    }

    // Show the highest priority announcement
    const announcement = activeAnnouncements[0];

    const iconMap = {
        'info': 'ℹ️',
        'warning': '⚠️',
        'alert': '🚨',
        'success': '✅'
    };

    const icon = iconMap[announcement.type] || iconMap['info'];
    const bannerHTML = `
        <div class="announcement-banner ${announcement.type}" id="announcementBanner" data-id="${announcement.id}">
            <div class="announcement-content">
                <span class="announcement-icon">${icon}</span>
                <div class="announcement-text">
                    <div class="announcement-title">${announcement.title || ''}</div>
                    <div class="announcement-message">${announcement.message || ''}</div>
                </div>
            </div>
            ${announcement.dismissible !== false ? '<button class="announcement-close" id="announcementClose">✕</button>' : ''}
        </div>
    `;

    // Insert after the unified header
    const header = document.querySelector('.unified-header');
    if (header) {
        header.insertAdjacentHTML('afterend', bannerHTML);
        document.body.classList.add('has-announcement');

        // Add close handler
        if (announcement.dismissible !== false) {
            const closeBtn = document.getElementById('announcementClose');
            if (closeBtn) {
                closeBtn.addEventListener('click', () => {
                    dismissAnnouncement(announcement.id);
                });
            }
        }
    }
}

function dismissAnnouncement(announcementId) {
    // Get the announcement banner to extract details
    const banner = document.getElementById('announcementBanner');
    if (!banner) return;

    // Get announcement details from the banner
    const title = banner.querySelector('.announcement-title')?.textContent || '';
    const message = banner.querySelector('.announcement-message')?.textContent || '';
    const type = banner.classList.contains('info') ? 'info' :
                 banner.classList.contains('warning') ? 'warning' :
                 banner.classList.contains('alert') ? 'alert' : 'success';

    // Get current dismissed list (now stores objects, not just IDs)
    const dismissed = JSON.parse(localStorage.getItem('dismissedAnnouncements') || '[]');
    const dismissedNotifications = JSON.parse(localStorage.getItem('dismissedNotifications') || '[]');

    // Add ID to dismissed list for filtering
    if (!dismissed.includes(announcementId)) {
        dismissed.push(announcementId);
        localStorage.setItem('dismissedAnnouncements', JSON.stringify(dismissed));
    }

    // Check if this announcement is already in the notifications list (prevent duplicates)
    const alreadyExists = dismissedNotifications.some(notif => notif.id === announcementId);

    if (!alreadyExists) {
        // Add full notification details for display in bell panel
        const notification = {
            id: announcementId,
            title: title,
            message: message,
            type: type,
            dismissedAt: new Date().toISOString()
        };

        // Add to beginning of array (most recent first)
        dismissedNotifications.unshift(notification);

        // Keep only last 50 notifications
        if (dismissedNotifications.length > 50) {
            dismissedNotifications.splice(50);
        }

        localStorage.setItem('dismissedNotifications', JSON.stringify(dismissedNotifications));
    }

    // Remove the banner from DOM
    banner.remove();
    document.body.classList.remove('has-announcement');

    // Update notification badge
    updateNotificationBadge();
}

async function initializeAnnouncements() {
    // Check if announcements feature is enabled
    let announcementsEnabled = true;
    try {
        if (window.featureFlags) {
            announcementsEnabled = await window.featureFlags.check('announcements');
        } else {
            const response = await fetch('/feature-flags');
            if (response.ok) {
                const data = await response.json();
                announcementsEnabled = data.enabled_features && data.enabled_features.includes('announcements');
            }
        }
    } catch (error) {
        console.warn('[UnifiedHeader] Error checking announcements feature flag:', error);
    }
    if (!announcementsEnabled) {
        console.log('[UnifiedHeader] Announcements feature is disabled');
        // Hide bell icon and notifications panel when announcements are disabled
        const bell = document.getElementById('notificationBell');
        const panel = document.getElementById('notificationsPanel');
        if (bell) bell.style.display = 'none';
        if (panel) panel.style.display = 'none';
        return;
    }

    const data = await fetchAnnouncements();
    if (data) {
        renderAnnouncements(data);
    }

    // Poll for new announcements every 2 minutes
    setInterval(async () => {
        const newData = await fetchAnnouncements();
        if (newData) {
            checkForNewAnnouncements(newData);
        }
    }, 2 * 60 * 1000); // 2 minutes
}

function checkForNewAnnouncements(announcementsData) {
    if (!announcementsData || !announcementsData.active_announcements || announcementsData.active_announcements.length === 0) {
        return;
    }

    // Get current announcement ID (if any)
    const currentBanner = document.getElementById('announcementBanner');
    const currentAnnouncementId = currentBanner?.getAttribute('data-id');

    // Get dismissed announcements
    const dismissed = JSON.parse(localStorage.getItem('dismissedAnnouncements') || '[]');

    // Filter and sort announcements
    const activeAnnouncements = announcementsData.active_announcements
        .filter(ann => !dismissed.includes(ann.id))
        .sort((a, b) => (b.priority || 0) - (a.priority || 0));

    if (activeAnnouncements.length === 0) {
        return;
    }

    const newTopAnnouncement = activeAnnouncements[0];

    // If there's a new announcement with different ID or no current announcement
    if (!currentAnnouncementId || newTopAnnouncement.id !== currentAnnouncementId) {
        console.log('[UnifiedHeader] New announcement detected:', newTopAnnouncement.title);

        // Remove current banner if exists
        if (currentBanner) {
            currentBanner.remove();
            document.body.classList.remove('has-announcement');
        }

        // Render new announcement
        renderAnnouncements(announcementsData);
    }
}

function initializeNotifications() {
    const notificationBell = document.getElementById('notificationBell');
    const notificationsPanel = document.getElementById('notificationsPanel');
    const notificationsClose = document.getElementById('notificationsClose');

    if (!notificationBell || !notificationsPanel || !notificationsClose) return;

    function openNotifications() {
        notificationsPanel.classList.add('visible');
        notificationBell.classList.add('active');
        renderNotificationsList();
    }

    function closeNotifications() {
        notificationsPanel.classList.remove('visible');
        notificationBell.classList.remove('active');
    }

    function toggleNotifications() {
        if (notificationsPanel.classList.contains('visible')) {
            closeNotifications();
        } else {
            openNotifications();
        }
    }

    notificationBell.addEventListener('click', toggleNotifications);
    notificationsClose.addEventListener('click', closeNotifications);

    // Initial badge update
    updateNotificationBadge();
}

function updateNotificationBadge() {
    const badge = document.getElementById('notificationBadge');
    if (!badge) return;

    const notifications = JSON.parse(localStorage.getItem('dismissedNotifications') || '[]');
    const count = notifications.length;

    if (count > 0) {
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = 'block';
    } else {
        badge.style.display = 'none';
    }
}

function renderNotificationsList() {
    const content = document.getElementById('notificationsContent');
    if (!content) return;

    const notifications = JSON.parse(localStorage.getItem('dismissedNotifications') || '[]');

    if (notifications.length === 0) {
        content.innerHTML = `
            <div class="notifications-empty">
                <div class="notifications-empty-icon">🔔</div>
                <div>No notifications</div>
            </div>
        `;
        return;
    }

    const iconMap = {
        'info': 'ℹ️',
        'warning': '⚠️',
        'alert': '🚨',
        'success': '✅'
    };

    const notificationsHTML = notifications.map(notif => {
        const icon = iconMap[notif.type] || iconMap['info'];
        const timeAgo = getTimeAgo(notif.dismissedAt);

        return `
            <div class="notification-item ${notif.type}">
                <div class="notification-item-header">
                    <div class="notification-item-title">
                        <span class="notification-item-icon">${icon}</span>
                        ${notif.title || 'Announcement'}
                    </div>
                    <div class="notification-item-time">${timeAgo}</div>
                </div>
                <div class="notification-item-message">${notif.message || ''}</div>
            </div>
        `;
    }).join('');

    content.innerHTML = notificationsHTML;
}

function getTimeAgo(timestamp) {
    const now = new Date();
    const then = new Date(timestamp);
    const seconds = Math.floor((now - then) / 1000);

    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
    if (seconds < 604800) return `${Math.floor(seconds / 86400)}d ago`;
    return then.toLocaleDateString();
}

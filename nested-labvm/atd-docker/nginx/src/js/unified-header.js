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
            height: 28px;
            background: transparent;
            border: 1px solid #fbb500;
            color: #fbb500;
            font-size: 18px;
            cursor: pointer;
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

        body.header-collapsed .unified-header {
            transform: translateY(-44px);
            transition: transform 0.3s ease;
        }

        body.header-collapsed .header-expand-btn {
            transform: translateY(44px);
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
            height: 28px;
            background: transparent;
            border: 1px solid #fbb500;
            color: #fbb500;
            font-size: 18px;
            cursor: pointer;
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

        body.header-collapsed .unified-header {
            transform: translateY(-44px);
            transition: transform 0.3s ease;
        }

        body.header-collapsed .header-expand-btn {
            transform: translateY(44px);
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
                <button class="labguides-toggle" id="labguidesToggle">Lab Guides</button>
                <button class="header-collapse-btn" id="headerCollapseBtn" title="Hide Header">▲</button>
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

    // Hide Lab Guides button on labguides page
    if (window.location.pathname.startsWith('/labguides')) {
        const labguidesToggle = document.getElementById('labguidesToggle');
        if (labguidesToggle) {
            labguidesToggle.style.display = 'none';
        }
    }
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

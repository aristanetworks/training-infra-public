/**
 * Notification Bell - Standalone announcement notification system for uilanding pages.
 * Fetches announcements from /announcements and displays:
 *   1. A top banner for the highest-priority undismissed announcement
 *   2. A bell icon with notification panel showing dismissed notification history
 * Auto-detects page type (home vs terminal) and places bell appropriately.
 * Shares localStorage keys with unified-header.js for consistent dismiss state.
 */
(function() {
    if (window.__notificationBellInjected) return;
    window.__notificationBellInjected = true;
    if (window.self !== window.top) return;

    var announcements = [];
    var panelOpen = false;
    var isTerminalPage = false;

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    function init() {
        isTerminalPage = !!document.querySelector('.tab-bar-actions');
        injectCSS();
        injectBell();
        fetchAnnouncements();
        setInterval(fetchAnnouncements, 120000);
    }

    function getDismissed() {
        try { return JSON.parse(localStorage.getItem('dismissedAnnouncements') || '[]'); }
        catch(e) { return []; }
    }

    function saveDismissed(list) {
        try { localStorage.setItem('dismissedAnnouncements', JSON.stringify(list)); }
        catch(e) {}
    }

    function getNotifications() {
        try { return JSON.parse(localStorage.getItem('dismissedNotifications') || '[]'); }
        catch(e) { return []; }
    }

    function saveNotifications(list) {
        try { localStorage.setItem('dismissedNotifications', JSON.stringify(list)); }
        catch(e) {}
    }

    function escapeHtml(str) {
        var div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function getIcon(type) {
        var map = { info: '\u2139\uFE0F', warning: '\u26A0\uFE0F', alert: '\uD83D\uDEA8', success: '\u2705' };
        return map[type] || map.info;
    }

    function getTimeAgo(ts) {
        if (!ts) return '';
        var sec = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
        if (sec < 60) return 'Just now';
        if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
        if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
        return new Date(ts).toLocaleDateString();
    }

    function injectCSS() {
        var css = [
            /* ===== Announcement Banner (top of page) ===== */
            '.nb-banner {',
            '    position: fixed;',
            '    top: 0;',
            '    left: 0;',
            '    right: 0;',
            '    z-index: 99998;',
            '    display: flex;',
            '    align-items: center;',
            '    justify-content: space-between;',
            '    padding: 10px 20px;',
            '    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;',
            '    box-shadow: 0 2px 8px rgba(0,0,0,0.3);',
            '    animation: nbSlideDown 0.3s ease;',
            '}',
            '@keyframes nbSlideDown {',
            '    from { transform: translateY(-100%); opacity: 0; }',
            '    to { transform: translateY(0); opacity: 1; }',
            '}',
            /* Banner type styles */
            '.nb-banner-info { background: #1e3a5f; border-bottom: 2px solid #3498db; }',
            '.nb-banner-warning { background: #5a4a1a; border-bottom: 2px solid #f39c12; }',
            '.nb-banner-alert { background: #4a1a1a; border-bottom: 2px solid #e74c3c; }',
            '.nb-banner-success { background: #1a4a2e; border-bottom: 2px solid #27ae60; }',
            /* Banner content */
            '.nb-banner-content {',
            '    display: flex;',
            '    align-items: center;',
            '    gap: 10px;',
            '    flex: 1;',
            '    min-width: 0;',
            '}',
            '.nb-banner-icon { font-size: 18px; flex-shrink: 0; }',
            '.nb-banner-text { flex: 1; min-width: 0; }',
            '.nb-banner-title {',
            '    font-weight: 600;',
            '    color: #fff;',
            '    font-size: 13px;',
            '    margin-bottom: 2px;',
            '}',
            '.nb-banner-message {',
            '    font-size: 12px;',
            '    color: rgba(255,255,255,0.8);',
            '    line-height: 1.3;',
            '    overflow: hidden;',
            '    text-overflow: ellipsis;',
            '    white-space: nowrap;',
            '}',
            /* Banner close button */
            '.nb-banner-close {',
            '    background: transparent;',
            '    border: 1px solid rgba(255,255,255,0.3);',
            '    color: rgba(255,255,255,0.8);',
            '    font-size: 16px;',
            '    cursor: pointer;',
            '    padding: 4px 8px;',
            '    border-radius: 3px;',
            '    flex-shrink: 0;',
            '    margin-left: 12px;',
            '    transition: all 0.2s;',
            '}',
            '.nb-banner-close:hover {',
            '    background: rgba(255,255,255,0.15);',
            '    color: #fff;',
            '    border-color: rgba(255,255,255,0.5);',
            '}',

            /* ===== Divider between status and bell ===== */
            '.nb-divider {',
            '    width: 1px;',
            '    height: 20px;',
            '    background: rgba(255,255,255,0.25);',
            '    margin: 0 4px;',
            '    flex-shrink: 0;',
            '}',

            /* ===== Bell button - inline within system-status-badge ===== */
            '.nb-bell-inline {',
            '    position: relative;',
            '    padding: 4px 8px;',
            '    background: transparent;',
            '    border: none;',
            '    color: #fbb500;',
            '    font-size: 16px;',
            '    cursor: pointer;',
            '    display: flex;',
            '    align-items: center;',
            '    gap: 4px;',
            '    border-radius: 12px;',
            '    transition: all 0.2s;',
            '}',
            '.nb-bell-inline:hover { background: rgba(251,181,0,0.15); }',
            '.nb-bell-inline.nb-active { background: rgba(251,181,0,0.2); }',

            /* ===== Fallback: fixed position ===== */
            '.nb-bell-fixed {',
            '    position: fixed;',
            '    top: 20px;',
            '    right: 20px;',
            '    z-index: 1001;',
            '    padding: 8px 14px;',
            '    background: rgba(7,28,53,0.95);',
            '    border: 2px solid #fbb500;',
            '    color: #fbb500;',
            '    font-size: 18px;',
            '    cursor: pointer;',
            '    display: flex;',
            '    align-items: center;',
            '    border-radius: 20px;',
            '    box-shadow: 0 2px 12px rgba(0,0,0,0.3);',
            '    transition: all 0.2s;',
            '}',
            '.nb-bell-fixed:hover { background: rgba(251,181,0,0.15); }',
            '.nb-bell-fixed.nb-active { background: #fbb500; color: #071c35; }',

            /* ===== Bell button - terminal tab bar style ===== */
            '.nb-bell-tab {',
            '    position: relative;',
            '    padding: 6px 14px;',
            '    background: transparent;',
            '    border: 1px solid #fbb500;',
            '    border-radius: 0;',
            '    color: #fbb500;',
            '    font-size: 11px;',
            '    cursor: pointer;',
            '    white-space: nowrap;',
            '    font-weight: 600;',
            '    text-transform: uppercase;',
            '    letter-spacing: 0.5px;',
            '    display: flex;',
            '    align-items: center;',
            '    gap: 6px;',
            '    transition: all 0.2s;',
            '}',
            '.nb-bell-tab:hover { background: rgba(251,181,0,0.15); }',
            '.nb-bell-tab.nb-active { background: #fbb500; color: #04152a; }',

            /* ===== Badge ===== */
            '.nb-badge {',
            '    position: absolute;',
            '    top: -6px;',
            '    right: -6px;',
            '    background: #e74c3c;',
            '    color: #fff;',
            '    font-size: 10px;',
            '    font-weight: 700;',
            '    padding: 2px 6px;',
            '    border-radius: 10px;',
            '    min-width: 16px;',
            '    text-align: center;',
            '    line-height: 1.3;',
            '}',

            /* ===== Panel ===== */
            '.nb-panel {',
            '    position: fixed;',
            '    top: 0;',
            '    right: 0;',
            '    width: 400px;',
            '    max-width: 90vw;',
            '    height: 100vh;',
            '    background: #04152a;',
            '    border-left: 4px solid #fbb500;',
            '    transform: translateX(100%);',
            '    transition: transform 0.3s ease;',
            '    z-index: 100000;',
            '    display: flex;',
            '    flex-direction: column;',
            '    box-shadow: -4px 0 20px rgba(0,0,0,0.5);',
            '    font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;',
            '}',
            '.nb-panel.nb-visible { transform: translateX(0); }',

            /* Panel header */
            '.nb-panel-header {',
            '    padding: 14px 16px;',
            '    background: rgba(251,181,0,0.1);',
            '    border-bottom: 1px solid rgba(251,181,0,0.3);',
            '    display: flex;',
            '    justify-content: space-between;',
            '    align-items: center;',
            '    flex-shrink: 0;',
            '}',
            '.nb-panel-header h3 {',
            '    font-size: 14px;',
            '    font-weight: 600;',
            '    color: #fbb500;',
            '    text-transform: uppercase;',
            '    letter-spacing: 1px;',
            '    margin: 0;',
            '}',
            '.nb-panel-close {',
            '    background: transparent;',
            '    border: none;',
            '    color: #fbb500;',
            '    font-size: 24px;',
            '    cursor: pointer;',
            '    padding: 0;',
            '    width: 30px;',
            '    height: 30px;',
            '    display: flex;',
            '    align-items: center;',
            '    justify-content: center;',
            '    transition: all 0.2s;',
            '}',
            '.nb-panel-close:hover { background: rgba(251,181,0,0.2); }',

            /* Panel content */
            '.nb-panel-content {',
            '    flex: 1;',
            '    overflow-y: auto;',
            '    padding: 8px;',
            '}',

            /* ===== Notification items (in panel) ===== */
            '.nb-item {',
            '    background: rgba(255,255,255,0.05);',
            '    border-left: 3px solid #3498db;',
            '    padding: 12px;',
            '    margin-bottom: 8px;',
            '    border-radius: 0 4px 4px 0;',
            '    transition: opacity 0.3s;',
            '}',
            '.nb-item-warning { border-left-color: #f39c12; }',
            '.nb-item-alert { border-left-color: #e74c3c; }',
            '.nb-item-success { border-left-color: #27ae60; }',
            '.nb-item-info { border-left-color: #3498db; }',

            '.nb-item-header {',
            '    display: flex;',
            '    align-items: flex-start;',
            '    justify-content: space-between;',
            '    gap: 8px;',
            '    margin-bottom: 6px;',
            '}',
            '.nb-item-title {',
            '    font-weight: 600;',
            '    color: #fff;',
            '    font-size: 13px;',
            '    display: flex;',
            '    align-items: center;',
            '    gap: 6px;',
            '    flex: 1;',
            '}',
            '.nb-item-time {',
            '    font-size: 11px;',
            '    color: rgba(255,255,255,0.4);',
            '    white-space: nowrap;',
            '    flex-shrink: 0;',
            '}',
            '.nb-item-message {',
            '    font-size: 12px;',
            '    color: rgba(255,255,255,0.7);',
            '    line-height: 1.4;',
            '}',

            /* ===== Empty state ===== */
            '.nb-empty {',
            '    text-align: center;',
            '    color: rgba(255,255,255,0.4);',
            '    padding: 40px 20px;',
            '    font-size: 14px;',
            '}',
            '.nb-empty-icon {',
            '    font-size: 32px;',
            '    margin-bottom: 8px;',
            '}',

            /* ===== Overlay ===== */
            '.nb-overlay {',
            '    position: fixed;',
            '    top: 0; left: 0; right: 0; bottom: 0;',
            '    z-index: 99999;',
            '    display: none;',
            '}',
            '.nb-overlay.nb-visible { display: block; }'
        ].join('\n');

        var style = document.createElement('style');
        style.id = 'notification-bell-styles';
        style.textContent = css;
        document.head.appendChild(style);
    }

    function injectBell() {
        // Create bell button
        var bell = document.createElement('button');
        bell.id = 'nbBell';
        bell.title = 'Notifications';

        var tabBarActions = document.querySelector('.tab-bar-actions');
        if (tabBarActions) {
            // Terminal page - match panel-toggle style
            bell.className = 'nb-bell-tab';
            bell.innerHTML = '\uD83D\uDD14 <span class="nb-badge" id="nbBadge" style="display:none;">0</span>';
            tabBarActions.appendChild(bell);
        } else {
            // Home page - try to embed inside system-status-badge
            var statusBadge = document.getElementById('system-status-badge');
            if (statusBadge) {
                // Add divider and bell inside the existing badge
                var divider = document.createElement('span');
                divider.className = 'nb-divider';
                statusBadge.appendChild(divider);

                bell.className = 'nb-bell-inline';
                bell.innerHTML = '\uD83D\uDD14<span class="nb-badge" id="nbBadge" style="display:none;">0</span>';
                statusBadge.appendChild(bell);
            } else {
                // Fallback - fixed position
                bell.className = 'nb-bell-fixed';
                bell.innerHTML = '\uD83D\uDD14<span class="nb-badge" id="nbBadge" style="display:none;">0</span>';
                document.body.appendChild(bell);
            }
        }

        // Create overlay for click-outside-to-close
        var overlay = document.createElement('div');
        overlay.className = 'nb-overlay';
        overlay.id = 'nbOverlay';
        document.body.appendChild(overlay);

        // Create notification panel
        var panel = document.createElement('div');
        panel.className = 'nb-panel';
        panel.id = 'nbPanel';
        panel.innerHTML = [
            '<div class="nb-panel-header">',
            '    <h3>Notifications</h3>',
            '    <button class="nb-panel-close" id="nbClose">&times;</button>',
            '</div>',
            '<div class="nb-panel-content" id="nbContent">',
            '    <div class="nb-empty">',
            '        <div class="nb-empty-icon">\uD83D\uDD14</div>',
            '        <div>No notifications</div>',
            '    </div>',
            '</div>'
        ].join('\n');
        document.body.appendChild(panel);

        // Event listeners
        bell.addEventListener('click', function(e) {
            e.stopPropagation();
            togglePanel();
        });
        document.getElementById('nbClose').addEventListener('click', closePanel);
        overlay.addEventListener('click', closePanel);
    }

    function togglePanel() {
        if (panelOpen) closePanel();
        else openPanel();
    }

    function openPanel() {
        document.getElementById('nbPanel').classList.add('nb-visible');
        document.getElementById('nbOverlay').classList.add('nb-visible');
        document.getElementById('nbBell').classList.add('nb-active');
        panelOpen = true;
    }

    function closePanel() {
        document.getElementById('nbPanel').classList.remove('nb-visible');
        document.getElementById('nbOverlay').classList.remove('nb-visible');
        document.getElementById('nbBell').classList.remove('nb-active');
        panelOpen = false;
    }

    function fetchAnnouncements() {
        fetch('/announcements')
            .then(function(r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                return r.json();
            })
            .then(function(data) {
                var active = [];
                if (data && Array.isArray(data.active_announcements)) {
                    active = data.active_announcements;
                } else if (Array.isArray(data)) {
                    active = data;
                }
                announcements = active;
                updateUI();
            })
            .catch(function(e) {
                console.warn('[NotificationBell] Error fetching announcements:', e);
            });
    }

    /* ===== Banner: show top-priority undismissed announcement ===== */

    function renderBanner() {
        // Remove any existing banner
        var existing = document.getElementById('nbBanner');
        if (existing) existing.remove();

        var dismissed = getDismissed();
        var undismissed = announcements.filter(function(a) {
            return dismissed.indexOf(a.id) === -1;
        });

        // Sort by priority descending, pick the top one
        undismissed.sort(function(a, b) {
            return (b.priority || 0) - (a.priority || 0);
        });

        if (undismissed.length === 0) return;

        var ann = undismissed[0];
        var icon = getIcon(ann.type);
        var typeClass = 'nb-banner-' + (ann.type || 'info');

        var banner = document.createElement('div');
        banner.className = 'nb-banner ' + typeClass;
        banner.id = 'nbBanner';
        banner.setAttribute('data-id', ann.id);

        banner.innerHTML = [
            '<div class="nb-banner-content">',
            '    <span class="nb-banner-icon">' + icon + '</span>',
            '    <div class="nb-banner-text">',
            '        <div class="nb-banner-title">' + escapeHtml(ann.title) + '</div>',
            '        <div class="nb-banner-message">' + escapeHtml(ann.message) + '</div>',
            '    </div>',
            '</div>',
            ann.dismissible !== false
                ? '<button class="nb-banner-close" id="nbBannerClose">\u2715</button>'
                : ''
        ].join('\n');

        document.body.appendChild(banner);

        // Attach close handler
        var closeBtn = document.getElementById('nbBannerClose');
        if (closeBtn) {
            closeBtn.addEventListener('click', function() {
                dismissBanner(ann);
            });
        }
    }

    function dismissBanner(ann) {
        // 1. Add to dismissed IDs
        var dismissed = getDismissed();
        if (dismissed.indexOf(ann.id) === -1) {
            dismissed.push(ann.id);
            saveDismissed(dismissed);
        }

        // 2. Add to notification history (dismissedNotifications)
        var notifications = getNotifications();
        var notification = {
            id: ann.id,
            title: ann.title || '',
            message: ann.message || '',
            type: ann.type || 'info',
            dismissedAt: new Date().toISOString()
        };
        notifications.unshift(notification);
        // Keep max 50 notifications
        if (notifications.length > 50) {
            notifications = notifications.slice(0, 50);
        }
        saveNotifications(notifications);

        // 3. Remove banner with animation
        var banner = document.getElementById('nbBanner');
        if (banner) {
            banner.style.transition = 'transform 0.3s ease, opacity 0.3s ease';
            banner.style.transform = 'translateY(-100%)';
            banner.style.opacity = '0';
            setTimeout(function() { banner.remove(); }, 300);
        }

        // 4. Re-render (show next banner if any, update badge/panel)
        setTimeout(function() {
            renderBanner();
            updateBadge();
            updatePanel();
        }, 350);
    }

    /* ===== Badge: shows count of notifications in history ===== */

    function updateBadge() {
        var notifications = getNotifications();
        var badge = document.getElementById('nbBadge');
        if (!badge) return;

        if (notifications.length > 0) {
            badge.textContent = notifications.length > 99 ? '99+' : notifications.length;
            badge.style.display = '';
        } else {
            badge.style.display = 'none';
        }
    }

    /* ===== Panel: shows dismissed notification history ===== */

    function updatePanel() {
        var content = document.getElementById('nbContent');
        if (!content) return;

        var notifications = getNotifications();

        if (notifications.length === 0) {
            content.innerHTML = '<div class="nb-empty"><div class="nb-empty-icon">\uD83D\uDD14</div><div>No notifications yet</div></div>';
            return;
        }

        var html = '';
        notifications.forEach(function(n) {
            var icon = getIcon(n.type);
            var typeClass = 'nb-item-' + (n.type || 'info');
            var time = n.dismissedAt ? getTimeAgo(n.dismissedAt) : '';

            html += '<div class="nb-item ' + typeClass + '">';
            html += '<div class="nb-item-header">';
            html += '<span class="nb-item-title"><span>' + icon + '</span> ' + escapeHtml(n.title) + '</span>';
            if (time) html += '<span class="nb-item-time">' + time + '</span>';
            html += '</div>';
            html += '<div class="nb-item-message">' + escapeHtml(n.message) + '</div>';
            html += '</div>';
        });

        content.innerHTML = html;
    }

    /* ===== Main UI update (called after fetch) ===== */

    function updateUI() {
        renderBanner();
        updateBadge();
        updatePanel();
    }
})();

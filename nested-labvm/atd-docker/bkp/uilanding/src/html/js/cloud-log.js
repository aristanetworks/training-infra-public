/**
 * Cloud Logging helper - sends client-side events to server via sendBeacon.
 * Fire-and-forget, non-blocking, zero UX impact.
 *
 * Usage:
 *   cloudLog('info', 'Device SSH opened', { source: 'terminal-manager', device: 'spine1' });
 *   cloudLog('error', 'WebSocket failed', { source: 'atd-ws', action: 'ws_disconnect' });
 */
(function(window) {
    'use strict';

    function cloudLog(level, message, extra) {
        try {
            var payload = {
                level: level || 'info',
                message: message || ''
            };
            if (extra) {
                for (var key in extra) {
                    if (extra.hasOwnProperty(key)) {
                        payload[key] = extra[key];
                    }
                }
            }
            if (navigator.sendBeacon) {
                navigator.sendBeacon('/td-api/client-log', JSON.stringify(payload));
            }
        } catch(e) {
            // Never break the app due to logging
        }
    }

    window.cloudLog = cloudLog;
})(window);

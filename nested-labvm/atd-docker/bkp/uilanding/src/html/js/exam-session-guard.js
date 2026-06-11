/**
 * Exam Session Guard
 * Prevents multiple tabs/windows from running the same exam simultaneously
 */

class ExamSessionGuard {
    constructor() {
        this.STORAGE_KEY = 'exam_session_active';
        this.SESSION_ID_KEY = 'exam_session_id';
        this.HEARTBEAT_KEY = 'exam_session_heartbeat';
        this.HEARTBEAT_INTERVAL = 2000; // 2 seconds
        this.HEARTBEAT_TIMEOUT = 5000; // 5 seconds
        this.sessionId = null;
        this.heartbeatTimer = null;
    }

    /**
     * Generate a unique session ID
     */
    generateSessionId() {
        return `exam_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    /**
     * Check if an exam session is already active in another tab
     */
    isExamRunningInAnotherTab() {
        const activeSession = localStorage.getItem(this.STORAGE_KEY);
        const sessionId = localStorage.getItem(this.SESSION_ID_KEY);
        const lastHeartbeat = localStorage.getItem(this.HEARTBEAT_KEY);

        if (!activeSession || activeSession !== 'true') {
            return false;
        }

        // Check if this is our session
        if (this.sessionId && sessionId === this.sessionId) {
            return false;
        }

        // Check if the heartbeat is recent (session is still alive)
        if (lastHeartbeat) {
            const timeSinceLastHeartbeat = Date.now() - parseInt(lastHeartbeat);
            if (timeSinceLastHeartbeat < this.HEARTBEAT_TIMEOUT) {
                // Session is active in another tab
                return true;
            }
        }

        // Heartbeat is stale, session is dead - clean it up
        // This fixes the issue where second exam attempts are blocked by stale sessions
        console.log('Stale exam session detected, cleaning up localStorage...');
        localStorage.removeItem(this.STORAGE_KEY);
        localStorage.removeItem(this.SESSION_ID_KEY);
        localStorage.removeItem(this.HEARTBEAT_KEY);

        return false;
    }

    /**
     * Start the exam session (call when exam authentication begins)
     */
    startExamSession() {
        if (this.isExamRunningInAnotherTab()) {
            console.warn('Exam is already running in another tab!');
            return false;
        }

        // Create a new session
        this.sessionId = this.generateSessionId();
        localStorage.setItem(this.STORAGE_KEY, 'true');
        localStorage.setItem(this.SESSION_ID_KEY, this.sessionId);
        localStorage.setItem(this.HEARTBEAT_KEY, Date.now().toString());

        console.log('Exam session started:', this.sessionId);

        // Start heartbeat
        this.startHeartbeat();

        // Listen for storage events (another tab trying to start)
        window.addEventListener('storage', this.handleStorageEvent.bind(this));

        // Clean up on page unload
        window.addEventListener('beforeunload', this.endExamSession.bind(this));

        return true;
    }

    /**
     * Send periodic heartbeats to indicate this session is alive
     */
    startHeartbeat() {
        this.heartbeatTimer = setInterval(() => {
            if (this.sessionId) {
                localStorage.setItem(this.HEARTBEAT_KEY, Date.now().toString());
            }
        }, this.HEARTBEAT_INTERVAL);
    }

    /**
     * Stop heartbeat
     */
    stopHeartbeat() {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    }

    /**
     * Handle storage events (another tab trying to interfere)
     */
    handleStorageEvent(event) {
        // If another tab is trying to start a session, alert the user
        if (event.key === this.SESSION_ID_KEY && event.newValue && event.newValue !== this.sessionId) {
            console.warn('Another tab attempted to start an exam session');
            this.showWarningInCurrentTab();
        }
    }

    /**
     * Show warning in the current (original) tab
     */
    showWarningInCurrentTab() {
        const warning = document.createElement('div');
        warning.style.cssText = `
            position: fixed;
            top: 20px;
            left: 50%;
            transform: translateX(-50%);
            background: #dc3545;
            color: white;
            padding: 15px 30px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.3);
            z-index: 10000;
            font-family: Arial, sans-serif;
            font-size: 16px;
            font-weight: bold;
            text-align: center;
        `;
        warning.innerHTML = '⚠️ Warning: Exam was opened in another tab. Close the other tab immediately!';
        document.body.appendChild(warning);

        setTimeout(() => {
            warning.remove();
        }, 10000);
    }

    /**
     * End the exam session (call when exam is submitted or window closes)
     */
    endExamSession() {
        console.log('Ending exam session:', this.sessionId);

        // Stop heartbeat
        this.stopHeartbeat();

        // Clear session data only if it's our session
        const currentSessionId = localStorage.getItem(this.SESSION_ID_KEY);
        if (currentSessionId === this.sessionId) {
            localStorage.removeItem(this.STORAGE_KEY);
            localStorage.removeItem(this.SESSION_ID_KEY);
            localStorage.removeItem(this.HEARTBEAT_KEY);
        }

        this.sessionId = null;

        // Remove event listeners
        window.removeEventListener('storage', this.handleStorageEvent.bind(this));
        window.removeEventListener('beforeunload', this.endExamSession.bind(this));
    }

    /**
     * Check and redirect if exam is running in another tab
     * Call this on page load of exam authentication page
     */
    checkAndRedirect() {
        if (this.isExamRunningInAnotherTab()) {
            console.warn('Exam is already running in another tab, redirecting...');
            window.location.href = '/exam-already-running';
            return true;
        }
        return false;
    }
}

// Export as global and ES module
window.ExamSessionGuard = ExamSessionGuard;
export default ExamSessionGuard;

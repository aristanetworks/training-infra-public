/**
 * Console Manager - Serial Console Terminal
 *
 * Manages WebSocket connections to virsh console for VM serial access.
 * Uses xterm.js for terminal rendering.
 *
 * This module is loaded by console.html and provides:
 * - Terminal initialization with proper font handling
 * - WebSocket connection to console-api service
 * - Automatic reconnection with exponential backoff
 * - Terminal resize handling
 */

const ConsoleManager = {
  term: null,
  ws: null,
  fitAddon: null,
  currentDevice: null,
  reconnectAttempts: 0,
  maxReconnectAttempts: 3,

  // Terminal configuration
  terminalConfig: {
    cursorBlink: true,
    cursorStyle: 'block',
    fontFamily: "'JetBrains Mono', Menlo, Monaco, 'Cascadia Code', Consolas, monospace",
    fontSize: 14,
    lineHeight: 1.1,
    theme: {
      background: '#000000',
      foreground: '#ffffff',
      cursor: '#ffffff',
      cursorAccent: '#000000',
      selectionBackground: '#3a3d41',
    },
    allowProposedApi: true
  },

  /**
   * Initialize the console manager.
   * Sets up xterm.js terminal and event handlers.
   */
  init() {
    // Initialize xterm.js
    this.term = new Terminal(this.terminalConfig);

    this.fitAddon = new FitAddon.FitAddon();
    const webLinksAddon = new WebLinksAddon.WebLinksAddon();

    this.term.loadAddon(this.fitAddon);
    this.term.loadAddon(webLinksAddon);

    this.term.open(document.getElementById('terminal'));
    this.fitAddon.fit();

    // Handle terminal resize
    window.addEventListener('resize', () => this.handleResize());

    // Setup retry button handler
    const retryBtn = document.getElementById('retryBtn');
    if (retryBtn) {
      retryBtn.addEventListener('click', () => {
        if (this.currentDevice) {
          document.getElementById('errorOverlay').classList.add('hidden');
          this.connect(this.currentDevice);
        }
      });
    }

    // Check URL for device parameter
    const urlParams = new URLSearchParams(window.location.search);
    const device = urlParams.get('device');

    if (device) {
      this.connect(device);
    } else {
      // No device specified - show redirect to terminal page
      document.getElementById('connectingOverlay').classList.add('hidden');
      document.getElementById('noDeviceOverlay').classList.remove('hidden');
    }
  },

  /**
   * Connect to a device's serial console via WebSocket.
   * @param {string} device - The VM name to connect to
   */
  async connect(device) {
    this.currentDevice = device;
    this.reconnectAttempts++;

    // Update UI
    document.getElementById('connectingDevice').textContent = device;
    document.getElementById('connectingOverlay').classList.remove('hidden');
    document.getElementById('errorOverlay').classList.add('hidden');
    document.getElementById('noDeviceOverlay').classList.add('hidden');

    // Close existing connection if any
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }

    // Clear terminal
    this.term.clear();
    this.term.write('\x1b[2J\x1b[H'); // Clear screen and move cursor home
    this.term.write(`Connecting to ${device}...\r\n`);

    try {
      // Build WebSocket URL
      const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const wsUrl = `${wsProtocol}//${window.location.host}/console-api/ws/console/${device}`;

      this.ws = new WebSocket(wsUrl);
      this.ws.binaryType = 'arraybuffer';

      this.ws.onopen = () => {
        console.log('WebSocket connected');
        document.getElementById('connectingOverlay').classList.add('hidden');
        this.reconnectAttempts = 0;

        // Send initial resize
        this.sendResize();

        // Focus terminal
        this.term.focus();
      };

      this.ws.onmessage = (event) => {
        if (event.data instanceof ArrayBuffer) {
          const decoder = new TextDecoder();
          this.term.write(decoder.decode(event.data));
        } else {
          this.term.write(event.data);
        }
      };

      this.ws.onclose = (event) => {
        console.log('WebSocket closed:', event.code, event.reason);

        if (event.code !== 1000 && event.code !== 1001) {
          // Abnormal close - attempt reconnect with exponential backoff
          if (this.reconnectAttempts < this.maxReconnectAttempts) {
            const delay = this.getReconnectDelay();
            this.term.write(`\r\n\x1b[33mConnection lost. Reconnecting in ${delay/1000}s...\x1b[0m\r\n`);
            setTimeout(() => this.connect(device), delay);
          } else {
            this.showError('Connection lost after multiple attempts');
          }
        } else {
          this.term.write('\r\n\x1b[33mConnection closed.\x1b[0m\r\n');
        }
      };

      this.ws.onerror = (error) => {
        console.error('WebSocket error:', error);
        document.getElementById('connectingOverlay').classList.add('hidden');
        this.showError('Failed to connect to console service');
      };

      // Handle terminal input
      this.term.onData((data) => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          this.ws.send(data);
        }
      });

    } catch (error) {
      console.error('Connection error:', error);
      document.getElementById('connectingOverlay').classList.add('hidden');
      this.showError(error.message || 'Failed to establish connection');
    }
  },

  /**
   * Calculate reconnection delay with exponential backoff.
   * @returns {number} Delay in milliseconds
   */
  getReconnectDelay() {
    // Exponential backoff: 1s, 2s, 4s, max 10s
    return Math.min(1000 * Math.pow(2, this.reconnectAttempts - 1), 10000);
  },

  /**
   * Handle terminal resize events.
   */
  handleResize() {
    if (this.fitAddon) {
      this.fitAddon.fit();
      this.sendResize();
    }
  },

  /**
   * Send terminal size to the server.
   */
  sendResize() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN && this.term) {
      const resizeMsg = JSON.stringify({
        type: 'resize',
        cols: this.term.cols,
        rows: this.term.rows
      });
      this.ws.send(resizeMsg);
    }
  },

  /**
   * Show error overlay with a message.
   * @param {string} message - Error message to display
   */
  showError(message) {
    document.getElementById('errorMessage').textContent = message;
    document.getElementById('errorOverlay').classList.remove('hidden');
  }
};

/**
 * Wait for fonts to load before initializing terminal.
 * This ensures xterm.js calculates character cell width correctly.
 * Includes timeout fallback in case font loading hangs.
 */
async function waitForFonts() {
  const FONT_LOAD_TIMEOUT = 3000; // 3 seconds max wait

  if (document.fonts && document.fonts.ready) {
    const fontLoadTimeout = new Promise(resolve => setTimeout(resolve, FONT_LOAD_TIMEOUT));
    await Promise.race([document.fonts.ready, fontLoadTimeout]);
  }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', async () => {
  await waitForFonts();
  ConsoleManager.init();
});

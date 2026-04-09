/**
 * Terminal Manager - Multi-tab terminal management for ATL
 *
 * Handles device terminals (SSH and console), tab management,
 * split view, topology integration, and device tree sidebar.
 */

// Terminal Tab Manager
const TerminalManager = {
  tabs: [],
  activeTabId: null,
  splitMode: false,
  splitLeft: { device: null, iframe: null },
  splitRight: { device: null, iframe: null },
  nextSplitPane: 'left', // Alternates between 'left' and 'right'
  autoFocusEnabled: false, // Auto-focus topology on active terminal
  _tabCounter: 0, // Monotonic counter for unique tab IDs
  _pendingNoVnc: new Set(), // Track in-flight noVNC opens to prevent async race
  _sshQueue: [], // Queued SSH opens — serialized to avoid WebSSH2 session race
  _sshQueueProcessing: false, // Whether the queue is currently draining
  // Tunable delay (ms) after iframe load before starting the next tab.
  // Adjust via browser console: TerminalManager._sshSettleMs = 1000
  _sshSettleMs: 2000,

  init() {
    this.loadDevices();
    this.setupJumpServer();
    this.setupPanelToggles();
    this.setupSidebarToggle();
    this.setupTabOverflow();
    this.setupSplitView();
  },

  setupJumpServer() {
    const jumpLink = document.getElementById('jumpServerLink');
    jumpLink.addEventListener('click', () => {
      const ip = jumpLink.dataset.ip;
      const name = jumpLink.dataset.name;
      this.openTerminal(name, ip, 'ssh');  // Jump server only supports SSH
    });
  },

  async loadDevices() {
    const deviceGroups = document.getElementById('deviceGroups');

    try {
      const response = await fetch('/td-api/devices');
      const data = await response.json();

      // Check for HTTP errors with improved backend response
      if (!response.ok) {
        const errorMsg = data.error || `HTTP ${response.status}`;
        const errorDetail = data.detail || response.statusText;
        const showRetry = data.retry !== false; // Default to showing retry

        this.showDeviceLoadError(deviceGroups, errorMsg, errorDetail, showRetry);
        return;
      }

      // Validate response structure
      if (!data.groups || !Array.isArray(data.groups)) {
        throw new Error('Invalid response: missing groups array');
      }

      this.renderDeviceTree(data.groups);
    } catch (error) {
      console.error('Failed to load devices:', error);
      this.showDeviceLoadError(deviceGroups, 'Failed to load devices', error.message, true);
    }
  },

  /**
   * Display error message in device groups area
   * @param {HTMLElement} container - Container element
   * @param {string} title - Error title
   * @param {string} detail - Error detail message
   * @param {boolean} showRetry - Whether to show retry button
   */
  showDeviceLoadError(container, title, detail, showRetry) {
    const retryButton = showRetry
      ? '<button class="retry-btn" onclick="TerminalManager.loadDevices()">Retry</button>'
      : '';

    container.innerHTML = `
      <div class="device-load-error">
        <p>${title}</p>
        <p class="error-detail">${detail}</p>
        ${retryButton}
      </div>
    `;
  },

  renderDeviceTree(groups) {
    const tree = document.getElementById('deviceGroups');
    tree.innerHTML = '';

    groups.forEach(group => {
      const groupEl = document.createElement('div');
      groupEl.className = 'device-group';

      const headerEl = document.createElement('div');
      headerEl.className = 'group-header';
      headerEl.setAttribute('role', 'button');
      headerEl.setAttribute('aria-expanded', 'true');

      const arrowSpan = document.createElement('span');
      arrowSpan.className = 'arrow';
      arrowSpan.innerHTML = '&#9660;';

      const nameSpan = document.createElement('span');
      nameSpan.className = 'group-name';
      nameSpan.textContent = group.group;

      const openAllSpan = document.createElement('span');
      openAllSpan.className = 'group-open-all';
      openAllSpan.textContent = 'All';
      openAllSpan.title = 'Open SSH to all devices in this group';

      headerEl.appendChild(arrowSpan);
      headerEl.appendChild(nameSpan);
      headerEl.appendChild(openAllSpan);

      const devicesEl = document.createElement('div');
      devicesEl.className = 'group-devices';
      devicesEl.setAttribute('role', 'list');

      group.devices.forEach(device => {
        const deviceEl = document.createElement('div');
        deviceEl.className = 'device-item';
        deviceEl.setAttribute('role', 'listitem');
        deviceEl.dataset.ip = device.ip;
        deviceEl.dataset.name = device.name;
        deviceEl.dataset.vmName = device.vmName || device.name;  // Original VM name for virsh
        deviceEl.dataset.supportsConsole = device.supportsConsole ? 'true' : 'false';
        deviceEl.dataset.supportsNoVnc = device.supportsNoVnc ? 'true' : 'false';
        deviceEl.dataset.supportsWebUI = device.supportsWebUI ? 'true' : 'false';
        deviceEl.tabIndex = 0;

        // Build HTML with stacked status dots and action icons
        // Show dots for available connection types: SSH (all), Console (if supported), noVNC (if supported), Web UI (if supported)
        let html = `
          <span class="status-dots" aria-hidden="true">
            <span class="status-dot ssh" title="SSH"></span>
            ${device.supportsConsole ? '<span class="status-dot console" title="Console"></span>' : ''}
            ${device.supportsNoVnc ? '<span class="status-dot novnc" title="Desktop"></span>' : ''}
            ${device.supportsWebUI ? '<span class="status-dot webui" title="Web UI"></span>' : ''}
          </span>
          <span class="device-name">${device.name}</span>
          <span class="device-ip">${device.ip}</span>
        `;

        // Add desktop icon for Linux hosts (noVNC)
        if (device.supportsNoVnc) {
          html += `<span class="desktop-icon" title="Open Desktop (noVNC)" aria-label="Open desktop for ${device.name}">&#128421;</span>`;
        }

        // Add console icon if device supports console
        if (device.supportsConsole) {
          html += `<span class="console-icon" title="Open Serial Console" aria-label="Open serial console for ${device.name}">&#9000;</span>`;
        }

        // Add Web UI icon for VeloCloud Orchestrator
        if (device.supportsWebUI) {
          html += `<span class="webui-icon" title="Open Web UI" aria-label="Open web UI for ${device.name}">&#127760;</span>`;
        }

        deviceEl.innerHTML = html;

        // Left-click on device name area
        // For Linux hosts (supportsNoVnc), open desktop by default
        // For other devices, open SSH
        const openTerminalHandler = (e) => {
          // Don't trigger if clicking on action icons
          if (e.target.classList.contains('console-icon') ||
              e.target.classList.contains('desktop-icon') ||
              e.target.classList.contains('webui-icon')) return;

          console.log(`%c[DEBUG click] device="${device.name}" ip="${device.ip}" target=${e.target.className} time=${performance.now().toFixed(2)}ms`, 'color: #fbb500');

          if (device.supportsNoVnc) {
            // Linux hosts: open desktop by default
            const vmName = device.vmName || device.name;
            this.openTerminal(device.name, device.ip, 'novnc', vmName);
          } else {
            // Other devices: open SSH
            this.openTerminal(device.name, device.ip, 'ssh');
          }
        };
        deviceEl.addEventListener('click', openTerminalHandler);

        // Click on desktop icon opens noVNC
        if (device.supportsNoVnc) {
          const desktopIcon = deviceEl.querySelector('.desktop-icon');
          if (desktopIcon) {
            desktopIcon.addEventListener('click', (e) => {
              e.stopPropagation();
              const vmName = device.vmName || device.name;
              this.openTerminal(device.name, device.ip, 'novnc', vmName);
            });
          }
        }

        // Click on console icon opens console
        if (device.supportsConsole) {
          const consoleIcon = deviceEl.querySelector('.console-icon');
          if (consoleIcon) {
            consoleIcon.addEventListener('click', (e) => {
              e.stopPropagation();
              // Use vmName for console (original name for virsh)
              const vmName = device.vmName || device.name;
              this.openTerminal(device.name, device.ip, 'console', vmName);
            });
          }
        }

        // Click on webui icon opens VCO web UI in new tab
        if (device.supportsWebUI) {
          const webuiIcon = deviceEl.querySelector('.webui-icon');
          if (webuiIcon) {
            webuiIcon.addEventListener('click', (e) => {
              e.stopPropagation();
              // Open VCO web UI in a new browser tab
              window.open('/vco/', '_blank');
            });
          }
        }

        // Right-click shows context menu
        deviceEl.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          this.showContextMenu(e, device);
        });

        // Keyboard handler - same logic as click
        deviceEl.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (device.supportsNoVnc) {
              const vmName = device.vmName || device.name;
              this.openTerminal(device.name, device.ip, 'novnc', vmName);
            } else {
              this.openTerminal(device.name, device.ip, 'ssh');
            }
          }
        });

        devicesEl.appendChild(deviceEl);
      });

      // "Open All" button click
      openAllSpan.addEventListener('click', (e) => {
        e.stopPropagation();
        this.openAllInGroup(devicesEl);
      });

      // Group header collapse toggle
      headerEl.addEventListener('click', (e) => {
        // Don't toggle collapse when clicking "Open All"
        if (e.target.classList.contains('group-open-all')) return;
        const isCollapsed = headerEl.classList.toggle('collapsed');
        headerEl.setAttribute('aria-expanded', !isCollapsed);
        devicesEl.classList.toggle('hidden');
      });

      groupEl.appendChild(headerEl);
      groupEl.appendChild(devicesEl);
      tree.appendChild(groupEl);
    });
  },

  openTerminal(name, ip, type = 'ssh', vmName = null) {
    // vmName is the original name for virsh console (defaults to name if not provided)
    const effectiveVmName = vmName || name;
    const callTime = performance.now();

    console.log(`%c[DEBUG openTerminal] ENTER name="${name}" ip="${ip}" type="${type}" time=${callTime.toFixed(2)}ms`, 'color: #fbb500; font-weight: bold');
    console.log(`[DEBUG openTerminal]   tabs.length=${this.tabs.length} _tabCounter=${this._tabCounter} activeTabId=${this.activeTabId}`);
    console.log(`[DEBUG openTerminal]   current tabs:`, this.tabs.map(t => `${t.id}(${t.name}/${t.type})`).join(', '));

    // If in split mode, open in split pane instead
    if (this.splitMode) {
      console.log(`[DEBUG openTerminal]   -> split mode, delegating`);
      this.openInSplitPane(name, ip, type, effectiveVmName);
      return;
    }

    // Check if tab already exists for this device AND type
    // Allow one SSH, one Console, and one noVNC tab per device
    // Match by name (unique device identifier) not IP (can be shared/empty)
    const existingTab = this.tabs.find(t => t.name === name && t.type === type);
    if (existingTab) {
      console.log(`[DEBUG openTerminal]   -> DUPLICATE found: ${existingTab.id} for "${existingTab.name}" type=${existingTab.type}, activating`);
      this.activateTab(existingTab.id);
      return;
    }

    // For noVNC, we need to get a token first (async — guard against duplicate opens)
    if (type === 'novnc') {
      const pendingKey = name + ':novnc';
      if (this._pendingNoVnc.has(pendingKey)) {
        console.log(`[DEBUG openTerminal]   -> noVNC pending guard hit for "${name}"`);
        return;
      }
      this._pendingNoVnc.add(pendingKey);
      console.log(`[DEBUG openTerminal]   -> noVNC async path for "${name}"`);
      this.openNoVncTerminal(name, ip, effectiveVmName).finally(() => {
        this._pendingNoVnc.delete(pendingKey);
      });
      return;
    }

    // Queue the SSH/console open — WebSSH2 stores the target host in an express
    // session shared by all iframes. Opening multiple iframes simultaneously
    // causes session overwrites (last request wins), connecting tabs to the wrong
    // host. Serializing ensures each iframe's HTTP request + WebSocket handshake
    // completes before the next one starts.
    this._sshQueue.push({ name, ip, type, vmName: effectiveVmName });
    console.log(`[DEBUG openTerminal]   -> QUEUED for "${name}" (queue length=${this._sshQueue.length})`);

    // Mark device as queued in sidebar
    this._setSidebarQueueState(name, 'queued');

    this._processSshQueue();

    console.log(`[DEBUG openTerminal] EXIT name="${name}" elapsed=${(performance.now() - callTime).toFixed(2)}ms`);
  },

  /**
   * Process the SSH open queue one at a time.
   * Each iframe must fully load (WebSSH2 page + WebSocket handshake) before
   * the next one is created, to avoid express-session race conditions.
   */
  async _processSshQueue() {
    if (this._sshQueueProcessing) return;
    this._sshQueueProcessing = true;

    while (this._sshQueue.length > 0) {
      const { name, ip, type, vmName } = this._sshQueue.shift();

      // Re-check for duplicate (may have been opened while queued)
      if (this.tabs.find(t => t.name === name && t.type === type)) {
        console.log(`[DEBUG _processSshQueue] skip duplicate "${name}" type=${type}`);
        this._setSidebarQueueState(name, null);
        continue;
      }

      console.log(`%c[DEBUG _processSshQueue] PROCESSING "${name}" ip=${ip} type=${type} (remaining=${this._sshQueue.length})`, 'color: #78d82c; font-weight: bold');

      // Transition sidebar from queued → loading
      this._setSidebarQueueState(name, 'loading');

      await this._createTabAndWaitForLoad(name, ip, type, vmName);

      // Clear loading state (updateDeviceStatus inside _createTab sets connected)
      this._setSidebarQueueState(name, null);
    }

    this._sshQueueProcessing = false;
  },

  /**
   * Create a tab + iframe and wait for the iframe to finish loading.
   * Returns a promise that resolves when the iframe fires its 'load' event,
   * or after a timeout (so the queue doesn't stall forever).
   */
  _createTabAndWaitForLoad(name, ip, type, vmName) {
    return new Promise((resolve) => {
      const tabId = 'tab-' + (++this._tabCounter);
      const tab = { id: tabId, name, ip, type, vmName };
      this.tabs.push(tab);
      console.log(`[DEBUG _createTab] tabId="${tabId}" for "${name}" (counter=${this._tabCounter})`);

      // Create tab element
      const tabsScrollArea = document.getElementById('tabsScrollArea');
      const tabEl = document.createElement('div');
      tabEl.className = 'tab';
      tabEl.id = tabId;
      tabEl.dataset.type = type;
      tabEl.setAttribute('role', 'tab');
      tabEl.setAttribute('aria-selected', 'false');

      let displayName = name;
      let dotClass = 'ssh';
      if (type === 'console') {
        displayName = name + ' \u2328';  // keyboard icon
        dotClass = 'console';
      } else if (type === 'novnc') {
        displayName = name + ' \uD83D\uDDA5';  // desktop icon
        dotClass = 'novnc';
      }

      // Build tab content with safe DOM methods
      const dotSpan = document.createElement('span');
      dotSpan.className = 'tab-status-dot ' + dotClass;
      dotSpan.setAttribute('aria-hidden', 'true');

      const nameSpan = document.createElement('span');
      nameSpan.className = 'tab-name';
      nameSpan.textContent = displayName;

      const closeSpan = document.createElement('span');
      closeSpan.className = 'close-btn';
      closeSpan.title = 'Close';
      closeSpan.setAttribute('aria-label', 'Close ' + name + ' tab');
      closeSpan.textContent = '\u00D7';

      tabEl.appendChild(dotSpan);
      tabEl.appendChild(nameSpan);
      tabEl.appendChild(closeSpan);

      nameSpan.addEventListener('click', () => {
        this.activateTab(tabId);
      });
      closeSpan.addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeTab(tabId);
      });

      tabsScrollArea.appendChild(tabEl);

      // Create iframe
      const terminalFrames = document.getElementById('terminalFrames');
      const iframe = document.createElement('iframe');
      iframe.className = 'terminal-frame';
      iframe.id = 'frame-' + tabId;
      iframe.setAttribute('title', 'Terminal: ' + name + ' (' + type.toUpperCase() + ')');

      if (type === 'console') {
        iframe.src = '/console?device=' + encodeURIComponent(vmName);
      } else {
        iframe.src = '/ssh/host/' + ip;
      }

      console.log('[DEBUG _createTab] iframe src="' + iframe.src + '"');

      // Wait for iframe load (WebSSH2 page + WebSocket handshake completes)
      // or timeout after 5s so the queue doesn't stall
      let settled = false;
      const settle = () => {
        if (settled) return;
        settled = true;
        console.log('[DEBUG _createTab] "' + name + '" iframe settled, releasing queue');
        resolve();
      };

      iframe.addEventListener('load', () => {
        // Wait for WebSSH2's WebSocket handshake + SSH connection to complete.
        // The iframe 'load' fires when the HTML page is rendered, but the
        // WebSocket connect + SSH session establishment takes another 1-2s.
        // The session must be fully consumed before the next iframe's HTTP
        // request overwrites session.sshCredentials.host.
        // Tunable: TerminalManager._sshSettleMs = <value> in browser console
        console.log('[DEBUG _createTab] "' + name + '" load event, waiting ' + this._sshSettleMs + 'ms');
        setTimeout(settle, this._sshSettleMs);
      });
      // Safety timeout — don't block the queue forever
      setTimeout(settle, this._sshSettleMs + 6000);

      terminalFrames.appendChild(iframe);

      // Mark device as connected
      this.updateDeviceStatus(name, type, true);

      // Activate the new tab
      this.activateTab(tabId);

      // Hide empty state
      document.getElementById('emptyState').style.display = 'none';

      // Update overflow menu
      this.updateTabOverflow();
    });
  },

  /**
   * Set sidebar queue visual state for a device
   * @param {string} name - Device name
   * @param {string|null} state - 'queued', 'loading', or null to clear
   */
  _setSidebarQueueState(name, state) {
    const deviceEl = document.querySelector('.device-item[data-name="' + CSS.escape(name) + '"]');
    if (!deviceEl) return;
    deviceEl.classList.remove('ssh-queued', 'ssh-loading');
    if (state === 'queued') {
      deviceEl.classList.add('ssh-queued');
    } else if (state === 'loading') {
      deviceEl.classList.add('ssh-loading');
    }
  },

  /**
   * Open a noVNC desktop terminal for Linux hosts
   * Fetches a token from the API and opens the noVNC viewer
   */
  async openNoVncTerminal(name, ip, vmName) {
    try {
      // Get noVNC token from API
      const response = await fetch(`/td-api/nodes/novnc-token/${encodeURIComponent(vmName)}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || 'Failed to get VNC token');
      }

      const tokenData = await response.json();

      // Create new tab
      const tabId = 'tab-' + (++this._tabCounter);
      const tab = { id: tabId, name, ip, type: 'novnc', vmName };
      this.tabs.push(tab);

      // Create tab element
      const tabsScrollArea = document.getElementById('tabsScrollArea');
      const tabEl = document.createElement('div');
      tabEl.className = 'tab';
      tabEl.id = tabId;
      tabEl.dataset.type = 'novnc';
      tabEl.setAttribute('role', 'tab');
      tabEl.setAttribute('aria-selected', 'false');

      tabEl.innerHTML = `
        <span class="tab-status-dot novnc" aria-hidden="true"></span>
        <span class="tab-name">${name} &#128421;</span>
        <span class="close-btn" title="Close" aria-label="Close ${name} tab">&times;</span>
      `;

      tabEl.querySelector('.tab-name').addEventListener('click', () => this.activateTab(tabId));
      tabEl.querySelector('.close-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        this.closeTab(tabId);
      });

      tabsScrollArea.appendChild(tabEl);

      // Create iframe with noVNC URL
      const terminalFrames = document.getElementById('terminalFrames');
      const iframe = document.createElement('iframe');
      iframe.className = 'terminal-frame';
      iframe.id = 'frame-' + tabId;
      iframe.setAttribute('title', `Desktop: ${name} (noVNC)`);

      // Build noVNC URL with token
      // The noVNC client connects to websockify which authenticates with the token
      // path=websockify tells noVNC to connect to /websockify/ endpoint for the WebSocket
      const vncUrl = tokenData.novnc_url || `/novnc/vnc.html?autoconnect=true&resize=scale&path=websockify/?token=${encodeURIComponent(tokenData.token)}`;
      iframe.src = vncUrl;

      terminalFrames.appendChild(iframe);

      // Mark device as connected
      this.updateDeviceStatus(name, 'novnc', true);

      // Activate the new tab
      this.activateTab(tabId);

      // Hide empty state
      document.getElementById('emptyState').style.display = 'none';

      // Update overflow menu
      this.updateTabOverflow();

    } catch (error) {
      console.error('[TerminalManager] Failed to open noVNC terminal:', error);
      alert(`Failed to open desktop: ${error.message}`);
    }
  },

  activateTab(tabId) {
    const tabData = this.tabs.find(t => t.id === tabId);
    const tabName = tabData ? tabData.name : 'UNKNOWN';
    console.log(`%c[DEBUG activateTab] tabId="${tabId}" device="${tabName}" prev=${this.activeTabId} time=${performance.now().toFixed(2)}ms`, 'color: #4c5cae');

    // Deactivate all tabs
    const allTabs = document.querySelectorAll('.tab');
    const allFrames = document.querySelectorAll('.terminal-frame');
    console.log(`[DEBUG activateTab]   deactivating ${allTabs.length} tabs, ${allFrames.length} frames`);
    allTabs.forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    allFrames.forEach(f => f.classList.remove('active'));

    // Activate selected tab
    const tabEl = document.getElementById(tabId);
    const frameEl = document.getElementById('frame-' + tabId);

    if (tabEl) {
      tabEl.classList.add('active');
      tabEl.setAttribute('aria-selected', 'true');
      // Log what the tab element actually contains
      const tabNameEl = tabEl.querySelector('.tab-name');
      console.log(`[DEBUG activateTab]   tab DOM: id="${tabEl.id}" textContent="${tabNameEl ? tabNameEl.textContent.trim() : 'N/A'}"`);
    } else {
      console.error(`%c[DEBUG activateTab]   !!! TAB ELEMENT NOT FOUND for id="${tabId}" !!!`, 'color: red; font-weight: bold');
    }

    if (frameEl) {
      frameEl.classList.add('active');
      console.log(`[DEBUG activateTab]   frame DOM: id="${frameEl.id}" src="${frameEl.src}"`);
      // Focus the iframe so keyboard input goes to the terminal
      setTimeout(() => frameEl.focus(), 50);
    } else {
      console.error(`%c[DEBUG activateTab]   !!! FRAME ELEMENT NOT FOUND for id="frame-${tabId}" !!!`, 'color: red; font-weight: bold');
    }

    // Cross-reference: does the tab name match the frame's target?
    if (tabData && frameEl) {
      const expectedSrc = tabData.type === 'console'
        ? `/console?device=${encodeURIComponent(tabData.vmName)}`
        : `/ssh/host/${tabData.ip}`;
      const srcMatch = frameEl.src.includes(expectedSrc);
      if (!srcMatch) {
        console.error(`%c[DEBUG activateTab]   !!! MISMATCH !!! tab="${tabData.name}" expects src containing "${expectedSrc}" but frame.src="${frameEl.src}"`, 'color: red; font-weight: bold; font-size: 14px');
      } else {
        console.log(`[DEBUG activateTab]   src cross-ref OK: "${tabData.name}" -> "${expectedSrc}"`);
      }
    }

    this.activeTabId = tabId;

    // Highlight active tab's device in sidebar
    this.updateSidebarActiveDevice();

    // Update overflow menu to reflect active state
    this.updateTabOverflow();

    // Auto-focus topology on the active device if enabled
    this.updateTopologyFocus();
  },

  closeTab(tabId) {
    const tabIndex = this.tabs.findIndex(t => t.id === tabId);
    if (tabIndex === -1) return;

    const tab = this.tabs[tabIndex];

    // Remove tab element
    const tabEl = document.getElementById(tabId);
    if (tabEl) tabEl.remove();

    // Remove iframe
    const frameEl = document.getElementById('frame-' + tabId);
    if (frameEl) frameEl.remove();

    // Update device status for this connection type
    this.updateDeviceStatus(tab.name, tab.type || 'ssh', false);

    // Remove from tabs array
    this.tabs.splice(tabIndex, 1);

    // Activate another tab or show empty state
    if (this.tabs.length > 0) {
      const newActiveIndex = Math.min(tabIndex, this.tabs.length - 1);
      this.activateTab(this.tabs[newActiveIndex].id);
    } else {
      this.activeTabId = null;
      document.getElementById('emptyState').style.display = 'block';
      // Clear sidebar active highlight when no tabs remain
      this.updateSidebarActiveDevice();
    }

    // Update overflow menu
    this.updateTabOverflow();
  },

  /**
   * Check if a device has a specific connection type open
   * @param {string} name - Device name
   * @param {string} type - Connection type ('ssh', 'console', or 'novnc')
   * @returns {boolean} True if connection exists
   */
  hasConnectionType(name, type) {
    return this.tabs.some(t => t.name === name && t.type === type);
  },

  updateDeviceStatus(name, type, connected) {
    // Match by device name (unique identifier) not IP (can be shared/empty)
    const deviceEl = document.querySelector(`.device-item[data-name="${CSS.escape(name)}"]`);
    if (deviceEl) {
      // Check if there are other connections of different type still open
      const hasSSH = type === 'ssh'
        ? connected
        : this.hasConnectionType(name, 'ssh');
      const hasConsole = type === 'console'
        ? connected
        : this.hasConnectionType(name, 'console');
      const hasNoVnc = type === 'novnc'
        ? connected
        : this.hasConnectionType(name, 'novnc');

      // Remove all connection classes
      deviceEl.classList.remove('ssh-connected', 'console-connected', 'novnc-connected', 'both-connected', 'multi-connected');

      // Add class for EACH active connection type to light up corresponding dots
      if (hasSSH) {
        deviceEl.classList.add('ssh-connected');
      }
      if (hasConsole) {
        deviceEl.classList.add('console-connected');
      }
      if (hasNoVnc) {
        deviceEl.classList.add('novnc-connected');
      }

      // Add multi-connected class if 2+ connections (for potential future styling)
      const connectionCount = [hasSSH, hasConsole, hasNoVnc].filter(Boolean).length;
      if (connectionCount >= 2) {
        deviceEl.classList.add('multi-connected');
      }
    }

    // Check jump server link (SSH only)
    const jumpLink = document.getElementById('jumpServerLink');
    if (jumpLink && jumpLink.dataset.name === name && type === 'ssh') {
      jumpLink.classList.toggle('connected', connected);
    }
  },

  // Context menu for device right-click
  showContextMenu(event, device) {
    // Remove any existing context menu
    this.hideContextMenu();

    const menu = document.createElement('div');
    menu.className = 'device-context-menu';
    menu.id = 'deviceContextMenu';
    menu.setAttribute('role', 'menu');

    // Build menu items
    let menuHTML = `
      <div class="menu-item default" data-action="ssh" role="menuitem">
        <span class="menu-icon" aria-hidden="true">&#9679;</span>
        Open SSH Terminal
      </div>
    `;

    // Add console option if supported
    if (device.supportsConsole) {
      menuHTML += `
        <div class="menu-item console-action" data-action="console" role="menuitem">
          <span class="menu-icon" aria-hidden="true">&#9000;</span>
          Open Serial Console
        </div>
      `;
    }

    // Add noVNC desktop option if supported (Linux hosts)
    if (device.supportsNoVnc) {
      menuHTML += `
        <div class="menu-item novnc-action" data-action="novnc" role="menuitem">
          <span class="menu-icon" aria-hidden="true">&#128421;</span>
          Open Desktop (noVNC)
        </div>
      `;
    }

    // Add Web UI option if supported (VeloCloud Orchestrator)
    if (device.supportsWebUI) {
      menuHTML += `
        <div class="menu-item webui-action" data-action="webui" role="menuitem">
          <span class="menu-icon" aria-hidden="true">&#127760;</span>
          Open Web UI
        </div>
      `;
    }

    menuHTML += `
      <div class="menu-divider" role="separator"></div>
      <div class="menu-item" data-action="highlight" role="menuitem">
        <span class="menu-icon" aria-hidden="true">&#9678;</span>
        Highlight on Diagram
      </div>
      <div class="menu-divider" role="separator"></div>
      <div class="menu-item" data-action="copy-ip" role="menuitem">
        <span class="menu-icon" aria-hidden="true">&#10697;</span>
        Copy IP Address
      </div>
    `;

    menu.innerHTML = menuHTML;

    // Position menu at click location
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;

    // Add click handlers to menu items
    menu.querySelectorAll('.menu-item').forEach(item => {
      item.addEventListener('click', () => {
        const action = item.dataset.action;
        this.handleContextMenuAction(action, device);
        this.hideContextMenu();
      });
    });

    document.body.appendChild(menu);

    // Adjust position if menu goes off screen
    const menuRect = menu.getBoundingClientRect();
    if (menuRect.right > window.innerWidth) {
      menu.style.left = `${window.innerWidth - menuRect.width - 10}px`;
    }
    if (menuRect.bottom > window.innerHeight) {
      menu.style.top = `${window.innerHeight - menuRect.height - 10}px`;
    }

    // Close menu when clicking outside
    setTimeout(() => {
      document.addEventListener('click', this.hideContextMenu.bind(this), { once: true });
    }, 0);
  },

  hideContextMenu() {
    const existingMenu = document.getElementById('deviceContextMenu');
    if (existingMenu) {
      existingMenu.remove();
    }
  },

  handleContextMenuAction(action, device) {
    switch (action) {
      case 'ssh':
        this.openTerminal(device.name, device.ip, 'ssh');
        break;
      case 'console':
        // Use vmName (original name) for virsh console
        this.openTerminal(device.name, device.ip, 'console', device.vmName);
        break;
      case 'novnc':
        // Open noVNC desktop for Linux hosts
        this.openTerminal(device.name, device.ip, 'novnc', device.vmName);
        break;
      case 'webui':
        // Open VCO web UI in new tab
        window.open('/vco/', '_blank');
        break;
      case 'highlight':
        this.highlightOnDiagram(device.name);
        break;
      case 'copy-ip':
        navigator.clipboard.writeText(device.ip).then(() => {
          console.log('IP copied to clipboard:', device.ip);
        }).catch(err => {
          console.error('Failed to copy IP:', err);
        });
        break;
    }
  },

  highlightOnDiagram(deviceName) {
    // Open topology panel if not visible
    const topoPanel = document.getElementById('topologyPanel');
    const topoToggle = document.getElementById('topoToggle');
    const labguidesPanel = document.getElementById('labguidesPanel');
    const labguidesToggle = document.getElementById('labguidesToggle');

    if (!topoPanel.classList.contains('visible')) {
      topoPanel.classList.add('visible');
      topoToggle.classList.add('active');
      // Close labguides if open
      labguidesPanel.classList.remove('visible');
      labguidesToggle.classList.remove('active');
    }

    // Wait for panel to open, then highlight the device
    setTimeout(() => {
      if (this.topologyManager) {
        this.topologyManager.focusOnDevice(deviceName);
      }
    }, 150);
  },

  setupPanelToggles() {
    // Topology panel
    const topoToggle = document.getElementById('topoToggle');
    const topoPanel = document.getElementById('topologyPanel');
    const topoClose = document.getElementById('topoClose');

    // Lab guides panel
    const labguidesToggle = document.getElementById('labguidesToggle');
    const labguidesPanel = document.getElementById('labguidesPanel');
    const labguidesClose = document.getElementById('labguidesClose');

    // Toggle topology panel
    topoToggle.addEventListener('click', () => {
      const isVisible = topoPanel.classList.toggle('visible');
      topoToggle.classList.toggle('active', isVisible);
      // Close labguides if opening topology
      if (isVisible) {
        labguidesPanel.classList.remove('visible');
        labguidesToggle.classList.remove('active');
        // Fit topology to panel after it becomes visible
        if (this.topologyManager) {
          setTimeout(() => this.topologyManager.fit(), 100);
        }
      }
    });

    topoClose.addEventListener('click', () => {
      topoPanel.classList.remove('visible');
      topoToggle.classList.remove('active');
    });

    // Toggle lab guides panel
    labguidesToggle.addEventListener('click', () => {
      const isVisible = labguidesPanel.classList.toggle('visible');
      labguidesToggle.classList.toggle('active', isVisible);
      // Close topology if opening labguides
      if (isVisible) {
        topoPanel.classList.remove('visible');
        topoToggle.classList.remove('active');
      }
    });

    labguidesClose.addEventListener('click', () => {
      labguidesPanel.classList.remove('visible');
      labguidesToggle.classList.remove('active');
    });

    // Capture panel toggle
    const captureToggle = document.getElementById('captureToggle');
    captureToggle.addEventListener('click', () => {
      const capturePanel = document.getElementById('capture-panel');
      if (capturePanel) {
        const isVisible = capturePanel.classList.contains('visible');
        if (isVisible) {
          capturePanel.classList.remove('visible');
          captureToggle.classList.remove('active');
        } else {
          capturePanel.classList.add('visible');
          captureToggle.classList.add('active');
        }
      }
    });

    // Watch for capture panel visibility changes to sync button state
    this.setupCaptureButtonSync();
  },

  /**
   * Sync capture toggle button state with capture panel visibility
   * (panel can be closed via its own close button)
   */
  setupCaptureButtonSync() {
    const captureToggle = document.getElementById('captureToggle');

    // Wait for capture panel to exist, then observe it
    const checkAndObserve = () => {
      const capturePanel = document.getElementById('capture-panel');
      if (capturePanel) {
        const observer = new MutationObserver(() => {
          const isVisible = capturePanel.classList.contains('visible');
          captureToggle.classList.toggle('active', isVisible);
        });
        observer.observe(capturePanel, { attributes: true, attributeFilter: ['class'] });
      } else {
        // Panel not yet created, check again shortly
        setTimeout(checkAndObserve, 100);
      }
    };
    checkAndObserve();
  },

  setupSidebarToggle() {
    const sidebar = document.getElementById('deviceSidebar');
    const toggleBtn = document.getElementById('sidebarToggle');

    toggleBtn.addEventListener('click', () => {
      sidebar.classList.toggle('collapsed');
      // Add class to body for capture panel positioning (fallback for :has() selector)
      document.body.classList.toggle('sidebar-collapsed', sidebar.classList.contains('collapsed'));
      // Change icon based on state
      toggleBtn.innerHTML = sidebar.classList.contains('collapsed') ? '&#9654;' : '&#9776;';
      toggleBtn.title = sidebar.classList.contains('collapsed') ? 'Expand sidebar' : 'Collapse sidebar';
    });
  },

  setupTabOverflow() {
    const overflowBtn = document.getElementById('tabOverflowBtn');
    const overflowMenu = document.getElementById('tabOverflowMenu');

    // Toggle menu on button click
    overflowBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      overflowMenu.classList.toggle('open');
    });

    // Close menu when clicking outside
    document.addEventListener('click', () => {
      overflowMenu.classList.remove('open');
    });

    // Prevent menu from closing when clicking inside it
    overflowMenu.addEventListener('click', (e) => {
      e.stopPropagation();
    });
  },

  updateTabOverflow() {
    const overflow = document.getElementById('tabOverflow');
    const menu = document.getElementById('tabOverflowMenu');
    const count = document.getElementById('tabCount');

    // Show/hide dropdown based on tab count
    if (this.tabs.length > 0) {
      overflow.classList.add('visible');
      count.textContent = this.tabs.length;

      // Rebuild menu
      menu.innerHTML = '';
      this.tabs.forEach(tab => {
        const item = document.createElement('div');
        item.className = 'tab-overflow-item' + (tab.id === this.activeTabId ? ' active' : '');
        item.setAttribute('role', 'menuitem');

        // Add status dot for connection type
        const dotSpan = document.createElement('span');
        dotSpan.className = `tab-status-dot ${tab.type || 'ssh'}`;
        dotSpan.style.marginRight = '8px';
        dotSpan.setAttribute('aria-hidden', 'true');

        const nameSpan = document.createElement('span');
        let displayName = tab.name;
        if (tab.type === 'console') {
          displayName = `${tab.name} &#9000;`;
        } else if (tab.type === 'novnc') {
          displayName = `${tab.name} &#128421;`;
        }
        nameSpan.innerHTML = displayName;

        const ipSpan = document.createElement('span');
        ipSpan.className = 'device-ip';
        ipSpan.textContent = tab.ip;

        item.appendChild(dotSpan);
        item.appendChild(nameSpan);
        item.appendChild(ipSpan);

        item.addEventListener('click', () => {
          this.activateTab(tab.id);
          menu.classList.remove('open');
        });
        menu.appendChild(item);
      });
    } else {
      overflow.classList.remove('visible');
    }
  },

  setupSplitView() {
    const splitToggle = document.getElementById('splitToggle');
    const splitContainer = document.getElementById('splitContainer');

    splitToggle.addEventListener('click', () => {
      if (this.splitMode) {
        this.exitSplitMode();
      } else {
        this.enterSplitMode();
      }
    });
  },

  enterSplitMode() {
    this.splitMode = true;
    this.nextSplitPane = 'left';

    const splitContainer = document.getElementById('splitContainer');
    const splitToggle = document.getElementById('splitToggle');

    splitContainer.classList.add('visible');
    splitToggle.classList.add('active');
    splitToggle.textContent = 'Exit Split';

    // Clear any existing split iframes
    this.clearSplitPane('left');
    this.clearSplitPane('right');

    // Update pane headers
    document.getElementById('leftDevice').textContent = 'Click a device';
    document.getElementById('rightDevice').textContent = 'Click a device';

    // Show empty state in panes with improved messaging
    document.getElementById('leftContent').innerHTML = `
      <div class="split-pane-empty">
        <p>Select a device from the sidebar</p>
        <p class="hint">Left-click for SSH, or use context menu for options</p>
      </div>
    `;
    document.getElementById('rightContent').innerHTML = `
      <div class="split-pane-empty">
        <p>Select a device from the sidebar</p>
        <p class="hint">Left-click for SSH, or use context menu for options</p>
      </div>
    `;

    // Highlight the first pane as the target
    this.updateSplitPaneIndicator();
  },

  updateSplitPaneIndicator() {
    const leftHeader = document.querySelector('#splitLeft .split-pane-header');
    const rightHeader = document.querySelector('#splitRight .split-pane-header');

    leftHeader.classList.toggle('next-target', this.nextSplitPane === 'left');
    rightHeader.classList.toggle('next-target', this.nextSplitPane === 'right');
  },

  exitSplitMode() {
    this.splitMode = false;

    const splitContainer = document.getElementById('splitContainer');
    const splitToggle = document.getElementById('splitToggle');

    splitContainer.classList.remove('visible');
    splitToggle.classList.remove('active');
    splitToggle.textContent = 'Split View';

    // Clean up split iframes
    this.clearSplitPane('left');
    this.clearSplitPane('right');

    // If we have tabs, make sure one is active
    if (this.tabs.length > 0 && this.activeTabId) {
      this.activateTab(this.activeTabId);
    }
  },

  clearSplitPane(pane) {
    const paneData = pane === 'left' ? this.splitLeft : this.splitRight;
    if (paneData.iframe) {
      paneData.iframe.remove();
      paneData.iframe = null;
    }
    paneData.device = null;
  },

  openInSplitPane(name, ip, type = 'ssh', vmName = null) {
    // vmName is the original name for virsh console (defaults to name if not provided)
    const effectiveVmName = vmName || name;

    const pane = this.nextSplitPane;
    const contentEl = document.getElementById(pane === 'left' ? 'leftContent' : 'rightContent');
    const deviceEl = document.getElementById(pane === 'left' ? 'leftDevice' : 'rightDevice');
    const paneData = pane === 'left' ? this.splitLeft : this.splitRight;

    // Clear previous iframe if exists
    this.clearSplitPane(pane);

    // For noVNC in split mode, we need to get a token first
    if (type === 'novnc') {
      this.openNoVncInSplitPane(pane, name, ip, effectiveVmName, contentEl, deviceEl, paneData);
      return;
    }

    // Create new iframe with appropriate URL
    const iframe = document.createElement('iframe');
    if (type === 'console') {
      // Use vmName (original name) for virsh console
      iframe.src = `/console?device=${encodeURIComponent(effectiveVmName)}`;
      iframe.title = `Console to ${name}`;
    } else {
      iframe.src = `/ssh/host/${ip}`;
      iframe.title = `SSH to ${name}`;
    }

    contentEl.innerHTML = '';
    contentEl.appendChild(iframe);

    // Update state
    paneData.device = { name, ip, type, vmName: effectiveVmName };
    paneData.iframe = iframe;

    // Display name with type indicator for console
    const displayName = type === 'console' ? `${name} &#9000;` : name;
    deviceEl.innerHTML = displayName;

    // Alternate pane for next click
    this.nextSplitPane = pane === 'left' ? 'right' : 'left';

    // Update the visual indicator
    this.updateSplitPaneIndicator();

    // Focus the iframe
    setTimeout(() => iframe.focus(), 50);
  },

  /**
   * Open noVNC in split pane (needs async token fetch)
   */
  async openNoVncInSplitPane(pane, name, ip, vmName, contentEl, deviceEl, paneData) {
    try {
      // Show loading state
      contentEl.innerHTML = '<div class="split-pane-loading">Loading desktop...</div>';

      // Get noVNC token from API
      const response = await fetch(`/td-api/nodes/novnc-token/${encodeURIComponent(vmName)}`);
      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.error || 'Failed to get VNC token');
      }

      const tokenData = await response.json();

      // Create iframe with noVNC URL
      const iframe = document.createElement('iframe');
      // path=websockify tells noVNC to connect to /websockify/ endpoint for the WebSocket
      const vncUrl = tokenData.novnc_url || `/novnc/vnc.html?autoconnect=true&resize=scale&path=websockify/?token=${encodeURIComponent(tokenData.token)}`;
      iframe.src = vncUrl;
      iframe.title = `Desktop: ${name}`;

      contentEl.innerHTML = '';
      contentEl.appendChild(iframe);

      // Update state
      paneData.device = { name, ip, type: 'novnc', vmName };
      paneData.iframe = iframe;

      // Display name with desktop icon
      deviceEl.innerHTML = `${name} &#128421;`;

      // Alternate pane for next click
      this.nextSplitPane = pane === 'left' ? 'right' : 'left';

      // Update the visual indicator
      this.updateSplitPaneIndicator();

      // Focus the iframe
      setTimeout(() => iframe.focus(), 50);

    } catch (error) {
      console.error('[TerminalManager] Failed to open noVNC in split pane:', error);
      contentEl.innerHTML = `<div class="split-pane-error">Failed to open desktop: ${error.message}</div>`;
    }
  },

  // Topology Manager for side panel
  topologyManager: null,
  topoInitialized: false,

  async initTopology() {
    if (this.topoInitialized) return;

    try {
      // Dynamically import the TopologyManager module
      // Path is relative to current script location (js/ directory)
      const { TopologyManager } = await import('./topology/topology-manager.js');

      // Clear the loading text before initializing
      const topoContainer = document.getElementById('terminal-topology');
      topoContainer.textContent = '';
      topoContainer.classList.remove('loading');

      this.topologyManager = new TopologyManager('terminal-topology', {
        apiUrl: '/td-api/topology',
        layout: 'preset',
        enableStatus: true,
        enableFilters: false,  // Using our own compact controls
        // Custom terminal handler for opening devices in this page's tabs
        onOpenTerminal: (deviceName, ip, type, vmName) => {
          TerminalManager.openTerminal(deviceName, ip, type, vmName);
        }
      });

      await this.topologyManager.init();
      this.topoInitialized = true;

      // Setup compact controls
      this.setupTopoControls();

      // Wait for capture panel to exist in DOM before setting up observer
      await this.waitForCapturePanel();
      this.setupCapturePanelObserver();

    } catch (error) {
      console.error('Failed to initialize topology:', error);
      document.getElementById('terminal-topology').innerHTML =
        '<div style="padding: 20px; text-align: center; color: #e74c3c;">Failed to load topology</div>';
    }
  },

  setupTopoControls() {
    // Search input
    const searchInput = document.getElementById('topo-search');
    if (searchInput) {
      let searchTimeout;
      searchInput.addEventListener('input', () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
          if (this.topologyManager) {
            this.topologyManager.search(searchInput.value);
          }
        }, 300);
      });
    }

    // Reset button
    const resetBtn = document.getElementById('topo-reset');
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        if (this.topologyManager) {
          this.topologyManager.clearHighlights();
          this.topologyManager.setLayout('preset');
          this.topologyManager.resetFilters();
          document.getElementById('topo-search').value = '';
        }
      });
    }

    // Fit button
    const fitBtn = document.getElementById('topo-fit');
    if (fitBtn) {
      fitBtn.addEventListener('click', () => {
        if (this.topologyManager) {
          this.topologyManager.fit();
        }
      });
    }

    // Auto-focus toggle button
    const autoFocusBtn = document.getElementById('topo-auto-focus');
    if (autoFocusBtn) {
      autoFocusBtn.addEventListener('click', () => {
        this.autoFocusEnabled = !this.autoFocusEnabled;
        autoFocusBtn.classList.toggle('active', this.autoFocusEnabled);

        if (this.autoFocusEnabled) {
          // Immediately focus on current active tab's device
          this.updateTopologyFocus();
        } else {
          // Clear focus when disabling
          if (this.topologyManager) {
            this.topologyManager.clearFocus();
          }
        }
      });
    }
  },

  /**
   * Wait for capture panel to exist in DOM
   * Returns immediately if panel exists, otherwise polls with timeout
   */
  async waitForCapturePanel() {
    return new Promise((resolve) => {
      const maxAttempts = 50; // 5 seconds max
      let attempts = 0;

      const checkPanel = () => {
        const panel = document.getElementById('capture-panel');
        if (panel) {
          resolve();
        } else if (attempts++ < maxAttempts) {
          setTimeout(checkPanel, 100);
        } else {
          console.warn('[TerminalManager] Capture panel not found after waiting');
          resolve(); // Resolve anyway to not block
        }
      };
      checkPanel();
    });
  },

  // Store observer reference for cleanup
  capturePanelObserver: null,

  /**
   * Setup observer for capture panel to update main-content layout
   * The capture panel should push up terminal iframes and side panels but NOT the sidebar
   */
  setupCapturePanelObserver() {
    const capturePanel = document.getElementById('capture-panel');
    const mainContent = document.querySelector('.main-content');

    if (!capturePanel || !mainContent) {
      console.warn('[TerminalManager] Could not find capture panel or main content');
      return;
    }

    // Disconnect existing observer if any (prevents memory leaks on reinitialization)
    if (this.capturePanelObserver) {
      this.capturePanelObserver.disconnect();
    }

    // Function to update main-content classes based on capture panel state
    const updateMainContentLayout = () => {
      // Check if elements still exist in DOM
      if (!document.body.contains(capturePanel) || !document.body.contains(mainContent)) {
        if (this.capturePanelObserver) {
          this.capturePanelObserver.disconnect();
        }
        return;
      }

      const isVisible = capturePanel.classList.contains('visible');
      const isMinimized = capturePanel.classList.contains('minimized');
      const isExpanded = capturePanel.classList.contains('expanded');

      mainContent.classList.toggle('capture-panel-visible', isVisible);
      mainContent.classList.toggle('capture-panel-minimized', isVisible && isMinimized);
      mainContent.classList.toggle('capture-panel-expanded', isVisible && isExpanded);
    };

    // Use MutationObserver to watch for class changes on capture panel
    this.capturePanelObserver = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'class') {
          updateMainContentLayout();
        }
      }
    });

    this.capturePanelObserver.observe(capturePanel, {
      attributes: true,
      attributeFilter: ['class']
    });

    // Initial state (use rAF to ensure classes are set)
    requestAnimationFrame(() => updateMainContentLayout());

    console.log('[TerminalManager] Capture panel observer setup complete');
  },

  /**
   * Update topology focus based on active terminal tab
   * Called when auto-focus is enabled and tab changes
   */
  updateTopologyFocus() {
    if (!this.autoFocusEnabled || !this.topologyManager || !this.activeTabId) {
      return;
    }

    // Find the active tab's device name
    const activeTab = this.tabs.find(t => t.id === this.activeTabId);
    if (activeTab && activeTab.name) {
      // Focus the topology on this device
      this.topologyManager.focusOnDevice(activeTab.name);
    }
  },

  /**
   * Highlight the active tab's device in the sidebar
   * Removes active-tab from all devices, then adds it to the matching one
   */
  updateSidebarActiveDevice() {
    // Clear all active highlights
    document.querySelectorAll('.device-item.active-tab').forEach(el => {
      el.classList.remove('active-tab');
    });

    // Find and highlight the active tab's device
    const activeTab = this.tabs.find(t => t.id === this.activeTabId);
    if (activeTab && activeTab.name) {
      const deviceEl = document.querySelector(`.device-item[data-name="${CSS.escape(activeTab.name)}"]`);
      if (deviceEl) {
        deviceEl.classList.add('active-tab');
      }
    }
  },

  /**
   * Open SSH terminals for all devices in a group
   *
   * WebSSH2 requires authentication on the first connection — a session cookie
   * is set after the user logs in. Subsequent connections reuse that cookie.
   *
   * If no SSH session exists yet, we open only the first device and prompt the
   * user to authenticate before clicking "All" again. This prevents multiple
   * simultaneous login prompts.
   */
  openAllInGroup(groupEl) {
    const devices = groupEl.querySelectorAll('.device-item');

    // Collect devices that need opening (have IP and no existing SSH tab)
    const toOpen = [];
    devices.forEach(deviceEl => {
      const name = deviceEl.dataset.name;
      const ip = deviceEl.dataset.ip;
      if (ip && ip !== 'N/A' && !this.tabs.some(t => t.name === name && t.type === 'ssh')) {
        toOpen.push({ name, ip });
      }
    });

    if (toOpen.length === 0) return;

    // Check if user has an existing SSH session (any SSH tab already open)
    const hasExistingSession = this.tabs.some(t => t.type === 'ssh');

    if (!hasExistingSession) {
      // No session yet — open only the first device so user can authenticate
      this.openTerminal(toOpen[0].name, toOpen[0].ip, 'ssh');
      this.showOpenAllNotice(toOpen.length - 1);
      return;
    }

    // Session exists — open all devices (queue serializes them automatically)
    toOpen.forEach(device => {
      this.openTerminal(device.name, device.ip, 'ssh');
    });
  },

  /**
   * Show a brief notice prompting the user to authenticate then retry "All"
   */
  showOpenAllNotice(remaining) {
    // Remove any existing notice
    const existing = document.getElementById('openAllNotice');
    if (existing) existing.remove();

    const notice = document.createElement('div');
    notice.id = 'openAllNotice';
    notice.className = 'open-all-notice';
    notice.textContent = `Log in to the terminal, then click "All" again to open the remaining ${remaining} device${remaining !== 1 ? 's' : ''}.`;

    // Insert at top of device tree
    const tree = document.getElementById('deviceGroups');
    tree.parentElement.insertBefore(notice, tree);

    // Auto-dismiss after 10 seconds
    setTimeout(() => {
      if (notice.parentElement) notice.remove();
    }, 10000);
  },

  /**
   * DEBUG: Full state audit — call from browser console: TerminalManager._debugAudit()
   * Dumps the complete mapping of tabs array to DOM elements to find any drift
   */
  _debugAudit() {
    console.log('%c=== TERMINAL MANAGER STATE AUDIT ===', 'color: #fbb500; font-weight: bold; font-size: 16px');
    console.log(`activeTabId: ${this.activeTabId}`);
    console.log(`_tabCounter: ${this._tabCounter}`);
    console.log(`tabs.length: ${this.tabs.length}`);

    // Check each tab in the array
    this.tabs.forEach((tab, i) => {
      const tabEl = document.getElementById(tab.id);
      const frameEl = document.getElementById('frame-' + tab.id);
      const tabLabel = tabEl ? tabEl.querySelector('.tab-name')?.textContent.trim() : 'NO DOM';
      const frameSrc = frameEl ? frameEl.src : 'NO DOM';
      const isActive = tab.id === this.activeTabId;
      const tabHasActiveClass = tabEl ? tabEl.classList.contains('active') : false;
      const frameHasActiveClass = frameEl ? frameEl.classList.contains('active') : false;

      const status = [];
      if (!tabEl) status.push('MISSING TAB DOM');
      if (!frameEl) status.push('MISSING FRAME DOM');
      if (isActive !== tabHasActiveClass) status.push(`TAB ACTIVE MISMATCH (data=${isActive} dom=${tabHasActiveClass})`);
      if (isActive !== frameHasActiveClass) status.push(`FRAME ACTIVE MISMATCH (data=${isActive} dom=${frameHasActiveClass})`);

      const color = status.length > 0 ? 'color: red' : 'color: #78d82c';
      console.log(
        `%c[${i}] ${tab.id} | name="${tab.name}" type=${tab.type} ip=${tab.ip}` +
        ` | label="${tabLabel}" | src="${frameSrc}"` +
        ` | active=${isActive}` +
        (status.length > 0 ? ` | ISSUES: ${status.join(', ')}` : ' | OK'),
        color
      );
    });

    // Check for orphan DOM elements (tabs/frames not in the array)
    const domTabs = document.querySelectorAll('.tab[id^="tab-"]');
    const domFrames = document.querySelectorAll('.terminal-frame[id^="frame-tab-"]');
    const tabIds = new Set(this.tabs.map(t => t.id));

    domTabs.forEach(el => {
      if (!tabIds.has(el.id)) {
        console.error(`%c ORPHAN TAB DOM: id="${el.id}" text="${el.textContent.trim()}" (not in tabs array!)`, 'color: red; font-weight: bold');
      }
    });
    domFrames.forEach(el => {
      const expectedTabId = el.id.replace('frame-', '');
      if (!tabIds.has(expectedTabId)) {
        console.error(`%c ORPHAN FRAME DOM: id="${el.id}" src="${el.src}" (not in tabs array!)`, 'color: red; font-weight: bold');
      }
    });

    console.log(`DOM tabs: ${domTabs.length}, DOM frames: ${domFrames.length}, Array tabs: ${this.tabs.length}`);
    console.log('%c=== END AUDIT ===', 'color: #fbb500; font-weight: bold');
  }
};

// Make TerminalManager globally accessible
window.TerminalManager = TerminalManager;

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', async () => {
  TerminalManager.init();

  // Initialize topology when panel is opened
  await TerminalManager.initTopology();

  // Handle URL parameters from topology diagram clicks (first open)
  const urlParams = new URLSearchParams(window.location.search);
  const deviceName = urlParams.get('device');
  const deviceIp = urlParams.get('ip');
  const connectionType = urlParams.get('type') || 'ssh';
  const vmName = urlParams.get('vmName');

  if (deviceName && (deviceIp || connectionType === 'console')) {
    // Open the device terminal after a short delay to ensure devices are loaded
    setTimeout(() => {
      TerminalManager.openTerminal(deviceName, deviceIp || '', connectionType, vmName);
      // Clear URL params to prevent reopening on refresh
      window.history.replaceState({}, '', '/terminal');
    }, 500);
  }

  // Listen for messages from topology diagram (subsequent opens)
  window.addEventListener('message', (event) => {
    // Verify origin for security
    if (event.origin !== window.location.origin) return;

    const data = event.data;
    if (data && data.type === 'openDevice' && data.device) {
      const type = data.connectionType || 'ssh';
      const vmName = data.vmName || data.device;
      TerminalManager.openTerminal(data.device, data.ip || '', type, vmName);
    }
  });
});

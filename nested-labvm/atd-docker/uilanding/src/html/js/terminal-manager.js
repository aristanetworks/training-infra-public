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
      headerEl.innerHTML = `<span class="arrow">&#9660;</span>${group.group}`;

      const devicesEl = document.createElement('div');
      devicesEl.className = 'group-devices';
      devicesEl.setAttribute('role', 'list');

      group.devices.forEach(device => {
        const deviceEl = document.createElement('div');
        deviceEl.className = 'device-item';
        deviceEl.setAttribute('role', 'listitem');
        deviceEl.dataset.ip = device.ip;
        deviceEl.dataset.name = device.name;
        deviceEl.dataset.supportsConsole = device.supportsConsole ? 'true' : 'false';
        deviceEl.tabIndex = 0;

        // Build HTML with stacked status dots and console icon
        let html = `
          <span class="status-dots" aria-hidden="true">
            <span class="status-dot ssh"></span>
            <span class="status-dot console"></span>
          </span>
          <span class="device-name">${device.name}</span>
          <span class="device-ip">${device.ip}</span>
        `;

        // Add console icon if device supports console
        if (device.supportsConsole) {
          html += `<span class="console-icon" title="Open Serial Console" aria-label="Open serial console for ${device.name}">&#9000;</span>`;
        }

        deviceEl.innerHTML = html;

        // Left-click on device name area opens SSH
        const openTerminalHandler = (e) => {
          // Don't trigger if clicking on console icon
          if (e.target.classList.contains('console-icon')) return;
          this.openTerminal(device.name, device.ip, 'ssh');
        };
        deviceEl.addEventListener('click', openTerminalHandler);

        // Click on console icon opens console
        if (device.supportsConsole) {
          const consoleIcon = deviceEl.querySelector('.console-icon');
          if (consoleIcon) {
            consoleIcon.addEventListener('click', (e) => {
              e.stopPropagation();
              this.openTerminal(device.name, device.ip, 'console');
            });
          }
        }

        // Right-click shows context menu
        deviceEl.addEventListener('contextmenu', (e) => {
          e.preventDefault();
          this.showContextMenu(e, device);
        });

        // Keyboard handler
        deviceEl.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            this.openTerminal(device.name, device.ip, 'ssh');
          }
        });

        devicesEl.appendChild(deviceEl);
      });

      headerEl.addEventListener('click', () => {
        const isCollapsed = headerEl.classList.toggle('collapsed');
        headerEl.setAttribute('aria-expanded', !isCollapsed);
        devicesEl.classList.toggle('hidden');
      });

      groupEl.appendChild(headerEl);
      groupEl.appendChild(devicesEl);
      tree.appendChild(groupEl);
    });
  },

  openTerminal(name, ip, type = 'ssh') {
    // If in split mode, open in split pane instead
    if (this.splitMode) {
      this.openInSplitPane(name, ip, type);
      return;
    }

    // Check if tab already exists for this ip AND type
    // Allow one SSH and one Console tab per device
    const existingTab = this.tabs.find(t => t.ip === ip && t.type === type);
    if (existingTab) {
      this.activateTab(existingTab.id);
      return;
    }

    // Create new tab
    const tabId = 'tab-' + Date.now();
    const tab = { id: tabId, name, ip, type };
    this.tabs.push(tab);

    // Create tab element
    const tabsScrollArea = document.getElementById('tabsScrollArea');

    const tabEl = document.createElement('div');
    tabEl.className = 'tab';
    tabEl.id = tabId;
    tabEl.dataset.type = type;
    tabEl.setAttribute('role', 'tab');
    tabEl.setAttribute('aria-selected', 'false');

    // Tab display: status dot (colored by type) + name (+ icon for console)
    const displayName = type === 'console' ? `${name} &#9000;` : name;
    const dotClass = type === 'console' ? 'console' : 'ssh';
    tabEl.innerHTML = `
      <span class="tab-status-dot ${dotClass}" aria-hidden="true"></span>
      <span class="tab-name">${displayName}</span>
      <span class="close-btn" title="Close" aria-label="Close ${name} tab">&times;</span>
    `;

    tabEl.querySelector('.tab-name').addEventListener('click', () => this.activateTab(tabId));
    tabEl.querySelector('.close-btn').addEventListener('click', (e) => {
      e.stopPropagation();
      this.closeTab(tabId);
    });

    tabsScrollArea.appendChild(tabEl);

    // Create iframe with appropriate URL
    const terminalFrames = document.getElementById('terminalFrames');
    const iframe = document.createElement('iframe');
    iframe.className = 'terminal-frame';
    iframe.id = 'frame-' + tabId;
    iframe.setAttribute('title', `Terminal: ${name} (${type.toUpperCase()})`);

    if (type === 'console') {
      // Console uses the console page with device parameter
      iframe.src = `/console?device=${encodeURIComponent(name)}`;
    } else {
      // SSH connection
      iframe.src = `/ssh/host/${ip}`;
    }

    terminalFrames.appendChild(iframe);

    // Mark device as connected for this type
    this.updateDeviceStatus(ip, type, true);

    // Activate the new tab
    this.activateTab(tabId);

    // Hide empty state
    document.getElementById('emptyState').style.display = 'none';

    // Update overflow menu
    this.updateTabOverflow();
  },

  activateTab(tabId) {
    // Deactivate all tabs
    document.querySelectorAll('.tab').forEach(t => {
      t.classList.remove('active');
      t.setAttribute('aria-selected', 'false');
    });
    document.querySelectorAll('.terminal-frame').forEach(f => f.classList.remove('active'));

    // Activate selected tab
    const tabEl = document.getElementById(tabId);
    const frameEl = document.getElementById('frame-' + tabId);

    if (tabEl) {
      tabEl.classList.add('active');
      tabEl.setAttribute('aria-selected', 'true');
    }
    if (frameEl) {
      frameEl.classList.add('active');
      // Focus the iframe so keyboard input goes to the terminal
      setTimeout(() => frameEl.focus(), 50);
    }

    this.activeTabId = tabId;

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
    this.updateDeviceStatus(tab.ip, tab.type || 'ssh', false);

    // Remove from tabs array
    this.tabs.splice(tabIndex, 1);

    // Activate another tab or show empty state
    if (this.tabs.length > 0) {
      const newActiveIndex = Math.min(tabIndex, this.tabs.length - 1);
      this.activateTab(this.tabs[newActiveIndex].id);
    } else {
      this.activeTabId = null;
      document.getElementById('emptyState').style.display = 'block';
    }

    // Update overflow menu
    this.updateTabOverflow();
  },

  /**
   * Check if a device has a specific connection type open
   * @param {string} ip - Device IP address
   * @param {string} type - Connection type ('ssh' or 'console')
   * @returns {boolean} True if connection exists
   */
  hasConnectionType(ip, type) {
    return this.tabs.some(t => t.ip === ip && t.type === type);
  },

  updateDeviceStatus(ip, type, connected) {
    // Check regular device items
    const deviceEl = document.querySelector(`.device-item[data-ip="${ip}"]`);
    if (deviceEl) {
      // Check if there are other connections of different type still open
      const hasSSH = type === 'ssh'
        ? connected
        : this.hasConnectionType(ip, 'ssh');
      const hasConsole = type === 'console'
        ? connected
        : this.hasConnectionType(ip, 'console');

      // Remove all connection classes
      deviceEl.classList.remove('ssh-connected', 'console-connected', 'both-connected');

      // Add appropriate class based on connection state
      if (hasSSH && hasConsole) {
        deviceEl.classList.add('both-connected');
      } else if (hasSSH) {
        deviceEl.classList.add('ssh-connected');
      } else if (hasConsole) {
        deviceEl.classList.add('console-connected');
      }
    }

    // Check jump server link (SSH only)
    const jumpLink = document.getElementById('jumpServerLink');
    if (jumpLink && jumpLink.dataset.ip === ip && type === 'ssh') {
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
        this.openTerminal(device.name, device.ip, 'console');
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
        const displayName = tab.type === 'console' ? `${tab.name} &#9000;` : tab.name;
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

  openInSplitPane(name, ip, type = 'ssh') {
    const pane = this.nextSplitPane;
    const contentEl = document.getElementById(pane === 'left' ? 'leftContent' : 'rightContent');
    const deviceEl = document.getElementById(pane === 'left' ? 'leftDevice' : 'rightDevice');
    const paneData = pane === 'left' ? this.splitLeft : this.splitRight;

    // Clear previous iframe if exists
    this.clearSplitPane(pane);

    // Create new iframe with appropriate URL
    const iframe = document.createElement('iframe');
    if (type === 'console') {
      iframe.src = `/console?device=${encodeURIComponent(name)}`;
      iframe.title = `Console to ${name}`;
    } else {
      iframe.src = `/ssh/host/${ip}`;
      iframe.title = `SSH to ${name}`;
    }

    contentEl.innerHTML = '';
    contentEl.appendChild(iframe);

    // Update state
    paneData.device = { name, ip, type };
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
        onOpenTerminal: (deviceName, ip) => {
          TerminalManager.openTerminal(deviceName, ip);
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

  if (deviceName && deviceIp) {
    // Open the device terminal after a short delay to ensure devices are loaded
    setTimeout(() => {
      TerminalManager.openTerminal(deviceName, deviceIp);
      // Clear URL params to prevent reopening on refresh
      window.history.replaceState({}, '', '/terminal');
    }, 500);
  }

  // Listen for messages from topology diagram (subsequent opens)
  window.addEventListener('message', (event) => {
    // Verify origin for security
    if (event.origin !== window.location.origin) return;

    const data = event.data;
    if (data && data.type === 'openDevice' && data.device && data.ip) {
      TerminalManager.openTerminal(data.device, data.ip);
    }
  });
});

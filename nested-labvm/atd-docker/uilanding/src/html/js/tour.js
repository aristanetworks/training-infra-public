/**
 * ATL Guided Tour using Driver.js
 * Provides an interactive walkthrough of the UILanding interface
 */

const ATLTour = {
  driver: null,
  STORAGE_KEY: 'atl-tour-completed',

  /**
   * Define tour steps - each step highlights a UI element with description
   */
  getSteps() {
    const steps = [];

    // Step 1: Welcome + System Status
    if (document.getElementById('system-status-badge')) {
      steps.push({
        element: '#system-status-badge',
        popover: {
          title: 'Welcome to Arista Training Labs!',
          description: 'This badge shows your system connectivity status. Click it to see detailed connection information for CVP, WebSocket, and gRPC services.',
          side: 'bottom',
          align: 'end'
        }
      });
    }

    // Step 2: Sidebar Navigation
    if (document.getElementById('sidebar')) {
      steps.push({
        element: '#sidebar',
        popover: {
          title: 'Navigation Menu',
          description: 'Use this sidebar to navigate between different sections of your lab environment. All your tools and resources are accessible from here.',
          side: 'right',
          align: 'start'
        }
      });
    }

    // Step 3: Lab Guides
    const labGuidesLink = document.querySelector('a[href*="labguides"]');
    if (labGuidesLink) {
      steps.push({
        element: 'a[href*="labguides"]',
        popover: {
          title: 'Lab Guides',
          description: 'Access step-by-step documentation for your lab exercises. This opens in a new tab with detailed instructions.',
          side: 'right',
          align: 'start'
        }
      });
    }

    // Step 4: CVP Access
    const cvpLink = document.getElementById('cvpLoaded');
    if (cvpLink) {
      steps.push({
        element: '#cvpLoaded',
        popover: {
          title: 'CloudVision Portal (CVP)',
          description: 'Access CloudVision Portal to manage your network devices, view telemetry data, and execute change controls.',
          side: 'right',
          align: 'start'
        }
      });
    }

    // Step 5: Programmability IDE
    const ideLink = document.querySelector('a[href="/coder"]');
    if (ideLink) {
      steps.push({
        element: 'a[href="/coder"]',
        popover: {
          title: 'Programmability IDE',
          description: 'Launch VS Code in your browser to write and test automation scripts. Pre-configured with Arista tools and examples.',
          side: 'right',
          align: 'start'
        }
      });
    }

    // Step 6: Lab Menu
    const labMenu = document.getElementById('labMenu');
    if (labMenu) {
      steps.push({
        element: '#labMenu',
        popover: {
          title: 'Lab Menu',
          description: 'Select and configure different lab scenarios. Each lab option applies specific configurations to your network devices.',
          side: 'right',
          align: 'start'
        },
        onHighlightStarted: () => {
          // Switch to Lab Menu panel
          const menuClick = document.getElementById('labMenu');
          if (menuClick) {
            menuClick.click();
          }
        }
      });
    }

    // Step 7: Lab Menu Panel (if we switched to it)
    const labMenuPanel = document.getElementById('lab-menu');
    if (labMenuPanel) {
      steps.push({
        element: '#lab-menu',
        popover: {
          title: 'Lab Options',
          description: 'Choose a lab scenario from the available options, then click "Start Lab" to apply the configuration. Progress will be shown below.',
          side: 'left',
          align: 'start'
        }
      });
    }

    // Step 8: Passwords
    const passwordsBtn = document.getElementById('myBtn');
    if (passwordsBtn) {
      steps.push({
        element: '#myBtn',
        popover: {
          title: 'Passwords',
          description: 'Click here to view all usernames and passwords for accessing devices, CVP, and other lab resources.',
          side: 'right',
          align: 'start'
        },
        onHighlightStarted: () => {
          // Return to Home panel
          const homeLink = document.querySelector('a[data-id="home"]');
          if (homeLink) {
            homeLink.click();
          }
        }
      });
    }

    // Step 9: Lab Status
    const labStatus = document.getElementById('labStaus');
    if (labStatus) {
      steps.push({
        element: '#labStaus',
        popover: {
          title: 'Lab Status',
          description: 'Monitor the health and status of all devices in your lab environment. Useful for troubleshooting connectivity issues.',
          side: 'right',
          align: 'start'
        }
      });
    }

    // Step 10: Topology View
    const topology = document.querySelector('.topology');
    if (topology) {
      steps.push({
        element: '.topology',
        popover: {
          title: 'Network Topology',
          description: 'Interactive visualization of your lab network. Click on devices to see details, drag to rearrange, and use scroll to zoom.',
          side: 'top',
          align: 'center'
        },
        onHighlightStarted: () => {
          // Ensure we're on Home panel
          const homeLink = document.querySelector('a[data-id="home"]');
          if (homeLink) {
            homeLink.click();
          }
        }
      });
    }

    // Step 11: Topology Controls
    const topoControls = document.querySelector('.topology-controls');
    if (topoControls) {
      steps.push({
        element: '.topology-controls',
        popover: {
          title: 'Topology Controls',
          description: 'Switch between static and interactive views, search for devices, filter by device type, and reset the layout.',
          side: 'bottom',
          align: 'start'
        }
      });
    }

    // Step 12: Time Remaining
    const timer = document.getElementById('countdown_timer');
    if (timer) {
      steps.push({
        element: '#countdown_timer',
        popover: {
          title: 'Time Remaining',
          description: 'Keep an eye on your remaining lab time. The timer shows how much time you have left in your session.',
          side: 'bottom',
          align: 'center'
        }
      });
    }

    return steps;
  },

  /**
   * Initialize the Driver.js tour
   */
  init() {
    // Check if Driver.js is loaded (v1.x uses window.driver.js)
    if (typeof window.driver === 'undefined' || typeof window.driver.js === 'undefined') {
      console.error('[ATLTour] Driver.js not loaded');
      return;
    }

    const steps = this.getSteps();

    if (steps.length === 0) {
      console.warn('[ATLTour] No tour steps available');
      return;
    }

    // Driver.js v1.x API
    const self = this;
    this.driver = window.driver.js.driver({
      showProgress: true,
      animate: true,
      allowClose: true,
      overlayClickNext: false,
      stagePadding: 10,
      stageRadius: 8,
      popoverClass: 'atl-tour-popover',
      progressText: 'Step {{current}} of {{total}}',
      nextBtnText: 'Next',
      prevBtnText: 'Back',
      doneBtnText: 'Finish',
      onHighlightStarted: (element) => {
        // Add background to sidebar elements that are normally transparent
        if (element && element.element) {
          const el = element.element;
          const isInSidebar = el.closest('#sidebar') || el.closest('.left-sidebar');
          if (isInSidebar) {
            el.dataset.originalBg = el.style.backgroundColor || '';
            el.style.backgroundColor = '#04152a';
          }
        }
      },
      onDeselected: (element) => {
        // Restore original background
        if (element && element.element) {
          const el = element.element;
          if (el.dataset.originalBg !== undefined) {
            el.style.backgroundColor = el.dataset.originalBg;
            delete el.dataset.originalBg;
          }
        }
      },
      onDestroyed: () => {
        // Remove tour class from body
        document.body.classList.remove('tour-active');

        // Mark tour as completed
        self.markCompleted();

        // Return to home panel
        const homeLink = document.querySelector('a[data-id="home"]');
        if (homeLink) {
          homeLink.click();
        }
      },
      steps: steps
    });

    console.log('[ATLTour] Initialized with', steps.length, 'steps');

    // Bind the start button
    this.bindStartButton();
  },

  /**
   * Start the tour
   */
  start() {
    if (this.driver) {
      // Add class to body for CSS targeting
      document.body.classList.add('tour-active');
      this.driver.drive();
    } else {
      console.error('[ATLTour] Tour not initialized');
    }
  },

  /**
   * Check if tour has been completed before
   */
  isCompleted() {
    return localStorage.getItem(this.STORAGE_KEY) === 'true';
  },

  /**
   * Mark tour as completed
   */
  markCompleted() {
    localStorage.setItem(this.STORAGE_KEY, 'true');
  },

  /**
   * Reset tour completion status (for testing)
   */
  reset() {
    localStorage.removeItem(this.STORAGE_KEY);
    console.log('[ATLTour] Tour reset - will show on next page load');
  },

  /**
   * Bind click handler to the tour button
   */
  bindStartButton() {
    const tourBtn = document.getElementById('startTourBtn');
    if (tourBtn) {
      tourBtn.addEventListener('click', (e) => {
        e.preventDefault();
        this.start();
      });
    }
  },

  /**
   * Auto-start tour for first-time visitors
   * Called after the initial loading overlay is hidden
   */
  autoStart() {
    if (!this.isCompleted()) {
      // Small delay to ensure UI is ready
      setTimeout(() => {
        this.start();
      }, 500);
    }
  }
};

// Initialize tour when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Always bind the button immediately so it works on click
  const tourBtn = document.getElementById('startTourBtn');
  if (tourBtn) {
    tourBtn.addEventListener('click', (e) => {
      e.preventDefault();
      // Initialize if not already done
      if (!ATLTour.driver) {
        ATLTour.init();
      }
      ATLTour.start();
    });
    console.log('[ATLTour] Button bound');
  }

  // Wait for initial loading overlay to close before auto-starting
  const checkOverlay = () => {
    const overlay = document.getElementById('initialLoadingOverlay');
    if (!overlay || overlay.style.display === 'none') {
      ATLTour.init();
      ATLTour.autoStart();
    } else {
      // Check again in 500ms
      setTimeout(checkOverlay, 500);
    }
  };

  // Start checking after a brief delay
  setTimeout(checkOverlay, 1000);
});

// Expose to global scope for manual triggering
window.ATLTour = ATLTour;

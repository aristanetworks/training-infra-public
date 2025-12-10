/**
 * ATL Guided Tour using TourGuide.js
 * Provides an interactive walkthrough of the UILanding interface
 */

const ATLTour = {
  tg: null,
  STORAGE_KEY: 'atl-tour-completed',

  /**
   * Define tour steps
   */
  getSteps() {
    const steps = [];

    // Step 1: Welcome + System Status
    if (document.getElementById('system-status-badge')) {
      steps.push({
        target: '#system-status-badge',
        title: 'Welcome to Arista Training Labs!',
        content: 'This badge shows your system connectivity status. Click it to see detailed connection information for CVP, WebSocket, and gRPC services.'
      });
    }

    // Step 2: Sidebar Navigation
    if (document.getElementById('sidebar')) {
      steps.push({
        target: '#sidebar',
        title: 'Navigation Menu',
        content: 'Use this sidebar to navigate between different sections of your lab environment. All your tools and resources are accessible from here.'
      });
    }

    // Step 3: Lab Guides
    const labGuidesLink = document.querySelector('a[href*="labguides"]');
    if (labGuidesLink) {
      steps.push({
        target: 'a[href*="labguides"]',
        title: 'Lab Guides',
        content: 'Access step-by-step documentation for your lab exercises. This opens in a new tab with detailed instructions.'
      });
    }

    // Step 4: CVP Access
    const cvpLink = document.getElementById('cvpLoaded');
    if (cvpLink) {
      steps.push({
        target: '#cvpLoaded',
        title: 'CloudVision Portal (CVP)',
        content: 'Access CloudVision Portal to manage your network devices, view telemetry data, and execute change controls.'
      });
    }

    // Step 5: Programmability IDE
    const ideLink = document.querySelector('a[href="/coder"]');
    if (ideLink) {
      steps.push({
        target: 'a[href="/coder"]',
        title: 'Programmability IDE',
        content: 'Launch VS Code in your browser to write and test automation scripts. Pre-configured with Arista tools and examples.'
      });
    }

    // Step 6: Lab Menu
    const labMenu = document.getElementById('labMenu');
    if (labMenu) {
      steps.push({
        target: '#labMenu',
        title: 'Lab Menu',
        content: 'Select and configure different lab scenarios. Each lab option applies specific configurations to your network devices.'
      });
    }

    // Step 7: Passwords
    const passwordsBtn = document.getElementById('myBtn');
    if (passwordsBtn) {
      steps.push({
        target: '#myBtn',
        title: 'Passwords',
        content: 'Click here to view all usernames and passwords for accessing devices, CVP, and other lab resources.'
      });
    }

    // Step 8: Lab Status
    const labStatus = document.getElementById('labStaus');
    if (labStatus) {
      steps.push({
        target: '#labStaus',
        title: 'Lab Status',
        content: 'Monitor the health and status of all devices in your lab environment. Useful for troubleshooting connectivity issues.'
      });
    }

    // Step 9: Topology View
    const topology = document.querySelector('.topology');
    if (topology) {
      steps.push({
        target: '.topology',
        title: 'Network Topology',
        content: 'Interactive visualization of your lab network. Click on devices to see details, drag to rearrange, and use scroll to zoom.'
      });
    }

    // Step 10: Topology Controls
    const topoControls = document.querySelector('.topology-controls');
    if (topoControls) {
      steps.push({
        target: '.topology-controls',
        title: 'Topology Controls',
        content: 'Switch between static and interactive views, search for devices, filter by device type, and reset the layout.'
      });
    }

    // Step 11: Time Remaining
    const timer = document.getElementById('countdown_timer');
    if (timer) {
      steps.push({
        target: '#countdown_timer',
        title: 'Time Remaining',
        content: 'Keep an eye on your remaining lab time. The timer shows how much time you have left in your session.'
      });
    }

    return steps;
  },

  /**
   * Initialize the TourGuide.js tour
   */
  init() {
    // Check if TourGuide.js is loaded
    if (typeof tourguide === 'undefined' || typeof tourguide.TourGuideClient === 'undefined') {
      console.error('[ATLTour] TourGuide.js not loaded');
      return;
    }

    const steps = this.getSteps();

    if (steps.length === 0) {
      console.warn('[ATLTour] No tour steps available');
      return;
    }

    // Create TourGuide instance
    this.tg = new tourguide.TourGuideClient({
      steps: steps,
      backdropColor: 'rgba(7, 28, 53, 0.8)',
      targetPadding: 10,
      nextLabel: 'Next',
      prevLabel: 'Back',
      finishLabel: 'Finish',
      showStepProgress: true,
      exitOnEscape: true,
      exitOnClickOutside: false,
      autoScroll: true,
      autoScrollOffset: 50
    });

    // Listen for tour finish
    this.tg.onFinish(() => {
      this.markCompleted();
      document.body.classList.remove('tour-active');

      // Return to home panel
      const homeLink = document.querySelector('a[data-id="home"]');
      if (homeLink) {
        homeLink.click();
      }
    });

    // Listen for tour close/exit
    this.tg.onAfterExit(() => {
      document.body.classList.remove('tour-active');
    });

    console.log('[ATLTour] Initialized with', steps.length, 'steps');
  },

  /**
   * Start the tour
   */
  start() {
    if (this.tg) {
      document.body.classList.add('tour-active');
      this.tg.start();
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
   * Auto-start tour for first-time visitors
   */
  autoStart() {
    if (!this.isCompleted()) {
      setTimeout(() => {
        this.start();
      }, 500);
    }
  }
};

// Initialize tour when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  // Bind the button immediately
  const tourBtn = document.getElementById('startTourBtn');
  if (tourBtn) {
    tourBtn.addEventListener('click', (e) => {
      e.preventDefault();
      if (!ATLTour.tg) {
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
      setTimeout(checkOverlay, 500);
    }
  };

  setTimeout(checkOverlay, 1000);
});

// Expose to global scope for manual triggering
window.ATLTour = ATLTour;

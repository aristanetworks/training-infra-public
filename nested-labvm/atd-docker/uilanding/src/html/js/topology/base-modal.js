/**
 * Base Modal Class
 *
 * Unified modal system for ATD topology dialogs.
 * Provides consistent theming, keyboard handling, and overlay behavior.
 */

class BaseModal {
    // Configuration constants
    static THEMES = {
        DARK: 'dark',
        LIGHT: 'light'
    };

    static SIZES = {
        SMALL: 'small',      // max-width: 400px
        MEDIUM: 'medium',    // max-width: 600px
        LARGE: 'large',      // max-width: 700px
        XLARGE: 'xlarge'     // max-width: 900px
    };

    /**
     * Create a new modal
     * @param {Object} options - Modal configuration
     * @param {string} options.id - Unique ID for the modal
     * @param {string} options.title - Modal title
     * @param {string} options.theme - 'dark' or 'light' (default: 'dark')
     * @param {string} options.size - 'small', 'medium', 'large', 'xlarge' (default: 'medium')
     * @param {boolean} options.closeOnOverlay - Close when clicking overlay (default: true)
     * @param {boolean} options.closeOnEscape - Close when pressing Escape (default: true)
     * @param {string} options.className - Additional CSS class names
     */
    constructor(options = {}) {
        this.id = options.id || `modal-${Date.now()}`;
        this.title = options.title || '';
        this.theme = options.theme || BaseModal.THEMES.DARK;
        this.size = options.size || BaseModal.SIZES.MEDIUM;
        this.closeOnOverlay = options.closeOnOverlay !== false;
        this.closeOnEscape = options.closeOnEscape !== false;
        this.className = options.className || '';

        this.overlay = null;
        this.modal = null;
        this.escapeHandler = null;
        this.focusTrapHandler = null;
        this.previouslyFocused = null;
        this.isVisible = false;
    }

    /**
     * Escape HTML to prevent XSS
     */
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    /**
     * Create the modal structure
     */
    create() {
        // Remove any existing modal with same ID
        this.destroy();

        // Create overlay
        this.overlay = document.createElement('div');
        this.overlay.id = `${this.id}-overlay`;
        this.overlay.className = `atd-modal-overlay atd-modal-overlay--${this.theme}`;

        // Create modal container
        this.modal = document.createElement('div');
        this.modal.id = this.id;
        this.modal.className = `atd-modal atd-modal--${this.theme} atd-modal--${this.size}`;
        if (this.className) {
            this.modal.classList.add(...this.className.split(' '));
        }

        // Build modal structure
        this.modal.innerHTML = `
            <div class="atd-modal__header">
                <h2 class="atd-modal__title">${this.escapeHtml(this.title)}</h2>
                <button class="atd-modal__close" title="Close" aria-label="Close modal">&times;</button>
            </div>
            <div class="atd-modal__content">
                <!-- Content rendered here -->
            </div>
            <div class="atd-modal__footer">
                <!-- Footer rendered here -->
            </div>
        `;

        // Attach event handlers
        this.setupEventHandlers();

        this.overlay.appendChild(this.modal);
        return this.overlay;
    }

    /**
     * Setup event handlers
     */
    setupEventHandlers() {
        // Close button
        const closeBtn = this.modal.querySelector('.atd-modal__close');
        if (closeBtn) {
            closeBtn.addEventListener('click', () => this.hide());
        }

        // Overlay click
        if (this.closeOnOverlay) {
            this.overlay.addEventListener('click', (e) => {
                if (e.target === this.overlay) {
                    this.hide();
                }
            });
        }

        // Escape key
        if (this.closeOnEscape) {
            this.escapeHandler = (e) => {
                if (e.key === 'Escape' && this.isVisible) {
                    this.hide();
                }
            };
            document.addEventListener('keydown', this.escapeHandler);
        }

        // Prevent scroll on body when modal is open
        this.overlay.addEventListener('wheel', (e) => {
            // Allow scrolling within modal content
            const content = this.modal.querySelector('.atd-modal__content');
            if (content && content.contains(e.target)) {
                return;
            }
            e.preventDefault();
        }, { passive: false });
    }

    /**
     * Get all focusable elements within the modal
     */
    getFocusableElements() {
        const focusableSelectors = [
            'button:not([disabled])',
            'input:not([disabled])',
            'select:not([disabled])',
            'textarea:not([disabled])',
            'a[href]',
            '[tabindex]:not([tabindex="-1"]):not([disabled])'
        ].join(', ');

        return this.modal.querySelectorAll(focusableSelectors);
    }

    /**
     * Set up focus trap to keep focus within the modal
     */
    setupFocusTrap() {
        this.focusTrapHandler = (e) => {
            if (e.key !== 'Tab' || !this.isVisible) return;

            const focusableElements = this.getFocusableElements();
            if (focusableElements.length === 0) return;

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];

            if (e.shiftKey) {
                // Shift + Tab: if on first element, go to last
                if (document.activeElement === firstElement) {
                    e.preventDefault();
                    lastElement.focus();
                }
            } else {
                // Tab: if on last element, go to first
                if (document.activeElement === lastElement) {
                    e.preventDefault();
                    firstElement.focus();
                }
            }
        };

        document.addEventListener('keydown', this.focusTrapHandler);
    }

    /**
     * Show the modal
     */
    show() {
        if (!this.overlay) {
            this.create();
        }

        // Store previously focused element to restore on close
        this.previouslyFocused = document.activeElement;

        document.body.appendChild(this.overlay);
        this.isVisible = true;

        // Trigger animation
        requestAnimationFrame(() => {
            this.overlay.classList.add('atd-modal-overlay--visible');
        });

        // Set up focus trap
        this.setupFocusTrap();

        // Focus management - focus first interactive element
        const focusableElements = this.getFocusableElements();
        if (focusableElements.length > 0) {
            focusableElements[0].focus();
        }
    }

    /**
     * Hide the modal
     */
    hide() {
        if (!this.isVisible) return;

        this.overlay.classList.remove('atd-modal-overlay--visible');
        this.isVisible = false;

        // Cleanup focus trap
        if (this.focusTrapHandler) {
            document.removeEventListener('keydown', this.focusTrapHandler);
            this.focusTrapHandler = null;
        }

        // Restore focus to previously focused element
        if (this.previouslyFocused && this.previouslyFocused.focus) {
            this.previouslyFocused.focus();
            this.previouslyFocused = null;
        }

        // Wait for animation before removing
        setTimeout(() => {
            if (this.overlay && this.overlay.parentNode) {
                this.overlay.remove();
            }
        }, 200);
    }

    /**
     * Destroy the modal and cleanup
     */
    destroy() {
        if (this.escapeHandler) {
            document.removeEventListener('keydown', this.escapeHandler);
            this.escapeHandler = null;
        }

        if (this.focusTrapHandler) {
            document.removeEventListener('keydown', this.focusTrapHandler);
            this.focusTrapHandler = null;
        }

        if (this.overlay && this.overlay.parentNode) {
            this.overlay.remove();
        }

        // Restore focus if still tracked
        if (this.previouslyFocused && this.previouslyFocused.focus) {
            this.previouslyFocused.focus();
            this.previouslyFocused = null;
        }

        this.overlay = null;
        this.modal = null;
        this.isVisible = false;
    }

    /**
     * Set modal content
     * @param {string|HTMLElement} content - HTML string or DOM element
     */
    setContent(content) {
        const contentEl = this.modal?.querySelector('.atd-modal__content');
        if (!contentEl) return;

        if (typeof content === 'string') {
            contentEl.innerHTML = content;
        } else if (content instanceof HTMLElement) {
            contentEl.innerHTML = '';
            contentEl.appendChild(content);
        }
    }

    /**
     * Set modal footer
     * @param {string|HTMLElement} footer - HTML string or DOM element
     */
    setFooter(footer) {
        const footerEl = this.modal?.querySelector('.atd-modal__footer');
        if (!footerEl) return;

        if (typeof footer === 'string') {
            footerEl.innerHTML = footer;
        } else if (footer instanceof HTMLElement) {
            footerEl.innerHTML = '';
            footerEl.appendChild(footer);
        }
    }

    /**
     * Update modal title
     * @param {string} title - New title
     */
    setTitle(title) {
        this.title = title;
        const titleEl = this.modal?.querySelector('.atd-modal__title');
        if (titleEl) {
            titleEl.textContent = title;
        }
    }

    /**
     * Get content element for direct manipulation
     */
    getContentElement() {
        return this.modal?.querySelector('.atd-modal__content');
    }

    /**
     * Get footer element for direct manipulation
     */
    getFooterElement() {
        return this.modal?.querySelector('.atd-modal__footer');
    }

    /**
     * Show loading state in content
     * @param {string} message - Loading message
     */
    showLoading(message = 'Loading...') {
        this.setContent(`
            <div class="atd-modal__loading">
                <div class="atd-modal__spinner"></div>
                <p>${this.escapeHtml(message)}</p>
            </div>
        `);
    }

    /**
     * Show error state in content
     * @param {string} message - Error message
     * @param {string} detail - Optional error detail
     */
    showError(message, detail = '') {
        this.setContent(`
            <div class="atd-modal__error">
                <div class="atd-modal__error-icon">&#10008;</div>
                <h3>${this.escapeHtml(message)}</h3>
                ${detail ? `<p class="atd-modal__error-detail">${this.escapeHtml(detail)}</p>` : ''}
            </div>
        `);
    }

    /**
     * Show success state in content
     * @param {string} message - Success message
     * @param {string} detail - Optional detail
     */
    showSuccess(message, detail = '') {
        this.setContent(`
            <div class="atd-modal__success">
                <div class="atd-modal__success-icon">&#10004;</div>
                <h3>${this.escapeHtml(message)}</h3>
                ${detail ? `<p>${this.escapeHtml(detail)}</p>` : ''}
            </div>
        `);
    }

    /**
     * Add a button to the footer
     * @param {Object} config - Button configuration
     * @param {string} config.text - Button text
     * @param {string} config.type - 'primary', 'secondary', 'danger' (default: 'secondary')
     * @param {Function} config.onClick - Click handler
     * @param {boolean} config.disabled - Whether button is disabled
     * @returns {HTMLButtonElement} The created button
     */
    addFooterButton(config) {
        const footerEl = this.getFooterElement();
        if (!footerEl) return null;

        const button = document.createElement('button');
        button.className = `atd-modal__btn atd-modal__btn--${config.type || 'secondary'}`;
        button.textContent = config.text;
        button.disabled = config.disabled || false;

        if (config.onClick) {
            button.addEventListener('click', config.onClick);
        }

        footerEl.appendChild(button);
        return button;
    }

    /**
     * Clear footer buttons
     */
    clearFooter() {
        const footerEl = this.getFooterElement();
        if (footerEl) {
            footerEl.innerHTML = '';
        }
    }
}

// Export for use in other modules
window.BaseModal = BaseModal;

/**
 * Viewer Event Handlers - Simplified event handling for embedded diagrams
 * Subset of EventManager: context menu with SSH/Console/Focus
 */

export class ViewerEventHandlers {
    constructor(cy, container) {
        this.cy = cy;
        this.container = container;
        this.contextMenu = null;
        this.focusMode = false;
        this.focusedNode = null;

        this.bindEvents();
    }

    bindEvents() {
        // Right-click context menu
        this.cy.on('cxttap', 'node', (e) => {
            if (e.target.data('isZone')) return;
            this.showContextMenu(e);
        });

        // Left-click to close context menu
        this.cy.on('tap', () => {
            this.hideContextMenu();
        });

        // Hover effects
        this.cy.on('mouseover', 'node', (e) => {
            if (e.target.data('isZone')) return;
            e.target.addClass('hover');
            this.container.style.cursor = 'pointer';
        });

        this.cy.on('mouseout', 'node', (e) => {
            e.target.removeClass('hover');
            this.container.style.cursor = '';
        });

        // Close context menu on outside click
        document.addEventListener('click', (e) => {
            if (this.contextMenu && !this.contextMenu.contains(e.target)) {
                this.hideContextMenu();
            }
        });

        // Keyboard
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape') {
                this.hideContextMenu();
                if (this.focusMode) this.exitFocusMode();
            }
        });
    }

    showContextMenu(e) {
        this.hideContextMenu();

        const node = e.target;
        const ip = node.data('ip');
        const label = node.data('label') || node.id();

        const menu = document.createElement('div');
        menu.className = 'atl-topology-context-menu';

        // Device info header
        const header = document.createElement('div');
        header.style.padding = '6px 14px';
        header.style.fontWeight = '600';
        header.style.color = '#071c35';
        header.style.borderBottom = '1px solid #e0e0e0';
        header.textContent = label;
        if (ip) {
            const ipSpan = document.createElement('span');
            ipSpan.style.fontSize = '11px';
            ipSpan.style.color = '#999';
            ipSpan.style.marginLeft = '8px';
            ipSpan.textContent = ip;
            header.appendChild(ipSpan);
        }
        menu.appendChild(header);

        // Open Terminal (SSH)
        if (ip) {
            this.addMenuItem(menu, 'Open Terminal', () => {
                this.openTerminal(ip, label);
            });

            // Open Console
            this.addMenuItem(menu, 'Open Console', () => {
                this.openConsole(label);
            });

            menu.appendChild(this.createSeparator());
        }

        // Focus Device
        this.addMenuItem(menu, this.focusMode && this.focusedNode === node ? 'Exit Focus' : 'Focus Device', () => {
            if (this.focusMode && this.focusedNode === node) {
                this.exitFocusMode();
            } else {
                this.enterFocusMode(node);
            }
        });

        // Position the menu
        const renderedPos = node.renderedPosition();
        const containerRect = this.container.getBoundingClientRect();
        menu.style.left = (containerRect.left + renderedPos.x + 10) + 'px';
        menu.style.top = (containerRect.top + renderedPos.y + 10) + 'px';

        document.body.appendChild(menu);
        this.contextMenu = menu;

        // Adjust if off screen
        const menuRect = menu.getBoundingClientRect();
        if (menuRect.right > window.innerWidth) {
            menu.style.left = (window.innerWidth - menuRect.width - 10) + 'px';
        }
        if (menuRect.bottom > window.innerHeight) {
            menu.style.top = (window.innerHeight - menuRect.height - 10) + 'px';
        }
    }

    addMenuItem(menu, label, onClick) {
        const item = document.createElement('button');
        item.className = 'atl-topology-context-menu-item';
        item.textContent = label;
        item.addEventListener('click', () => {
            onClick();
            this.hideContextMenu();
        });
        menu.appendChild(item);
    }

    createSeparator() {
        const sep = document.createElement('div');
        sep.className = 'atl-topology-context-menu-separator';
        return sep;
    }

    hideContextMenu() {
        if (this.contextMenu) {
            this.contextMenu.remove();
            this.contextMenu = null;
        }
    }

    openTerminal(ip, label) {
        const url = `/terminal?ip=${encodeURIComponent(ip)}&name=${encodeURIComponent(label)}`;
        window.open(url, `terminal-${label}`);
    }

    openConsole(label) {
        const url = `/console?name=${encodeURIComponent(label)}`;
        window.open(url, `console-${label}`);
    }

    enterFocusMode(node) {
        this.focusMode = true;
        this.focusedNode = node;

        // Fade all elements
        this.cy.elements().addClass('faded');

        // Highlight focused node and its neighborhood
        const neighborhood = node.neighborhood().add(node);
        neighborhood.removeClass('faded');
        node.addClass('focused');
        neighborhood.edges().addClass('highlighted');
        neighborhood.nodes().filter(n => n !== node).addClass('highlighted');
    }

    exitFocusMode() {
        this.focusMode = false;
        this.focusedNode = null;
        this.cy.elements().removeClass('faded focused highlighted');
    }
}

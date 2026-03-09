/**
 * Device Palette - Drag-and-drop device type panel
 * Organizes device types by category with search filtering
 */

// Device type definitions matching device_types.py
const DEVICE_TYPES = {
    provider: {
        label: 'Provider',
        types: [
            { id: 'internet', label: 'Internet', icon: 'images/router.png' },
            { id: 'isp', label: 'ISP', icon: 'images/router.png' },
        ]
    },
    core: {
        label: 'Core',
        types: [
            { id: 'core', label: 'Core', icon: 'images/router.png' },
            { id: 'dci', label: 'DCI', icon: 'images/router.png' },
            { id: 'p', label: 'P Router', icon: 'images/spine.png' },
            { id: 'rr', label: 'Route Reflector', icon: 'images/router.png' },
        ]
    },
    edge: {
        label: 'Edge',
        types: [
            { id: 'borderleaf', label: 'Borderleaf', icon: 'images/leaf.png' },
            { id: 'pe', label: 'PE Router', icon: 'images/spine.png' },
            { id: 'ce', label: 'CE Router', icon: 'images/spine.png' },
            { id: 'gw', label: 'WAN Gateway', icon: 'images/router.png' },
            { id: 'router', label: 'Router', icon: 'images/router.png' },
            { id: 'firewall', label: 'Firewall', icon: 'images/router.png' },
            { id: 'velo_orchestrator', label: 'VeloCloud Orch', icon: 'images/router.png' },
            { id: 'velo_gateway', label: 'VeloCloud GW', icon: 'images/router.png' },
            { id: 'velo_edge', label: 'VeloCloud Edge', icon: 'images/router.png' },
        ]
    },
    fabric: {
        label: 'Fabric',
        types: [
            { id: 'spine', label: 'Spine', icon: 'images/spine.png' },
            { id: 'leaf', label: 'Leaf', icon: 'images/leaf.png' },
            { id: 'memleaf', label: 'Member Leaf', icon: 'images/leaf.png' },
        ]
    },
    endpoint: {
        label: 'Endpoint',
        types: [
            { id: 'host', label: 'Host', icon: 'images/hosts.png' },
            { id: 'linux_host', label: 'Linux Host', icon: 'images/hosts.png' },
            { id: 'customer', label: 'Customer', icon: 'images/router.png' },
            { id: 'oob', label: 'OOB', icon: 'images/router.png' },
            { id: 'other', label: 'Other', icon: 'images/router.png' },
        ]
    },
};

export class DevicePalette {
    constructor(containerId, searchId, options = {}) {
        this.container = document.getElementById(containerId);
        this.searchInput = document.getElementById(searchId);
        this.options = options;

        this.render();
        this.bindSearch();
    }

    render() {
        this.container.innerHTML = '';

        for (const [catId, category] of Object.entries(DEVICE_TYPES)) {
            const catDiv = document.createElement('div');
            catDiv.className = 'palette-category';
            catDiv.setAttribute('data-category', catId);

            // Category header
            const header = document.createElement('div');
            header.className = 'palette-category-header';
            header.innerHTML = `<span class="category-toggle">&#x25BC;</span>${category.label}`;
            header.addEventListener('click', () => {
                catDiv.classList.toggle('collapsed');
            });
            catDiv.appendChild(header);

            // Category items
            const items = document.createElement('div');
            items.className = 'palette-category-items';

            for (const type of category.types) {
                const item = document.createElement('div');
                item.className = 'palette-item';
                item.setAttribute('data-type', type.id);
                item.setAttribute('data-label', type.label.toLowerCase());
                item.draggable = true;

                item.innerHTML = `
                    <div class="palette-item-icon">
                        <img src="${type.icon}" alt="${type.label}" />
                    </div>
                    <span class="palette-item-label">${type.label}</span>
                `;

                // Drag events
                item.addEventListener('dragstart', (e) => {
                    e.dataTransfer.setData('text/plain', type.id);
                    e.dataTransfer.effectAllowed = 'copy';

                    // Create drag ghost
                    const ghost = document.createElement('div');
                    ghost.className = 'palette-drag-ghost';
                    ghost.innerHTML = `<img src="${type.icon}" />`;
                    document.body.appendChild(ghost);
                    e.dataTransfer.setDragImage(ghost, 25, 25);
                    setTimeout(() => ghost.remove(), 0);

                    if (this.options.onDragStart) {
                        this.options.onDragStart(type.id, e);
                    }
                });

                items.appendChild(item);
            }

            catDiv.appendChild(items);
            this.container.appendChild(catDiv);
        }
    }

    bindSearch() {
        this.searchInput.addEventListener('input', (e) => {
            const query = e.target.value.toLowerCase().trim();
            const items = this.container.querySelectorAll('.palette-item');

            items.forEach(item => {
                const label = item.getAttribute('data-label');
                const type = item.getAttribute('data-type');
                const visible = !query || label.includes(query) || type.includes(query);
                item.classList.toggle('hidden', !visible);
            });

            // Expand categories with visible items, collapse empty ones
            this.container.querySelectorAll('.palette-category').forEach(cat => {
                const hasVisible = cat.querySelector('.palette-item:not(.hidden)');
                cat.classList.toggle('collapsed', !hasVisible && query);
            });
        });
    }

    getDeviceTypes() {
        return DEVICE_TYPES;
    }
}

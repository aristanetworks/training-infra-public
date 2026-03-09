/**
 * Properties Panel - Context-sensitive property editor
 * Shows different forms based on selected element type
 */

// All device types for the type dropdown
const ALL_DEVICE_TYPES = [
    'spine', 'leaf', 'borderleaf', 'memleaf', 'host', 'linux_host',
    'router', 'core', 'dci', 'pe', 'ce', 'gw', 'rr', 'p',
    'internet', 'isp', 'oob', 'firewall', 'customer',
    'velo_orchestrator', 'velo_gateway', 'velo_edge', 'other',
];

export class PropertiesPanel {
    constructor(options = {}) {
        this.container = document.getElementById('properties-content');
        this.titleEl = document.getElementById('properties-title');
        this.options = options;
        this.currentElement = null;
        this.currentAnnotation = null;
    }

    clear() {
        this.currentElement = null;
        this.currentAnnotation = null;
        this.titleEl.textContent = 'Properties';
        this.container.innerHTML = `
            <div class="properties-empty">
                <p>Select a node, edge, zone, or annotation to edit its properties.</p>
                <p class="properties-hint">Drag devices from the palette to add them to the canvas.</p>
            </div>
        `;
    }

    showNode(node) {
        this.currentElement = node;
        this.currentAnnotation = null;
        this.titleEl.textContent = 'Node Properties';

        const zones = this.options.zones ? this.options.zones.getZoneList() : [];
        const currentZone = node.parent().length ? node.parent().id() : '';

        const zoneOptions = zones.map(z =>
            `<option value="${z.id}" ${z.id === currentZone ? 'selected' : ''}>${z.label || z.id}</option>`
        ).join('');

        const typeOptions = ALL_DEVICE_TYPES.map(t =>
            `<option value="${t}" ${t === node.data('device_type') ? 'selected' : ''}>${t}</option>`
        ).join('');

        const pos = node.position();

        this.container.innerHTML = `
            <div class="form-group">
                <label for="prop-id">ID</label>
                <input type="text" id="prop-id" value="${this.escapeHtml(node.id())}" readonly />
            </div>
            <div class="form-group">
                <label for="prop-label">Label</label>
                <input type="text" id="prop-label" value="${this.escapeHtml(node.data('label') || '')}" />
            </div>
            <div class="form-group">
                <label for="prop-type">Type</label>
                <select id="prop-type">${typeOptions}</select>
            </div>
            <div class="form-group">
                <label for="prop-ip">IP Address</label>
                <input type="text" id="prop-ip" value="${this.escapeHtml(node.data('ip') || '')}" placeholder="192.168.0.x" />
            </div>
            <div class="form-group">
                <label for="prop-zone">Zone</label>
                <select id="prop-zone">
                    <option value="">None</option>
                    ${zoneOptions}
                </select>
            </div>
            <div class="form-row">
                <div class="form-group">
                    <label for="prop-x">X</label>
                    <input type="number" id="prop-x" value="${Math.round(pos.x)}" />
                </div>
                <div class="form-group">
                    <label for="prop-y">Y</label>
                    <input type="number" id="prop-y" value="${Math.round(pos.y)}" />
                </div>
            </div>
        `;

        this.bindNodeInputs(node.id());
    }

    bindNodeInputs(nodeId) {
        const bind = (elId, field) => {
            const el = document.getElementById(elId);
            if (!el) return;
            el.addEventListener('change', () => {
                if (this.options.onNodeChange) {
                    this.options.onNodeChange(nodeId, { [field]: el.value });
                }
            });
        };

        bind('prop-label', 'label');
        bind('prop-type', 'device_type');
        bind('prop-ip', 'ip');
        bind('prop-zone', 'zone');

        // Position inputs (special handling)
        const xEl = document.getElementById('prop-x');
        const yEl = document.getElementById('prop-y');
        if (xEl && yEl) {
            const updatePos = () => {
                const el = this.currentElement;
                if (el) {
                    el.position({
                        x: parseInt(xEl.value) || 0,
                        y: parseInt(yEl.value) || 0,
                    });
                }
            };
            xEl.addEventListener('change', updatePos);
            yEl.addEventListener('change', updatePos);
        }
    }

    showEdge(edge) {
        this.currentElement = edge;
        this.currentAnnotation = null;
        this.titleEl.textContent = 'Edge Properties';

        this.container.innerHTML = `
            <div class="form-group">
                <label>Source</label>
                <input type="text" value="${this.escapeHtml(edge.source().data('label') || edge.source().id())}" readonly />
            </div>
            <div class="form-group">
                <label>Target</label>
                <input type="text" value="${this.escapeHtml(edge.target().data('label') || edge.target().id())}" readonly />
            </div>
            <div class="form-group">
                <label for="prop-source-port">Source Port</label>
                <input type="text" id="prop-source-port" value="${this.escapeHtml(edge.data('source_port') || '')}" placeholder="Ethernet1" />
            </div>
            <div class="form-group">
                <label for="prop-target-port">Target Port</label>
                <input type="text" id="prop-target-port" value="${this.escapeHtml(edge.data('target_port') || '')}" placeholder="Ethernet1" />
            </div>
        `;

        const edgeId = edge.id();
        document.getElementById('prop-source-port').addEventListener('change', (e) => {
            if (this.options.onEdgeChange) {
                this.options.onEdgeChange(edgeId, { source_port: e.target.value });
            }
        });
        document.getElementById('prop-target-port').addEventListener('change', (e) => {
            if (this.options.onEdgeChange) {
                this.options.onEdgeChange(edgeId, { target_port: e.target.value });
            }
        });
    }

    showZone(node) {
        this.currentElement = node;
        this.currentAnnotation = null;
        this.titleEl.textContent = 'Zone Properties';

        this.container.innerHTML = `
            <div class="form-group">
                <label for="prop-zone-id">ID</label>
                <input type="text" id="prop-zone-id" value="${this.escapeHtml(node.id())}" readonly />
            </div>
            <div class="form-group">
                <label for="prop-zone-label">Label</label>
                <input type="text" id="prop-zone-label" value="${this.escapeHtml(node.data('label') || '')}" />
            </div>
            <div class="form-group">
                <label for="prop-zone-color">Border Color</label>
                <input type="color" id="prop-zone-color" value="${node.data('zoneColor') || '#071c35'}" />
            </div>
            <div class="form-group">
                <label for="prop-zone-bg">Background</label>
                <input type="color" id="prop-zone-bg" value="${this.extractHexFromRgba(node.data('zoneBackground') || '#071c35')}" />
            </div>
            <div class="form-group">
                <label for="prop-zone-border">Border Style</label>
                <select id="prop-zone-border">
                    <option value="solid" ${node.data('zoneBorderStyle') === 'solid' ? 'selected' : ''}>Solid</option>
                    <option value="dashed" ${node.data('zoneBorderStyle') === 'dashed' ? 'selected' : ''}>Dashed</option>
                </select>
            </div>
        `;

        const zoneId = node.id();
        const bindZone = (elId, field) => {
            const el = document.getElementById(elId);
            if (!el) return;
            el.addEventListener('change', () => {
                let value = el.value;
                if (field === 'background') {
                    // Convert hex to rgba
                    const r = parseInt(value.slice(1, 3), 16);
                    const g = parseInt(value.slice(3, 5), 16);
                    const b = parseInt(value.slice(5, 7), 16);
                    value = `rgba(${r}, ${g}, ${b}, 0.05)`;
                }
                if (this.options.onZoneChange) {
                    this.options.onZoneChange(zoneId, { [field]: value });
                }
            });
        };

        bindZone('prop-zone-label', 'label');
        bindZone('prop-zone-color', 'color');
        bindZone('prop-zone-bg', 'background');
        bindZone('prop-zone-border', 'border_style');
    }

    showAnnotation(annotation) {
        this.currentElement = null;
        this.currentAnnotation = annotation;
        this.titleEl.textContent = 'Annotation Properties';

        this.container.innerHTML = `
            <div class="form-group">
                <label for="prop-ann-text">Text</label>
                <input type="text" id="prop-ann-text" value="${this.escapeHtml(annotation.text || '')}" />
            </div>
            <div class="form-group">
                <label for="prop-ann-color">Color</label>
                <input type="color" id="prop-ann-color" value="${annotation.color || '#4c5cae'}" />
            </div>
            <div class="form-group">
                <label for="prop-ann-size">Font Size</label>
                <input type="number" id="prop-ann-size" value="${annotation.fontSize || 12}" min="8" max="48" />
            </div>
            <div class="form-group">
                <label>
                    <input type="checkbox" id="prop-ann-bg" ${annotation.background !== false ? 'checked' : ''} />
                    Show Background
                </label>
            </div>
        `;

        const idx = annotation.index;
        const bindAnn = (elId, field) => {
            const el = document.getElementById(elId);
            if (!el) return;
            el.addEventListener('change', () => {
                const value = el.type === 'checkbox' ? el.checked : el.value;
                if (this.options.onAnnotationChange) {
                    this.options.onAnnotationChange(idx, { [field]: value });
                }
            });
        };

        bindAnn('prop-ann-text', 'text');
        bindAnn('prop-ann-color', 'color');
        bindAnn('prop-ann-size', 'fontSize');
        bindAnn('prop-ann-bg', 'background');
    }

    extractHexFromRgba(rgba) {
        if (rgba.startsWith('#')) return rgba;
        const match = rgba.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (match) {
            const r = parseInt(match[1]).toString(16).padStart(2, '0');
            const g = parseInt(match[2]).toString(16).padStart(2, '0');
            const b = parseInt(match[3]).toString(16).padStart(2, '0');
            return `#${r}${g}${b}`;
        }
        return '#071c35';
    }

    escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}

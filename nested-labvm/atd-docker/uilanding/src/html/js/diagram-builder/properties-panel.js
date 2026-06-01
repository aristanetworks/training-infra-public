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
            <div class="form-group">
                <label for="prop-highlight">Highlight</label>
                <div class="form-row">
                    <div class="form-group" style="margin-bottom:0">
                        <input type="color" id="prop-highlight" value="${node.data('highlight') || '#fbb500'}" />
                    </div>
                    <div class="form-group" style="margin-bottom:0;flex:0">
                        <button class="export-btn btn-inline" id="btn-clear-highlight">Clear</button>
                    </div>
                </div>
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

        const highlightEl = document.getElementById('prop-highlight');
        if (highlightEl) {
            highlightEl.addEventListener('change', () => {
                if (this.options.onNodeChange) {
                    this.options.onNodeChange(nodeId, { highlight: highlightEl.value });
                }
            });
        }
        const clearBtn = document.getElementById('btn-clear-highlight');
        if (clearBtn) {
            clearBtn.addEventListener('click', () => {
                if (this.options.onNodeChange) {
                    this.options.onNodeChange(nodeId, { highlight: '' });
                }
                if (highlightEl) highlightEl.value = '#fbb500';
            });
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
            <div class="form-group">
                <label for="prop-edge-label">Label</label>
                <input type="text" id="prop-edge-label" value="${this.escapeHtml(edge.data('label') || '')}" placeholder="e.g., BGP Session" />
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
        document.getElementById('prop-edge-label').addEventListener('change', (e) => {
            if (this.options.onEdgeChange) {
                this.options.onEdgeChange(edgeId, { label: e.target.value });
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
            <div class="form-group">
                <label for="prop-zone-layer">Layer (higher = on top)</label>
                <input type="number" id="prop-zone-layer" value="${node.data('zoneLayer') || 0}" min="0" max="99" />
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
                    value = `rgba(${r}, ${g}, ${b}, 0.15)`;
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
        bindZone('prop-zone-layer', 'layer');
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
            <button class="export-btn btn-danger" id="btn-delete-annotation">Delete Annotation</button>
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

        document.getElementById('btn-delete-annotation').addEventListener('click', () => {
            if (this.options.onAnnotationDelete) {
                this.options.onAnnotationDelete(idx);
            }
        });
    }

    showFlow(flow, index) {
        this.currentElement = null;
        this.currentAnnotation = null;
        this.titleEl.textContent = 'Flow Properties';

        const hasPath = flow.path && flow.path.length > 0;

        // Build path list with remove buttons and hop labels
        const pathItemsHtml = hasPath ? (flow.path || []).map((nodeId, hopIdx) => {
            const label = (flow.labels || {})[nodeId] || '';
            return '<div class="flow-hop-item" data-hop="' + hopIdx + '">'
                + '<span class="flow-hop-number">' + (hopIdx + 1) + '</span>'
                + '<span class="flow-hop-name">' + this.escapeHtml(nodeId) + '</span>'
                + '<input type="text" class="flow-hop-label-input" data-node="' + this.escapeHtml(nodeId) + '" value="' + this.escapeHtml(label) + '" placeholder="label" />'
                + '<button class="flow-hop-remove" data-hop="' + hopIdx + '" title="Remove from path">&times;</button>'
                + '</div>';
        }).join('') : '<div class="flow-path-empty">No path defined yet</div>';

        this.container.innerHTML = '<div class="form-group">'
            + '<label for="prop-flow-name">Name</label>'
            + '<input type="text" id="prop-flow-name" value="' + this.escapeHtml(flow.name || '') + '" />'
            + '</div>'
            + '<div class="form-group">'
            + '<label for="prop-flow-color">Color</label>'
            + '<input type="color" id="prop-flow-color" value="' + (flow.color || '#4c5cae') + '" />'
            + '</div>'
            + '<div class="form-group">'
            + '<label for="prop-flow-speed">Speed: <span id="flow-speed-display">' + (flow.speed || 1) + 'x</span></label>'
            + '<input type="range" id="prop-flow-speed" min="0.25" max="3" step="0.25" value="' + (flow.speed || 1) + '" />'
            + '</div>'
            + '<div class="form-group">'
            + '<label for="prop-flow-desc">Description</label>'
            + '<input type="text" id="prop-flow-desc" value="' + this.escapeHtml(flow.description || '') + '" placeholder="e.g., VXLAN encapsulated traffic" />'
            + '</div>'
            + '<div class="form-group">'
            + '<label>Path &amp; Hop Labels</label>'
            + '<div id="flow-path-list">' + pathItemsHtml + '</div>'
            + '<div class="flow-path-actions">'
            + (hasPath
                ? '<button class="import-btn" id="btn-extend-path" title="Click nodes to add to end of path">Extend Path</button>'
                  + '<button class="export-btn" id="btn-rebuild-path" title="Start over from scratch">Rebuild Path</button>'
                : '<button class="import-btn import-btn-primary" id="btn-build-path">Build Path</button>')
            + '</div>'
            + '</div>'
            + '<div class="form-group">'
            + '<label for="prop-flow-headers">Initial Headers</label>'
            + '<input type="text" id="prop-flow-headers" value="' + this.escapeHtml((flow.initial_headers || ['Packet']).join(', ')) + '" placeholder="e.g., L2 Frame, IP" />'
            + '<div class="flow-path-empty" style="margin-top:2px">Comma-separated, bottom of stack first</div>'
            + '</div>'
            + (hasPath ? '<div class="form-group">'
            + '<label>Encapsulation (per hop)</label>'
            + '<div id="flow-encap-editor">' + this.buildEncapEditor(flow) + '</div>'
            + '</div>' : '')
            + '<button class="export-btn btn-danger" id="btn-delete-flow">Delete Flow</button>';

        this.bindFlowInputs(index);
    }

    buildEncapEditor(flow) {
        if (!flow.path || flow.path.length === 0) return '';
        const encap = flow.encapsulation || {};
        return flow.path.map(nodeId => {
            const ops = encap[nodeId] || [];
            const opsHtml = ops.map((op, opIdx) => {
                return '<div class="encap-op" data-node="' + this.escapeHtml(nodeId) + '" data-op="' + opIdx + '">'
                    + '<select class="encap-action" data-node="' + this.escapeHtml(nodeId) + '" data-op="' + opIdx + '">'
                    + '<option value="push"' + (op.action === 'push' ? ' selected' : '') + '>Push</option>'
                    + '<option value="pop"' + (op.action === 'pop' ? ' selected' : '') + '>Pop</option>'
                    + '</select>'
                    + '<input type="text" class="encap-header" data-node="' + this.escapeHtml(nodeId) + '" data-op="' + opIdx + '" value="' + this.escapeHtml(op.header || '') + '" placeholder="header name" />'
                    + '<button class="flow-hop-remove encap-remove" data-node="' + this.escapeHtml(nodeId) + '" data-op="' + opIdx + '" title="Remove">&times;</button>'
                    + '</div>';
            }).join('');

            return '<div class="encap-node-group">'
                + '<div class="encap-node-header">'
                + '<span class="flow-hop-name">' + this.escapeHtml(nodeId) + '</span>'
                + '<button class="encap-add-btn" data-node="' + this.escapeHtml(nodeId) + '" title="Add encap operation">+ Add</button>'
                + '</div>'
                + opsHtml
                + '</div>';
        }).join('');
    }

    bindFlowInputs(flowIndex) {
        const bindField = (elId, field) => {
            const el = document.getElementById(elId);
            if (!el) return;
            el.addEventListener('change', () => {
                if (this.options.onFlowChange) {
                    this.options.onFlowChange(flowIndex, { [field]: el.value });
                }
            });
        };

        bindField('prop-flow-name', 'name');
        bindField('prop-flow-color', 'color');
        bindField('prop-flow-desc', 'description');

        const speedEl = document.getElementById('prop-flow-speed');
        const speedDisplay = document.getElementById('flow-speed-display');
        if (speedEl) {
            speedEl.addEventListener('input', () => {
                if (speedDisplay) speedDisplay.textContent = speedEl.value + 'x';
                if (this.options.onFlowChange) {
                    this.options.onFlowChange(flowIndex, { speed: speedEl.value });
                }
            });
        }

        // Build path from scratch (no existing path)
        const buildBtn = document.getElementById('btn-build-path');
        if (buildBtn) {
            buildBtn.addEventListener('click', () => {
                if (this.options.onFlowBuildPath) {
                    this.options.onFlowBuildPath(flowIndex);
                }
            });
        }

        // Extend existing path (append nodes to end)
        const extendBtn = document.getElementById('btn-extend-path');
        if (extendBtn) {
            extendBtn.addEventListener('click', () => {
                if (this.options.onFlowExtendPath) {
                    this.options.onFlowExtendPath(flowIndex);
                }
            });
        }

        // Rebuild path from scratch (discard existing)
        const rebuildBtn = document.getElementById('btn-rebuild-path');
        if (rebuildBtn) {
            rebuildBtn.addEventListener('click', () => {
                if (this.options.onFlowBuildPath) {
                    this.options.onFlowBuildPath(flowIndex);
                }
            });
        }

        // Remove individual hop nodes
        document.querySelectorAll('.flow-hop-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const hopIdx = parseInt(btn.getAttribute('data-hop'));
                if (this.options.onFlowRemoveHop) {
                    this.options.onFlowRemoveHop(flowIndex, hopIdx);
                }
            });
        });

        // Hop labels
        document.querySelectorAll('.flow-hop-label-input').forEach(input => {
            input.addEventListener('change', () => {
                const nodeId = input.getAttribute('data-node');
                if (this.options.onFlowHopLabel) {
                    this.options.onFlowHopLabel(flowIndex, nodeId, input.value);
                }
            });
        });

        // Initial headers
        const headersInput = document.getElementById('prop-flow-headers');
        if (headersInput) {
            headersInput.addEventListener('change', () => {
                const headers = headersInput.value.split(',').map(h => h.trim()).filter(h => h);
                if (this.options.onFlowChange) {
                    this.options.onFlowChange(flowIndex, { initial_headers: headers });
                }
            });
        }

        // Encapsulation: add operation buttons
        document.querySelectorAll('.encap-add-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const nodeId = btn.getAttribute('data-node');
                if (this.options.onFlowEncapAdd) {
                    this.options.onFlowEncapAdd(flowIndex, nodeId);
                }
            });
        });

        // Encapsulation: action/header change
        document.querySelectorAll('.encap-action, .encap-header').forEach(el => {
            el.addEventListener('change', () => {
                const nodeId = el.getAttribute('data-node');
                const opIdx = parseInt(el.getAttribute('data-op'));
                if (this.options.onFlowEncapUpdate) {
                    const isAction = el.classList.contains('encap-action');
                    this.options.onFlowEncapUpdate(flowIndex, nodeId, opIdx,
                        isAction ? { action: el.value } : { header: el.value });
                }
            });
        });

        // Encapsulation: remove operation
        document.querySelectorAll('.encap-remove').forEach(btn => {
            btn.addEventListener('click', () => {
                const nodeId = btn.getAttribute('data-node');
                const opIdx = parseInt(btn.getAttribute('data-op'));
                if (this.options.onFlowEncapRemove) {
                    this.options.onFlowEncapRemove(flowIndex, nodeId, opIdx);
                }
            });
        });

        const deleteBtn = document.getElementById('btn-delete-flow');
        if (deleteBtn) {
            deleteBtn.addEventListener('click', () => {
                if (this.options.onFlowDelete) {
                    this.options.onFlowDelete(flowIndex);
                }
            });
        }
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

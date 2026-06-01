/**
 * Diagram Builder - Main Orchestrator
 * Initializes and coordinates all builder components
 */

import { BuilderCanvas } from './builder-canvas.js';
import { DevicePalette } from './device-palette.js';
import { PropertiesPanel } from './properties-panel.js';
import { ConnectionManager } from './connection-manager.js';
import { ZoneManager } from './zone-manager.js';
import { AnnotationManager } from './annotation-manager.js';
import { ExportManager } from './export-manager.js';
import { ImportManager } from './import-manager.js';
import { PreviewManager } from './preview-manager.js';
import { FlowEditor } from './flow-editor.js';

class DiagramBuilder {
    constructor() {
        this.canvas = null;
        this.palette = null;
        this.properties = null;
        this.connections = null;
        this.zones = null;
        this.annotations = null;
        this.exportMgr = null;
        this.importMgr = null;
        this.preview = null;
        this.flowEditor = null;

        // Undo/Redo history
        this.undoStack = [];
        this.redoStack = [];
        this.maxHistory = 50;

        // Current mode
        this.mode = 'select'; // select | connection | annotation | flow

        this.init();
    }

    init() {
        // Initialize canvas first (Cytoscape instance)
        this.canvas = new BuilderCanvas('cy-builder', {
            onSelect: (elements) => this.onSelectionChange(elements),
            onPositionChange: () => this.saveState(),
            onDrop: (type, position) => this.addDevice(type, position),
        });

        // Initialize zone manager (needs canvas)
        this.zones = new ZoneManager(this.canvas.cy, {
            onChange: () => {
                this.saveState();
                this.updateStatusBar();
            },
        });

        // Initialize annotation manager
        this.annotations = new AnnotationManager(
            this.canvas.cy,
            document.getElementById('annotation-overlay'),
            {
                onChange: () => this.saveState(),
                onSelect: (annotation) => this.properties.showAnnotation(annotation),
            }
        );

        // Initialize connection manager
        this.connections = new ConnectionManager(this.canvas.cy, {
            onConnectionCreated: (edge) => {
                this.saveState();
                this.updateStatusBar();
            },
            onModeChange: (active) => this.setMode(active ? 'connection' : 'select'),
        });

        // Initialize flow editor
        this.flowEditor = new FlowEditor(this.canvas.cy, {
            onChange: () => {
                this.saveState();
                this.refreshFlowList();
            },
            onSelect: (flow, index) => this.properties.showFlow(flow, index),
            onModeChange: (active) => {
                if (!active && this.mode === 'flow') {
                    this.setMode('select');
                    const flow = this.flowEditor.flows[this.flowEditor.currentFlowIndex];
                    if (flow && flow.path.length >= 2) {
                        this.showToast(`Path set: ${flow.path.join(' → ')}`, 'success');
                        this.properties.showFlow(flow, this.flowEditor.currentFlowIndex);
                    }
                }
            },
            onPathUpdate: (path) => {
                this.showBuildingPathStatus(path);
            },
        });

        // Initialize properties panel
        this.properties = new PropertiesPanel({
            onNodeChange: (id, changes) => this.updateNode(id, changes),
            onEdgeChange: (id, changes) => this.updateEdge(id, changes),
            onZoneChange: (id, changes) => this.zones.updateZone(id, changes),
            onAnnotationChange: (index, changes) => this.annotations.updateAnnotation(index, changes),
            onAnnotationDelete: (index) => {
                this.annotations.removeAnnotation(index);
                this.properties.clear();
                this.saveState();
            },
            onFlowChange: (index, changes) => {
                this.flowEditor.updateFlow(index, changes);
                this.refreshFlowList();
            },
            onFlowDelete: (index) => {
                this.flowEditor.removeFlow(index);
                this.properties.clear();
                this.refreshFlowList();
            },
            onFlowBuildPath: (index) => {
                this.flowEditor.selectFlow(index);
                this.flowEditor.startPathBuild();
                this.setMode('flow');
                this.showToast('Click nodes to build the flow path. Press Enter to finish, Escape to cancel.', 'info');
            },
            onFlowExtendPath: (index) => {
                this.flowEditor.selectFlow(index);
                this.flowEditor.startExtendPath();
                this.setMode('flow');
                this.showToast('Click nodes to extend the path. Press Enter to finish, Escape to cancel.', 'info');
            },
            onFlowRemoveHop: (flowIndex, hopIndex) => {
                this.flowEditor.removePathNode(flowIndex, hopIndex);
                const flow = this.flowEditor.flows[flowIndex];
                this.properties.showFlow(flow, flowIndex);
                this.refreshFlowList();
            },
            onFlowHopLabel: (flowIndex, nodeId, text) => {
                this.flowEditor.updateHopLabel(flowIndex, nodeId, text);
            },
            onFlowEncapAdd: (flowIndex, nodeId) => {
                const flow = this.flowEditor.flows[flowIndex];
                if (!flow.encapsulation) flow.encapsulation = {};
                if (!flow.encapsulation[nodeId]) flow.encapsulation[nodeId] = [];
                flow.encapsulation[nodeId].push({ action: 'push', header: '' });
                this.flowEditor.onChange();
                this.properties.showFlow(flow, flowIndex);
            },
            onFlowEncapUpdate: (flowIndex, nodeId, opIdx, changes) => {
                const flow = this.flowEditor.flows[flowIndex];
                if (!flow.encapsulation || !flow.encapsulation[nodeId]) return;
                const op = flow.encapsulation[nodeId][opIdx];
                if (!op) return;
                if (changes.action !== undefined) op.action = changes.action;
                if (changes.header !== undefined) op.header = changes.header;
                this.flowEditor.onChange();
            },
            onFlowEncapRemove: (flowIndex, nodeId, opIdx) => {
                const flow = this.flowEditor.flows[flowIndex];
                if (!flow.encapsulation || !flow.encapsulation[nodeId]) return;
                flow.encapsulation[nodeId].splice(opIdx, 1);
                if (flow.encapsulation[nodeId].length === 0) delete flow.encapsulation[nodeId];
                this.flowEditor.onChange();
                this.properties.showFlow(flow, flowIndex);
            },
            zones: this.zones,
        });

        // Initialize device palette
        this.palette = new DevicePalette('palette-body', 'palette-search', {
            onDragStart: (type, event) => this.canvas.startDrag(type, event),
        });

        // Initialize export manager
        this.exportMgr = new ExportManager({
            getState: () => this.getFullState(),
            cy: this.canvas.cy,
            annotations: this.annotations,
            zones: this.zones,
        });

        // Initialize import manager
        this.importMgr = new ImportManager({
            onImport: (data) => this.loadState(data),
            canvas: this.canvas,
            zones: this.zones,
            annotations: this.annotations,
        });

        // Initialize preview manager
        this.preview = new PreviewManager({
            getState: () => this.getFullState(),
        });

        this.bindToolbar();
        this.bindKeyboard();
        this.updateStatusBar();
    }

    bindToolbar() {
        document.getElementById('btn-add-connection').addEventListener('click', () => {
            this.toggleConnectionMode();
        });

        document.getElementById('btn-add-zone').addEventListener('click', () => {
            this.addZone();
        });

        document.getElementById('btn-add-annotation').addEventListener('click', () => {
            this.toggleAnnotationMode();
        });

        document.getElementById('btn-add-flow').addEventListener('click', () => {
            this.addNewFlow();
        });

        document.getElementById('btn-new-flow').addEventListener('click', () => {
            this.addNewFlow();
        });

        document.getElementById('btn-delete').addEventListener('click', () => {
            this.deleteSelected();
        });

        document.getElementById('btn-undo').addEventListener('click', () => {
            this.undo();
        });

        document.getElementById('btn-redo').addEventListener('click', () => {
            this.redo();
        });

        document.getElementById('btn-fit').addEventListener('click', () => {
            this.canvas.fit();
        });

        document.getElementById('select-layout').addEventListener('change', (e) => {
            this.canvas.runLayout(e.target.value);
        });

        document.getElementById('btn-preview').addEventListener('click', () => {
            this.preview.show();
        });

        // Export buttons
        document.getElementById('btn-copy-yaml').addEventListener('click', () => {
            this.exportMgr.copyYAML();
        });

        document.getElementById('btn-copy-rst').addEventListener('click', () => {
            this.exportMgr.copyRST();
        });

        document.getElementById('btn-download-yml').addEventListener('click', () => {
            this.exportMgr.downloadYML();
        });

        // Import buttons
        document.getElementById('btn-from-topology').addEventListener('click', () => {
            this.importMgr.showImportModal('topology');
        });

        document.getElementById('btn-from-api').addEventListener('click', () => {
            this.importMgr.importFromAPI();
        });

        document.getElementById('btn-import-yaml').addEventListener('click', () => {
            this.importMgr.showImportModal('yaml');
        });

        // Save/Load buttons
        document.getElementById('btn-save').addEventListener('click', () => {
            this.saveDiagram();
        });

        document.getElementById('btn-load').addEventListener('click', () => {
            this.loadDiagram();
        });

        document.getElementById('btn-delete-saved').addEventListener('click', () => {
            this.deleteDiagram();
        });

        // Refresh saved diagrams list on page load
        this.refreshDiagramList();

        // Settings
        document.getElementById('setting-title').addEventListener('change', () => this.saveState());
        document.getElementById('setting-height').addEventListener('change', () => this.saveState());

        // Section toggles
        document.querySelectorAll('.section-header[data-toggle]').forEach(header => {
            header.addEventListener('click', () => {
                const bodyId = header.getAttribute('data-toggle');
                const body = document.getElementById(bodyId);
                if (body) {
                    body.style.display = body.style.display === 'none' ? '' : 'none';
                }
            });
        });

        // Zoom tracking
        this.canvas.cy.on('zoom', () => {
            const zoom = Math.round(this.canvas.cy.zoom() * 100);
            document.getElementById('status-zoom').textContent = `Zoom: ${zoom}%`;
        });
    }

    bindKeyboard() {
        document.addEventListener('keydown', (e) => {
            // Don't handle shortcuts when typing in inputs
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
                return;
            }

            if (e.key === 'Delete' || e.key === 'Backspace') {
                this.deleteSelected();
                e.preventDefault();
            } else if (e.key === 'c' && !e.ctrlKey && !e.metaKey) {
                this.toggleConnectionMode();
            } else if (e.key === 'z' && !e.ctrlKey && !e.metaKey) {
                this.addZone();
            } else if (e.key === 'a' && !e.ctrlKey && !e.metaKey) {
                this.toggleAnnotationMode();
            } else if (e.key === 'w' && !e.ctrlKey && !e.metaKey) {
                this.addNewFlow();
            } else if (e.key === 'Enter' && this.mode === 'flow') {
                this.flowEditor.finishPathBuild();
                this.setMode('select');
            } else if (e.key === 'f') {
                this.canvas.fit();
            } else if (e.key === 'p') {
                this.preview.show();
            } else if (e.key === 'Escape') {
                if (this.mode === 'flow') {
                    this.flowEditor.cancelPathBuild();
                }
                this.setMode('select');
                this.canvas.cy.elements(':selected').unselect();
            } else if ((e.ctrlKey || e.metaKey) && e.key === 'z' && !e.shiftKey) {
                this.undo();
                e.preventDefault();
            } else if ((e.ctrlKey || e.metaKey) && (e.key === 'y' || (e.key === 'z' && e.shiftKey))) {
                this.redo();
                e.preventDefault();
            }
        });
    }

    // --- Mode Management ---

    setMode(mode) {
        this.mode = mode;
        const indicator = document.getElementById('mode-indicator');
        const canvasEl = document.getElementById('cy-builder');

        // Deactivate previous mode's handlers
        if (this.mode === 'connection' && mode !== 'connection') {
            this.connections.deactivate();
        }
        if (this.mode === 'annotation' && mode !== 'annotation') {
            this.annotations.deactivateCreateMode();
        }

        // Reset toolbar button states
        document.getElementById('btn-add-connection').classList.remove('active');
        document.getElementById('btn-add-annotation').classList.remove('active');
        document.getElementById('btn-add-flow').classList.remove('active');
        canvasEl.classList.remove('connection-mode', 'annotation-mode', 'flow-mode');

        switch (mode) {
            case 'connection':
                indicator.textContent = 'Connection';
                document.getElementById('btn-add-connection').classList.add('active');
                canvasEl.classList.add('connection-mode');
                break;
            case 'annotation':
                indicator.textContent = 'Annotation';
                document.getElementById('btn-add-annotation').classList.add('active');
                canvasEl.classList.add('annotation-mode');
                break;
            case 'flow':
                indicator.textContent = 'Flow Path';
                document.getElementById('btn-add-flow').classList.add('active');
                canvasEl.classList.add('flow-mode');
                break;
            default:
                indicator.textContent = 'Select';
        }
    }

    addNewFlow() {
        if (this.canvas.cy.nodes().filter(n => !n.data('isZone')).length < 2) {
            this.showToast('Add at least 2 devices before creating a flow', 'error');
            return;
        }
        // If already in flow path build mode, finish current build first
        if (this.mode === 'flow') {
            this.flowEditor.finishPathBuild();
        }
        this.flowEditor.addFlow();
        this.refreshFlowList();
        // Immediately enter path building mode
        this.flowEditor.startPathBuild();
        this.setMode('flow');
        this.showToast('Click nodes in order to define the flow path. Press Enter to finish, Escape to cancel.', 'info');
    }

    showBuildingPathStatus(path) {
        const titleEl = document.getElementById('properties-title');
        const contentEl = document.getElementById('properties-content');
        if (!titleEl || !contentEl) return;

        titleEl.textContent = 'Building Flow Path';
        const pathStr = path.length > 0 ? path.join(' → ') : '(click a node)';
        const countText = path.length + ' node' + (path.length !== 1 ? 's' : '');

        contentEl.textContent = '';
        const wrapper = document.createElement('div');
        wrapper.style.cssText = 'padding:12px 0;';

        const status = document.createElement('div');
        status.style.cssText = 'font-size:11px;color:#666;margin-bottom:8px;';
        status.textContent = countText + ' selected' + (path.length < 2 ? ' (need at least 2)' : '');
        wrapper.appendChild(status);

        const pathDisplay = document.createElement('div');
        pathDisplay.className = 'flow-path-display';
        pathDisplay.textContent = pathStr;
        wrapper.appendChild(pathDisplay);

        const hint = document.createElement('div');
        hint.style.cssText = 'font-size:11px;color:#999;margin-top:12px;';
        hint.textContent = 'Enter = finish path  |  Escape = cancel';
        wrapper.appendChild(hint);

        const finishBtn = document.createElement('button');
        finishBtn.className = 'import-btn import-btn-primary';
        finishBtn.textContent = 'Finish Path';
        finishBtn.style.marginTop = '8px';
        finishBtn.addEventListener('click', () => {
            this.flowEditor.finishPathBuild();
            this.setMode('select');
        });
        wrapper.appendChild(finishBtn);

        contentEl.appendChild(wrapper);
    }

    refreshFlowList() {
        const listEl = document.getElementById('flow-list');
        if (listEl) {
            this.flowEditor.renderFlowList(listEl);
        }
    }

    toggleConnectionMode() {
        if (this.mode === 'connection') {
            this.connections.deactivate();
            this.setMode('select');
        } else {
            this.connections.activate();
            this.setMode('connection');
        }
    }

    toggleAnnotationMode() {
        if (this.mode === 'annotation') {
            this.annotations.deactivateCreateMode();
            this.setMode('select');
        } else {
            this.annotations.activateCreateMode();
            this.setMode('annotation');
        }
    }

    // --- Device Operations ---

    addDevice(type, position) {
        const id = this.generateId(type);
        const label = this.generateLabel(type, id);

        this.canvas.cy.add({
            group: 'nodes',
            data: {
                id: id,
                label: label,
                device_type: type,
                ip: '',
            },
            position: position,
            classes: `device-type-${type}`,
        });

        this.saveState();
        this.updateStatusBar();
        this.showToast(`Added ${label}`, 'success');
    }

    generateId(type) {
        const existing = this.canvas.cy.nodes().map(n => n.id());
        let counter = 1;
        while (existing.includes(`${type}${counter}`)) {
            counter++;
        }
        return `${type}${counter}`;
    }

    generateLabel(type, id) {
        // Capitalize first letter and add space before number
        const match = id.match(/^([a-z_]+)(\d+)$/);
        if (match) {
            const name = match[1].charAt(0).toUpperCase() + match[1].slice(1);
            return `${name} ${match[2]}`;
        }
        return id;
    }

    updateNode(id, changes) {
        const node = this.canvas.cy.$id(id);
        if (node.empty()) return;

        if (changes.label !== undefined) node.data('label', changes.label);
        if (changes.ip !== undefined) node.data('ip', changes.ip);
        if (changes.device_type !== undefined) {
            node.data('device_type', changes.device_type);
            // Update CSS class
            node.classes(`device-type-${changes.device_type}`);
        }
        if (changes.zone !== undefined) {
            const oldParent = node.parent().length ? node.parent().id() : null;
            if (changes.zone) {
                if (oldParent && oldParent !== changes.zone) {
                    this.showToast(`Moved ${node.data('label') || node.id()} from ${oldParent} to ${changes.zone}`, 'info');
                }
                node.move({ parent: changes.zone });
            } else {
                node.move({ parent: null });
            }
        }
        if (changes.highlight !== undefined) {
            if (changes.highlight) {
                node.data('highlight', changes.highlight);
            } else {
                node.removeData('highlight');
            }
        }
        this.saveState();
    }

    updateEdge(id, changes) {
        const edge = this.canvas.cy.$id(id);
        if (edge.empty()) return;

        if (changes.source_port !== undefined) edge.data('source_port', changes.source_port);
        if (changes.target_port !== undefined) edge.data('target_port', changes.target_port);
        if (changes.label !== undefined) edge.data('label', changes.label);
        this.saveState();
    }

    deleteSelected() {
        const selected = this.canvas.cy.elements(':selected');
        if (selected.empty()) return;

        // Check if any selected nodes are zone parents
        selected.nodes().forEach(node => {
            if (node.isParent()) {
                // Move children out before deleting zone
                node.children().move({ parent: null });
            }
        });

        selected.remove();
        this.properties.clear();
        this.saveState();
        this.updateStatusBar();
    }

    addZone() {
        const selected = this.canvas.cy.nodes(':selected').filter(n => !n.isParent());

        // Warn if any selected nodes are already in a zone
        const inZone = selected.filter(n => n.parent().length > 0);
        if (inZone.length > 0) {
            const names = inZone.map(n => n.data('label') || n.id()).join(', ');
            if (!confirm(`${names} will be moved out of their current zone. Continue?`)) {
                return;
            }
        }

        this.zones.createZone(selected);
        this.saveState();
        this.updateStatusBar();
    }

    // --- Selection ---

    onSelectionChange(elements) {
        // Don't let selection override the properties panel during flow path building
        if (this.mode === 'flow') return;

        if (elements.length === 0) {
            this.properties.clear();
            return;
        }

        const el = elements[0];
        if (el.isNode()) {
            if (el.data('isZone')) {
                this.properties.showZone(el);
            } else {
                this.properties.showNode(el);
            }
        } else if (el.isEdge()) {
            this.properties.showEdge(el);
        }
    }

    // --- State Management ---

    getFullState() {
        const settings = {
            title: document.getElementById('setting-title').value || '',
            height: parseInt(document.getElementById('setting-height').value) || 500,
            layout: document.getElementById('select-layout').value,
            live_status: document.getElementById('setting-live-status').checked,
            device_access: document.getElementById('setting-device-access').checked,
            show_port_labels: document.getElementById('setting-port-labels').checked,
        };

        // Collect nodes (exclude zone parents)
        const nodes = [];
        this.canvas.cy.nodes().forEach(node => {
            if (node.data('isZone')) return;
            const pos = node.position();
            const nodeData = {
                id: node.id(),
                label: node.data('label') || node.id(),
                type: node.data('device_type') || 'other',
                position: { x: Math.round(pos.x), y: Math.round(pos.y) },
            };
            if (node.data('ip')) nodeData.ip = node.data('ip');
            if (node.data('highlight')) nodeData.highlight = node.data('highlight');
            if (node.parent().length) nodeData.zone = node.parent().id();
            nodes.push(nodeData);
        });

        // Collect edges
        const edges = [];
        this.canvas.cy.edges().forEach(edge => {
            const edgeData = {
                source: edge.source().id(),
                target: edge.target().id(),
            };
            if (edge.data('source_port')) edgeData.source_port = edge.data('source_port');
            if (edge.data('target_port')) edgeData.target_port = edge.data('target_port');
            if (edge.data('label')) edgeData.label = edge.data('label');
            edges.push(edgeData);
        });

        // Collect zones
        const zones = this.zones.getZones();

        // Collect annotations
        const annotationsList = this.annotations.getAnnotations();

        // Collect flows
        const flows = this.flowEditor.getFlows();

        return { settings, nodes, edges, zones, annotations: annotationsList, flows };
    }

    loadState(data) {
        // Clear current state
        this.canvas.cy.elements().remove();
        this.annotations.clear();

        // Apply settings
        if (data.settings) {
            if (data.settings.title) document.getElementById('setting-title').value = data.settings.title;
            if (data.settings.height) document.getElementById('setting-height').value = data.settings.height;
            if (data.settings.layout) document.getElementById('select-layout').value = data.settings.layout;
            if (data.settings.live_status !== undefined) document.getElementById('setting-live-status').checked = data.settings.live_status;
            if (data.settings.device_access !== undefined) document.getElementById('setting-device-access').checked = data.settings.device_access;
            if (data.settings.show_port_labels !== undefined) document.getElementById('setting-port-labels').checked = data.settings.show_port_labels;
        }

        // Add zones first (parent nodes)
        if (data.zones) {
            data.zones.forEach(zone => this.zones.addZone(zone));
        }

        // Add nodes
        if (data.nodes) {
            data.nodes.forEach(node => {
                const nodeData = {
                    id: node.id,
                    label: node.label || node.id,
                    device_type: node.type || 'other',
                    ip: node.ip || '',
                };
                if (node.highlight) nodeData.highlight = node.highlight;
                if (node.zone) nodeData.parent = node.zone;

                const elem = {
                    group: 'nodes',
                    data: nodeData,
                    classes: `device-type-${node.type || 'other'}`,
                };
                if (node.position) {
                    elem.position = { x: node.position.x, y: node.position.y };
                }
                this.canvas.cy.add(elem);
            });
        }

        // Add edges (use port info in ID to handle parallel links)
        if (data.edges) {
            data.edges.forEach((edge, i) => {
                const sp = edge.source_port || i;
                const tp = edge.target_port || i;
                this.canvas.cy.add({
                    group: 'edges',
                    data: {
                        id: `${edge.source}|${edge.target}:${sp}-${tp}`,
                        source: edge.source,
                        target: edge.target,
                        source_port: edge.source_port || '',
                        target_port: edge.target_port || '',
                        label: edge.label || '',
                    },
                });
            });
        }

        // Add annotations
        if (data.annotations) {
            data.annotations.forEach(ann => {
                this.annotations.createAnnotation(ann.text, ann.position, {
                    color: ann.color,
                    fontSize: ann.font_size,
                    background: ann.background !== false,
                });
            });
        }

        // Load flows
        if (data.flows) {
            this.flowEditor.setFlows(data.flows);
            this.refreshFlowList();
        }

        // Reapply zone layer z-index after all elements are loaded
        this.zones.reapplyAllLayers();

        // Run layout if not preset
        const layout = data.settings?.layout || 'dagre';
        if (layout !== 'preset') {
            this.canvas.runLayout(layout);
        } else {
            this.canvas.cy.fit(undefined, 30);
        }

        this.updateStatusBar();
        this.saveState();
        this.showToast('Topology imported', 'success');
    }

    // --- Undo/Redo ---

    saveState() {
        const state = this.canvas.cy.json().elements;
        const annotations = this.annotations.getAnnotations();
        this.undoStack.push({ elements: state, annotations });
        if (this.undoStack.length > this.maxHistory) {
            this.undoStack.shift();
        }
        this.redoStack = [];
    }

    undo() {
        if (this.undoStack.length <= 1) return;
        const current = this.undoStack.pop();
        this.redoStack.push(current);
        const prev = this.undoStack[this.undoStack.length - 1];
        this.restoreState(prev);
    }

    redo() {
        if (this.redoStack.length === 0) return;
        const next = this.redoStack.pop();
        this.undoStack.push(next);
        this.restoreState(next);
    }

    restoreState(state) {
        this.canvas.cy.json({ elements: state.elements });
        this.annotations.clear();
        if (state.annotations) {
            state.annotations.forEach(ann => {
                this.annotations.createAnnotation(ann.text, ann.position, {
                    color: ann.color,
                    fontSize: ann.font_size,
                    background: ann.background,
                });
            });
        }
        this.updateStatusBar();
    }

    // --- Save / Load / Delete ---

    async saveDiagram() {
        const nameInput = document.getElementById('save-name');
        const name = nameInput.value.trim();
        if (!name) {
            this.showToast('Enter a diagram name', 'error');
            nameInput.focus();
            return;
        }
        const yaml = this.exportMgr.toYAML();
        try {
            const resp = await fetch('/td-api/diagrams', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name, content: yaml }),
            });
            const result = await resp.json();
            if (resp.ok) {
                this.showToast(`Saved "${result.saved}"`, 'success');
                this.refreshDiagramList();
            } else {
                this.showToast(result.error || 'Save failed', 'error');
            }
        } catch (err) {
            this.showToast('Save failed: ' + err.message, 'error');
        }
    }

    async loadDiagram() {
        const select = document.getElementById('load-select');
        const name = select.value;
        if (!name) {
            this.showToast('Select a diagram to load', 'error');
            return;
        }
        try {
            const resp = await fetch('/td-api/diagrams?name=' + encodeURIComponent(name));
            const result = await resp.json();
            if (resp.ok) {
                const data = this.importMgr.parseDiagramData(result.content);
                this.loadState(data);
                document.getElementById('save-name').value = name;
            } else {
                this.showToast(result.error || 'Load failed', 'error');
            }
        } catch (err) {
            this.showToast('Load failed: ' + err.message, 'error');
        }
    }

    async deleteDiagram() {
        const select = document.getElementById('load-select');
        const name = select.value;
        if (!name) {
            this.showToast('Select a diagram to delete', 'error');
            return;
        }
        if (!confirm(`Delete diagram "${name}"?`)) return;
        try {
            const resp = await fetch('/td-api/diagrams?name=' + encodeURIComponent(name), {
                method: 'DELETE',
            });
            if (resp.ok) {
                this.showToast(`Deleted "${name}"`, 'success');
                this.refreshDiagramList();
            } else {
                const result = await resp.json();
                this.showToast(result.error || 'Delete failed', 'error');
            }
        } catch (err) {
            this.showToast('Delete failed: ' + err.message, 'error');
        }
    }

    async refreshDiagramList() {
        const select = document.getElementById('load-select');
        try {
            const resp = await fetch('/td-api/diagrams');
            const result = await resp.json();
            const current = select.value;
            select.innerHTML = '<option value="">-- Select --</option>';
            (result.diagrams || []).forEach(name => {
                const opt = document.createElement('option');
                opt.value = name;
                opt.textContent = name;
                if (name === current) opt.selected = true;
                select.appendChild(opt);
            });
        } catch {
            // Silent - save/load might not be available
        }
    }

    // --- UI Helpers ---

    updateStatusBar() {
        const nodes = this.canvas.cy.nodes().filter(n => !n.data('isZone'));
        const edges = this.canvas.cy.edges();
        const zones = this.canvas.cy.nodes().filter(n => n.data('isZone'));

        document.getElementById('status-nodes').textContent = `Nodes: ${nodes.length}`;
        document.getElementById('status-edges').textContent = `Edges: ${edges.length}`;
        document.getElementById('status-zones').textContent = `Zones: ${zones.length}`;
    }

    showToast(message, type = 'info') {
        const toast = document.getElementById('builder-toast');
        toast.textContent = message;
        toast.className = `builder-toast toast-${type}`;
        toast.style.display = 'block';
        setTimeout(() => {
            toast.style.display = 'none';
        }, 2500);
    }
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
    window.diagramBuilder = new DiagramBuilder();
});

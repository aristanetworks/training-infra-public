/**
 * Flow Editor - Packet flow definition and editing
 * Allows authors to define animated traffic paths through the topology
 */

export class FlowEditor {
    constructor(cy, options = {}) {
        this.cy = cy;
        this.options = options;
        this.flows = [];
        this.currentFlowIndex = -1;
        this.pathBuildMode = false;
        this.currentPath = [];
        this.flowCounter = 0;

        this.boundNodeClick = (e) => this.handleNodeClick(e);
    }

    addFlow() {
        this.flowCounter++;
        const flow = {
            name: `Flow ${this.flowCounter}`,
            path: [],
            color: this.getDefaultColor(this.flows.length),
            packet_size: 12,
            speed: 1.0,
            description: '',
            labels: {},
            initial_headers: ['Packet'],
            encapsulation: {},
        };
        this.flows.push(flow);
        this.selectFlow(this.flows.length - 1);
        this.onChange();
        return flow;
    }

    removeFlow(index) {
        if (index < 0 || index >= this.flows.length) return;
        this.flows.splice(index, 1);
        this.clearHighlight();
        if (this.currentFlowIndex >= this.flows.length) {
            this.currentFlowIndex = this.flows.length - 1;
        }
        if (this.currentFlowIndex >= 0) {
            this.highlightFlowPath(this.flows[this.currentFlowIndex]);
        }
        this.onChange();
    }

    selectFlow(index) {
        this.currentFlowIndex = index;
        this.clearHighlight();
        if (index >= 0 && index < this.flows.length) {
            this.highlightFlowPath(this.flows[index]);
            if (this.options.onSelect) {
                this.options.onSelect(this.flows[index], index);
            }
        }
    }

    updateFlow(index, changes) {
        if (index < 0 || index >= this.flows.length) return;
        const flow = this.flows[index];
        if (changes.name !== undefined) flow.name = changes.name;
        if (changes.color !== undefined) flow.color = changes.color;
        if (changes.speed !== undefined) flow.speed = parseFloat(changes.speed);
        if (changes.packet_size !== undefined) flow.packet_size = parseInt(changes.packet_size);
        if (changes.description !== undefined) flow.description = changes.description;
        if (changes.labels !== undefined) flow.labels = changes.labels;
        if (changes.initial_headers !== undefined) flow.initial_headers = changes.initial_headers;
        this.onChange();
    }

    updateHopLabel(flowIndex, nodeId, text) {
        if (flowIndex < 0 || flowIndex >= this.flows.length) return;
        const flow = this.flows[flowIndex];
        if (!flow.labels) flow.labels = {};
        if (text) {
            flow.labels[nodeId] = text;
        } else {
            delete flow.labels[nodeId];
        }
        this.onChange();
    }

    // --- Path Editing ---

    removePathNode(flowIndex, hopIndex) {
        if (flowIndex < 0 || flowIndex >= this.flows.length) return;
        const flow = this.flows[flowIndex];
        if (hopIndex < 0 || hopIndex >= flow.path.length) return;
        const removedId = flow.path[hopIndex];
        flow.path.splice(hopIndex, 1);
        if (flow.labels && flow.labels[removedId]) {
            // Only remove label if node no longer appears in path
            if (!flow.path.includes(removedId)) {
                delete flow.labels[removedId];
            }
        }
        this.clearHighlight();
        this.highlightFlowPath(flow);
        this.onChange();
    }

    insertPathNode(flowIndex, hopIndex, nodeId) {
        if (flowIndex < 0 || flowIndex >= this.flows.length) return;
        const flow = this.flows[flowIndex];
        flow.path.splice(hopIndex, 0, nodeId);
        this.clearHighlight();
        this.highlightFlowPath(flow);
        this.onChange();
    }

    startExtendPath() {
        if (this.currentFlowIndex < 0) return;
        const flow = this.flows[this.currentFlowIndex];
        this.pathBuildMode = true;
        this.currentPath = [...flow.path];
        this.clearHighlight();
        this.highlightFlowPath(flow);
        this.cy.nodes().unselectify();
        this.cy.on('tap', 'node', this.boundNodeClick);
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) node.addClass('hover');
        });
        if (this.options.onPathUpdate) {
            this.options.onPathUpdate([...this.currentPath]);
        }
        if (this.options.onModeChange) {
            this.options.onModeChange(true);
        }
    }

    // --- Path Building Mode (from scratch) ---

    startPathBuild() {
        if (this.currentFlowIndex < 0) return;
        this.pathBuildMode = true;
        this.currentPath = [];
        this.clearHighlight();
        // Disable default selection behavior during path building
        this.cy.nodes().unselectify();
        this.cy.on('tap', 'node', this.boundNodeClick);
        this.cy.nodes().forEach(node => {
            if (!node.data('isZone')) node.addClass('hover');
        });
        // Show initial empty path status
        if (this.options.onPathUpdate) {
            this.options.onPathUpdate([]);
        }
        if (this.options.onModeChange) {
            this.options.onModeChange(true);
        }
    }

    finishPathBuild() {
        this.pathBuildMode = false;
        this.cy.off('tap', 'node', this.boundNodeClick);
        this.cy.nodes().removeClass('hover');
        this.cy.nodes().selectify();

        if (this.currentFlowIndex >= 0 && this.currentPath.length >= 2) {
            this.flows[this.currentFlowIndex].path = [...this.currentPath];
            const flow = this.flows[this.currentFlowIndex];
            const pathSet = new Set(flow.path);
            for (const key of Object.keys(flow.labels || {})) {
                if (!pathSet.has(key)) delete flow.labels[key];
            }
        }
        this.currentPath = [];
        this.clearHighlight();
        if (this.currentFlowIndex >= 0) {
            this.highlightFlowPath(this.flows[this.currentFlowIndex]);
        }
        this.onChange();
        if (this.options.onModeChange) {
            this.options.onModeChange(false);
        }
    }

    cancelPathBuild() {
        this.pathBuildMode = false;
        this.cy.off('tap', 'node', this.boundNodeClick);
        this.cy.nodes().removeClass('hover');
        this.cy.nodes().selectify();
        this.currentPath = [];
        this.clearHighlight();
        if (this.currentFlowIndex >= 0) {
            this.highlightFlowPath(this.flows[this.currentFlowIndex]);
        }
        this.onChange();
        if (this.options.onModeChange) {
            this.options.onModeChange(false);
        }
    }

    handleNodeClick(e) {
        const node = e.target;
        if (node.data('isZone')) return;
        const nodeId = node.id();

        this.currentPath.push(nodeId);
        node.addClass('flow-path-node');

        if (this.currentPath.length >= 2) {
            const prevId = this.currentPath[this.currentPath.length - 2];
            const edge = this.cy.edges().filter(e => {
                const s = e.source().id(), t = e.target().id();
                return (s === prevId && t === nodeId) || (s === nodeId && t === prevId);
            });
            if (edge.length > 0) edge[0].addClass('flow-active');
        }

        if (this.options.onPathUpdate) {
            this.options.onPathUpdate([...this.currentPath]);
        }
    }

    // --- Visual Highlighting ---

    highlightFlowPath(flow) {
        if (!flow || !flow.path) return;
        const cy = this.cy;
        flow.path.forEach(nodeId => {
            const node = cy.$id(nodeId);
            if (!node.empty()) node.addClass('flow-path-node');
        });
        for (let i = 0; i < flow.path.length - 1; i++) {
            const srcId = flow.path[i];
            const tgtId = flow.path[i + 1];
            const edge = cy.edges().filter(e => {
                const s = e.source().id(), t = e.target().id();
                return (s === srcId && t === tgtId) || (s === tgtId && t === srcId);
            });
            if (edge.length > 0) edge[0].addClass('flow-active');
        }
    }

    clearHighlight() {
        this.cy.nodes().removeClass('flow-path-node');
        this.cy.edges().removeClass('flow-active');
    }

    // --- Serialization ---

    getFlows() {
        return this.flows.map(f => ({
            name: f.name,
            path: [...f.path],
            color: f.color,
            packet_size: f.packet_size,
            speed: f.speed,
            description: f.description || '',
            labels: { ...(f.labels || {}) },
            initial_headers: [...(f.initial_headers || ['Packet'])],
            encapsulation: JSON.parse(JSON.stringify(f.encapsulation || {})),
        })).filter(f => f.path.length >= 2);
    }

    setFlows(flowsData) {
        this.flows = (flowsData || []).map(f => ({
            name: f.name || '',
            path: [...(f.path || [])],
            color: f.color || '#4c5cae',
            packet_size: f.packet_size || 12,
            speed: f.speed || 1.0,
            description: f.description || '',
            labels: { ...(f.labels || {}) },
            initial_headers: [...(f.initial_headers || ['Packet'])],
            encapsulation: JSON.parse(JSON.stringify(f.encapsulation || {})),
        }));
        this.flowCounter = this.flows.length;
        this.currentFlowIndex = -1;
        this.clearHighlight();
    }

    // --- Rendering ---

    renderFlowList(containerEl) {
        containerEl.textContent = '';
        this.flows.forEach((flow, i) => {
            const item = document.createElement('div');
            item.className = 'flow-list-item' + (i === this.currentFlowIndex ? ' selected' : '');

            const dot = document.createElement('span');
            dot.className = 'flow-color-dot';
            dot.style.background = flow.color || '#4c5cae';
            item.appendChild(dot);

            const name = document.createElement('span');
            name.className = 'flow-name';
            name.textContent = flow.name;
            item.appendChild(name);

            const count = document.createElement('span');
            count.className = 'flow-node-count';
            count.textContent = flow.path.length + ' nodes';
            item.appendChild(count);

            item.addEventListener('click', () => {
                this.selectFlow(i);
            });
            containerEl.appendChild(item);
        });
    }

    // --- Helpers ---

    getDefaultColor(index) {
        const colors = ['#4c5cae', '#78d82c', '#e30909', '#fbb500', '#9b59b6', '#ff8c00'];
        return colors[index % colors.length];
    }

    onChange() {
        if (this.options.onChange) {
            this.options.onChange();
        }
    }
}

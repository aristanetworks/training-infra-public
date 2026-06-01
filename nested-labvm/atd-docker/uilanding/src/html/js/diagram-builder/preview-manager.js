/**
 * Preview Manager - Preview topology as it would render in a lab guide
 * Shows a read-only Cytoscape viewer in a modal dialog
 */

import { getCytoscapeStyles } from '../topology/cytoscape-styles.js';
import { getLayout } from '../topology/layout-config.js';

export class PreviewManager {
    constructor(options = {}) {
        this.getState = options.getState;
        this.previewCy = null;

        this.bindEvents();
    }

    bindEvents() {
        document.getElementById('preview-close').addEventListener('click', () => this.hide());
        document.getElementById('preview-modal').addEventListener('click', (e) => {
            if (e.target.id === 'preview-modal') this.hide();
        });
    }

    show() {
        const modal = document.getElementById('preview-modal');
        const container = document.getElementById('preview-container');
        modal.style.display = '';

        // Destroy previous preview
        if (this.previewCy) {
            this.previewCy.destroy();
            this.previewCy = null;
        }

        const state = this.getState();
        const elements = this.buildElements(state);

        const styles = getCytoscapeStyles();

        // Add compound node (zone) styles
        styles.push({
            selector: ':parent',
            style: {
                'background-color': 'data(zoneBackground)',
                'background-opacity': 0.3,
                'border-width': 2,
                'border-color': 'data(zoneColor)',
                'border-style': 'data(zoneBorderStyle)',
                'label': 'data(label)',
                'text-valign': 'top',
                'text-halign': 'left',
                'text-margin-x': 10,
                'text-margin-y': 10,
                'font-size': 14,
                'font-weight': 600,
                'color': 'data(zoneColor)',
                'padding': 20,
                'shape': 'roundrectangle',
                'corner-radius': 8,
                'text-outline-width': 0,
            }
        });

        // Create a sub-container for Cytoscape so the flow controls bar
        // sits below it instead of overlapping (prevents mouse event conflicts)
        const hasFlows = state.flows && state.flows.length > 0;
        container.textContent = '';
        container.style.display = 'flex';
        container.style.flexDirection = 'column';

        const cyContainer = document.createElement('div');
        cyContainer.style.cssText = 'flex:1;position:relative;min-height:0;';
        container.appendChild(cyContainer);

        // Delay initialization to let the modal container render with dimensions
        requestAnimationFrame(() => {
            this.previewCy = cytoscape({
                container: cyContainer,
                style: styles,
                elements: elements,
                layout: { name: 'preset' },
                userPanningEnabled: true,
                userZoomingEnabled: true,
                boxSelectionEnabled: false,
                selectionType: 'single',
                minZoom: 0.2,
                maxZoom: 3,
            });

            // Run layout after cytoscape has measured the container
            const layoutName = state.settings?.layout || 'dagre';
            if (layoutName !== 'preset') {
                const layoutConfig = getLayout(layoutName);
                // Disable animation for preview - ensures layout completes before fit
                layoutConfig.animate = false;
                this.previewCy.layout(layoutConfig).run();
            }
            this.previewCy.fit(undefined, 30);
            this.renderAnnotations(cyContainer, state.annotations || []);
            this.renderFlowPaths(state.flows || [], container, cyContainer);
        });
    }

    hide() {
        if (this._flowCleanup) {
            this._flowCleanup();
            this._flowCleanup = null;
        }
        const previewEl = document.getElementById('preview-container');
        const annOverlay = previewEl.querySelector('.preview-annotation-overlay');
        if (annOverlay) annOverlay.remove();
        document.getElementById('preview-modal').style.display = 'none';
        if (this.previewCy) {
            this.previewCy.destroy();
            this.previewCy = null;
        }
    }

    renderAnnotations(container, annotations) {
        const existing = container.querySelector('.preview-annotation-overlay');
        if (existing) existing.remove();
        if (!annotations || annotations.length === 0) return;

        const overlay = document.createElement('div');
        overlay.className = 'preview-annotation-overlay';
        overlay.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:10;overflow:hidden;';
        container.style.position = 'relative';
        container.appendChild(overlay);

        const cy = this.previewCy;
        const annElements = annotations.map(ann => {
            const el = document.createElement('div');
            el.className = 'atl-annotation';
            if (ann.background !== false) el.classList.add('has-background');
            el.textContent = ann.text || '';
            el.style.color = ann.color || '#4c5cae';
            el.style.fontSize = (ann.font_size || 12) + 'px';
            el.style.position = 'absolute';
            el.style.fontFamily = '"proxima-nova", sans-serif';
            el.style.fontWeight = '600';
            el.style.transformOrigin = 'left top';
            overlay.appendChild(el);
            return { el, pos: ann.position || { x: 0, y: 0 } };
        });

        const updatePositions = () => {
            const pan = cy.pan();
            const zoom = cy.zoom();
            annElements.forEach(({ el, pos }) => {
                el.style.left = (pos.x * zoom + pan.x) + 'px';
                el.style.top = (pos.y * zoom + pan.y) + 'px';
                el.style.transform = `scale(${Math.min(zoom, 1.5)})`;
            });
        };

        cy.on('pan zoom', updatePositions);
        setTimeout(updatePositions, 100);
    }

    renderFlowPaths(flows, outerContainer, cyContainer) {
        if (!flows || flows.length === 0 || !this.previewCy) return;
        const cy = this.previewCy;
        const container = cyContainer;

        // Clean up previous
        const existing = container.querySelector('.preview-flow-controls');
        if (existing) existing.remove();
        const existingDot = container.querySelector('.preview-flow-dot');
        if (existingDot) existingDot.remove();

        // State
        let currentFlowIdx = 0;
        let currentHop = 0;
        let isPlaying = false;
        let speed = 1.0;
        let animId = null;
        let headerStack = [];
        const baseDuration = 1500;

        // Packet dot
        const dot = document.createElement('div');
        dot.className = 'preview-flow-dot';
        dot.style.cssText = 'position:absolute;border-radius:50%;pointer-events:none;z-index:40;'
            + 'transform:translate(-50%,-50%);display:none;';
        container.appendChild(dot);

        // Header stack badge
        const stackEl = document.createElement('div');
        stackEl.className = 'atl-flow-header-stack';
        stackEl.style.display = 'none';
        container.appendChild(stackEl);

        const updateDotStyle = (flow) => {
            const size = flow.packet_size || 12;
            const color = flow.color || '#4c5cae';
            dot.style.width = size + 'px';
            dot.style.height = size + 'px';
            dot.style.backgroundColor = color;
            dot.style.boxShadow = '0 0 ' + (size / 2) + 'px ' + color;
        };

        // Controls bar
        const bar = document.createElement('div');
        bar.className = 'preview-flow-controls';
        bar.style.cssText = 'display:flex;align-items:center;flex-shrink:0;'
            + 'gap:8px;padding:6px 12px;background:rgba(7,28,53,0.92);color:#fff;'
            + 'font-family:"proxima-nova",sans-serif;font-size:12px;'
            + 'border-top:1px solid rgba(251,181,0,0.25);';

        // Flow selector
        const select = document.createElement('select');
        select.style.cssText = 'padding:4px 8px;border:1px solid rgba(255,255,255,0.3);border-radius:4px;'
            + 'background:rgba(255,255,255,0.1);color:#fff;font:inherit;font-size:12px;max-width:180px;';
        flows.forEach((f, i) => {
            const opt = document.createElement('option');
            opt.value = i;
            opt.textContent = f.name || ('Flow ' + (i + 1));
            opt.style.cssText = 'background:#071c35;color:#fff;';
            select.appendChild(opt);
        });

        // Buttons
        const makeBtn = (text, title) => {
            const btn = document.createElement('button');
            btn.textContent = text;
            btn.title = title;
            btn.style.cssText = 'padding:4px 10px;border:1px solid rgba(255,255,255,0.3);border-radius:4px;'
                + 'background:transparent;color:#fff;font:inherit;font-size:12px;cursor:pointer;';
            return btn;
        };

        const playBtn = makeBtn('▶', 'Play / Pause');
        const stepBtn = makeBtn('⏭', 'Step');
        const resetBtn = makeBtn('↺', 'Reset');

        // Speed
        const speedSlider = document.createElement('input');
        speedSlider.type = 'range';
        speedSlider.min = '0.25';
        speedSlider.max = '3';
        speedSlider.step = '0.25';
        speedSlider.value = '1';
        speedSlider.style.cssText = 'width:70px;accent-color:#fbb500;';

        const speedLabel = document.createElement('span');
        speedLabel.style.cssText = 'font-size:11px;color:rgba(255,255,255,0.7);min-width:25px;';
        speedLabel.textContent = '1x';

        // Hop indicator
        const hopText = document.createElement('span');
        hopText.style.cssText = 'color:#fbb500;font-weight:600;min-width:60px;';
        hopText.textContent = 'Ready';

        bar.appendChild(select);
        bar.appendChild(playBtn);
        bar.appendChild(stepBtn);
        bar.appendChild(resetBtn);
        bar.appendChild(speedSlider);
        bar.appendChild(speedLabel);
        bar.appendChild(hopText);
        outerContainer.appendChild(bar);

        // --- Header stack helpers ---
        const initStack = (flow) => {
            headerStack = (flow.initial_headers && flow.initial_headers.length > 0)
                ? [...flow.initial_headers] : ['Packet'];
            renderStack();
        };

        const renderStack = () => {
            stackEl.textContent = '';
            const initialCount = (flows[currentFlowIdx].initial_headers || ['Packet']).length;
            headerStack.forEach((h, i) => {
                const pill = document.createElement('div');
                pill.className = 'atl-flow-header';
                pill.textContent = h;
                pill.classList.add(i >= headerStack.length - initialCount ? 'header-original' : 'header-encap');
                stackEl.appendChild(pill);
            });
        };

        const applyEncap = (nodeId) => {
            const flow = flows[currentFlowIdx];
            if (!flow.encapsulation || !flow.encapsulation[nodeId]) return false;
            const ops = flow.encapsulation[nodeId];
            let delay = 0;
            ops.forEach(op => {
                setTimeout(() => {
                    if (op.action === 'push') headerStack.unshift(op.header);
                    else if (op.action === 'pop' && headerStack.length > 0) headerStack.shift();
                    renderStack();
                    if (op.action === 'push' && stackEl.firstChild) {
                        stackEl.firstChild.classList.add('pushing');
                    }
                }, delay);
                delay += 150;
            });
            return true;
        };

        const updateStackPos = (x, y) => {
            stackEl.style.left = x + 'px';
            stackEl.style.top = (y - 20 - (headerStack.length * 16)) + 'px';
        };

        // --- Highlight helpers ---
        const highlightPath = (flow) => {
            cy.nodes().removeClass('flow-path-node flow-pulse');
            cy.edges().removeClass('flow-active');
            if (!flow.path) return;
            flow.path.forEach(id => {
                const n = cy.$id(id);
                if (!n.empty()) n.addClass('flow-path-node');
            });
        };

        const highlightEdge = (srcId, tgtId) => {
            cy.edges().removeClass('flow-active');
            const edge = cy.edges().filter(e => {
                const s = e.source().id(), t = e.target().id();
                return (s === srcId && t === tgtId) || (s === tgtId && t === srcId);
            });
            if (edge.length > 0) edge[0].addClass('flow-active');
        };

        // --- Animation ---
        const animateHop = () => {
            const flow = flows[currentFlowIdx];
            const path = flow.path;
            if (currentHop >= path.length - 1) {
                if (isPlaying) {
                    currentHop = 0;
                    initStack(flow);
                    highlightPath(flow);
                    setTimeout(() => animateHop(), 400 / speed);
                } else {
                    hopText.textContent = 'Complete';
                }
                return;
            }

            const srcId = path[currentHop];
            const tgtId = path[currentHop + 1];
            const srcNode = cy.$id(srcId);
            const tgtNode = cy.$id(tgtId);
            if (srcNode.empty() || tgtNode.empty()) {
                currentHop++;
                if (isPlaying) animateHop();
                return;
            }

            hopText.textContent = 'Hop ' + (currentHop + 1) + '/' + (path.length - 1) + ': ' + srcId + ' → ' + tgtId;
            highlightEdge(srcId, tgtId);

            const fromPos = srcNode.renderedPosition();
            const toPos = tgtNode.renderedPosition();
            dot.style.display = 'block';
            stackEl.style.display = '';

            let startTime = null;
            const duration = baseDuration / speed;

            const frame = (ts) => {
                if (!startTime) startTime = ts;
                const progress = Math.min((ts - startTime) / duration, 1);
                const x = fromPos.x + (toPos.x - fromPos.x) * progress;
                const y = fromPos.y + (toPos.y - fromPos.y) * progress;
                dot.style.left = x + 'px';
                dot.style.top = y + 'px';
                updateStackPos(x, y);

                if (progress < 1) {
                    animId = requestAnimationFrame(frame);
                } else {
                    // Pulse on arrival
                    tgtNode.addClass('flow-pulse');
                    setTimeout(() => tgtNode.removeClass('flow-pulse'), 400);

                    // Apply encapsulation at this node
                    const hasEncap = applyEncap(tgtId);

                    // Show hop label if defined
                    const labels = flow.labels || {};
                    if (labels[tgtId]) {
                        const lbl = document.createElement('div');
                        lbl.style.cssText = 'position:absolute;padding:2px 8px;border-radius:4px;'
                            + 'background:rgba(7,28,53,0.85);color:#fbb500;font-size:11px;font-weight:600;'
                            + 'pointer-events:none;z-index:45;white-space:nowrap;transform:translateX(-50%);'
                            + 'font-family:"proxima-nova",sans-serif;';
                        lbl.textContent = labels[tgtId];
                        const tgtPos = tgtNode.renderedPosition();
                        lbl.style.left = tgtPos.x + 'px';
                        lbl.style.top = (tgtPos.y - 40) + 'px';
                        container.appendChild(lbl);
                        setTimeout(() => { if (lbl.parentNode) lbl.remove(); }, 2000);
                    }

                    currentHop++;
                    if (currentHop >= path.length - 1) {
                        hopText.textContent = 'Complete';
                    }
                    const dwellTime = hasEncap ? 600 / speed : 200 / speed;
                    setTimeout(() => {
                        if (isPlaying) animateHop();
                    }, dwellTime);
                }
            };
            animId = requestAnimationFrame(frame);
        };

        const play = () => {
            isPlaying = true;
            playBtn.textContent = '⏸';
            playBtn.style.background = 'rgba(251,181,0,0.3)';
            playBtn.style.borderColor = '#fbb500';
            playBtn.style.color = '#fbb500';
            const flow = flows[currentFlowIdx];
            if (currentHop >= flow.path.length - 1) currentHop = 0;
            animateHop();
        };

        const pause = () => {
            isPlaying = false;
            playBtn.textContent = '▶';
            playBtn.style.background = 'transparent';
            playBtn.style.borderColor = 'rgba(255,255,255,0.3)';
            playBtn.style.color = '#fff';
            if (animId) { cancelAnimationFrame(animId); animId = null; }
        };

        const reset = () => {
            pause();
            currentHop = 0;
            dot.style.display = 'none';
            stackEl.style.display = 'none';
            initStack(flows[currentFlowIdx]);
            highlightPath(flows[currentFlowIdx]);
            hopText.textContent = 'Ready';
        };

        // Event handlers
        playBtn.addEventListener('click', () => { if (isPlaying) pause(); else play(); });
        stepBtn.addEventListener('click', () => {
            pause();
            const flow = flows[currentFlowIdx];
            if (currentHop >= flow.path.length - 1) { reset(); return; }
            animateHop();
        });
        resetBtn.addEventListener('click', reset);
        speedSlider.addEventListener('input', () => {
            speed = parseFloat(speedSlider.value);
            speedLabel.textContent = speed + 'x';
        });
        select.addEventListener('change', () => {
            pause();
            currentFlowIdx = parseInt(select.value);
            currentHop = 0;
            dot.style.display = 'none';
            stackEl.style.display = 'none';
            updateDotStyle(flows[currentFlowIdx]);
            initStack(flows[currentFlowIdx]);
            highlightPath(flows[currentFlowIdx]);
            hopText.textContent = 'Ready';
        });

        // Initialize
        updateDotStyle(flows[0]);
        initStack(flows[0]);
        highlightPath(flows[0]);

        // Store cleanup ref
        this._flowCleanup = () => {
            pause();
            if (dot.parentNode) dot.remove();
            if (bar.parentNode) bar.remove();
        };
    }

    buildElements(state) {
        const elements = [];

        // Zone parent nodes
        if (state.zones) {
            for (const zone of state.zones) {
                elements.push({
                    group: 'nodes',
                    data: {
                        id: zone.id,
                        label: zone.label || zone.id,
                        isZone: true,
                        zoneColor: zone.color || '#071c35',
                        zoneBackground: zone.background || 'rgba(7, 28, 53, 0.05)',
                        zoneBorderStyle: zone.border_style || 'solid',
                    },
                });
            }
        }

        // Device nodes
        if (state.nodes) {
            for (const node of state.nodes) {
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
                elements.push(elem);
            }
        }

        // Edges (use port info in ID to handle parallel links)
        if (state.edges) {
            state.edges.forEach((edge, i) => {
                const sp = edge.source_port || i;
                const tp = edge.target_port || i;
                elements.push({
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

        return elements;
    }
}

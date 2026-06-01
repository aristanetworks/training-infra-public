/**
 * Import Manager - YAML/JSON/topo_build.yml/API import
 * Handles loading topology data from various sources
 */

import { TopoBuildConverter } from './topo-build-converter.js';

export class ImportManager {
    constructor(options = {}) {
        this.onImport = options.onImport;
        this.canvas = options.canvas;
        this.zones = options.zones;
        this.annotations = options.annotations;

        this.converter = new TopoBuildConverter();

        this.bindModalEvents();
    }

    bindModalEvents() {
        const modal = document.getElementById('import-modal');
        const closeBtn = document.getElementById('import-close');
        const cancelBtn = document.getElementById('btn-import-cancel');
        const confirmBtn = document.getElementById('btn-import-confirm');

        closeBtn.addEventListener('click', () => this.hideModal());
        cancelBtn.addEventListener('click', () => this.hideModal());
        confirmBtn.addEventListener('click', () => this.confirmImport());

        // Tab switching
        document.querySelectorAll('.import-tab').forEach(tab => {
            tab.addEventListener('click', () => {
                document.querySelectorAll('.import-tab').forEach(t => t.classList.remove('active'));
                tab.classList.add('active');

                const tabName = tab.getAttribute('data-tab');
                document.getElementById('import-tab-paste').style.display = tabName === 'paste' ? '' : 'none';
                document.getElementById('import-tab-upload').style.display = tabName === 'upload' ? '' : 'none';
            });
        });

        // File upload
        const dropzone = document.getElementById('import-dropzone');
        const fileInput = document.getElementById('import-file-input');

        dropzone.addEventListener('click', () => fileInput.click());
        dropzone.addEventListener('dragover', (e) => {
            e.preventDefault();
            dropzone.classList.add('dragover');
        });
        dropzone.addEventListener('dragleave', () => {
            dropzone.classList.remove('dragover');
        });
        dropzone.addEventListener('drop', (e) => {
            e.preventDefault();
            dropzone.classList.remove('dragover');
            const file = e.dataTransfer.files[0];
            if (file) this.readFile(file);
        });

        fileInput.addEventListener('change', () => {
            const file = fileInput.files[0];
            if (file) this.readFile(file);
        });

        // Close on background click
        modal.addEventListener('click', (e) => {
            if (e.target === modal) this.hideModal();
        });
    }

    readFile(file) {
        const reader = new FileReader();
        reader.onload = (e) => {
            document.getElementById('import-textarea').value = e.target.result;
            // Switch to paste tab to show content
            document.querySelectorAll('.import-tab').forEach(t => t.classList.remove('active'));
            document.querySelector('.import-tab[data-tab="paste"]').classList.add('active');
            document.getElementById('import-tab-paste').style.display = '';
            document.getElementById('import-tab-upload').style.display = 'none';
        };
        reader.readAsText(file);
    }

    showImportModal(type) {
        const modal = document.getElementById('import-modal');
        const title = document.getElementById('import-modal-title');
        const textarea = document.getElementById('import-textarea');

        if (type === 'topology') {
            title.textContent = 'Import from Topology';
            textarea.placeholder = 'Paste topo_build.yml content here...';
        } else {
            title.textContent = 'Import YAML/JSON';
            textarea.placeholder = 'Paste topology diagram YAML or JSON here...';
        }

        textarea.value = '';
        textarea.setAttribute('data-import-type', type);
        modal.style.display = '';
    }

    hideModal() {
        document.getElementById('import-modal').style.display = 'none';
    }

    confirmImport() {
        if (this.canvas && this.canvas.cy.elements().length > 0) {
            if (!confirm('This will replace the current diagram. Continue?')) {
                return;
            }
        }

        const textarea = document.getElementById('import-textarea');
        const content = textarea.value.trim();
        const importType = textarea.getAttribute('data-import-type');

        if (!content) {
            this.showToast('No content to import', 'error');
            return;
        }

        try {
            let data;

            if (importType === 'topology') {
                // Parse as topo_build.yml and convert
                data = this.converter.convert(content);
            } else {
                // Parse as diagram YAML/JSON
                data = this.parseDiagramData(content);
            }

            if (this.onImport) {
                this.onImport(data);
            }

            this.hideModal();
        } catch (error) {
            this.showToast(`Import error: ${error.message}`, 'error');
            console.error('[ImportManager] Import error:', error);
        }
    }

    /**
     * Import from the live topology API
     */
    async importFromAPI() {
        if (this.canvas && this.canvas.cy.elements().length > 0) {
            if (!confirm('This will replace the current diagram. Continue?')) {
                return;
            }
        }

        try {
            this.showToast('Fetching topology from API...', 'info');

            const response = await fetch('/td-api/topology');
            if (!response.ok) {
                throw new Error(`API returned ${response.status}`);
            }

            const apiData = await response.json();
            const data = this.convertAPIData(apiData);

            if (this.onImport) {
                this.onImport(data);
            }
        } catch (error) {
            this.showToast(`API import error: ${error.message}`, 'error');
            console.error('[ImportManager] API import error:', error);
        }
    }

    /**
     * Parse diagram YAML or JSON content
     */
    parseDiagramData(content) {
        // Try YAML first (jsyaml available globally from CDN)
        if (typeof jsyaml !== 'undefined') {
            try {
                return jsyaml.load(content);
            } catch {
                // Fall through to JSON
            }
        }

        // Try JSON
        try {
            return JSON.parse(content);
        } catch {
            throw new Error('Could not parse content as YAML or JSON');
        }
    }

    /**
     * Convert /td-api/topology response to diagram schema
     */
    convertAPIData(apiData) {
        const nodes = [];
        const edges = [];

        // Convert API nodes
        if (apiData.nodes) {
            for (const apiNode of apiData.nodes) {
                const node = {
                    id: apiNode.data.id,
                    label: apiNode.data.label || apiNode.data.id,
                    type: apiNode.data.device_type || 'other',
                };
                if (apiNode.data.ip) node.ip = apiNode.data.ip;
                if (apiNode.position) {
                    node.position = {
                        x: Math.round(apiNode.position.x),
                        y: Math.round(apiNode.position.y),
                    };
                }
                nodes.push(node);
            }
        }

        // Convert API edges
        if (apiData.edges) {
            for (const apiEdge of apiData.edges) {
                const edge = {
                    source: apiEdge.data.source,
                    target: apiEdge.data.target,
                };
                if (apiEdge.data.source_port) edge.source_port = apiEdge.data.source_port;
                if (apiEdge.data.target_port) edge.target_port = apiEdge.data.target_port;
                edges.push(edge);
            }
        }

        return {
            settings: {
                title: '',
                layout: 'preset',
                height: 500,
                live_status: true,
                device_access: true,
                show_port_labels: true,
            },
            nodes,
            edges,
            zones: [],
            annotations: [],
            flows: [],
        };
    }

    showToast(message, type) {
        const toast = document.getElementById('builder-toast');
        if (toast) {
            toast.textContent = message;
            toast.className = `builder-toast toast-${type}`;
            toast.style.display = 'block';
            setTimeout(() => { toast.style.display = 'none'; }, 2500);
        }
    }
}

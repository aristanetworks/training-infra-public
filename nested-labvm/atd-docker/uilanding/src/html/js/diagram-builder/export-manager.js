/**
 * Export Manager - YAML/JSON/RST export
 * Generates topology diagram files in various formats
 */

export class ExportManager {
    constructor(options = {}) {
        this.getState = options.getState;
        this.cy = options.cy;
        this.annotations = options.annotations;
        this.zones = options.zones;
    }

    /**
     * Generate YAML string from current state
     */
    toYAML() {
        const state = this.getState();
        const lines = [];

        // Title
        if (state.settings.title) {
            lines.push(`title: "${this.escapeYaml(state.settings.title)}"`);
            lines.push('');
        }

        // Settings
        lines.push('settings:');
        lines.push(`  layout: ${state.settings.layout || 'preset'}`);
        lines.push(`  height: ${state.settings.height || 500}`);
        lines.push(`  live_status: ${state.settings.live_status}`);
        lines.push(`  device_access: ${state.settings.device_access}`);
        lines.push(`  show_port_labels: ${state.settings.show_port_labels}`);
        lines.push('');

        // Nodes
        if (state.nodes.length > 0) {
            lines.push('nodes:');
            for (const node of state.nodes) {
                lines.push(`  - id: ${node.id}`);
                lines.push(`    label: "${this.escapeYaml(node.label)}"`);
                lines.push(`    type: ${node.type}`);
                if (node.ip) lines.push(`    ip: ${node.ip}`);
                if (node.highlight) lines.push(`    highlight: "${node.highlight}"`);
                if (node.zone) lines.push(`    zone: ${node.zone}`);
                if (node.position) {
                    lines.push(`    position:`);
                    lines.push(`      x: ${node.position.x}`);
                    lines.push(`      y: ${node.position.y}`);
                }
            }
            lines.push('');
        }

        // Edges
        if (state.edges.length > 0) {
            lines.push('edges:');
            for (const edge of state.edges) {
                lines.push(`  - source: ${edge.source}`);
                lines.push(`    target: ${edge.target}`);
                if (edge.source_port) lines.push(`    source_port: ${edge.source_port}`);
                if (edge.target_port) lines.push(`    target_port: ${edge.target_port}`);
                if (edge.label) lines.push(`    label: "${this.escapeYaml(edge.label)}"`);
            }
            lines.push('');
        }

        // Zones
        if (state.zones.length > 0) {
            lines.push('zones:');
            for (const zone of state.zones) {
                lines.push(`  - id: ${zone.id}`);
                lines.push(`    label: "${this.escapeYaml(zone.label)}"`);
                if (zone.color) lines.push(`    color: "${zone.color}"`);
                if (zone.background) lines.push(`    background: "${zone.background}"`);
                if (zone.border_style && zone.border_style !== 'solid') {
                    lines.push(`    border_style: ${zone.border_style}`);
                }
                if (zone.layer !== undefined && zone.layer !== 0) {
                    lines.push(`    layer: ${zone.layer}`);
                }
            }
            lines.push('');
        }

        // Annotations
        if (state.annotations.length > 0) {
            lines.push('annotations:');
            for (const ann of state.annotations) {
                lines.push(`  - text: "${this.escapeYaml(ann.text)}"`);
                lines.push(`    position: {x: ${ann.position.x}, y: ${ann.position.y}}`);
                if (ann.color) lines.push(`    color: "${ann.color}"`);
                if (ann.font_size && ann.font_size !== 12) lines.push(`    font_size: ${ann.font_size}`);
                if (ann.background === false) lines.push(`    background: false`);
            }
            lines.push('');
        }

        // Flows
        if (state.flows && state.flows.length > 0) {
            lines.push('flows:');
            for (const flow of state.flows) {
                lines.push(`  - name: "${this.escapeYaml(flow.name)}"`);
                lines.push(`    path: [${flow.path.join(', ')}]`);
                if (flow.color) lines.push(`    color: "${flow.color}"`);
                if (flow.packet_size && flow.packet_size !== 12) lines.push(`    packet_size: ${flow.packet_size}`);
                if (flow.speed && flow.speed !== 1) lines.push(`    speed: ${flow.speed}`);
                if (flow.description) lines.push(`    description: "${this.escapeYaml(flow.description)}"`);
                if (flow.labels && Object.keys(flow.labels).length > 0) {
                    lines.push('    labels:');
                    for (const [nodeId, label] of Object.entries(flow.labels)) {
                        lines.push(`      ${nodeId}: "${this.escapeYaml(label)}"`);
                    }
                }
                if (flow.initial_headers && flow.initial_headers.length > 0) {
                    const defaults = flow.initial_headers.length === 1 && flow.initial_headers[0] === 'Packet';
                    if (!defaults) {
                        lines.push(`    initial_headers: [${flow.initial_headers.map(h => `"${this.escapeYaml(h)}"`).join(', ')}]`);
                    }
                }
                if (flow.encapsulation && Object.keys(flow.encapsulation).length > 0) {
                    lines.push('    encapsulation:');
                    for (const [nodeId, ops] of Object.entries(flow.encapsulation)) {
                        lines.push(`      ${nodeId}:`);
                        for (const op of ops) {
                            lines.push(`        - action: ${op.action}`);
                            lines.push(`          header: "${this.escapeYaml(op.header)}"`);
                        }
                    }
                }
            }
            lines.push('');
        }

        return lines.join('\n');
    }

    /**
     * Generate RST directive snippet
     */
    toRST() {
        const state = this.getState();
        const yamlContent = this.toYAML();

        const lines = [];
        lines.push('.. topology-diagram::');

        // Add directive options
        if (state.settings.height && state.settings.height !== 500) {
            lines.push(`   :height: ${state.settings.height}`);
        }
        lines.push(`   :layout: ${state.settings.layout || 'preset'}`);
        if (!state.settings.live_status) {
            lines.push('   :no-live-status:');
        }
        if (!state.settings.device_access) {
            lines.push('   :no-device-access:');
        }
        if (state.settings.title) {
            lines.push(`   :title: ${state.settings.title}`);
        }
        lines.push('');

        // Indent YAML content for RST directive body
        const indentedYaml = yamlContent.split('\n').map(line => '   ' + line).join('\n');
        lines.push(indentedYaml);

        return lines.join('\n');
    }

    /**
     * Copy YAML to clipboard
     */
    async copyYAML() {
        const yaml = this.toYAML();
        await this.copyToClipboard(yaml, 'YAML copied to clipboard');
    }

    /**
     * Copy RST snippet to clipboard
     */
    async copyRST() {
        const rst = this.toRST();
        await this.copyToClipboard(rst, 'RST snippet copied to clipboard');
    }

    /**
     * Download as .yml file
     */
    downloadYML() {
        const yaml = this.toYAML();
        const state = this.getState();
        const filename = (state.settings.title || 'topology')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '') + '.yml';

        const blob = new Blob([yaml], { type: 'text/yaml' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        URL.revokeObjectURL(url);

        this.showToast('Downloaded ' + filename, 'success');
    }

    async copyToClipboard(text, message) {
        try {
            await navigator.clipboard.writeText(text);
            this.showToast(message, 'success');
        } catch {
            // Fallback for older browsers
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            this.showToast(message, 'success');
        }
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

    escapeYaml(str) {
        return str.replace(/"/g, '\\"');
    }
}

/**
 * Topo Build Converter - Auto-convert topo_build.yml format to diagram YAML schema
 * Handles device type classification, edge deduplication, and auto-zone detection
 *
 * topo_build.yml format:
 * nodes:
 *   - DeviceName:
 *       ip_addr: 192.168.0.X
 *       sys_mac: 00:1c:73:XX:XX:XX
 *       neighbors:
 *         - neighborDevice: OtherDevice
 *           neighborPort: EthernetX
 *           port: EthernetY
 */

// Classification patterns matching device_types.py
const CLASSIFICATION_ORDER = [
    { type: 'borderleaf', patterns: ['borderleaf'], startswith: ['BL'] },
    { type: 'memleaf', patterns: ['memleaf'], startswith: [] },
    { type: 'internet', patterns: ['internet'], startswith: [] },
    { type: 'isp', patterns: ['isp'], startswith: [] },
    { type: 'core', patterns: ['core'], startswith: [] },
    { type: 'dci', patterns: ['dci'], startswith: [] },
    { type: 'oob', patterns: ['oob'], startswith: [] },
    { type: 'spine', patterns: ['spine'], startswith: [] },
    { type: 'leaf', patterns: ['leaf'], startswith: [] },
    { type: 'host', patterns: ['host'], startswith: [] },
    { type: 'router', patterns: ['router'], startswith: [] },
];

export class TopoBuildConverter {
    /**
     * Convert topo_build.yml content string to diagram schema
     */
    convert(yamlContent) {
        let parsed;
        if (typeof jsyaml !== 'undefined') {
            parsed = jsyaml.load(yamlContent);
        } else {
            throw new Error('YAML parser not available');
        }

        if (!parsed || (!parsed.nodes && !parsed.servers)) {
            throw new Error('Invalid topo_build.yml: missing "nodes" and "servers" arrays');
        }

        const nodes = [];
        const edgeMap = new Map(); // Deduplicate bidirectional edges
        const deviceNames = new Set();

        // Parse both nodes and servers sections (same format)
        const allEntries = [
            ...(parsed.nodes || []),
            ...(parsed.servers || []),
        ];

        for (const nodeEntry of allEntries) {
            // Each entry is { DeviceName: { ip_addr, sys_mac, neighbors } }
            const name = Object.keys(nodeEntry)[0];
            const config = nodeEntry[name];

            // Skip if already processed (shouldn't happen but guard against it)
            if (deviceNames.has(name)) continue;
            deviceNames.add(name);

            const type = this.classifyDevice(name);

            nodes.push({
                id: name,
                label: name,
                type: type,
                ip: config.ip_addr || '',
            });

            // Extract edges from neighbors
            if (config.neighbors) {
                for (const neighbor of config.neighbors) {
                    const edgeKey = this.makeEdgeKey(
                        name, neighbor.port,
                        neighbor.neighborDevice, neighbor.neighborPort
                    );

                    if (!edgeMap.has(edgeKey)) {
                        edgeMap.set(edgeKey, {
                            source: name,
                            target: neighbor.neighborDevice,
                            source_port: neighbor.port,
                            target_port: neighbor.neighborPort,
                        });
                    }
                }
            }
        }

        // Edges
        const edges = Array.from(edgeMap.values());

        // Auto-detect zones from device naming patterns
        const zones = this.detectZones(nodes);

        return {
            settings: {
                title: '',
                layout: 'dagre',
                height: 500,
                live_status: true,
                device_access: true,
                show_port_labels: true,
            },
            nodes,
            edges,
            zones,
            annotations: [],
        };
    }

    /**
     * Classify device type from name - mirrors device_types.py logic
     */
    classifyDevice(name) {
        if (!name) return 'other';

        // Custom matchers (checked first)
        if (name === 'RR' || (name.startsWith('RR') && name.length > 2 && /\d/.test(name[2]))) {
            return 'rr';
        }
        if (name.startsWith('P') && name.length > 1 && /\d/.test(name[1])) {
            return 'p';
        }
        if (name.startsWith('GW') && name.length > 2 && /\d/.test(name[2])) {
            return 'gw';
        }
        if (name.length > 1 && 'ABCD'.includes(name[0]) && /\d/.test(name[1])) {
            return 'customer';
        }

        // Startswith patterns (case-sensitive)
        if (name.startsWith('PE')) return 'pe';
        if (name.startsWith('CE')) return 'ce';

        // Contains patterns (case-insensitive)
        const nameLower = name.toLowerCase();
        for (const entry of CLASSIFICATION_ORDER) {
            for (const sw of entry.startswith) {
                if (name.startsWith(sw)) return entry.type;
            }
            for (const pattern of entry.patterns) {
                if (nameLower.includes(pattern)) return entry.type;
            }
        }

        return 'other';
    }

    /**
     * Create a normalized edge key for deduplication
     * Sorts source/target alphabetically so A->B and B->A produce the same key
     */
    makeEdgeKey(device1, port1, device2, port2) {
        const pair1 = `${device1}:${port1}`;
        const pair2 = `${device2}:${port2}`;
        return pair1 < pair2 ? `${pair1}|${pair2}` : `${pair2}|${pair1}`;
    }

    /**
     * Auto-detect zones from device naming patterns
     * Looks for DC suffixes (e.g., "spine1-DC1", "leaf2-DC2") or
     * groups by device type categories
     */
    detectZones(nodes) {
        const zones = [];
        const dcGroups = new Map();

        for (const node of nodes) {
            // Check for DC suffix pattern: DeviceName-DC1, DeviceName_DC2
            const dcMatch = node.id.match(/[-_](DC\d+)$/i);
            if (dcMatch) {
                const dcName = dcMatch[1].toUpperCase();
                if (!dcGroups.has(dcName)) {
                    dcGroups.set(dcName, []);
                }
                dcGroups.get(dcName).push(node.id);
                node.zone = dcName.toLowerCase();
            }
        }

        // Create zone definitions for detected DCs
        const colors = ['#071c35', '#4c5cae', '#008b8b', '#8b4513'];
        let colorIdx = 0;
        for (const [dcName, nodeIds] of dcGroups) {
            if (nodeIds.length >= 2) { // Only create zones with 2+ devices
                const color = colors[colorIdx % colors.length];
                zones.push({
                    id: dcName.toLowerCase(),
                    label: dcName,
                    color: color,
                    background: `rgba(${this.hexToRgb(color)}, 0.05)`,
                    border_style: 'solid',
                });
                colorIdx++;
            }
        }

        return zones;
    }

    hexToRgb(hex) {
        const r = parseInt(hex.slice(1, 3), 16);
        const g = parseInt(hex.slice(3, 5), 16);
        const b = parseInt(hex.slice(5, 7), 16);
        return `${r}, ${g}, ${b}`;
    }
}

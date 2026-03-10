import json
import yaml
from pathlib import Path
from docutils.parsers.rst import directives
from sphinx.util.docutils import SphinxDirective
from .nodes import TopologyDiagramNode
from .schema import validate_topology
from .device_types import DEVICE_TYPE_ICONS, classify_device


class TopologyDiagramDirective(SphinxDirective):
    has_content = True
    optional_arguments = 0

    option_spec = {
        'height': directives.positive_int,
        'layout': lambda x: directives.choice(x, ('preset', 'dagre', 'cose', 'concentric', 'grid')),
        'file': directives.path,
        'no-live-status': directives.flag,
        'no-device-access': directives.flag,
        'title': directives.unchanged,
    }

    def run(self):
        has_file = 'file' in self.options
        has_content = bool(self.content)

        if has_file and has_content:
            raise self.error('Cannot specify both :file: and inline content for topology-diagram directive')

        if not has_file and not has_content:
            raise self.error('topology-diagram directive requires either :file: or inline content')

        if has_file:
            rel_path = self.options['file']
            source_dir = Path(self.state.document.settings.env.srcdir)
            file_path = source_dir / rel_path
            if not file_path.exists():
                raise self.error(f'Topology file not found: {rel_path}')
            raw_yaml = file_path.read_text(encoding='utf-8')
            # Add the file as a dependency so Sphinx rebuilds when it changes
            self.state.document.settings.env.note_dependency(str(file_path))
        else:
            raw_yaml = '\n'.join(self.content)

        try:
            topo_data = yaml.safe_load(raw_yaml)
        except yaml.YAMLError as e:
            raise self.error(f'Invalid YAML in topology-diagram: {e}')

        errors = validate_topology(topo_data)
        if errors:
            raise self.error(f'Topology validation errors: {"; ".join(errors)}')

        # Apply directive options as overrides
        height = self.options.get('height', topo_data.get('settings', {}).get('height', 500))
        layout = self.options.get('layout', topo_data.get('settings', {}).get('layout', 'dagre'))
        title = self.options.get('title', topo_data.get('title', ''))
        live_status = 'no-live-status' not in self.options and topo_data.get('settings', {}).get('live_status', True)
        device_access = 'no-device-access' not in self.options and topo_data.get('settings', {}).get('device_access', True)
        show_port_labels = topo_data.get('settings', {}).get('show_port_labels', True)

        # Convert to Cytoscape elements
        elements = self._convert_to_cytoscape(topo_data)
        annotations = topo_data.get('annotations', [])

        viewer_data = {
            'title': title,
            'height': height,
            'layout': layout,
            'liveStatus': live_status,
            'deviceAccess': device_access,
            'showPortLabels': show_port_labels,
            'elements': elements,
            'annotations': annotations,
        }

        node = TopologyDiagramNode()
        node['topology_data'] = json.dumps(viewer_data)
        node['height'] = height
        node['title'] = title
        return [node]

    def _convert_to_cytoscape(self, topo_data):
        """Convert diagram YAML schema to Cytoscape.js elements array."""
        elements = []

        # Add zone parent nodes
        for zone in topo_data.get('zones', []):
            elements.append({
                'group': 'nodes',
                'data': {
                    'id': zone['id'],
                    'label': zone.get('label', zone['id']),
                    'isZone': True,
                    'zoneColor': zone.get('color', '#071c35'),
                    'zoneBackground': zone.get('background', 'rgba(7, 28, 53, 0.05)'),
                    'zoneBorderStyle': zone.get('border_style', 'solid'),
                },
                'classes': 'zone-parent',
            })

        # Add device nodes
        for node_def in topo_data.get('nodes', []):
            device_type = node_def.get('type', classify_device(node_def['id']))
            icon = DEVICE_TYPE_ICONS.get(device_type, 'router.png')

            node_data = {
                'id': node_def['id'],
                'label': node_def.get('label', node_def['id']),
                'device_type': device_type,
                'ip': node_def.get('ip', ''),
                'icon': icon,
            }

            # Assign to zone parent if specified
            if node_def.get('zone'):
                node_data['parent'] = node_def['zone']

            elem = {
                'group': 'nodes',
                'data': node_data,
                'classes': f'device-type-{device_type}',
            }

            if node_def.get('position'):
                elem['position'] = {
                    'x': node_def['position']['x'],
                    'y': node_def['position']['y'],
                }

            elements.append(elem)

        # Add edges (include ports in ID to support parallel links)
        for i, edge_def in enumerate(topo_data.get('edges', [])):
            sp = edge_def.get('source_port', str(i))
            tp = edge_def.get('target_port', str(i))
            edge_id = f"{edge_def['source']}|{edge_def['target']}:{sp}-{tp}"
            edge_data = {
                'id': edge_id,
                'source': edge_def['source'],
                'target': edge_def['target'],
            }
            if edge_def.get('source_port'):
                edge_data['source_port'] = edge_def['source_port']
            if edge_def.get('target_port'):
                edge_data['target_port'] = edge_def['target_port']

            elements.append({
                'group': 'edges',
                'data': edge_data,
            })

        return elements

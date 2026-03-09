def validate_topology(data):
    """Validate topology diagram YAML data. Returns list of error strings."""
    errors = []

    if not isinstance(data, dict):
        return ['Topology data must be a YAML mapping']

    # Validate nodes
    nodes = data.get('nodes', [])
    if not isinstance(nodes, list):
        errors.append('"nodes" must be a list')
    else:
        node_ids = set()
        for i, node in enumerate(nodes):
            if not isinstance(node, dict):
                errors.append(f'Node {i} must be a mapping')
                continue
            if 'id' not in node:
                errors.append(f'Node {i} missing required "id" field')
            else:
                if node['id'] in node_ids:
                    errors.append(f'Duplicate node id: {node["id"]}')
                node_ids.add(node['id'])

            if 'position' in node:
                pos = node['position']
                if not isinstance(pos, dict) or 'x' not in pos or 'y' not in pos:
                    errors.append(f'Node {node.get("id", i)}: position must have x and y')

    # Validate edges
    edges = data.get('edges', [])
    if not isinstance(edges, list):
        errors.append('"edges" must be a list')
    else:
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                errors.append(f'Edge {i} must be a mapping')
                continue
            if 'source' not in edge:
                errors.append(f'Edge {i} missing "source"')
            if 'target' not in edge:
                errors.append(f'Edge {i} missing "target"')

    # Validate zones
    zones = data.get('zones', [])
    if not isinstance(zones, list):
        errors.append('"zones" must be a list')
    else:
        for i, zone in enumerate(zones):
            if not isinstance(zone, dict):
                errors.append(f'Zone {i} must be a mapping')
                continue
            if 'id' not in zone:
                errors.append(f'Zone {i} missing "id"')

    # Validate annotations
    annotations = data.get('annotations', [])
    if not isinstance(annotations, list):
        errors.append('"annotations" must be a list')
    else:
        for i, ann in enumerate(annotations):
            if not isinstance(ann, dict):
                errors.append(f'Annotation {i} must be a mapping')
                continue
            if 'text' not in ann:
                errors.append(f'Annotation {i} missing "text"')
            if 'position' in ann:
                pos = ann['position']
                if not isinstance(pos, dict) or 'x' not in pos or 'y' not in pos:
                    errors.append(f'Annotation {i}: position must have x and y')

    # Validate settings
    settings = data.get('settings', {})
    if settings and not isinstance(settings, dict):
        errors.append('"settings" must be a mapping')
    elif isinstance(settings, dict):
        valid_layouts = {'preset', 'dagre', 'cose', 'concentric', 'grid'}
        layout = settings.get('layout')
        if layout and layout not in valid_layouts:
            errors.append(f'Invalid layout: {layout}. Must be one of: {", ".join(valid_layouts)}')

    return errors

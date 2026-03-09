from pathlib import Path
from .directive import TopologyDiagramDirective
from .nodes import TopologyDiagramNode
from .translator import visit_topology_node, depart_topology_node


def setup(app):
    app.add_node(
        TopologyDiagramNode,
        html=(visit_topology_node, depart_topology_node),
    )
    app.add_directive('topology-diagram', TopologyDiagramDirective)

    static_path = str(Path(__file__).parent / '_static')
    app.connect('builder-inited', lambda app: app.config.html_static_path.append(static_path))

    app.add_css_file('atl-topology-viewer.css')
    app.add_js_file('cytoscape.min.js')
    app.add_js_file('dagre.min.js')
    app.add_js_file('cytoscape-dagre.js')
    app.add_js_file('atl-topology-viewer.js')

    return {
        'version': '1.0.0',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }

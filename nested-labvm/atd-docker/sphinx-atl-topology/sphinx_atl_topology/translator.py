import html


def visit_topology_node(self, node):
    title = node.get('title', '')
    height = node.get('height', 600)
    topology_json = node.get('topology_data', '{}')

    # Title rendered inside the container as a top-right overlay
    title_html = ''
    if title:
        title_html = (
            f'<div class="atl-topology-diagram-title">'
            f'{html.escape(title)}</div>'
        )

    self.body.append(
        f'<div class="atl-topology-container" '
        f'style="height: {height}px;" '
        f'data-topology=\'{html.escape(topology_json, quote=True)}\'>'
        f'{title_html}'
        f'</div>'
    )


def depart_topology_node(self, node):
    pass

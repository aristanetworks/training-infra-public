import html


def visit_topology_node(self, node):
    title = node.get('title', '')
    height = node.get('height', 500)
    topology_json = node.get('topology_data', '{}')

    title_html = ''
    if title:
        title_html = f'<div class="atl-topology-title">{html.escape(title)}</div>'

    self.body.append(
        f'{title_html}'
        f'<div class="atl-topology-container" '
        f'style="height: {height}px;" '
        f'data-topology=\'{html.escape(topology_json, quote=True)}\'>'
        f'</div>'
    )


def depart_topology_node(self, node):
    pass

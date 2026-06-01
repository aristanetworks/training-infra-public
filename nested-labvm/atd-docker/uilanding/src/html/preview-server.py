#!/usr/bin/env python3
"""Lightweight preview server for the diagram builder UI.
Serves static files and renders the Tornado template with stub data.
Run from the html/ directory: python3 preview-server.py
"""
import os
import tornado.ioloop
import tornado.web

BASE_PATH = os.path.dirname(os.path.abspath(__file__))

class DiagramBuilderHandler(tornado.web.RequestHandler):
    def get(self):
        self.render(os.path.join(BASE_PATH, 'diagram-builder.html'),
                    topo_title='Preview Lab Topology')

class CSSHandler(tornado.web.StaticFileHandler):
    """Serves CSS files, falling back to non-minified versions."""
    def validate_absolute_path(self, root, absolute_path):
        if not os.path.exists(absolute_path) and absolute_path.endswith('.min.css'):
            fallback = absolute_path.replace('.min.css', '.css')
            if os.path.exists(fallback):
                return fallback
        return super().validate_absolute_path(root, absolute_path)

class StubAPIHandler(tornado.web.RequestHandler):
    """Stub API endpoints so the builder doesn't error on fetch calls."""
    def get(self):
        if 'name' in self.get_arguments('name'):
            self.write({'content': ''})
        else:
            self.write({'diagrams': []})

    def post(self):
        self.write({'saved': 'preview'})

class StubTopologyHandler(tornado.web.RequestHandler):
    def get(self):
        self.write({
            'nodes': [
                {'data': {'id': 'spine1', 'label': 'Spine 1', 'device_type': 'spine', 'ip': '192.168.0.11'}},
                {'data': {'id': 'spine2', 'label': 'Spine 2', 'device_type': 'spine', 'ip': '192.168.0.12'}},
                {'data': {'id': 'leaf1', 'label': 'Leaf 1', 'device_type': 'leaf', 'ip': '192.168.0.21'}},
                {'data': {'id': 'leaf2', 'label': 'Leaf 2', 'device_type': 'leaf', 'ip': '192.168.0.22'}},
                {'data': {'id': 'leaf3', 'label': 'Leaf 3', 'device_type': 'leaf', 'ip': '192.168.0.23'}},
                {'data': {'id': 'host1', 'label': 'Host 1', 'device_type': 'host', 'ip': '192.168.0.100'}},
                {'data': {'id': 'host2', 'label': 'Host 2', 'device_type': 'host', 'ip': '192.168.0.101'}},
            ],
            'edges': [
                {'data': {'source': 'spine1', 'target': 'leaf1', 'source_port': 'Ethernet1', 'target_port': 'Ethernet1'}},
                {'data': {'source': 'spine1', 'target': 'leaf2', 'source_port': 'Ethernet2', 'target_port': 'Ethernet1'}},
                {'data': {'source': 'spine1', 'target': 'leaf3', 'source_port': 'Ethernet3', 'target_port': 'Ethernet1'}},
                {'data': {'source': 'spine2', 'target': 'leaf1', 'source_port': 'Ethernet1', 'target_port': 'Ethernet2'}},
                {'data': {'source': 'spine2', 'target': 'leaf2', 'source_port': 'Ethernet2', 'target_port': 'Ethernet2'}},
                {'data': {'source': 'spine2', 'target': 'leaf3', 'source_port': 'Ethernet3', 'target_port': 'Ethernet2'}},
                {'data': {'source': 'leaf1', 'target': 'host1', 'source_port': 'Ethernet3', 'target_port': 'Ethernet1'}},
                {'data': {'source': 'leaf3', 'target': 'host2', 'source_port': 'Ethernet3', 'target_port': 'Ethernet1'}},
            ],
        })

def make_app():
    return tornado.web.Application([
        (r'/diagram-builder', DiagramBuilderHandler),
        (r'/td-api/diagrams', StubAPIHandler),
        (r'/td-api/topology', StubTopologyHandler),
        (r'/js/(.*)', tornado.web.StaticFileHandler, {'path': os.path.join(BASE_PATH, 'js')}),
        (r'/css/(.*)', CSSHandler, {'path': os.path.join(BASE_PATH, 'css')}),
        (r'/images/(.*)', tornado.web.StaticFileHandler, {'path': os.path.join(BASE_PATH, 'images')}),
        (r'/', tornado.web.RedirectHandler, {'url': '/diagram-builder'}),
    ], debug=True)

if __name__ == '__main__':
    app = make_app()
    port = 8888
    app.listen(port)
    print(f'Preview server running at http://localhost:{port}/diagram-builder')
    print('Press Ctrl+C to stop')
    tornado.ioloop.IOLoop.current().start()

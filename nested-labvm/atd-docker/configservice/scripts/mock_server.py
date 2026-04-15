#!/usr/bin/env python3
"""
Mock Config Service Server - For local testing without Firestore.

Runs a local HTTP server with configurable mock data for testing
the configservice API and client library.

Usage:
    # Run with default mock data
    python mock_server.py

    # Run with custom port
    python mock_server.py --port 8080

    # Run with custom topology
    python mock_server.py --topology training-level7-cl

    # Load mock data from JSON file
    python mock_server.py --data mock_data.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, List, Optional
from urllib.parse import urlparse

# Default mock data
DEFAULT_MOCK_DATA = {
    'topology': 'training-level7-cl',
    'features': {
        'enabled_features': ['dark_mode', 'new_dashboard', 'base_capture'],
        'requested_features': ['dark_mode', 'new_dashboard', 'base_capture', 'advanced_capture'],
        'global_features': ['dark_mode'],
        'topology_features': ['new_dashboard', 'base_capture', 'advanced_capture'],
        'dependency_resolution': {
            'enabled': ['dark_mode', 'new_dashboard', 'base_capture'],
            'disabled_missing_deps': {
                'advanced_capture': ['premium_license']
            },
            'disabled_circular': [],
            'dependency_chain': {
                'base_capture': [],
                'new_dashboard': []
            }
        },
        'source': 'mock'
    },
    'announcements': {
        'active_announcements': [
            {
                'id': 'ann-001',
                'title': 'System Maintenance Scheduled',
                'message': 'The lab environment will undergo maintenance on Saturday.',
                'type': 'warning',
                'priority': 100,
                'start_date': '2024-01-01T00:00:00Z',
                'end_date': '2030-12-31T23:59:59Z'
            },
            {
                'id': 'ann-002',
                'title': 'Welcome to Arista Training Labs',
                'message': 'Get started with the lab guides on the left panel.',
                'type': 'info',
                'priority': 50,
                'start_date': '2024-01-01T00:00:00Z',
                'end_date': '2030-12-31T23:59:59Z'
            }
        ],
        'global_announcements': [
            {
                'id': 'ann-001',
                'title': 'System Maintenance Scheduled',
                'message': 'The lab environment will undergo maintenance on Saturday.',
                'type': 'warning',
                'priority': 100
            }
        ],
        'topology_announcements': [
            {
                'id': 'ann-002',
                'title': 'Welcome to Arista Training Labs',
                'message': 'Get started with the lab guides on the left panel.',
                'type': 'info',
                'priority': 50
            }
        ],
        'source': 'mock'
    }
}


class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    END = '\033[0m'


class MockConfigHandler(BaseHTTPRequestHandler):
    """HTTP request handler for mock config service."""

    mock_data: Dict = DEFAULT_MOCK_DATA

    def log_message(self, format, *args):
        """Override to use colored output."""
        method = args[0].split()[0] if args else 'GET'
        path = args[0].split()[1] if args and len(args[0].split()) > 1 else '/'
        status = args[1] if len(args) > 1 else '200'

        # Color based on status
        if str(status).startswith('2'):
            color = Colors.GREEN
        elif str(status).startswith('4'):
            color = Colors.YELLOW
        else:
            color = Colors.RED

        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f"{Colors.CYAN}[{timestamp}]{Colors.END} {color}{method}{Colors.END} {path} -> {color}{status}{Colors.END}")

    def _send_json(self, data: Dict, status: int = 200):
        """Send JSON response."""
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        topology = self.mock_data.get('topology', 'unknown')
        now = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

        if path == '' or path == '/':
            # Root - show available endpoints
            self._send_json({
                'service': 'configservice-mock',
                'version': '1.0.0',
                'endpoints': [
                    'GET /health',
                    'GET /features',
                    'GET /features/{id}',
                    'GET /announcements',
                    'GET /announcements/{id}',
                    'GET /config',
                    'POST /refresh',
                    'POST /announcements/refresh',
                    'POST /refresh/all'
                ]
            })

        elif path == '/health':
            self._send_json({
                'status': 'ok',
                'service': 'configservice-mock',
                'version': '1.0.0'
            })

        elif path == '/features':
            features = self.mock_data.get('features', {}).copy()
            features['topology'] = topology
            features['fetched_at'] = now
            self._send_json(features)

        elif path.startswith('/features/'):
            feature_id = path.split('/features/')[1]
            enabled_features = self.mock_data.get('features', {}).get('enabled_features', [])
            self._send_json({
                'feature_id': feature_id,
                'enabled': feature_id in enabled_features,
                'topology': topology
            })

        elif path == '/announcements':
            announcements = self.mock_data.get('announcements', {}).copy()
            announcements['topology'] = topology
            announcements['fetched_at'] = now
            self._send_json(announcements)

        elif path.startswith('/announcements/') and path != '/announcements/refresh':
            announcement_id = path.split('/announcements/')[1]
            active = self.mock_data.get('announcements', {}).get('active_announcements', [])
            announcement = next((a for a in active if a['id'] == announcement_id), None)
            self._send_json({
                'announcement_id': announcement_id,
                'active': announcement is not None,
                'announcement': announcement,
                'topology': topology
            })

        elif path == '/config':
            features = self.mock_data.get('features', {}).copy()
            announcements = self.mock_data.get('announcements', {}).copy()
            features['topology'] = topology
            features['fetched_at'] = now
            announcements['topology'] = topology
            announcements['fetched_at'] = now
            self._send_json({
                'features': features,
                'announcements': announcements,
                'topology': topology,
                'fetched_at': now
            })

        else:
            self._send_json({'error': 'Not found', 'path': path}, 404)

    def do_POST(self):
        """Handle POST requests."""
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/')

        topology = self.mock_data.get('topology', 'unknown')

        if path == '/refresh':
            features = self.mock_data.get('features', {})
            self._send_json({
                'status': 'refreshed',
                'features_count': len(features.get('enabled_features', [])),
                'source': 'mock'
            })

        elif path == '/announcements/refresh':
            announcements = self.mock_data.get('announcements', {})
            self._send_json({
                'status': 'refreshed',
                'announcements_count': len(announcements.get('active_announcements', [])),
                'source': 'mock'
            })

        elif path == '/refresh/all':
            features = self.mock_data.get('features', {})
            announcements = self.mock_data.get('announcements', {})
            self._send_json({
                'status': 'refreshed',
                'features_count': len(features.get('enabled_features', [])),
                'announcements_count': len(announcements.get('active_announcements', [])),
                'features_source': 'mock',
                'announcements_source': 'mock'
            })

        else:
            self._send_json({'error': 'Not found', 'path': path}, 404)


def load_mock_data(file_path: str) -> Dict:
    """Load mock data from JSON file."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"{Colors.RED}Error loading mock data: {e}{Colors.END}")
        sys.exit(1)


def print_banner(port: int, topology: str):
    """Print startup banner."""
    print(f"\n{Colors.HEADER}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}  Config Service Mock Server{Colors.END}")
    print(f"{Colors.HEADER}{'='*60}{Colors.END}\n")
    print(f"  {Colors.CYAN}URL:{Colors.END}      http://localhost:{port}")
    print(f"  {Colors.CYAN}Topology:{Colors.END} {topology}")
    print(f"\n  {Colors.BOLD}Endpoints:{Colors.END}")
    print(f"    GET  /health              - Health check")
    print(f"    GET  /features            - Get all features")
    print(f"    GET  /features/{{id}}       - Check specific feature")
    print(f"    GET  /announcements       - Get all announcements")
    print(f"    GET  /announcements/{{id}}  - Get specific announcement")
    print(f"    GET  /config              - Get combined config")
    print(f"    POST /refresh             - Refresh features")
    print(f"    POST /refresh/all         - Refresh all")
    print(f"\n  {Colors.YELLOW}Press Ctrl+C to stop{Colors.END}\n")
    print(f"{Colors.HEADER}{'='*60}{Colors.END}\n")


def main():
    parser = argparse.ArgumentParser(
        description='Run a mock Config Service server for testing',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Run with defaults (port 50011, training-level7-cl topology)
    python mock_server.py

    # Run on different port
    python mock_server.py --port 8080

    # Run with different topology
    python mock_server.py --topology training-level3-cl

    # Load custom mock data
    python mock_server.py --data my_mock_data.json

    # Add a feature to enabled list
    python mock_server.py --add-feature cvp_integration

    # Add an announcement
    python mock_server.py --add-announcement "New Feature Available" "Check out dark mode!"
        """
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=50011,
        help='Port to run the server on (default: 50011)'
    )
    parser.add_argument(
        '--topology', '-t',
        default='training-level7-cl',
        help='Topology name to return (default: training-level7-cl)'
    )
    parser.add_argument(
        '--data', '-d',
        help='Path to JSON file with mock data'
    )
    parser.add_argument(
        '--add-feature', '-f',
        action='append',
        dest='add_features',
        help='Add a feature to the enabled list (can be used multiple times)'
    )
    parser.add_argument(
        '--add-announcement', '-a',
        nargs=2,
        action='append',
        dest='add_announcements',
        metavar=('TITLE', 'MESSAGE'),
        help='Add an announcement (title and message)'
    )

    args = parser.parse_args()

    # Load or create mock data
    if args.data:
        mock_data = load_mock_data(args.data)
    else:
        mock_data = DEFAULT_MOCK_DATA.copy()

    # Update topology
    mock_data['topology'] = args.topology

    # Add extra features
    if args.add_features:
        for feature in args.add_features:
            if feature not in mock_data['features']['enabled_features']:
                mock_data['features']['enabled_features'].append(feature)
            if feature not in mock_data['features']['requested_features']:
                mock_data['features']['requested_features'].append(feature)

    # Add extra announcements
    if args.add_announcements:
        for i, (title, message) in enumerate(args.add_announcements):
            ann = {
                'id': f'custom-{i+1:03d}',
                'title': title,
                'message': message,
                'type': 'info',
                'priority': 75,
                'start_date': '2024-01-01T00:00:00Z',
                'end_date': '2030-12-31T23:59:59Z'
            }
            mock_data['announcements']['active_announcements'].append(ann)

    # Set mock data on handler
    MockConfigHandler.mock_data = mock_data

    # Print banner
    print_banner(args.port, args.topology)

    # Start server
    server = HTTPServer(('0.0.0.0', args.port), MockConfigHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Shutting down...{Colors.END}")
        server.shutdown()


if __name__ == '__main__':
    main()

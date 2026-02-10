#!/usr/bin/env python3
"""
Config Service API Tester - Interactive tool for testing the configservice API.

Usage:
    # Test against local development server
    python test_api.py --url http://localhost:50011

    # Test against Docker container
    python test_api.py --url http://atd-configservice:50011

    # Use curl-style output
    python test_api.py --url http://localhost:50011 --curl
"""

import argparse
import json
import sys
import urllib.request
import urllib.error
from datetime import datetime
from typing import Optional


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


def print_header(text: str):
    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}  {text}{Colors.END}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*60}{Colors.END}\n")


def print_success(text: str):
    print(f"{Colors.GREEN}✓ {text}{Colors.END}")


def print_error(text: str):
    print(f"{Colors.RED}✗ {text}{Colors.END}")


def print_info(text: str):
    print(f"{Colors.CYAN}ℹ {text}{Colors.END}")


def print_warning(text: str):
    print(f"{Colors.YELLOW}⚠ {text}{Colors.END}")


def fetch_endpoint(base_url: str, endpoint: str, timeout: float = 5.0) -> dict:
    """Fetch data from an API endpoint."""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return {
                'success': True,
                'status': response.status,
                'data': json.loads(response.read().decode('utf-8')),
                'url': url
            }
    except urllib.error.HTTPError as e:
        return {
            'success': False,
            'status': e.code,
            'error': str(e),
            'url': url
        }
    except urllib.error.URLError as e:
        return {
            'success': False,
            'status': None,
            'error': str(e.reason),
            'url': url
        }
    except Exception as e:
        return {
            'success': False,
            'status': None,
            'error': str(e),
            'url': url
        }


def print_json(data: dict, indent: int = 2):
    """Pretty print JSON data."""
    print(json.dumps(data, indent=indent, default=str))


def test_health(base_url: str) -> bool:
    """Test the health endpoint."""
    print(f"\n{Colors.BOLD}Testing /health endpoint...{Colors.END}")
    result = fetch_endpoint(base_url, '/health')

    if result['success']:
        print_success(f"Health check passed (status: {result['status']})")
        print_json(result['data'])
        return True
    else:
        print_error(f"Health check failed: {result['error']}")
        return False


def test_features(base_url: str) -> bool:
    """Test the features endpoint."""
    print(f"\n{Colors.BOLD}Testing /features endpoint...{Colors.END}")
    result = fetch_endpoint(base_url, '/features')

    if result['success']:
        data = result['data']
        print_success(f"Features endpoint responded (status: {result['status']})")

        # Summary
        enabled = data.get('enabled_features', [])
        requested = data.get('requested_features', [])
        topology = data.get('topology', 'unknown')
        source = data.get('source', 'unknown')

        print(f"\n{Colors.CYAN}Summary:{Colors.END}")
        print(f"  Topology: {Colors.BOLD}{topology}{Colors.END}")
        print(f"  Source: {source}")
        print(f"  Requested features: {len(requested)}")
        print(f"  Enabled features: {len(enabled)}")

        if enabled:
            print(f"\n{Colors.GREEN}Enabled Features:{Colors.END}")
            for f in enabled:
                print(f"    ✓ {f}")

        # Check for dependency issues
        resolution = data.get('dependency_resolution')
        if resolution:
            disabled = resolution.get('disabled_missing_deps', {})
            circular = resolution.get('disabled_circular', [])

            if disabled:
                print(f"\n{Colors.YELLOW}Disabled (missing dependencies):{Colors.END}")
                for feat, deps in disabled.items():
                    print(f"    ✗ {feat} (needs: {', '.join(deps)})")

            if circular:
                print(f"\n{Colors.RED}Disabled (circular dependencies):{Colors.END}")
                for feat in circular:
                    print(f"    ⚠ {feat}")

        print(f"\n{Colors.CYAN}Full Response:{Colors.END}")
        print_json(data)
        return True
    else:
        print_error(f"Features endpoint failed: {result['error']}")
        return False


def test_announcements(base_url: str) -> bool:
    """Test the announcements endpoint."""
    print(f"\n{Colors.BOLD}Testing /announcements endpoint...{Colors.END}")
    result = fetch_endpoint(base_url, '/announcements')

    if result['success']:
        data = result['data']
        print_success(f"Announcements endpoint responded (status: {result['status']})")

        # Summary
        active = data.get('active_announcements', [])
        topology = data.get('topology', 'unknown')
        source = data.get('source', 'unknown')

        print(f"\n{Colors.CYAN}Summary:{Colors.END}")
        print(f"  Topology: {Colors.BOLD}{topology}{Colors.END}")
        print(f"  Source: {source}")
        print(f"  Active announcements: {len(active)}")

        if active:
            print(f"\n{Colors.YELLOW}Active Announcements:{Colors.END}")
            for ann in active:
                ann_type = ann.get('type', 'info')
                title = ann.get('title', 'No title')
                priority = ann.get('priority', 0)

                # Color based on type
                color = Colors.BLUE
                if ann_type == 'warning':
                    color = Colors.YELLOW
                elif ann_type == 'alert':
                    color = Colors.RED
                elif ann_type == 'success':
                    color = Colors.GREEN

                print(f"    {color}[{ann_type.upper()}]{Colors.END} {title} (priority: {priority})")
                if ann.get('message'):
                    print(f"         {ann['message'][:60]}...")

        print(f"\n{Colors.CYAN}Full Response:{Colors.END}")
        print_json(data)
        return True
    else:
        print_error(f"Announcements endpoint failed: {result['error']}")
        return False


def test_feature_check(base_url: str, feature_id: str) -> bool:
    """Test checking a specific feature."""
    print(f"\n{Colors.BOLD}Testing /features/{feature_id} endpoint...{Colors.END}")
    result = fetch_endpoint(base_url, f'/features/{feature_id}')

    if result['success']:
        data = result['data']
        enabled = data.get('enabled', False)

        if enabled:
            print_success(f"Feature '{feature_id}' is ENABLED")
        else:
            print_warning(f"Feature '{feature_id}' is DISABLED")

        print_json(data)
        return True
    else:
        print_error(f"Feature check failed: {result['error']}")
        return False


def run_all_tests(base_url: str) -> bool:
    """Run all API tests."""
    print_header("Config Service API Tester")
    print_info(f"Testing against: {base_url}")
    print_info(f"Time: {datetime.now().isoformat()}")

    results = []

    # Test health
    results.append(('Health', test_health(base_url)))

    # Test features
    results.append(('Features', test_features(base_url)))

    # Test announcements
    results.append(('Announcements', test_announcements(base_url)))

    # Summary
    print_header("Test Summary")
    all_passed = True
    for name, passed in results:
        if passed:
            print_success(f"{name}: PASSED")
        else:
            print_error(f"{name}: FAILED")
            all_passed = False

    return all_passed


def interactive_mode(base_url: str):
    """Run in interactive mode."""
    print_header("Config Service API Tester - Interactive Mode")
    print_info(f"Connected to: {base_url}")

    while True:
        print(f"\n{Colors.BOLD}Available Commands:{Colors.END}")
        print("  1. Test health endpoint")
        print("  2. Get all features")
        print("  3. Get all announcements")
        print("  4. Check specific feature")
        print("  5. Run all tests")
        print("  6. Show curl commands")
        print("  q. Quit")

        choice = input(f"\n{Colors.CYAN}Select option: {Colors.END}").strip().lower()

        if choice == '1':
            test_health(base_url)
        elif choice == '2':
            test_features(base_url)
        elif choice == '3':
            test_announcements(base_url)
        elif choice == '4':
            feature_id = input("Feature ID to check: ").strip()
            if feature_id:
                test_feature_check(base_url, feature_id)
        elif choice == '5':
            run_all_tests(base_url)
        elif choice == '6':
            print_curl_commands(base_url)
        elif choice == 'q':
            print_info("Goodbye!")
            break
        else:
            print_warning("Invalid option")


def print_curl_commands(base_url: str):
    """Print curl commands for testing."""
    print(f"\n{Colors.BOLD}Curl Commands for Testing:{Colors.END}")
    print(f"\n{Colors.CYAN}# Health check{Colors.END}")
    print(f"curl -s {base_url}/health | jq .")

    print(f"\n{Colors.CYAN}# Get all features{Colors.END}")
    print(f"curl -s {base_url}/features | jq .")

    print(f"\n{Colors.CYAN}# Get all announcements{Colors.END}")
    print(f"curl -s {base_url}/announcements | jq .")

    print(f"\n{Colors.CYAN}# Check specific feature{Colors.END}")
    print(f"curl -s {base_url}/features/dark_mode | jq .")

    print(f"\n{Colors.CYAN}# Get enabled features only{Colors.END}")
    print(f"curl -s {base_url}/features | jq '.enabled_features'")

    print(f"\n{Colors.CYAN}# Get active announcements only{Colors.END}")
    print(f"curl -s {base_url}/announcements | jq '.active_announcements'")


def main():
    parser = argparse.ArgumentParser(
        description='Test the Config Service API',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    # Interactive mode with local server
    python test_api.py --url http://localhost:50011

    # Run all tests
    python test_api.py --url http://localhost:50011 --all

    # Show curl commands
    python test_api.py --url http://localhost:50011 --curl

    # Check specific feature
    python test_api.py --url http://localhost:50011 --feature dark_mode
        """
    )
    parser.add_argument(
        '--url', '-u',
        default='http://localhost:50011',
        help='Base URL of the config service (default: http://localhost:50011)'
    )
    parser.add_argument(
        '--all', '-a',
        action='store_true',
        help='Run all tests and exit'
    )
    parser.add_argument(
        '--curl', '-c',
        action='store_true',
        help='Print curl commands and exit'
    )
    parser.add_argument(
        '--feature', '-f',
        help='Check a specific feature and exit'
    )
    parser.add_argument(
        '--health',
        action='store_true',
        help='Run health check only'
    )

    args = parser.parse_args()

    # Handle specific commands
    if args.curl:
        print_curl_commands(args.url)
        sys.exit(0)

    if args.health:
        success = test_health(args.url)
        sys.exit(0 if success else 1)

    if args.feature:
        success = test_feature_check(args.url, args.feature)
        sys.exit(0 if success else 1)

    if args.all:
        success = run_all_tests(args.url)
        sys.exit(0 if success else 1)

    # Default: interactive mode
    try:
        interactive_mode(args.url)
    except KeyboardInterrupt:
        print("\n")
        print_info("Interrupted. Goodbye!")
        sys.exit(0)


if __name__ == '__main__':
    main()

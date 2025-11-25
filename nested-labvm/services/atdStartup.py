#!/usr/bin/env python3
"""
ATD Startup Script - Python replacement for atdStartup.sh

This script performs all startup operations for the ATD environment.

Usage:
    sudo python3 /usr/local/bin/atdStartup.py

Or via the wrapper shell script:
    sudo sh /usr/local/bin/atdStartup.sh
"""

import sys
import os

# Add the services directory to path for imports
sys.path.insert(0, '/opt/atd/nested-labvm/services')

from atd_manager import ATDStartup, ATDConfig


def main():
    """Run ATD Startup"""
    print("=" * 60)
    print("  ATD Startup - Python Edition")
    print("=" * 60)
    print()

    config = ATDConfig()
    startup = ATDStartup(config)

    success = startup.run()

    if success:
        print()
        print("=" * 60)
        print("  ATD Startup completed successfully!")
        print("=" * 60)
        sys.exit(0)
    else:
        print()
        print("=" * 60)
        print("  ATD Startup FAILED!")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()

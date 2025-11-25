#!/usr/bin/env python3
"""
ATD Update Script - Python replacement for atdUpdate.sh

This script updates the ATD repository and runs startup.
It maintains the same flow as the original shell script:
    atdUpdate -> git pull -> atdStartup

Usage:
    sudo python3 /usr/local/bin/atdUpdate.py

Or via the wrapper shell script:
    sudo sh /usr/local/bin/atdUpdate.sh
"""

import sys
import os

# Add the services directory to path for imports
sys.path.insert(0, '/opt/atd/nested-labvm/services')

from atd_manager import ATDUpdate, ATDConfig


def main():
    """Run ATD Update (which internally calls ATD Startup)"""
    print("=" * 60)
    print("  ATD Update - Python Edition")
    print("=" * 60)
    print()

    config = ATDConfig()
    update = ATDUpdate(config)

    success = update.run()

    if success:
        print()
        print("=" * 60)
        print("  ATD Update completed successfully!")
        print("=" * 60)
        sys.exit(0)
    else:
        print()
        print("=" * 60)
        print("  ATD Update FAILED!")
        print("=" * 60)
        sys.exit(1)


if __name__ == '__main__':
    main()

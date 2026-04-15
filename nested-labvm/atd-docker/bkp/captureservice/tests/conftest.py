"""
Pytest fixtures for captureservice tests.
"""

import os
import sys
import pytest

# Add src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

"""
Static analysis regression tests for uilanding stability fixes.

These tests scan source code text to ensure previously-fixed crash vectors
do not regress. No code is executed — patterns are checked via regex.
"""

import os
import re
import pytest

SRC_DIR = os.path.join(os.path.dirname(__file__), '..', 'src')


def read_source(filename):
    """Return full source text for a file in the src directory."""
    path = os.path.join(SRC_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return f.read()


def read_source_lines(filename):
    """Return list of (line_number, line_text) tuples for a file in the src directory."""
    path = os.path.join(SRC_DIR, filename)
    with open(path, 'r', encoding='utf-8') as f:
        return list(enumerate(f.readlines(), start=1))


class TestNoFileHandleLeaks:
    """Ensure open() file handles are not passed directly into YAML().load()."""

    def test_uilanding_no_yaml_load_open(self):
        source = read_source('uilanding.py')
        matches = re.findall(r'YAML\(\)\.load\(open\(', source)
        assert matches == [], (
            f"Found {len(matches)} instance(s) of YAML().load(open( in uilanding.py. "
            "Use a context manager instead: with open(...) as f: yaml.safe_load(f)"
        )

    def test_topology_converter_no_yaml_load_open(self):
        source = read_source('topology_converter.py')
        matches = re.findall(r'YAML\(\)\.load\(open\(', source)
        assert matches == [], (
            f"Found {len(matches)} instance(s) of YAML().load(open( in topology_converter.py. "
            "Use a context manager instead: with open(...) as f: yaml.safe_load(f)"
        )


class TestNoCommandInjection:
    """Ensure subprocess calls do not use shell=True which enables injection attacks."""

    def test_uilanding_no_shell_true(self):
        source = read_source('uilanding.py')
        matches = re.findall(r'subprocess\.[^\n]*shell\s*=\s*True', source)
        assert matches == [], (
            f"Found {len(matches)} subprocess call(s) with shell=True in uilanding.py. "
            "Pass arguments as a list to avoid shell injection."
        )


class TestAllRequestsHaveTimeout:
    """Ensure all requests.get() and requests.post() calls include a timeout= argument."""

    def _check_timeout_in_window(self, lines, start_idx, call_text):
        """Check whether timeout= appears within the call starting at start_idx."""
        window = ''.join(line for _, line in lines[start_idx:start_idx + 4])
        # The call itself may span multiple lines; combine the starting line with next 3
        combined = call_text + window
        return 'timeout=' in combined

    def test_uilanding_requests_get_have_timeout(self):
        lines = read_source_lines('uilanding.py')
        violations = []
        for idx, (lineno, line) in enumerate(lines):
            if re.search(r'requests\.get\(', line):
                # Check the call line plus the next 3 lines (4 total) for timeout=
                window = ''.join(ln for _, ln in lines[idx:idx + 4])
                if 'timeout=' not in window:
                    violations.append(lineno)
        assert violations == [], (
            f"requests.get() calls without timeout= found at lines: {violations} in uilanding.py"
        )

    def test_uilanding_requests_post_have_timeout(self):
        lines = read_source_lines('uilanding.py')
        violations = []
        for idx, (lineno, line) in enumerate(lines):
            if re.search(r'requests\.post\(', line):
                # Check the call line plus the next 5 lines (6 total) for timeout=
                # Some multi-arg calls span multiple lines before the timeout= arg
                window = ''.join(ln for _, ln in lines[idx:idx + 6])
                if 'timeout=' not in window:
                    violations.append(lineno)
        assert violations == [], (
            f"requests.post() calls without timeout= found at lines: {violations} in uilanding.py"
        )


class TestNoBareExcept:
    """Ensure bare except: clauses are not used — they swallow all exceptions including KeyboardInterrupt."""

    def test_uilanding_no_bare_except(self):
        lines = read_source_lines('uilanding.py')
        violations = []
        for lineno, line in lines:
            stripped = line.strip()
            if stripped == 'except:':
                violations.append(lineno)
        assert violations == [], (
            f"Bare except: found at lines {violations} in uilanding.py. "
            "Use 'except Exception:' to avoid catching SystemExit and KeyboardInterrupt."
        )


class TestNoIOLoopInstance:
    """Ensure deprecated IOLoop.instance() is not used — use IOLoop.current() instead."""

    def test_uilanding_no_ioloop_instance(self):
        source = read_source('uilanding.py')
        matches = re.findall(r'IOLoop\.instance\(\)', source)
        assert matches == [], (
            f"Found {len(matches)} use(s) of IOLoop.instance() in uilanding.py. "
            "Use IOLoop.current() instead."
        )


class TestDockerClientReused:
    """Ensure Docker client is not repeatedly instantiated — only one module-level singleton."""

    def test_uilanding_docker_from_env_at_most_once(self):
        source = read_source('uilanding.py')
        matches = re.findall(r'docker\.from_env\(', source)
        assert len(matches) <= 1, (
            f"Found {len(matches)} calls to docker.from_env() in uilanding.py. "
            "Docker client should be a module-level singleton to avoid connection exhaustion."
        )


class TestTopologyConverterSafety:
    """Ensure topology_converter.py contains required concurrency and safety constructs."""

    def test_conversion_lock_exists(self):
        source = read_source('topology_converter.py')
        assert '_conversion_lock' in source, (
            "_conversion_lock not found in topology_converter.py. "
            "A threading lock is required to prevent concurrent topology conversions."
        )

    def test_threading_lock_instantiated(self):
        source = read_source('topology_converter.py')
        assert 'threading.Lock()' in source, (
            "threading.Lock() not found in topology_converter.py. "
            "The conversion lock must be initialised with threading.Lock()."
        )

    def test_process_wait_has_timeout(self):
        source = read_source('topology_converter.py')
        matches = re.findall(r'process\.wait\(timeout=', source)
        assert len(matches) >= 1, (
            "No process.wait(timeout=...) found in topology_converter.py. "
            "Subprocess waits must have a timeout to avoid hanging indefinitely."
        )

    def test_bounded_log_deque_exists(self):
        source = read_source('topology_converter.py')
        assert 'deque' in source, (
            "deque not found in topology_converter.py. "
            "Log buffers must use deque to prevent unbounded memory growth."
        )

    def test_bounded_log_maxlen_exists(self):
        source = read_source('topology_converter.py')
        assert 'maxlen=' in source, (
            "maxlen= not found in topology_converter.py. "
            "deque must have a maxlen to cap log buffer size."
        )

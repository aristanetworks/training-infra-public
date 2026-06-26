#!/usr/bin/env python3
"""
Instance Metrics Collector

Collects host and Docker container metrics, logs to Google Cloud Logging.
Runs as a systemd oneshot service every 3 minutes.
"""

import json
import logging
import os
import subprocess
import sys
import time
import atexit

try:
    import yaml
except ImportError:
    yaml = None

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    from google.cloud import logging as cloud_logging
    from google.cloud.logging_v2.handlers import CloudLoggingHandler
    from google.cloud.logging_v2.handlers.transports import SyncTransport
    CLOUD_LOGGING_AVAILABLE = True
except ImportError:
    CLOUD_LOGGING_AVAILABLE = False

ACCESS_INFO_PATH = '/etc/atd/ACCESS_INFO.yaml'

_cloud_handlers = []


def _flush_all_handlers():
    for handler in _cloud_handlers:
        try:
            if hasattr(handler, 'transport'):
                handler.transport.flush()
            handler.flush()
            handler.close()
        except Exception:
            pass
    if _cloud_handlers:
        time.sleep(0.5)


atexit.register(_flush_all_handlers)


def _read_access_info():
    if not yaml or not os.path.exists(ACCESS_INFO_PATH):
        return {}
    try:
        with open(ACCESS_INFO_PATH, 'r') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def setup_logging():
    access_info = _read_access_info()
    lab_hostname = access_info.get('name', 'unknown')
    lab_project = access_info.get('project')

    console_logger = logging.getLogger('instance-metrics')
    console_logger.setLevel(logging.INFO)
    console_logger.handlers.clear()
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
    console_logger.addHandler(console_handler)

    cloud_logger = None
    if CLOUD_LOGGING_AVAILABLE:
        try:
            client = cloud_logging.Client(project=lab_project) if lab_project else cloud_logging.Client()
            cloud_logger = client.logger('instance-metrics')
        except Exception as e:
            print(f"Cloud logging setup failed: {e}", file=sys.stderr)

    return console_logger, cloud_logger, lab_hostname


def collect_host_metrics():
    if not PSUTIL_AVAILABLE:
        return {'host_error': 'psutil not available'}

    metrics = {}
    try:
        metrics['cpu_percent'] = psutil.cpu_percent(interval=1)
    except Exception as e:
        metrics['cpu_error'] = str(e)

    try:
        mem = psutil.virtual_memory()
        metrics['memory_total_mb'] = round(mem.total / (1024 * 1024))
        metrics['memory_used_mb'] = round(mem.used / (1024 * 1024))
        metrics['memory_available_mb'] = round(mem.available / (1024 * 1024))
        metrics['memory_percent'] = mem.percent
    except Exception as e:
        metrics['memory_error'] = str(e)

    try:
        disk = psutil.disk_usage('/')
        metrics['disk_total_gb'] = round(disk.total / (1024 ** 3), 1)
        metrics['disk_used_gb'] = round(disk.used / (1024 ** 3), 1)
        metrics['disk_free_gb'] = round(disk.free / (1024 ** 3), 1)
        metrics['disk_percent'] = disk.percent
    except Exception as e:
        metrics['disk_error'] = str(e)

    try:
        net = psutil.net_io_counters()
        metrics['network_bytes_sent'] = net.bytes_sent
        metrics['network_bytes_recv'] = net.bytes_recv
    except Exception as e:
        metrics['network_error'] = str(e)

    return metrics


def collect_docker_metrics():
    fmt = '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}'
    try:
        result = subprocess.run(
            ['docker', 'stats', '--no-stream', '--format', fmt],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            return {'docker_error': result.stderr.strip(), 'container_count': 0, 'containers': {}}

        containers = {}
        for line in result.stdout.strip().split('\n'):
            if not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 4:
                containers[parts[0]] = {
                    'cpu': parts[1],
                    'mem_usage': parts[2],
                    'mem_percent': parts[3],
                }

        return {'container_count': len(containers), 'containers': containers}

    except subprocess.TimeoutExpired:
        return {'docker_error': 'timeout', 'container_count': -1, 'containers': {}}
    except Exception as e:
        return {'docker_error': str(e), 'container_count': -1, 'containers': {}}


def main():
    console_logger, cloud_logger, lab_hostname = setup_logging()

    host_metrics = collect_host_metrics()
    docker_metrics = collect_docker_metrics()

    payload = {
        'event_type': 'instance_metrics',
        'hostname': lab_hostname,
    }
    payload.update(host_metrics)
    payload.update(docker_metrics)

    cpu = host_metrics.get('cpu_percent', '?')
    mem = host_metrics.get('memory_percent', '?')
    disk = host_metrics.get('disk_percent', '?')
    containers = docker_metrics.get('container_count', '?')

    summary = f"CPU={cpu}% RAM={mem}% Disk={disk}% Containers={containers}"
    console_logger.info(summary)

    if cloud_logger:
        try:
            labels = {
                'lab_hostname': lab_hostname,
                'service': 'instance-metrics',
                'event_type': 'instance_metrics',
            }
            cloud_logger.log_text(
                json.dumps(payload),
                severity='INFO',
                labels=labels,
            )
        except Exception as e:
            console_logger.warning(f"Cloud Logging failed: {e}")

    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        sys.exit(1)

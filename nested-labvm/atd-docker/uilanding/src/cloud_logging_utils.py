#!/usr/bin/env python3
"""
Cloud Logging Utility for ATD Container Scripts
Provides centralized cloud logging setup for ATD container scripts
"""

import logging
import os
import sys
import yaml
import atexit
import time

try:
    from google.cloud import logging as cloud_logging
    from google.cloud.logging_v2.handlers import CloudLoggingHandler
    from google.cloud.logging_v2.handlers.transports import SyncTransport
    CLOUD_LOGGING_AVAILABLE = True
except ImportError:
    CLOUD_LOGGING_AVAILABLE = False

# Global list to track cloud handlers for flushing on exit
_cloud_handlers = []


def get_lab_hostname():
    """
    Get the lab hostname from ACCESS_INFO.yaml

    Returns:
        str: Lab hostname or 'unknown' if not found
    """
    try:
        access_info_path = '/etc/atd/ACCESS_INFO.yaml'
        if os.path.exists(access_info_path):
            with open(access_info_path, 'r') as f:
                access_info = yaml.safe_load(f)
            return access_info.get('name', 'unknown')
        return 'unknown'
    except Exception:
        return 'unknown'


def get_lab_project():
    """
    Get the GCP project from ACCESS_INFO.yaml

    Returns:
        str: GCP project or None if not found
    """
    try:
        access_info_path = '/etc/atd/ACCESS_INFO.yaml'
        if os.path.exists(access_info_path):
            with open(access_info_path, 'r') as f:
                access_info = yaml.safe_load(f)
            return access_info.get('project')
        return None
    except Exception:
        return None


def _flush_all_handlers():
    """
    Flush all cloud logging handlers to ensure logs are sent before exit
    This is registered with atexit to run on script termination
    """
    for handler in _cloud_handlers:
        try:
            # Flush the handler's transport
            if hasattr(handler, 'transport'):
                handler.transport.flush()
            # Force flush by calling handler's flush method
            handler.flush()
            # Explicitly close the handler to send remaining logs
            handler.close()
        except Exception:
            # Silently ignore flush errors on exit
            pass

    # Give background threads time to complete
    if _cloud_handlers:
        time.sleep(1.0)


# Register the flush function to run on exit
atexit.register(_flush_all_handlers)


def flush_cloud_logs():
    """
    Manually flush all cloud logging handlers
    Call this before script exit to ensure all logs are sent
    """
    _flush_all_handlers()


def setup_cloud_logging(service_name, default_labels=None):
    """
    Setup cloud logging with fallback to standard logging

    Args:
        service_name: Name of the service/script (e.g., 'login', 'uploadExam')
        default_labels: Additional default labels to add to all log entries

    Returns:
        logging.Logger: Configured logger instance
    """
    logger = logging.getLogger(service_name)
    logger.setLevel(logging.INFO)

    # Clear any existing handlers
    logger.handlers.clear()

    # Get lab information
    lab_hostname = get_lab_hostname()
    lab_project = get_lab_project()

    # Base labels for all log entries
    base_labels = {
        'lab_hostname': lab_hostname,
        'service': service_name,
        'environment': 'uilanding-container'
    }

    # Merge with any additional default labels
    if default_labels:
        base_labels.update(default_labels)

    if CLOUD_LOGGING_AVAILABLE:
        try:
            # Initialize Cloud Logging client
            client = cloud_logging.Client(project=lab_project) if lab_project else cloud_logging.Client()

            # Create cloud logging handler with synchronous transport
            # This ensures logs are sent immediately without background threads
            cloud_handler = CloudLoggingHandler(
                client,
                name=service_name,
                labels=base_labels,
                transport=SyncTransport
            )
            cloud_handler.setLevel(logging.INFO)

            # Track handler for flushing on exit
            _cloud_handlers.append(cloud_handler)

            # Add handler to logger
            logger.addHandler(cloud_handler)

            # Also add console handler for local visibility
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.INFO)
            console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(console_formatter)
            logger.addHandler(console_handler)

            logger.info(f"Cloud logging initialized for {service_name}", extra={'labels': {'status': 'initialized'}})
            return logger

        except Exception as e:
            # Fall back to standard logging if cloud logging fails
            print(f"Warning: Cloud logging setup failed: {e}. Falling back to standard logging.", file=sys.stderr)

    # Fallback to standard logging
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    if not CLOUD_LOGGING_AVAILABLE:
        logger.warning(f"google-cloud-logging not available for {service_name}. Using standard logging.")

    return logger


def log_with_labels(logger, level, message, labels=None):
    """
    Helper function to log messages with additional labels

    Args:
        logger: Logger instance
        level: Log level (INFO, WARNING, ERROR, etc.)
        message: Log message
        labels: Additional labels for this specific log entry
    """
    extra_data = {}
    if labels:
        extra_data['labels'] = labels

    log_method = getattr(logger, level.lower(), logger.info)
    if extra_data:
        log_method(message, extra=extra_data)
    else:
        log_method(message)


def log_operation_start(logger, operation, **kwargs):
    """
    Log the start of an operation with standard labels

    Args:
        logger: Logger instance
        operation: Operation name
        **kwargs: Additional fields to log as labels (will be converted to strings)
    """
    labels = {
        'operation': operation,
        'phase': 'start',
        'status': 'in_progress'
    }
    # Convert all kwargs values to strings for Cloud Logging compatibility
    labels.update({k: str(v) for k, v in kwargs.items()})
    log_with_labels(logger, 'INFO', f"Starting operation: {operation}", labels)


def log_operation_success(logger, operation, **kwargs):
    """
    Log the successful completion of an operation

    Args:
        logger: Logger instance
        operation: Operation name
        **kwargs: Additional fields to log as labels (will be converted to strings)
    """
    labels = {
        'operation': operation,
        'phase': 'complete',
        'status': 'success'
    }
    # Convert all kwargs values to strings for Cloud Logging compatibility
    labels.update({k: str(v) for k, v in kwargs.items()})
    log_with_labels(logger, 'INFO', f"Operation completed successfully: {operation}", labels)


def log_operation_error(logger, operation, error_msg, **kwargs):
    """
    Log an operation failure

    Args:
        logger: Logger instance
        operation: Operation name
        error_msg: Error message
        **kwargs: Additional fields to log as labels (will be converted to strings)
    """
    labels = {
        'operation': operation,
        'phase': 'complete',
        'status': 'error'
    }
    # Convert all kwargs values to strings for Cloud Logging compatibility
    labels.update({k: str(v) for k, v in kwargs.items()})
    log_with_labels(logger, 'ERROR', f"Operation failed: {operation} - {error_msg}", labels)

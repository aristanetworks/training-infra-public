#!/usr/bin/env python3

"""
ATD Unit Test Orchestrator
Runs all unit tests and provides a consolidated test report
"""

import sys
import logging
import yaml
import os
import time
from datetime import datetime

# Import test modules
import test_atd_config
import test_cvp_ssh
import test_web
import test_cvp_inventory
import test_node_ssh

# Log directory
LOG_DIR = '/etc/atd/logs'

# Global logger (will be configured in main)
logger = None


def setup_logging():
    """
    Setup logging to both console and file with epoch timestamp
    Configures the root logger so all modules use the same handlers

    Returns:
        tuple: (logger, log_filepath)
    """
    # Create log directory if it doesn't exist
    os.makedirs(LOG_DIR, exist_ok=True)

    # Generate log filename with epoch timestamp
    epoch_timestamp = int(time.time())
    log_filename = f"atd_unit_test_{epoch_timestamp}.log"
    log_filepath = os.path.join(LOG_DIR, log_filename)

    # Get the root logger (this affects all loggers)
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Clear any existing handlers
    root_logger.handlers.clear()

    # Create formatters
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(log_filepath)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    # Add handlers to root logger (this will affect all loggers in all modules)
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)

    # Get logger for this module
    log = logging.getLogger(__name__)

    return log, log_filepath


def print_header(title):
    """Print a formatted header"""
    logger.info("\n" + "="*80)
    logger.info(f"  {title}")
    logger.info("="*80)


def run_test(test_name, test_function):
    """
    Run a single test and capture the result

    Args:
        test_name: Name of the test
        test_function: Test function to execute

    Returns:
        tuple: (test_name, exit_code, duration)
    """
    print_header(f"Running: {test_name}")

    start_time = datetime.now()

    try:
        exit_code = test_function()
        duration = (datetime.now() - start_time).total_seconds()

        if exit_code == 0:
            logger.info(f"✓ {test_name} PASSED")
        else:
            logger.error(f"✗ {test_name} FAILED")

        return (test_name, exit_code, duration)

    except Exception as e:
        duration = (datetime.now() - start_time).total_seconds()
        logger.error(f"✗ {test_name} FAILED with exception: {str(e)}")
        return (test_name, 1, duration)


def get_customer_details():
    """
    Load customer details from ACCESS_INFO.yaml

    Returns:
        dict: Customer details or None if not available
    """
    try:
        access_info_path = '/etc/atd/ACCESS_INFO.yaml'

        if not os.path.exists(access_info_path):
            return None

        with open(access_info_path, 'r') as f:
            access_info = yaml.safe_load(f)

        customer_details = access_info.get('customer_details')

        if not customer_details or not isinstance(customer_details, dict):
            return None

        return {
            'full_name': customer_details.get('exam_taker_full_name', 'N/A'),
            'email': customer_details.get('exam_taker_email', 'N/A'),
            'exam_id': customer_details.get('external_exam_id', 'N/A'),
            'attempt_id': customer_details.get('exam_taker_attempt_id', 'N/A')
        }
    except Exception:
        return None


def print_test_report(test_results, customer_details=None):
    """
    Print consolidated test report

    Args:
        test_results: List of tuples (test_name, exit_code, duration)
        customer_details: Optional dict with customer information
    """
    print_header("ATD Unit Test Report")

    # Display customer information if available
    if customer_details:
        logger.info("\nCustomer Information:")
        logger.info(f"  Full Name: {customer_details['full_name']}")
        logger.info(f"  Email: {customer_details['email']}")
        logger.info(f"  Exam ID: {customer_details['exam_id']}")
        logger.info(f"  Attempt ID: {customer_details['attempt_id']}")

    total_tests = len(test_results)
    passed_tests = sum(1 for _, exit_code, _ in test_results if exit_code == 0)
    failed_tests = total_tests - passed_tests
    total_duration = sum(duration for _, _, duration in test_results)

    logger.info("\n" + "-"*80)
    logger.info("Test Results:")
    logger.info("-"*80)

    for test_name, exit_code, duration in test_results:
        status = "✓ PASS" if exit_code == 0 else "✗ FAIL"
        logger.info(f"{status:10} | {test_name:40} | {duration:.2f}s")

    logger.info("-"*80)
    logger.info(f"Total Tests:    {total_tests}")
    logger.info(f"Passed:         {passed_tests}")
    logger.info(f"Failed:         {failed_tests}")
    logger.info(f"Success Rate:   {(passed_tests/total_tests*100):.1f}%")
    logger.info(f"Total Duration: {total_duration:.2f}s")
    logger.info("="*80)

    if failed_tests > 0:
        logger.error("\n⚠ Some tests FAILED. Please review the output above.")
        logger.info("\nFailed tests:")
        for test_name, exit_code, _ in test_results:
            if exit_code != 0:
                logger.error(f"  - {test_name}")
    else:
        logger.info("\n✓ All tests PASSED!")

    logger.info("="*80)


def main():
    """Main orchestrator function"""
    global logger

    # Setup logging to console and file
    logger, log_filepath = setup_logging()

    logger.info("="*80)
    logger.info("ATD Unit Test Suite")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Log file: {log_filepath}")
    logger.info("="*80)

    # Load customer details
    customer_details = get_customer_details()

    # Define test suite
    test_suite = [
        ("ATD Configuration Validation", test_atd_config.main),
        ("CVP SSH Connectivity Test", test_cvp_ssh.main),
        ("Web Service Test", test_web.main),
        ("CVP Inventory Test", test_cvp_inventory.main),
        ("Node SSH Connectivity Test", test_node_ssh.main),
    ]

    # Run all tests
    test_results = []
    for test_name, test_func in test_suite:
        result = run_test(test_name, test_func)
        test_results.append(result)

    # Print consolidated report with customer details
    print_test_report(test_results, customer_details)

    # Exit with appropriate code
    all_passed = all(exit_code == 0 for _, exit_code, _ in test_results)
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())

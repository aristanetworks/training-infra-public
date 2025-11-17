#!/usr/bin/env python3

"""
Web Service Test
Tests HTTP/HTTPS connectivity to web services
"""

import requests
import logging
import urllib3
import yaml
import os

# Disable SSL warnings for self-signed certificates
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration file path
CONFIG_PATH = '/etc/atd/UNIT_TEST_CONFIG.yaml'

# Global config
config = None

# Web service configuration (will be loaded from config)
LABGUIDES_URL = None
EXPECTED_STATUS_CODE = None
REQUEST_TIMEOUT = None


def load_config(file_path=CONFIG_PATH):
    """
    Load configuration from UNIT_TEST_CONFIG.yaml

    Args:
        file_path: Path to config file

    Returns:
        dict: Configuration data or None if failed
    """
    try:
        if not os.path.exists(file_path):
            logger.error(f"Config file not found: {file_path}")
            return None

        with open(file_path, 'r') as f:
            cfg = yaml.safe_load(f)

        logger.info(f"✓ Configuration loaded from {file_path}")
        return cfg

    except Exception as e:
        logger.error(f"Failed to load config: {str(e)}")
        return None


def test_labguides_http_response(url=LABGUIDES_URL, expected_status=EXPECTED_STATUS_CODE, timeout=REQUEST_TIMEOUT):
    """
    Test HTTP response from labguides URL

    Args:
        url: URL to test
        expected_status: Expected HTTP status code (default: 200)
        timeout: Request timeout in seconds

    Returns:
        tuple: (success: bool, status_code: int or None)
    """
    try:
        logger.info(f"Testing HTTP response for: {url}")
        logger.info(f"Expected status code: {expected_status}")

        # Make HTTPS request with SSL verification disabled (equivalent to curl -k)
        # and follow redirects (equivalent to curl -L)
        response = requests.get(
            url,
            verify=False,  # -k flag: ignore SSL certificate warnings
            allow_redirects=True,  # -L flag: follow redirects
            timeout=timeout
        )

        status_code = response.status_code

        logger.info(f"Received status code: {status_code}")

        if status_code == expected_status:
            logger.info(f" Status code matches expected: {status_code}")
            logger.info(f"Response size: {len(response.content)} bytes")
            return True, status_code
        else:
            logger.error(f"Status code mismatch: expected {expected_status}, got {status_code}")
            return False, status_code

    except requests.exceptions.Timeout:
        logger.error(f"Request timed out after {timeout} seconds")
        return False, None

    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error: {str(e)}")
        return False, None

    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {str(e)}")
        return False, None

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return False, None


def main():
    """Main function for web service testing"""
    global config, LABGUIDES_URL, EXPECTED_STATUS_CODE, REQUEST_TIMEOUT

    # Load configuration
    config = load_config()
    if not config:
        logger.error("Failed to load configuration. Exiting.")
        return 1

    # Extract web configuration
    web_config = config.get('web', {})
    LABGUIDES_URL = web_config.get('labguides_url', 'https://192.168.0.1/labguides/index.html')
    EXPECTED_STATUS_CODE = web_config.get('expected_status_code', 200)
    REQUEST_TIMEOUT = web_config.get('request_timeout', 10)

    logger.info("="*60)
    logger.info("Starting Web Service Test")
    logger.info("="*60)
    logger.info(f"Target URL: {LABGUIDES_URL}")
    logger.info("="*60)

    # Test labguides HTTP response
    logger.info("\n" + "="*60)
    logger.info("Test: Labguides HTTP Response")
    logger.info("="*60)
    success, status_code = test_labguides_http_response(LABGUIDES_URL, EXPECTED_STATUS_CODE, REQUEST_TIMEOUT)

    # Summary
    logger.info("\n" + "="*60)
    logger.info("Test Summary")
    logger.info("="*60)
    logger.info(f"URL: {LABGUIDES_URL}")
    logger.info(f"Expected Status: {EXPECTED_STATUS_CODE}")
    logger.info(f"Actual Status: {status_code if status_code else 'N/A'}")
    logger.info(f"Result: {'PASS' if success else 'FAIL'}")
    logger.info("="*60)

    # Exit with appropriate code
    if success:
        logger.info("Web service test PASSED")
        return 0
    else:
        logger.error("Web service test FAILED")
        return 1


if __name__ == "__main__":
    exit(main())

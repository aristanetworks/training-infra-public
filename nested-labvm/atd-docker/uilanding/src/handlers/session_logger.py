"""
Log lab session metadata to Cloud Logging at startup.

Called once when uilanding starts. Captures lab identity, user details,
course info, and topology config for analytics and troubleshooting.
"""

from utils import safe_log


def log_lab_session(host_yaml):
    """Log lab session details from ACCESS_INFO.yaml."""
    try:
        cust = host_yaml.get('customer_details') or {}
        login = (host_yaml.get('login_info') or {}).get('jump_host') or {}
        modules = host_yaml.get('labguides_modules') or []

        safe_log('info', 'Lab session started',
            event='session',
            action='lab_session_start',
            lab_name=host_yaml.get('name') or '',
            topology=host_yaml.get('topology') or '',
            course_name=cust.get('course_name') or '',
            lab_type=cust.get('lab_type') or 'LAB',
            user_email=cust.get('exam_taker_email') or '',
            user_name=cust.get('exam_taker_full_name') or '',
            labguides_modules=','.join(str(m) for m in modules) if isinstance(modules, list) else str(modules),
            login_user=login.get('user') or '',
            eos_type=host_yaml.get('eos_type') or 'veos',
            zone=host_yaml.get('zone') or '',
        )
    except Exception as e:
        safe_log('warning', f'Failed to log lab session metadata: {e}',
            event='session', action='lab_session_start_failed')

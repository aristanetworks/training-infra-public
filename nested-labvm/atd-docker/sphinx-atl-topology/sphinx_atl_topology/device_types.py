"""Device type icon mapping for Sphinx topology diagrams."""

# Maps device type -> icon filename
DEVICE_TYPE_ICONS = {
    'spine': 'spine.png',
    'leaf': 'spine.png',
    'borderleaf': 'leaf.png',
    'memleaf': 'leaf.png',
    'pe': 'spine.png',
    'ce': 'spine.png',
    'p': 'spine.png',
    'router': 'router.png',
    'core': 'router.png',
    'dci': 'router.png',
    'rr': 'router.png',
    'gw': 'router.png',
    'internet': 'router.png',
    'isp': 'router.png',
    'oob': 'router.png',
    'firewall': 'router.png',
    'host': 'hosts.png',
    'linux_host': 'hosts.png',
    'customer': 'router.png',
    'velo_orchestrator': 'router.png',
    'velo_gateway': 'router.png',
    'velo_edge': 'router.png',
    'other': 'router.png',
}

# Classification patterns (simplified version of uilanding device_types.py)
_PATTERNS = [
    ('borderleaf', ['borderleaf'], ['BL']),
    ('memleaf', ['memleaf'], []),
    ('internet', ['internet'], []),
    ('isp', ['isp'], []),
    ('core', ['core'], []),
    ('dci', ['dci'], []),
    ('oob', ['oob'], []),
    ('spine', ['spine'], []),
    ('leaf', ['leaf'], []),
    ('host', ['host'], []),
    ('router', ['router'], []),
]

_STARTSWITH = [
    ('pe', 'PE'),
    ('ce', 'CE'),
]


def classify_device(name):
    """Classify device type from name. Simplified version of DeviceTypeConfig.classify_device()."""
    if not name:
        return 'other'

    # Custom matchers
    if name == 'RR' or (name.startswith('RR') and len(name) > 2 and name[2].isdigit()):
        return 'rr'
    if name.startswith('P') and len(name) > 1 and name[1].isdigit():
        return 'p'
    if name.startswith('GW') and len(name) > 2 and name[2].isdigit():
        return 'gw'
    if len(name) > 1 and name[0] in ('A', 'B', 'C', 'D') and name[1].isdigit():
        return 'customer'

    # Startswith patterns (case-sensitive)
    for dtype, prefix in _STARTSWITH:
        if name.startswith(prefix):
            return dtype

    # Contains patterns (case-insensitive)
    name_lower = name.lower()
    for dtype, patterns, startswith_pats in _PATTERNS:
        for sw in startswith_pats:
            if name.startswith(sw):
                return dtype
        for pat in patterns:
            if pat in name_lower:
                return dtype

    return 'other'

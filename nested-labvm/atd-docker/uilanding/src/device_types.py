"""
Device Type Configuration Module

Single source of truth for device type metadata used by both
topology rendering and device grouping across the ATD platform.
"""


class DeviceTypeConfig:
    """
    Centralized device type classification and metadata.
    Used by TopologyAPIHandler, DevicesAPIHandler, and frontend.
    """

    # Device types with full metadata
    # priority: lower = checked first (for overlapping patterns like borderleaf/leaf)
    # tier: vertical position in topology (0=top, 9=bottom)
    DEVICE_TYPES = {
        'internet': {
            'tier': 0,
            'priority': 10,
            'label': 'Internet',
            'group_name': 'ISP',
            'color': '#e30909',
            'shape': 'star',
            'patterns': ['internet'],
        },
        'isp': {
            'tier': 1,
            'priority': 11,
            'label': 'ISP',
            'group_name': 'ISP',
            'color': '#e30909',
            'shape': 'star',
            'patterns': ['isp'],
        },
        'rr': {
            'tier': 1,
            'priority': 12,
            'label': 'Route Reflectors',
            'group_name': 'Route Reflectors',
            'color': '#008b8b',
            'shape': 'star',
            'patterns': [],  # Custom matcher
        },
        'core': {
            'tier': 2,
            'priority': 20,
            'label': 'Core',
            'group_name': 'Core',
            'color': '#071c35',
            'shape': 'triangle',
            'patterns': ['core'],
        },
        'dci': {
            'tier': 2,
            'priority': 21,
            'label': 'DCI',
            'group_name': 'Core',
            'color': '#051431',
            'shape': 'octagon',
            'patterns': ['dci'],
        },
        'p': {
            'tier': 2,
            'priority': 22,
            'label': 'P Routers',
            'group_name': 'P Routers',
            'color': '#6b7cc9',
            'shape': 'diamond',
            'patterns': [],  # Custom matcher
        },
        'borderleaf': {
            'tier': 3,
            'priority': 1,  # Check before 'leaf'
            'label': 'Borderleafs',
            'group_name': 'Borderleaf',
            'color': '#fbb500',
            'shape': 'hexagon',
            'patterns': ['borderleaf'],
            'startswith_patterns': ['BL'],
        },
        'pe': {
            'tier': 3,
            'priority': 30,
            'label': 'PE Routers',
            'group_name': 'PE Routers',
            'color': '#4c5cae',
            'shape': 'diamond',
            'patterns': [],
            'startswith_patterns': ['PE'],
        },
        'ce': {
            'tier': 3,
            'priority': 31,
            'label': 'CE Routers',
            'group_name': 'CE Routers',
            'color': '#4c5cae',
            'shape': 'round-rectangle',
            'patterns': [],
            'startswith_patterns': ['CE'],
        },
        'gw': {
            'tier': 3,
            'priority': 32,
            'label': 'WAN Gateways',
            'group_name': 'WAN Gateways',
            'color': '#d4a400',
            'shape': 'pentagon',
            'patterns': [],  # Custom matcher
        },
        'router': {
            'tier': 4,
            'priority': 40,
            'label': 'Routers',
            'group_name': 'Router',
            'color': '#8b4513',
            'shape': 'diamond',
            'patterns': ['router'],
        },
        'spine': {
            'tier': 5,
            'priority': 50,
            'label': 'Spines',
            'group_name': 'Spine',
            'color': '#4c5cae',
            'shape': 'diamond',
            'patterns': ['spine'],
        },
        'leaf': {
            'tier': 6,
            'priority': 60,
            'label': 'Leafs',
            'group_name': 'Leaf',
            'color': '#20b2aa',
            'shape': 'round-rectangle',
            'patterns': ['leaf'],
        },
        'memleaf': {
            'tier': 7,
            'priority': 2,  # Check before 'leaf'
            'label': 'Member Leafs',
            'group_name': 'Memleaf',
            'color': '#32cd32',
            'shape': 'round-rectangle',
            'patterns': ['memleaf'],
        },
        'host': {
            'tier': 8,
            'priority': 80,
            'label': 'Hosts',
            'group_name': 'Host',
            'color': '#dae0fe',
            'shape': 'ellipse',
            'patterns': ['host'],
        },
        'customer': {
            'tier': 8,
            'priority': 81,
            'label': 'Customer',
            'group_name': 'Customer',
            'color': '#20b2aa',
            'shape': 'round-rectangle',
            'patterns': [],  # Custom matcher
        },
        'oob': {
            'tier': 8,
            'priority': 82,
            'label': 'OOB',
            'group_name': 'Other',
            'color': '#808080',
            'shape': 'round-rectangle',
            'patterns': ['oob'],
        },
        'other': {
            'tier': 9,
            'priority': 99,
            'label': 'Other',
            'group_name': 'Other',
            'color': '#666666',
            'shape': 'rectangle',
            'patterns': [],
        }
    }

    # Cached sorted types for classification
    _sorted_types = None

    @classmethod
    def _get_sorted_types(cls):
        """Get device types sorted by priority for classification."""
        if cls._sorted_types is None:
            cls._sorted_types = sorted(
                cls.DEVICE_TYPES.items(),
                key=lambda x: x[1].get('priority', 99)
            )
        return cls._sorted_types

    @classmethod
    def _custom_match(cls, device_type, device_name):
        """
        Custom matchers for device types that need special logic.
        Returns True if device matches, False otherwise.
        """
        if device_type == 'rr':
            # Route Reflector: RR or RR1, RR2, etc.
            return (device_name == 'RR' or
                    (device_name.startswith('RR') and
                     len(device_name) > 2 and
                     device_name[2].isdigit()))

        elif device_type == 'p':
            # P routers: P1, P2, etc. but not PE
            return (device_name.startswith('P') and
                    len(device_name) > 1 and
                    device_name[1].isdigit())

        elif device_type == 'gw':
            # WAN Gateways: GW11, GW12, GW21, etc.
            return (device_name.startswith('GW') and
                    len(device_name) > 2 and
                    device_name[2].isdigit())

        elif device_type == 'customer':
            # Customer devices: A1, A2, B1, B2, C1, C2, D1, D2, etc.
            return (len(device_name) > 1 and
                    device_name[0] in ('A', 'B', 'C', 'D') and
                    device_name[1].isdigit())

        return False

    @classmethod
    def classify_device(cls, device_name):
        """
        Classify a device based on its name.
        Returns device type string (e.g., 'spine', 'leaf').
        """
        if not device_name:
            return 'other'

        name_lower = device_name.lower()

        for device_type, config in cls._get_sorted_types():
            # Try custom matcher first
            if cls._custom_match(device_type, device_name):
                return device_type

            # Try startswith patterns (case-sensitive for uppercase patterns)
            startswith_patterns = config.get('startswith_patterns', [])
            for pattern in startswith_patterns:
                if device_name.startswith(pattern):
                    return device_type

            # Try contains patterns (case-insensitive)
            for pattern in config.get('patterns', []):
                pattern_lower = pattern.lower()
                if pattern_lower in name_lower:
                    return device_type

        return 'other'

    @classmethod
    def get_tier(cls, device_type):
        """Get tier number for a device type."""
        return cls.DEVICE_TYPES.get(device_type, {}).get('tier', 9)

    @classmethod
    def get_tiers_dict(cls):
        """Get dict of device_type -> tier for all types."""
        return {dt: config['tier'] for dt, config in cls.DEVICE_TYPES.items()}

    @classmethod
    def get_group_name(cls, device_type):
        """Get display group name for a device type."""
        return cls.DEVICE_TYPES.get(device_type, {}).get('group_name', 'Other')

    @classmethod
    def get_color(cls, device_type):
        """Get color for a device type."""
        return cls.DEVICE_TYPES.get(device_type, {}).get('color', '#666666')

    @classmethod
    def get_label(cls, device_type):
        """Get display label for a device type."""
        return cls.DEVICE_TYPES.get(device_type, {}).get('label', 'Other')

    @classmethod
    def get_shape(cls, device_type):
        """Get shape for a device type."""
        return cls.DEVICE_TYPES.get(device_type, {}).get('shape', 'rectangle')

    @classmethod
    def get_metadata(cls, device_type):
        """Get all metadata for a device type."""
        return cls.DEVICE_TYPES.get(device_type, cls.DEVICE_TYPES['other']).copy()

    @classmethod
    def get_all_group_names(cls):
        """Get ordered list of unique group names for terminal page."""
        seen = set()
        groups = []
        # Maintain order by tier
        for device_type, config in sorted(cls.DEVICE_TYPES.items(),
                                          key=lambda x: x[1]['tier']):
            group_name = config['group_name']
            if group_name not in seen:
                seen.add(group_name)
                groups.append(group_name)
        return groups

    @classmethod
    def export_for_frontend(cls):
        """Export device type config for frontend JavaScript."""
        frontend_config = {}
        for device_type, config in cls.DEVICE_TYPES.items():
            frontend_config[device_type] = {
                'label': config['label'],
                'color': config['color'],
                'shape': config['shape'],
                'tier': config['tier'],
                'group_name': config['group_name']
            }
        return frontend_config

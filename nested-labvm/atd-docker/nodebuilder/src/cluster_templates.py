"""
Cluster Templates for Nodebuilder Service

Pre-configured groups of nodes with internal and external connections.
Clusters allow users to quickly add common topology patterns:
- Internet Cluster: 2 ISP routers with external connection to border devices
- MPLS Core: 3 P-routers in a triangle mesh
- Remote Site: Dual redundant leaf switches

Usage:
    from cluster_templates import get_cluster_templates, get_template_by_id

    templates = get_cluster_templates()
    internet = get_template_by_id('internet')
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger('nodebuilder')


@dataclass
class NodeTemplate:
    """Template for a single node within a cluster"""
    name_suffix: str
    description: str = ''

    def get_full_name(self, prefix: str = '') -> str:
        """Generate full node name with optional prefix"""
        if prefix:
            return f"{prefix}_{self.name_suffix}"
        return self.name_suffix


@dataclass
class InternalConnection:
    """Connection between two nodes within the same cluster"""
    from_node: str  # name_suffix of source node
    to_node: str    # name_suffix of target node


@dataclass
class ExternalConnectionSlot:
    """Slot for an external connection from a cluster node to an existing device"""
    from_node: str      # name_suffix of the node that has this connection
    description: str    # User-friendly description for UI
    required: bool = True  # Whether this connection must be configured


@dataclass
class ClusterTemplate:
    """
    Template defining a cluster of nodes with their interconnections.

    Clusters are pre-built topology patterns that users can add to their labs.
    Each cluster defines:
    - A set of nodes to create
    - Internal connections between those nodes
    - External connection slots for connecting to existing topology
    """
    id: str
    display_name: str
    description: str
    nodes: List[NodeTemplate]
    internal_connections: List[InternalConnection] = field(default_factory=list)
    external_connections: List[ExternalConnectionSlot] = field(default_factory=list)
    default_impairments: Dict = field(default_factory=dict)

    def get_required_ip_count(self) -> int:
        """Return number of IPs needed for this cluster"""
        return len(self.nodes)

    def get_node_names(self, prefix: str = '') -> List[str]:
        """Get list of full node names with optional prefix"""
        return [node.get_full_name(prefix) for node in self.nodes]

    def to_dict(self) -> Dict:
        """Convert to dictionary for API response"""
        return {
            'id': self.id,
            'display_name': self.display_name,
            'description': self.description,
            'node_count': len(self.nodes),
            'required_ips': self.get_required_ip_count(),
            'nodes': [
                {'name_suffix': n.name_suffix, 'description': n.description}
                for n in self.nodes
            ],
            'internal_connections': [
                {'from': c.from_node, 'to': c.to_node}
                for c in self.internal_connections
            ],
            'external_connections': [
                {
                    'from_node': c.from_node,
                    'description': c.description,
                    'required': c.required
                }
                for c in self.external_connections
            ],
            'default_impairments': self.default_impairments
        }


# ===========================================
# Built-in Cluster Templates
# ===========================================

CLUSTER_TEMPLATES: Dict[str, ClusterTemplate] = {
    'internet': ClusterTemplate(
        id='internet',
        display_name='Internet Cluster',
        description='Simulated Internet with 2 ISP routers. Use for testing external connectivity, BGP peering, or WAN scenarios.',
        nodes=[
            NodeTemplate(name_suffix='isp1', description='ISP Router 1'),
            NodeTemplate(name_suffix='isp2', description='ISP Router 2'),
        ],
        internal_connections=[
            InternalConnection(from_node='isp1', to_node='isp2'),
        ],
        external_connections=[
            ExternalConnectionSlot(
                from_node='isp1',
                description='Connect ISP1 to border/edge device',
                required=True
            ),
            ExternalConnectionSlot(
                from_node='isp2',
                description='Connect ISP2 to border/edge device (redundancy)',
                required=False
            ),
        ],
        default_impairments={
            'latency_ms': 20,
            'loss_percent': 0.1
        }
    ),

    'mpls_core': ClusterTemplate(
        id='mpls_core',
        display_name='MPLS Core',
        description='3-node P-router triangle for MPLS transport. Provides redundant paths and supports segment routing or LDP.',
        nodes=[
            NodeTemplate(name_suffix='p1', description='P-Router 1'),
            NodeTemplate(name_suffix='p2', description='P-Router 2'),
            NodeTemplate(name_suffix='p3', description='P-Router 3'),
        ],
        internal_connections=[
            # Full mesh triangle
            InternalConnection(from_node='p1', to_node='p2'),
            InternalConnection(from_node='p2', to_node='p3'),
            InternalConnection(from_node='p3', to_node='p1'),
        ],
        external_connections=[
            ExternalConnectionSlot(
                from_node='p1',
                description='Connect P1 to PE router',
                required=True
            ),
            ExternalConnectionSlot(
                from_node='p2',
                description='Connect P2 to PE router',
                required=True
            ),
            ExternalConnectionSlot(
                from_node='p3',
                description='Connect P3 to PE router (optional)',
                required=False
            ),
        ]
    ),

    'remote_site': ClusterTemplate(
        id='remote_site',
        display_name='Remote Site',
        description='Dual-homed remote site with redundant leaf switches. Simulates a branch office or remote datacenter.',
        nodes=[
            NodeTemplate(name_suffix='leaf1', description='Site Leaf 1'),
            NodeTemplate(name_suffix='leaf2', description='Site Leaf 2'),
        ],
        internal_connections=[
            InternalConnection(from_node='leaf1', to_node='leaf2'),
        ],
        external_connections=[
            ExternalConnectionSlot(
                from_node='leaf1',
                description='WAN uplink from Leaf1',
                required=True
            ),
            ExternalConnectionSlot(
                from_node='leaf2',
                description='WAN uplink from Leaf2 (redundancy)',
                required=False
            ),
        ],
        default_impairments={
            'latency_ms': 50,
            'jitter_ms': 5
        }
    ),

    'wan_router_pair': ClusterTemplate(
        id='wan_router_pair',
        display_name='WAN Router Pair',
        description='Pair of WAN edge routers for site-to-site connectivity testing.',
        nodes=[
            NodeTemplate(name_suffix='wan1', description='WAN Router 1'),
            NodeTemplate(name_suffix='wan2', description='WAN Router 2'),
        ],
        internal_connections=[
            InternalConnection(from_node='wan1', to_node='wan2'),
        ],
        external_connections=[
            ExternalConnectionSlot(
                from_node='wan1',
                description='Connect WAN1 to datacenter',
                required=True
            ),
            ExternalConnectionSlot(
                from_node='wan2',
                description='Connect WAN2 to datacenter',
                required=False
            ),
        ],
        default_impairments={
            'latency_ms': 30
        }
    ),
}


def get_cluster_templates() -> List[Dict]:
    """
    Get all available cluster templates for API response.

    Returns:
        List of template dictionaries
    """
    return [template.to_dict() for template in CLUSTER_TEMPLATES.values()]


def get_template_by_id(template_id: str) -> Optional[ClusterTemplate]:
    """
    Get a specific cluster template by ID.

    Args:
        template_id: Template identifier (e.g., 'internet', 'mpls_core')

    Returns:
        ClusterTemplate if found, None otherwise
    """
    return CLUSTER_TEMPLATES.get(template_id)


def validate_cluster_request(
    template_id: str,
    external_connections: List[Dict],
    available_ip_count: int
) -> tuple[bool, Optional[str]]:
    """
    Validate a cluster creation request.

    Args:
        template_id: ID of the template to use
        external_connections: List of external connection configurations
        available_ip_count: Number of available IPs

    Returns:
        Tuple of (is_valid, error_message)
    """
    template = get_template_by_id(template_id)

    if not template:
        return False, f"Unknown cluster template: {template_id}"

    # Check IP availability
    required_ips = template.get_required_ip_count()
    if available_ip_count < required_ips:
        return False, f"Not enough IPs available. Need {required_ips}, have {available_ip_count}"

    # Check required external connections are provided
    provided_nodes = {conn.get('from_node') for conn in external_connections}
    for ext_conn in template.external_connections:
        if ext_conn.required and ext_conn.from_node not in provided_nodes:
            return False, f"Required external connection from '{ext_conn.from_node}' not provided"

    # Validate all provided connections reference valid nodes
    valid_nodes = {n.name_suffix for n in template.nodes}
    for conn in external_connections:
        from_node = conn.get('from_node')
        if from_node not in valid_nodes:
            return False, f"Unknown node '{from_node}' in external connections"
        if not conn.get('target_device'):
            return False, f"Missing target_device for connection from '{from_node}'"

    return True, None

"""
Feature flag data model with dependency resolution.

Provides functionality for:
- Feature definition parsing from Firestore
- Dependency graph validation (circular dependency detection)
- Resolving which features are actually enabled based on dependencies
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger('configservice')


class RolloutType(str, Enum):
    """Types of feature rollout strategies."""
    FULL = "full"
    PERCENTAGE = "percentage"


@dataclass
class RolloutConfig:
    """Configuration for feature rollout strategy."""
    type: RolloutType = RolloutType.FULL
    percentage: int = 100
    hash_key: str = "instance_id"

    @classmethod
    def from_dict(cls, data: Optional[Dict]) -> 'RolloutConfig':
        """Create RolloutConfig from dictionary."""
        if not data:
            return cls()
        return cls(
            type=RolloutType(data.get('type', 'full')),
            percentage=int(data.get('percentage', 100)),
            hash_key=data.get('hash_key', 'instance_id')
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'type': self.type.value,
            'percentage': self.percentage,
            'hash_key': self.hash_key
        }


@dataclass
class FeatureDefinition:
    """
    Represents a feature flag definition with metadata and dependencies.

    Attributes:
        name: Unique feature identifier
        description: Human-readable description
        category: Feature category (ui, network, integration, etc.)
        owner: Team or person responsible
        dependencies: List of feature names this feature depends on
        rollout: Rollout configuration
    """
    name: str
    description: str = ""
    category: str = "other"
    owner: str = ""
    dependencies: List[str] = field(default_factory=list)
    rollout: RolloutConfig = field(default_factory=RolloutConfig)
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_dict(cls, name: str, data: Optional[Dict]) -> 'FeatureDefinition':
        """
        Create FeatureDefinition from Firestore document data.

        Args:
            name: Feature name/identifier
            data: Dictionary with feature definition fields (can be None)

        Returns:
            FeatureDefinition instance
        """
        if data is None:
            data = {}
        return cls(
            name=name,
            description=data.get('description', ''),
            category=data.get('category', 'other'),
            owner=data.get('owner', ''),
            dependencies=data.get('dependencies', []),
            rollout=RolloutConfig.from_dict(data.get('rollout')),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', '')
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'name': self.name,
            'description': self.description,
            'category': self.category,
            'owner': self.owner,
            'dependencies': self.dependencies,
            'rollout': self.rollout.to_dict(),
            'created_at': self.created_at,
            'updated_at': self.updated_at
        }


class DependencyResolver:
    """
    Resolves feature dependencies and detects circular dependencies.

    Uses topological sorting to determine the correct order of feature
    evaluation and identifies any dependency issues.
    """

    def __init__(self, definitions: Dict[str, FeatureDefinition]):
        """
        Initialize resolver with feature definitions.

        Args:
            definitions: Dict mapping feature name to FeatureDefinition
        """
        self.definitions = definitions
        self._circular_deps: List[Tuple[str, List[str]]] = []

    def detect_circular_dependencies(self) -> List[Tuple[str, List[str]]]:
        """
        Detect circular dependencies in the feature graph.

        Returns:
            List of (feature_name, cycle_path) tuples for each circular dependency found
        """
        self._circular_deps = []
        visited: Set[str] = set()
        rec_stack: Set[str] = set()

        def dfs(feature: str, path: List[str]) -> bool:
            """Depth-first search to detect cycles."""
            visited.add(feature)
            rec_stack.add(feature)

            if feature in self.definitions:
                for dep in self.definitions[feature].dependencies:
                    if dep not in visited:
                        if dfs(dep, path + [dep]):
                            return True
                    elif dep in rec_stack:
                        # Found a cycle
                        cycle_start = path.index(dep) if dep in path else len(path)
                        cycle = path[cycle_start:] + [dep]
                        self._circular_deps.append((feature, cycle))
                        return True

            rec_stack.remove(feature)
            return False

        for feature in self.definitions:
            if feature not in visited:
                dfs(feature, [feature])

        return self._circular_deps

    def get_missing_dependencies(self, feature: str) -> List[str]:
        """
        Get list of dependencies that are not defined or not enabled.

        Args:
            feature: Feature name to check

        Returns:
            List of missing dependency names
        """
        if feature not in self.definitions:
            return []

        missing = []
        for dep in self.definitions[feature].dependencies:
            if dep not in self.definitions:
                missing.append(dep)

        return missing

    def resolve_enabled_features(
        self,
        requested_features: Set[str],
        all_definitions: Dict[str, FeatureDefinition]
    ) -> Dict:
        """
        Resolve which features are actually enabled based on dependencies.

        A feature is enabled only if:
        1. It is in the requested_features set
        2. All its dependencies are also enabled (recursively)
        3. It is not part of a circular dependency

        Args:
            requested_features: Set of feature names that are requested to be enabled
            all_definitions: All feature definitions (for dependency lookup)

        Returns:
            Dictionary with:
                - enabled: List of features that are actually enabled
                - disabled_missing_deps: Dict mapping feature -> list of missing dependencies
                - disabled_circular: List of features disabled due to circular dependencies
                - dependency_chain: Dict mapping feature -> list of resolved dependencies
        """
        # Detect circular dependencies first
        circular_features: Set[str] = set()
        circular_deps = self.detect_circular_dependencies()
        for feature, _ in circular_deps:
            circular_features.add(feature)

        enabled: Set[str] = set()
        disabled_missing_deps: Dict[str, List[str]] = {}
        dependency_chain: Dict[str, List[str]] = {}

        def can_enable(feature: str, visited: Set[str]) -> Tuple[bool, List[str]]:
            """
            Check if a feature can be enabled (all dependencies satisfied).

            Returns:
                Tuple of (can_enable, missing_dependencies)
            """
            # Prevent infinite recursion
            if feature in visited:
                return (False, [f"circular:{feature}"])
            visited = visited | {feature}

            # If feature is not requested, it's not enabled
            if feature not in requested_features:
                return (False, [feature])

            # If feature is in circular dependency, it cannot be enabled
            if feature in circular_features:
                return (False, [f"circular:{feature}"])

            # If feature has no definition, it can be enabled (legacy support)
            if feature not in all_definitions:
                return (True, [])

            # Check all dependencies
            definition = all_definitions[feature]
            missing = []
            resolved_deps = []

            for dep in definition.dependencies:
                # Check if dependency is enabled
                if dep in enabled:
                    resolved_deps.append(dep)
                    continue

                # Check if dependency can be enabled
                can_enable_dep, dep_missing = can_enable(dep, visited)
                if can_enable_dep:
                    resolved_deps.append(dep)
                else:
                    missing.extend(dep_missing)

            if missing:
                return (False, missing)

            dependency_chain[feature] = resolved_deps
            return (True, [])

        # Process each requested feature
        for feature in requested_features:
            can_be_enabled, missing = can_enable(feature, set())
            if can_be_enabled:
                enabled.add(feature)
            elif missing:
                disabled_missing_deps[feature] = missing

        return {
            'enabled': sorted(list(enabled)),
            'disabled_missing_deps': disabled_missing_deps,
            'disabled_circular': sorted(list(circular_features & requested_features)),
            'dependency_chain': dependency_chain,
            'circular_dependencies': [
                {'feature': f, 'cycle': c} for f, c in circular_deps
            ]
        }


def parse_feature_definitions(data: Dict) -> Dict[str, FeatureDefinition]:
    """
    Parse feature definitions from Firestore global document.

    Args:
        data: The 'feature_definitions' dict from Firestore

    Returns:
        Dict mapping feature name to FeatureDefinition
    """
    definitions = {}
    for name, defn_data in data.items():
        try:
            definitions[name] = FeatureDefinition.from_dict(name, defn_data)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning(f"Failed to parse feature definition '{name}': {e}")
    return definitions

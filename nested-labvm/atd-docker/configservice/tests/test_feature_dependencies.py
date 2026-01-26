"""
Unit tests for feature dependency resolution.

Tests cover:
- Basic dependency resolution
- Circular dependency detection
- Missing dependency handling
- Complex dependency chains
- Edge cases
"""

import pytest
import sys
import os

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from models.feature import (
    FeatureDefinition,
    RolloutConfig,
    RolloutType,
    DependencyResolver,
    parse_feature_definitions
)


class TestRolloutConfig:
    """Tests for RolloutConfig dataclass."""

    def test_default_values(self):
        """Test default rollout config values."""
        config = RolloutConfig()
        assert config.type == RolloutType.FULL
        assert config.percentage == 100
        assert config.hash_key == "instance_id"

    def test_from_dict_empty(self):
        """Test creating from empty/None dict."""
        config = RolloutConfig.from_dict(None)
        assert config.type == RolloutType.FULL
        assert config.percentage == 100

    def test_from_dict_percentage(self):
        """Test creating percentage rollout from dict."""
        config = RolloutConfig.from_dict({
            'type': 'percentage',
            'percentage': 50,
            'hash_key': 'user_id'
        })
        assert config.type == RolloutType.PERCENTAGE
        assert config.percentage == 50
        assert config.hash_key == 'user_id'

    def test_to_dict(self):
        """Test serialization to dict."""
        config = RolloutConfig(
            type=RolloutType.PERCENTAGE,
            percentage=25,
            hash_key='lab_id'
        )
        result = config.to_dict()
        assert result == {
            'type': 'percentage',
            'percentage': 25,
            'hash_key': 'lab_id'
        }


class TestFeatureDefinition:
    """Tests for FeatureDefinition dataclass."""

    def test_from_dict_minimal(self):
        """Test creating from minimal dict."""
        defn = FeatureDefinition.from_dict('test_feature', {})
        assert defn.name == 'test_feature'
        assert defn.description == ''
        assert defn.category == 'other'
        assert defn.dependencies == []

    def test_from_dict_full(self):
        """Test creating from full dict."""
        defn = FeatureDefinition.from_dict('dark_mode', {
            'description': 'Enable dark mode',
            'category': 'ui',
            'owner': 'ui-team',
            'dependencies': ['base_theme'],
            'rollout': {'type': 'full', 'percentage': 100},
            'created_at': '2025-01-01T00:00:00Z'
        })
        assert defn.name == 'dark_mode'
        assert defn.description == 'Enable dark mode'
        assert defn.category == 'ui'
        assert defn.owner == 'ui-team'
        assert defn.dependencies == ['base_theme']
        assert defn.rollout.type == RolloutType.FULL

    def test_to_dict(self):
        """Test serialization to dict."""
        defn = FeatureDefinition(
            name='test',
            description='Test feature',
            dependencies=['dep1', 'dep2']
        )
        result = defn.to_dict()
        assert result['name'] == 'test'
        assert result['description'] == 'Test feature'
        assert result['dependencies'] == ['dep1', 'dep2']


class TestDependencyResolver:
    """Tests for DependencyResolver class."""

    def test_no_dependencies(self):
        """Test features with no dependencies are all enabled."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a'),
            'feature_b': FeatureDefinition(name='feature_b'),
        }
        resolver = DependencyResolver(definitions)
        result = resolver.resolve_enabled_features(
            {'feature_a', 'feature_b'},
            definitions
        )
        assert sorted(result['enabled']) == ['feature_a', 'feature_b']
        assert result['disabled_missing_deps'] == {}
        assert result['disabled_circular'] == []

    def test_simple_dependency_satisfied(self):
        """Test feature with satisfied dependency is enabled."""
        definitions = {
            'base': FeatureDefinition(name='base'),
            'advanced': FeatureDefinition(name='advanced', dependencies=['base']),
        }
        resolver = DependencyResolver(definitions)
        result = resolver.resolve_enabled_features(
            {'base', 'advanced'},
            definitions
        )
        assert 'base' in result['enabled']
        assert 'advanced' in result['enabled']

    def test_simple_dependency_not_satisfied(self):
        """Test feature with unsatisfied dependency is disabled."""
        definitions = {
            'base': FeatureDefinition(name='base'),
            'advanced': FeatureDefinition(name='advanced', dependencies=['base']),
        }
        resolver = DependencyResolver(definitions)
        # Only request 'advanced', not 'base'
        result = resolver.resolve_enabled_features(
            {'advanced'},
            definitions
        )
        assert 'advanced' not in result['enabled']
        assert 'advanced' in result['disabled_missing_deps']
        assert 'base' in result['disabled_missing_deps']['advanced']

    def test_chain_dependency(self):
        """Test chain of dependencies: C -> B -> A."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a'),
            'feature_b': FeatureDefinition(name='feature_b', dependencies=['feature_a']),
            'feature_c': FeatureDefinition(name='feature_c', dependencies=['feature_b']),
        }
        resolver = DependencyResolver(definitions)

        # All enabled
        result = resolver.resolve_enabled_features(
            {'feature_a', 'feature_b', 'feature_c'},
            definitions
        )
        assert sorted(result['enabled']) == ['feature_a', 'feature_b', 'feature_c']

        # Missing middle of chain
        result = resolver.resolve_enabled_features(
            {'feature_a', 'feature_c'},
            definitions
        )
        assert 'feature_a' in result['enabled']
        assert 'feature_c' not in result['enabled']

    def test_circular_dependency_simple(self):
        """Test simple circular dependency: A -> B -> A."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a', dependencies=['feature_b']),
            'feature_b': FeatureDefinition(name='feature_b', dependencies=['feature_a']),
        }
        resolver = DependencyResolver(definitions)

        # Detect circular dependencies
        circular = resolver.detect_circular_dependencies()
        assert len(circular) > 0

        # Both should be disabled
        result = resolver.resolve_enabled_features(
            {'feature_a', 'feature_b'},
            definitions
        )
        assert result['enabled'] == []
        assert len(result['disabled_circular']) > 0

    def test_circular_dependency_complex(self):
        """Test complex circular dependency: A -> B -> C -> A."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a', dependencies=['feature_c']),
            'feature_b': FeatureDefinition(name='feature_b', dependencies=['feature_a']),
            'feature_c': FeatureDefinition(name='feature_c', dependencies=['feature_b']),
        }
        resolver = DependencyResolver(definitions)

        circular = resolver.detect_circular_dependencies()
        assert len(circular) > 0

        result = resolver.resolve_enabled_features(
            {'feature_a', 'feature_b', 'feature_c'},
            definitions
        )
        assert result['enabled'] == []

    def test_partial_circular(self):
        """Test that non-circular features work even when circular ones exist."""
        definitions = {
            'good_feature': FeatureDefinition(name='good_feature'),
            'circular_a': FeatureDefinition(name='circular_a', dependencies=['circular_b']),
            'circular_b': FeatureDefinition(name='circular_b', dependencies=['circular_a']),
        }
        resolver = DependencyResolver(definitions)

        result = resolver.resolve_enabled_features(
            {'good_feature', 'circular_a', 'circular_b'},
            definitions
        )
        assert 'good_feature' in result['enabled']
        assert 'circular_a' not in result['enabled']
        assert 'circular_b' not in result['enabled']

    def test_undefined_dependency(self):
        """Test feature with undefined dependency."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a', dependencies=['nonexistent']),
        }
        resolver = DependencyResolver(definitions)

        missing = resolver.get_missing_dependencies('feature_a')
        assert 'nonexistent' in missing

    def test_multiple_dependencies(self):
        """Test feature with multiple dependencies."""
        definitions = {
            'dep1': FeatureDefinition(name='dep1'),
            'dep2': FeatureDefinition(name='dep2'),
            'dep3': FeatureDefinition(name='dep3'),
            'main': FeatureDefinition(name='main', dependencies=['dep1', 'dep2', 'dep3']),
        }
        resolver = DependencyResolver(definitions)

        # All deps enabled
        result = resolver.resolve_enabled_features(
            {'dep1', 'dep2', 'dep3', 'main'},
            definitions
        )
        assert 'main' in result['enabled']

        # One dep missing
        result = resolver.resolve_enabled_features(
            {'dep1', 'dep3', 'main'},
            definitions
        )
        assert 'main' not in result['enabled']
        assert 'dep2' in result['disabled_missing_deps']['main']

    def test_diamond_dependency(self):
        """Test diamond dependency pattern: D depends on B and C, both depend on A."""
        definitions = {
            'a': FeatureDefinition(name='a'),
            'b': FeatureDefinition(name='b', dependencies=['a']),
            'c': FeatureDefinition(name='c', dependencies=['a']),
            'd': FeatureDefinition(name='d', dependencies=['b', 'c']),
        }
        resolver = DependencyResolver(definitions)

        result = resolver.resolve_enabled_features(
            {'a', 'b', 'c', 'd'},
            definitions
        )
        assert sorted(result['enabled']) == ['a', 'b', 'c', 'd']

    def test_legacy_feature_no_definition(self):
        """Test that features without definitions can still be enabled."""
        definitions = {
            'defined_feature': FeatureDefinition(name='defined_feature'),
        }
        resolver = DependencyResolver(definitions)

        # Request a feature that has no definition
        result = resolver.resolve_enabled_features(
            {'defined_feature', 'legacy_feature'},
            definitions
        )
        # Both should be enabled - legacy features are allowed
        assert 'defined_feature' in result['enabled']
        assert 'legacy_feature' in result['enabled']

    def test_empty_request(self):
        """Test with no requested features."""
        definitions = {
            'feature_a': FeatureDefinition(name='feature_a'),
        }
        resolver = DependencyResolver(definitions)

        result = resolver.resolve_enabled_features(set(), definitions)
        assert result['enabled'] == []


class TestParseFeatureDefinitions:
    """Tests for parse_feature_definitions function."""

    def test_empty_data(self):
        """Test parsing empty data."""
        result = parse_feature_definitions({})
        assert result == {}

    def test_valid_definitions(self):
        """Test parsing valid definitions."""
        data = {
            'feature_a': {
                'description': 'Feature A',
                'category': 'ui',
                'dependencies': []
            },
            'feature_b': {
                'description': 'Feature B',
                'dependencies': ['feature_a']
            }
        }
        result = parse_feature_definitions(data)
        assert 'feature_a' in result
        assert 'feature_b' in result
        assert result['feature_a'].description == 'Feature A'
        assert result['feature_b'].dependencies == ['feature_a']

    def test_malformed_definition_skipped(self):
        """Test that malformed definitions are skipped with warning."""
        data = {
            'good_feature': {'description': 'Good'},
            'bad_feature': None,  # Invalid - will be skipped
        }
        result = parse_feature_definitions(data)
        assert 'good_feature' in result
        # bad_feature should be skipped (would raise TypeError)


class TestIntegrationScenarios:
    """Integration tests for real-world scenarios."""

    def test_atl_typical_setup(self):
        """Test typical ATL feature setup."""
        definitions = {
            'base_capture': FeatureDefinition(
                name='base_capture',
                description='Basic packet capture',
                category='network'
            ),
            'advanced_capture': FeatureDefinition(
                name='advanced_capture',
                description='Advanced packet capture with filtering',
                category='network',
                dependencies=['base_capture']
            ),
            'cvp_integration': FeatureDefinition(
                name='cvp_integration',
                description='CloudVision Portal integration',
                category='integration'
            ),
            'dark_mode': FeatureDefinition(
                name='dark_mode',
                description='Dark mode UI theme',
                category='ui'
            ),
        }
        resolver = DependencyResolver(definitions)

        # Typical Level 7 topology - all features
        result = resolver.resolve_enabled_features(
            {'base_capture', 'advanced_capture', 'cvp_integration', 'dark_mode'},
            definitions
        )
        assert len(result['enabled']) == 4

        # Level 3 topology - basic features only
        result = resolver.resolve_enabled_features(
            {'dark_mode'},
            definitions
        )
        assert result['enabled'] == ['dark_mode']

        # Misconfigured - advanced without base
        result = resolver.resolve_enabled_features(
            {'advanced_capture', 'dark_mode'},
            definitions
        )
        assert 'dark_mode' in result['enabled']
        assert 'advanced_capture' not in result['enabled']
        assert 'advanced_capture' in result['disabled_missing_deps']

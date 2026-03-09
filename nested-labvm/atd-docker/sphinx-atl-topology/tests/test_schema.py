import pytest
from sphinx_atl_topology.schema import validate_topology


def test_valid_minimal():
    data = {
        'nodes': [{'id': 'spine1'}],
        'edges': [{'source': 'spine1', 'target': 'leaf1'}],
    }
    assert validate_topology(data) == []


def test_valid_full():
    data = {
        'title': 'Test Topology',
        'settings': {'layout': 'dagre', 'height': 600},
        'nodes': [
            {'id': 'spine1', 'type': 'spine', 'position': {'x': 100, 'y': 100}},
            {'id': 'leaf1', 'type': 'leaf', 'zone': 'dc1'},
        ],
        'edges': [
            {'source': 'spine1', 'target': 'leaf1', 'source_port': 'Ethernet1', 'target_port': 'Ethernet2'},
        ],
        'zones': [{'id': 'dc1', 'label': 'DC1'}],
        'annotations': [{'text': 'VLAN 100', 'position': {'x': 200, 'y': 200}}],
    }
    assert validate_topology(data) == []


def test_missing_node_id():
    data = {'nodes': [{'type': 'spine'}], 'edges': []}
    errors = validate_topology(data)
    assert any('missing required "id"' in e for e in errors)


def test_duplicate_node_id():
    data = {'nodes': [{'id': 'a'}, {'id': 'a'}], 'edges': []}
    errors = validate_topology(data)
    assert any('Duplicate' in e for e in errors)


def test_invalid_layout():
    data = {'nodes': [], 'edges': [], 'settings': {'layout': 'invalid'}}
    errors = validate_topology(data)
    assert any('Invalid layout' in e for e in errors)


def test_missing_edge_source():
    data = {'nodes': [], 'edges': [{'target': 'leaf1'}]}
    errors = validate_topology(data)
    assert any('missing "source"' in e for e in errors)


def test_invalid_position():
    data = {'nodes': [{'id': 'a', 'position': {'x': 1}}], 'edges': []}
    errors = validate_topology(data)
    assert any('position must have x and y' in e for e in errors)

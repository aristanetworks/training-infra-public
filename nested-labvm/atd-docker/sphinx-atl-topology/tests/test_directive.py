import pytest
from sphinx_atl_topology.device_types import classify_device


def test_classify_spine():
    assert classify_device('spine1') == 'spine'
    assert classify_device('Spine2') == 'spine'


def test_classify_leaf():
    assert classify_device('leaf1') == 'leaf'
    assert classify_device('Leaf3') == 'leaf'


def test_classify_borderleaf():
    assert classify_device('borderleaf1') == 'borderleaf'
    assert classify_device('BL1') == 'borderleaf'


def test_classify_pe():
    assert classify_device('PE1') == 'pe'
    assert classify_device('PE2') == 'pe'


def test_classify_ce():
    assert classify_device('CE1') == 'ce'


def test_classify_p_router():
    assert classify_device('P1') == 'p'
    assert classify_device('P3') == 'p'


def test_classify_rr():
    assert classify_device('RR1') == 'rr'
    assert classify_device('RR') == 'rr'


def test_classify_customer():
    assert classify_device('A1') == 'customer'
    assert classify_device('B2') == 'customer'


def test_classify_host():
    assert classify_device('host1') == 'host'


def test_classify_gw():
    assert classify_device('GW11') == 'gw'


def test_classify_other():
    assert classify_device('unknown') == 'other'
    assert classify_device('') == 'other'

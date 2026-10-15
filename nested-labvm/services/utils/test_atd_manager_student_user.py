"""Tests for the {STUDENT_USER_REPLACE} RADIUS user.

Run: cd nested-labvm/services/utils && python3 -m pytest test_atd_manager_student_user.py
"""

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import atd_manager  # noqa: E402
from atd_manager import DEFAULT_STUDENT_USER, AccessInfo, derive_student_user  # noqa: E402

AUTHORIZE = os.path.join(os.path.dirname(__file__), '..', '..', 'atd-docker', 'runtime-configs', 'freeradius', 'authorize')


@pytest.mark.parametrize('email, expected', [
    ('john.doe@acme.com', 'john.doe'),
    ('  John.Doe+Lab@ACME.com ', 'john.doelab'),
    ('o\'brien_k-9@x.org', 'obrien_k-9'),
    ('.-weird-.@x.org', 'weird'),
    ('a' * 40 + '@x.org', 'a' * 32),
])
def test_uses_sanitized_email_local_part(email, expected):
    user, source = derive_student_user({'exam_taker_email': email})
    assert user == expected
    assert source == 'customer_details.exam_taker_email'


@pytest.mark.parametrize('details', [
    None, {}, 'not-a-dict', {'exam_taker_email': ''}, {'exam_taker_email': None},
    {'exam_taker_email': 'no-at-sign'}, {'exam_taker_email': '+++@x.org'},
    {'exam_taker_email': 'Arista@customer.com'}, {'exam_taker_email': 'root@x.org'},
])
def test_falls_back_to_generic_user(details):
    user, source = derive_student_user(details)
    assert user == DEFAULT_STUDENT_USER
    assert source.startswith('fallback')


def write_access_info(tmp_path, body):
    path = tmp_path / 'ACCESS_INFO.yaml'
    path.write_text('login_info:\n  jump_host:\n    user: arista\n    pw: labpass\ntopology: t1\n' + body)
    return str(path)


def test_access_info_reads_student_user(tmp_path):
    info = AccessInfo.from_yaml(write_access_info(tmp_path, 'customer_details:\n  exam_taker_email: Jane.Smith@corp.com\n  lab_type: Exam\n'))
    assert (info.student_user, info.lab_type) == ('jane.smith', 'Exam')


@pytest.mark.parametrize('body', ['', 'customer_details:\n', 'customer_details: {}\n'])
def test_access_info_without_customer_details(tmp_path, body):
    info = AccessInfo.from_yaml(write_access_info(tmp_path, body))
    assert (info.student_user, info.lab_type) == (DEFAULT_STUDENT_USER, 'Lab')


def test_replaces_placeholder_in_authorize_template(tmp_path):
    docker_dir = tmp_path / 'nested-labvm' / 'atd-docker' / 'runtime-configs' / 'freeradius'
    docker_dir.mkdir(parents=True)
    with open(AUTHORIZE) as f:
        (docker_dir / 'authorize').write_text(f.read())
    (tmp_path / 'topologies' / 't1' / 'files').mkdir(parents=True)

    startup = types.SimpleNamespace(
        access_info=AccessInfo(password='labpass', topology='t1', student_user='jane.smith', student_user_source='test'),
        config=types.SimpleNamespace(atd_opt_path=str(tmp_path)),
        file_manager=atd_manager.FileManager(),
        logger=atd_manager.logging.getLogger('test'),
    )
    atd_manager.ATDStartup._replace_password_placeholders(startup)

    content = (docker_dir / 'authorize').read_text()
    assert '{STUDENT_USER_REPLACE}' not in content and '{ARISTA_REPLACE}' not in content
    assert 'jane.smith\tCleartext-Password := "labpass"' in content
    assert 'arista\tCleartext-Password := "labpass"' in content

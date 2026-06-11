"""Tests for auth handlers — login flow and credential validation."""

import os, sys, hashlib
import tornado.web
import tornado.testing

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from handlers.auth import BaseHandler, LoginHandler
from utils import encodeID

TEST_USER = 'arista'
TEST_PASS = 'arista123'
TEST_SALT = 'testsalt12345'
TEST_ACCOUNTS = {
    hashlib.sha512((TEST_USER + TEST_SALT).encode('utf-8')).hexdigest():
    hashlib.sha512((TEST_PASS + TEST_SALT).encode('utf-8')).hexdigest()
}


class MainStub(tornado.web.RequestHandler):
    def get(self):
        self.write("OK")


class TestLoginHandler(tornado.testing.AsyncHTTPTestCase):
    def setUp(self):
        # Create minimal login template
        self.html_dir = os.path.join(os.path.dirname(__file__), 'fixtures', 'html')
        os.makedirs(self.html_dir, exist_ok=True)
        login_html = os.path.join(self.html_dir, 'login.html')
        if not os.path.exists(login_html):
            with open(login_html, 'w') as f:
                f.write('<html><body>Login {{ LOGIN_MESSAGE }}</body></html>')
        super().setUp()

    def get_app(self):
        return tornado.web.Application([
            (r'/login', LoginHandler, {
                'accounts': TEST_ACCOUNTS,
                'salt': TEST_SALT,
                'base_path': os.path.join(os.path.dirname(__file__), 'fixtures', 'html') + '/',
            }),
            (r'/', MainStub),
        ], cookie_secret='test-secret')

    def test_get_login_renders_form(self):
        response = self.fetch('/login')
        assert response.code == 200
        assert b'Login' in response.body

    def test_post_valid_credentials_redirects(self):
        response = self.fetch('/login', method='POST',
            body=f'name={TEST_USER}&pwd={TEST_PASS}', follow_redirects=False)
        assert response.code == 302
        assert response.headers.get('Location') == '/'

    def test_post_invalid_credentials_shows_error(self):
        response = self.fetch('/login', method='POST',
            body='name=wrong&pwd=wrong', follow_redirects=False)
        assert response.code == 200
        assert b'Wrong username' in response.body

    def test_auth_parameter_auto_login(self):
        cred = encodeID({'user': TEST_USER, 'pwd': TEST_PASS})
        response = self.fetch(f'/login?auth={cred}', follow_redirects=False)
        assert response.code == 302

    def test_auth_parameter_invalid_shows_form(self):
        response = self.fetch('/login?auth=invalid!!!', follow_redirects=False)
        assert response.code == 200

    def test_post_sets_cookie(self):
        response = self.fetch('/login', method='POST',
            body=f'name={TEST_USER}&pwd={TEST_PASS}', follow_redirects=False)
        cookies = response.headers.get_list('Set-Cookie')
        cookie_names = [c.split('=')[0] for c in cookies]
        assert 'user' in cookie_names

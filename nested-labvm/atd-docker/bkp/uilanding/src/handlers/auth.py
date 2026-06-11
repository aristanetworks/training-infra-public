"""Authentication handlers for UILanding."""

import hashlib
import secrets
import tornado.web
from utils import safe_log, decodeID


class BaseHandler(tornado.web.RequestHandler):
    """Base handler with cookie-based authentication."""
    def get_current_user(self):
        return self.get_secure_cookie("user")


class LoginHandler(BaseHandler):
    """Login form and credential validation."""

    def initialize(self, accounts, salt, base_path):
        self.accounts = accounts
        self.salt = salt
        self.base_path = base_path

    def _validate_credentials(self, username, password):
        tmp_username_hash = hashlib.sha512((username + self.salt).encode('utf-8')).hexdigest()
        tmp_pwd_hash = hashlib.sha512((password + self.salt).encode('utf-8')).hexdigest()
        stored_pwd_hash = self.accounts.get(tmp_username_hash, 'invalid_user_dummy_hash')
        return secrets.compare_digest(tmp_pwd_hash, stored_pwd_hash)

    def get(self):
        safe_log('info', 'Login page accessed', event='page_view', page='login')
        AUTH = False
        decoded_cred = None
        if 'auth' in self.request.arguments:
            try:
                decoded_cred = decodeID(self.get_argument('auth'))
                AUTH = self._validate_credentials(decoded_cred['user'], decoded_cred['pwd'])
            except Exception as e:
                safe_log('warning', f'Auth parameter decode failed: {e}', event='auth', action='decode_failure')
        if AUTH and decoded_cred:
            self.set_secure_cookie("user", decoded_cred['user'])
            self.redirect('/')
        else:
            self.render(self.base_path + 'login.html', LOGIN_MESSAGE="")

    def post(self):
        username = self.get_argument("name")
        password = self.get_argument("pwd")
        if self._validate_credentials(username, password):
            safe_log('info', 'Login successful', event='auth', action='login_success', username=username)
            self.set_secure_cookie("user", username)
            self.redirect("/")
        else:
            safe_log('warning', 'Login failed', event='auth', action='login_failure', username=username)
            self.render(self.base_path + 'login.html', LOGIN_MESSAGE="Wrong username and/or password.")

"""Contract tests for safe HTML and ApiErrorV1 responses."""

from unittest.mock import patch

from flask import abort
from werkzeug.exceptions import TooManyRequests

from app import db
from tests.helpers import BaseTestCase


class TestHttpErrors(BaseTestCase):
    def _route_for_status(self, status):
        endpoint = f'raise_{status}_{len(self.app.view_functions)}'
        path = f'/_test/error/{status}'

        def raise_error():
            abort(status, description='private provider payload must stay hidden')

        self.app.add_url_rule(path, endpoint, raise_error)
        return path

    def test_supported_html_errors_use_one_safe_view_model(self):
        statuses = (400, 401, 403, 404, 405, 429, 500, 502, 503, 505)
        paths = {status: self._route_for_status(status) for status in statuses}
        for status, path in paths.items():
            response = self.client.get(path)
            self.assertEqual(response.status_code, status)
            self.assertIn(str(status).encode(), response.data)
            self.assertIn(b'Reference', response.data)
            self.assertNotIn(b'private provider payload', response.data)
            self.assertEqual(response.headers['X-Request-ID'].encode() in response.data, True)

    def test_json_negotiation_returns_stable_api_error(self):
        response = self.client.get(
            '/missing-api-resource',
            headers={'Accept': 'application/json'},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.content_type, 'application/json')
        payload = response.get_json()
        self.assertEqual(payload['version'], 'ApiErrorV1')
        self.assertEqual(payload['code'], 'not_found')
        self.assertEqual(payload['details'], {})
        self.assertEqual(payload['request_id'], response.headers['X-Request-ID'])

    def test_browser_default_prefers_html(self):
        response = self.client.get('/missing-browser-page')
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response.content_type.startswith('text/html'))

    def test_retry_after_is_preserved(self):
        def limited():
            raise TooManyRequests(retry_after=17)

        self.app.add_url_rule('/_test/limited', 'limited', limited)
        response = self.client.get('/_test/limited')
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers['Retry-After'], '17')

    def test_server_error_rolls_back_the_database_session(self):
        path = self._route_for_status(500)
        with patch.object(db.session, 'rollback') as rollback:
            response = self.client.get(path)
        self.assertEqual(response.status_code, 500)
        rollback.assert_called_once_with()

    def test_authenticated_error_offers_private_recovery(self):
        self.register_and_login()
        response = self.client.get('/missing-private-page')
        self.assertIn(b'Dashboard', response.data)
        self.assertIn(b'Position log', response.data)

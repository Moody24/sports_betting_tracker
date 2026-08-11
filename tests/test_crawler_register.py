"""Every HTML surface declares exactly one crawler register, and it fails closed.

These assertions read the RENDERED response, not the template. A template that
looks correct can still render wrong — the register is computed from
`request.endpoint`, which only exists at request time.

The stakes are asymmetric. Shipping `noindex` on a page that should be public
costs some traffic and is fixed by editing one set. Shipping `index` on a
private page publishes a user's bet history, stakes, and P/L, and no later fix
removes it from a search index that has already crawled it. So the default is
private and the allowlist is explicit.
"""
import unittest

from app import PUBLIC_ENDPOINTS

from tests.helpers import BaseTestCase

INDEXABLE = b'<meta name="robots" content="index, follow">'
PRIVATE = b'<meta name="robots" content="noindex, nofollow">'

# Every GET route that renders a full HTML page, and the register it must
# declare. Kept explicit rather than derived, so adding a page forces a
# deliberate decision here instead of inheriting one silently.
PRIVATE_PAGES = [
    '/dashboard',
    '/bets',
    '/bets/new',
    '/nba/today',
    '/nba/analysis',
    '/nba/stat-analysis',
]
PUBLIC_PAGES = ['/']


class TestCrawlerRegister(BaseTestCase):

    def test_public_pages_are_indexable(self):
        for path in PUBLIC_PAGES:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(INDEXABLE, resp.data)
                self.assertNotIn(PRIVATE, resp.data)

    def test_private_pages_are_noindex(self):
        self.register_and_login()
        for path in PRIVATE_PAGES:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(resp.status_code, 200)
                self.assertIn(PRIVATE, resp.data)
                self.assertNotIn(INDEXABLE, resp.data)

    def test_auth_pages_are_noindex(self):
        for path in ('/auth/login', '/auth/register'):
            with self.subTest(path=path):
                self.assertIn(PRIVATE, self.client.get(path).data)

    def test_error_pages_are_noindex(self):
        """A 404 has no endpoint at all, so this is the fail-closed path."""
        resp = self.client.get('/definitely-not-a-route')
        self.assertEqual(resp.status_code, 404)
        self.assertIn(PRIVATE, resp.data)

    def test_every_page_declares_exactly_one_register(self):
        self.register_and_login()
        for path in PUBLIC_PAGES + PRIVATE_PAGES:
            with self.subTest(path=path):
                body = self.client.get(path).data
                self.assertEqual(
                    body.count(INDEXABLE) + body.count(PRIVATE), 1,
                    'a page must declare one robots directive, never two')

    def test_allowlist_stays_deliberately_small(self):
        """A guard on the guard.

        If this fails, someone widened the public surface. That may be right —
        but it is a publishing decision and should be made on purpose, with the
        page's copy reviewed against the honesty contract (no fabricated win
        rates or accuracy claims), not slipped in with a refactor.
        """
        self.assertEqual(set(PUBLIC_ENDPOINTS), {'main.home'})


if __name__ == '__main__':
    unittest.main()

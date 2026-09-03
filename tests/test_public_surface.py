"""Crawler, metadata, and public trust-surface contracts."""

import unittest
from unittest.mock import patch
from xml.etree import ElementTree

from app import create_app
from app.public_pages import HOME_PAGE, PUBLIC_PAGES
from tests.helpers import BaseTestCase


ALL_PUBLIC_PAGES = (HOME_PAGE, *PUBLIC_PAGES)


class TestPublicSurface(BaseTestCase):
    def test_public_pages_have_unique_metadata_and_canonicals(self):
        titles = set()
        descriptions = set()
        for page in ALL_PUBLIC_PAGES:
            with self.subTest(path=page.path):
                response = self.client.get(page.path)
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn(
                    f'<link rel="canonical" href="http://localhost:5000{page.path}">',
                    body,
                )
                self.assertIn(
                    f'<meta name="description" content="{page.description}">',
                    body,
                )
                self.assertEqual(body.count('<meta property="og:url"'), 1)
                titles.add(page.title)
                descriptions.add(page.description)
        self.assertEqual(len(titles), len(ALL_PUBLIC_PAGES))
        self.assertEqual(len(descriptions), len(ALL_PUBLIC_PAGES))

    def test_public_child_has_accessible_breadcrumb_and_json_ld(self):
        body = self.client.get('/methodology').get_data(as_text=True)
        self.assertIn('<nav class="breadcrumbs" aria-label="Breadcrumb">', body)
        self.assertIn('<span aria-current="page">Model methodology', body)
        self.assertIn('"@type": "BreadcrumbList"', body)

    def test_private_page_has_no_canonical_or_structured_data(self):
        body = self.client.get('/auth/login').get_data(as_text=True)
        self.assertNotIn('rel="canonical"', body)
        self.assertNotIn('application/ld+json', body)

    def test_sitemap_contains_only_public_canonical_pages(self):
        response = self.client.get('/sitemap.xml')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith('application/xml'))
        root = ElementTree.fromstring(response.data)
        namespace = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
        locations = [
            node.text for node in root.findall('sm:url/sm:loc', namespace)
        ]
        expected = [
            f'http://localhost:5000{page.path}' for page in ALL_PUBLIC_PAGES
        ]
        self.assertEqual(locations, expected)
        self.assertEqual(len(locations), len(set(locations)))
        self.assertFalse(any('/auth' in location for location in locations))

    def test_nonproduction_robots_disallows_all(self):
        response = self.client.get('/robots.txt')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content_type.startswith('text/plain'))
        self.assertEqual(
            response.get_data(as_text=True),
            'User-agent: *\nDisallow: /\n',
        )

    def test_production_robots_exposes_sitemap_and_blocks_private_routes(self):
        self.app.config.update(
            DEPLOYMENT_IS_PRODUCTION=True,
            PUBLIC_BASE_URL='https://edge.example',
        )
        body = self.client.get('/robots.txt').get_data(as_text=True)
        self.assertIn('Allow: /\n', body)
        self.assertIn('Disallow: /dashboard\n', body)
        self.assertIn('Disallow: /bets\n', body)
        self.assertIn('Sitemap: https://edge.example/sitemap.xml\n', body)


class TestPublicOriginConfiguration(unittest.TestCase):
    def test_public_origin_rejects_paths(self):
        with patch.dict(
            'os.environ',
            {
                'SECRET_KEY': 'test',
                'PUBLIC_BASE_URL': 'https://example.com/app',
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, 'absolute origin'):
                create_app(testing=True)

    def test_production_origin_requires_https(self):
        with patch.dict(
            'os.environ',
            {
                'SECRET_KEY': 'test',
                'PUBLIC_BASE_URL': 'http://example.com',
                'RAILWAY_ENVIRONMENT': 'production',
            },
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, 'HTTPS'):
                create_app(testing=True)


if __name__ == '__main__':
    unittest.main()

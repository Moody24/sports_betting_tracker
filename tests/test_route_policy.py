"""The live Flask URL map must match the reviewed route policy catalog."""

from app.route_policy import ROUTE_POLICIES
from tests.helpers import BaseTestCase


class TestRoutePolicy(BaseTestCase):
    def _runtime_routes(self):
        return {
            rule.endpoint: (
                frozenset(rule.methods - {'HEAD', 'OPTIONS'}),
                rule.rule,
            )
            for rule in self.app.url_map.iter_rules()
            if rule.endpoint != 'static'
        }

    def test_catalog_exactly_matches_runtime_routes(self):
        runtime = self._runtime_routes()
        self.assertEqual(set(ROUTE_POLICIES), set(runtime))
        for endpoint, policy in ROUTE_POLICIES.items():
            with self.subTest(endpoint=endpoint):
                self.assertEqual(
                    (policy.methods, policy.path),
                    runtime[endpoint],
                )

    def test_every_mutation_has_explicit_security_classification(self):
        for endpoint, policy in ROUTE_POLICIES.items():
            if not policy.methods.intersection({'POST', 'PUT', 'PATCH', 'DELETE'}):
                continue
            with self.subTest(endpoint=endpoint):
                self.assertIn(
                    policy.authentication,
                    {'anonymous', 'optional', 'required', 'fresh-required'},
                )
                self.assertIn(policy.csrf, {'protected', 'exempt-safe-event'})
                self.assertTrue(policy.rate_limit)
                self.assertTrue(policy.owner)

    def test_only_safe_telemetry_is_csrf_exempt(self):
        exempt = {
            endpoint
            for endpoint, policy in ROUTE_POLICIES.items()
            if policy.csrf.startswith('exempt')
        }
        self.assertEqual(exempt, {'main.ux_telemetry'})

    def test_user_data_routes_require_authentication(self):
        for endpoint, policy in ROUTE_POLICIES.items():
            if policy.owner not in {'betting', 'nba'}:
                continue
            with self.subTest(endpoint=endpoint):
                self.assertIn(
                    policy.authentication,
                    {'required', 'fresh-required'},
                )

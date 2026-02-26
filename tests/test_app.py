"""Backward-compatible entry point — imports all split test modules.

Tests have been split into focused modules for maintainability:
  - test_models.py   — User / Bet / GameSnapshot model tests
  - test_auth.py     — Authentication route tests
  - test_bets.py     — Bet CRUD, NBA endpoints, JSON endpoints
  - test_main.py     — Home page and dashboard tests
  - test_nba.py      — NBA service layer tests
  - test_security.py — Authorization and isolation tests
  - test_coverage.py — Gap-filling tests for >=80% coverage

Running `python -m unittest discover -s tests` will pick up every module
automatically.  This file re-exports everything so that any tooling that
references `tests.test_app` directly still works.
"""

# Re-export so `from tests.test_app import *` or direct references keep working.
from tests.helpers import BaseTestCase, make_bet, make_user  # noqa: F401
from tests.test_models import *     # noqa: F401,F403
from tests.test_auth import *       # noqa: F401,F403
from tests.test_bets import *       # noqa: F401,F403
from tests.test_main import *       # noqa: F401,F403
from tests.test_nba import *        # noqa: F401,F403
from tests.test_security import *   # noqa: F401,F403
from tests.test_coverage import *   # noqa: F401,F403

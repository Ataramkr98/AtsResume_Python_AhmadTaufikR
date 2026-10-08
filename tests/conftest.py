"""Shared fixtures for the test suite.

The demo workspace is expensive to build (roughly ten seconds), so it is seeded **once per
session** rather than per test. Every test still runs inside its own transaction, so writes
are rolled back and the seeded rows stay read-only from each test's point of view.
"""

from __future__ import annotations

import pytest
from django.core.management import call_command

from apps.accounts.models import Organization

DEMO_SLUG = "northstar-labs"
DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo123"


@pytest.fixture(scope="session")
def seeded_workspace(django_db_setup, django_db_blocker):
    """Build the demo organization once for the whole session."""
    with django_db_blocker.unblock():
        call_command("seed_demo", verbosity=0, reset=True)


@pytest.fixture
def demo(client, db, seeded_workspace):
    """The seeded organization plus a client authenticated as the demo administrator."""
    organization = Organization.objects.get(slug=DEMO_SLUG)
    assert client.login(username=DEMO_EMAIL, password=DEMO_PASSWORD)
    return organization


@pytest.fixture
def demo_client(client, db, seeded_workspace):
    """An authenticated client for tests that do not need the organization itself."""
    assert client.login(username=DEMO_EMAIL, password=DEMO_PASSWORD)
    return client

"""Authentication flows: sign-in with MFA, invitation acceptance, and profile validation.

These paths carry more risk per line than anything else in the project — they decide who gets
in and with which role — and they were the least covered part of the suite.

Writing them down immediately found a real break: both MFA call sites passed three arguments
to `django_otp.login`, which takes two. Enrolling in two-factor authentication *and* signing
in with it both raised `TypeError`. See `test_signing_in_with_a_valid_code_records_the_verified_device`
and `test_mfa_setup_confirms_the_device`.
"""

from __future__ import annotations

import time

import pytest
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.forms import InvitationForm, LoginForm, ProfileForm
from apps.accounts.models import Invitation, OrganizationMembership, User

PASSWORD = "LongPassword123!"


def current_token(device: TOTPDevice) -> str:
    """The code this device would accept right now.

    Constructed exactly the way `TOTPDevice.verify_token` constructs its generator, so the
    test cannot silently drift away from the verification path it is exercising.
    """
    generator = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    generator.time = time.time()
    return str(generator.token())


def make_member(organization, email: str, *, role, confirmed_device: bool = False):
    """Create a user in the workspace, optionally with a confirmed authenticator."""
    user = User.objects.create_user(email, PASSWORD, full_name="Test Member")
    OrganizationMembership.objects.create(organization=organization, user=user, role=role)
    device = None
    if confirmed_device:
        device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    return user, device


@pytest.fixture
def mfa_user(demo):
    """A recruiter in the demo workspace with a confirmed authenticator device."""
    return make_member(
        demo, "mfa.user@example.com", role=OrganizationMembership.Role.RECRUITER, confirmed_device=True
    )


# --------------------------------------------------------------------------- login


@pytest.mark.django_db
def test_signing_in_without_a_device_needs_no_code(client, demo):
    response = client.post(
        reverse("login"), {"username": "demo@example.com", "password": "demo123"}
    )
    assert response.status_code == 302
    assert "otp_device_id" not in client.session


@pytest.mark.django_db
def test_login_form_demands_a_code_once_a_device_is_confirmed(mfa_user):
    user, _ = mfa_user
    form = LoginForm(data={"username": user.email, "password": PASSWORD})

    assert not form.is_valid()
    assert "authenticator app" in form.errors["otp_token"][0]


@pytest.mark.django_db
def test_login_form_rejects_a_wrong_code(mfa_user):
    user, _ = mfa_user
    form = LoginForm(data={"username": user.email, "password": PASSWORD, "otp_token": "000000"})

    assert not form.is_valid()
    assert "invalid" in form.errors["otp_token"][0]


@pytest.mark.django_db
def test_signing_in_with_a_valid_code_records_the_verified_device(client, mfa_user):
    """Regression: this used to raise TypeError because django_otp.login takes (request, device)."""
    user, device = mfa_user

    response = client.post(
        reverse("login"),
        {"username": user.email, "password": PASSWORD, "otp_token": current_token(device)},
    )

    assert response.status_code == 302
    assert client.session["otp_device_id"] == device.persistent_id


# --------------------------------------------------------------------------- MFA enrolment


@pytest.mark.django_db
def test_mfa_setup_confirms_the_device(client, demo):
    """Regression: the confirmation POST used to raise TypeError for the same reason."""
    user, _ = make_member(
        demo, "enrol@example.com", role=OrganizationMembership.Role.RECRUITER
    )
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=False)
    client.force_login(user)

    response = client.post(reverse("mfa-setup"), {"token": current_token(device)})

    assert response.status_code == 302
    device.refresh_from_db()
    assert device.confirmed is True


@pytest.mark.django_db
def test_mfa_setup_page_mints_a_device_on_first_visit(client, demo):
    """The GET branch enrols the user, so it has to create the unconfirmed device on demand."""
    user, _ = make_member(demo, "fresh@example.com", role=OrganizationMembership.Role.RECRUITER)
    client.force_login(user)

    response = client.get(reverse("mfa-setup"))

    assert response.status_code == 200
    device = TOTPDevice.objects.get(user=user)
    assert device.confirmed is False
    # The page shows the otpauth:// address the authenticator app has to scan.
    assert b"otpauth://" in response.content


@pytest.mark.django_db
def test_mfa_setup_rejects_a_wrong_code(client, demo):
    user, _ = make_member(demo, "enrol2@example.com", role=OrganizationMembership.Role.RECRUITER)
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=False)
    client.force_login(user)

    response = client.post(reverse("mfa-setup"), {"token": "000000"})

    assert response.status_code == 200
    device.refresh_from_db()
    assert device.confirmed is False


@pytest.mark.django_db
def test_mfa_setup_rejects_a_malformed_code(client, demo):
    """The form enforces 6-8 digits before any token is verified."""
    user, _ = make_member(demo, "enrol3@example.com", role=OrganizationMembership.Role.RECRUITER)
    client.force_login(user)

    response = client.post(reverse("mfa-setup"), {"token": "abcdef"})

    assert response.status_code == 200
    assert b"6 to 8 digit" in response.content


# --------------------------------------------------------------------------- invitations


@pytest.mark.django_db
def test_accepting_an_invitation_creates_the_account_and_signs_in(client, demo):
    inviter = User.objects.get(email="demo@example.com")
    invitation, token = Invitation.issue(
        organization=demo,
        email="new.hire@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        invited_by=inviter,
    )

    response = client.post(
        reverse("accept-invitation", args=[token]),
        {"full_name": "New Hire", "password": "a-long-password-123"},
    )

    assert response.status_code == 302
    user = User.objects.get(email="new.hire@example.com")
    assert user.full_name == "New Hire"
    assert user.check_password("a-long-password-123")

    membership = OrganizationMembership.objects.get(organization=demo, user=user)
    assert membership.role == OrganizationMembership.Role.RECRUITER
    assert membership.active is True

    invitation.refresh_from_db()
    assert invitation.accepted_at is not None
    assert client.session["_auth_user_id"] == str(user.pk)


@pytest.mark.django_db
def test_accepting_an_invitation_cannot_take_over_an_existing_account(client, demo):
    """An invitation token must not become a way to seize an account or grant yourself a role."""
    existing = User.objects.create_user(
        "taken@example.com", "ExistingPass123!", full_name="Existing Person"
    )
    inviter = User.objects.get(email="demo@example.com")
    _, token = Invitation.issue(
        organization=demo,
        email="taken@example.com",
        role=OrganizationMembership.Role.ORG_ADMIN,
        invited_by=inviter,
    )

    response = client.post(
        reverse("accept-invitation", args=[token]),
        {"full_name": "Impostor", "password": "a-long-password-123"},
    )

    assert response.status_code == 200
    assert b"already exists" in response.content

    existing.refresh_from_db()
    assert existing.full_name == "Existing Person"
    assert not existing.check_password("a-long-password-123")
    assert not OrganizationMembership.objects.filter(organization=demo, user=existing).exists()


@pytest.mark.django_db
def test_an_expired_invitation_token_is_gone(client, demo):
    inviter = User.objects.get(email="demo@example.com")
    invitation, token = Invitation.issue(
        organization=demo,
        email="stale@example.com",
        role=OrganizationMembership.Role.RECRUITER,
        invited_by=inviter,
    )
    # find_valid() is what decides this, so move the expiry rather than trusting the clock.
    Invitation.objects.filter(pk=invitation.pk).update(expires_at=invitation.expires_at.replace(year=2000))

    response = client.get(reverse("accept-invitation", args=[token]))

    assert response.status_code == 410
    assert not User.objects.filter(email="stale@example.com").exists()


# --------------------------------------------------------------------------- entry point


@pytest.mark.django_db
def test_app_entry_sends_anonymous_visitors_to_login(client):
    response = client.get(reverse("app-entry"))
    assert response.status_code == 302
    assert response.url == reverse("login")


@pytest.mark.django_db
def test_app_entry_redirects_a_user_with_no_membership(client):
    user = User.objects.create_user("orphan@example.com", PASSWORD, full_name="Orphan")
    client.force_login(user)

    response = client.get(reverse("app-entry"))

    assert response.status_code == 302
    assert response.url == reverse("login")


# --------------------------------------------------------------------------- form validation


def test_invitation_form_casefolds_the_email():
    form = InvitationForm(
        data={"email": "Mixed.Case@Example.COM", "role": OrganizationMembership.Role.RECRUITER}
    )
    assert form.is_valid()
    assert form.cleaned_data["email"] == "mixed.case@example.com"


def test_profile_form_trims_a_valid_name():
    form = ProfileForm(data={"full_name": "  Dana Reyes  "})
    assert form.is_valid()
    assert form.cleaned_data["full_name"] == "Dana Reyes"


def test_profile_form_rejects_a_one_character_name():
    form = ProfileForm(data={"full_name": " X "})
    assert not form.is_valid()
    assert "full_name" in form.errors

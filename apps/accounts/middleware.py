from django.conf import settings
from django.shortcuts import redirect

from .models import OrganizationMembership


class MFAEnforcementMiddleware:
    EXEMPT_PREFIXES = (
        "/accounts/mfa/",
        "/accounts/logout/",
        "/static/",
        "/healthz",
        "/readyz",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            settings.MFA_ENFORCEMENT
            and user
            and user.is_authenticated
            and not request.path.startswith(self.EXEMPT_PREFIXES)
        ):
            requires_mfa = user.is_staff or user.memberships.filter(
                active=True, role=OrganizationMembership.Role.ORG_ADMIN
            ).exists()
            if requires_mfa and not user.is_verified():
                return redirect("mfa-setup")
        return self.get_response(request)

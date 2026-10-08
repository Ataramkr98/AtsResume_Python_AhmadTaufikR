from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.shortcuts import redirect, render
from django.utils import timezone
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice

from .forms import AcceptInvitationForm, LoginForm, MFAConfirmForm
from .models import Invitation, OrganizationMembership, User


class AccountLoginView(LoginView):
    authentication_form = LoginForm
    template_name = "accounts/login.html"
    template_engine = "jinja2"

    def form_valid(self, form):
        response = super().form_valid(form)
        if form.otp_device:
            # django_otp.login(request, device) -- it reads the user off the request, and rejects
            # the device outright if it does not belong to that user.
            otp_login(self.request, form.otp_device)
        return response


class AccountLogoutView(LogoutView):
    pass


def app_entry(request):
    if not request.user.is_authenticated:
        return redirect("login")
    membership = request.user.memberships.filter(
        active=True,
        organization__status="ACTIVE",
    ).select_related("organization").first()
    if not membership:
        messages.error(request, "Your account is not assigned to an organization.")
        return redirect("login")
    return redirect("dashboard", organization_slug=membership.organization.slug)


def accept_invitation(request, token: str):
    invitation = Invitation.find_valid(token)
    if not invitation:
        return render(request, "accounts/invitation_invalid.html", using="jinja2", status=410)
    if request.method == "POST":
        form = AcceptInvitationForm(request.POST)
        if form.is_valid():
            user, created = User.objects.get_or_create(
                email=invitation.email,
                defaults={"full_name": form.cleaned_data["full_name"]},
            )
            if not created and user.has_usable_password():
                form.add_error(None, "An account already exists for this email. Sign in to continue.")
            else:
                user.full_name = form.cleaned_data["full_name"]
                user.set_password(form.cleaned_data["password"])
                user.save(update_fields=["full_name", "password"])
                OrganizationMembership.objects.update_or_create(
                    organization=invitation.organization,
                    user=user,
                    defaults={"role": invitation.role, "active": True},
                )
                invitation.accepted_at = timezone.now()
                invitation.save(update_fields=["accepted_at"])
                login(request, user)
                return redirect("dashboard", organization_slug=invitation.organization.slug)
    else:
        form = AcceptInvitationForm()
    return render(
        request,
        "accounts/accept_invitation.html",
        {"form": form, "invitation": invitation},
        using="jinja2",
    )


@login_required
def mfa_setup(request):
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if not device:
        device = TOTPDevice.objects.create(user=request.user, name="default", confirmed=False)
    if request.method == "POST":
        form = MFAConfirmForm(request.POST)
        if form.is_valid():
            if device.verify_token(int(form.cleaned_data["token"])):
                device.confirmed = True
                device.save(update_fields=["confirmed"])
                otp_login(request, device)
                messages.success(request, "Multi-factor authentication is now enabled.")
                return redirect("app-entry")
            form.add_error("token", "The authenticator code is invalid.")
    else:
        form = MFAConfirmForm()
    return render(
        request,
        "accounts/mfa_setup.html",
        {"device": device, "form": form},
        using="jinja2",
    )

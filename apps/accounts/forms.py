from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django_otp import match_token

from .models import OrganizationMembership


class LoginForm(AuthenticationForm):
    username = forms.EmailField(widget=forms.EmailInput(attrs={"autofocus": True, "autocomplete": "email"}))
    otp_token = forms.CharField(
        max_length=8,
        required=False,
        label="Authenticator code",
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )

    def clean(self):
        cleaned = super().clean()
        user = self.get_user()
        self.otp_device = None
        if user:
            has_device = user.totpdevice_set.filter(confirmed=True).exists()
            token = cleaned.get("otp_token", "")
            if has_device and not token:
                self.add_error("otp_token", "Enter the code from your authenticator app.")
            elif has_device:
                self.otp_device = match_token(user, token)
                if not self.otp_device:
                    self.add_error("otp_token", "The authenticator code is invalid.")
        return cleaned


class AcceptInvitationForm(forms.Form):
    full_name = forms.CharField(max_length=160)
    password = forms.CharField(widget=forms.PasswordInput, min_length=10)


class InvitationForm(forms.Form):
    email = forms.EmailField()
    role = forms.ChoiceField(choices=OrganizationMembership.Role.choices)

    def clean_email(self):
        return self.cleaned_data["email"].casefold()


class MFAConfirmForm(forms.Form):
    token = forms.RegexField(
        regex=r"^\d{6,8}$",
        error_messages={"invalid": "Enter a 6 to 8 digit authenticator code."},
        widget=forms.TextInput(attrs={"autocomplete": "one-time-code", "inputmode": "numeric"}),
    )


class ProfileForm(forms.Form):
    full_name = forms.CharField(max_length=160, label="Full name")

    def clean_full_name(self):
        name = self.cleaned_data["full_name"].strip()
        if len(name) < 2:
            raise forms.ValidationError("Enter your full name.")
        return name

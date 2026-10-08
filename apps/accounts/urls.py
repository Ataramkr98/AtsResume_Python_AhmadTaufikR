from django.urls import path

from .views import AccountLoginView, AccountLogoutView, accept_invitation, mfa_setup

urlpatterns = [
    path("login/", AccountLoginView.as_view(), name="login"),
    path("logout/", AccountLogoutView.as_view(), name="logout"),
    path("invitations/<str:token>/", accept_invitation, name="accept-invitation"),
    path("mfa/setup/", mfa_setup, name="mfa-setup"),
]

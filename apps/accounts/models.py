from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from django.contrib.auth.base_user import BaseUserManager
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

from apps.common.models import UUIDTimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email: str, password: str | None = None, **extra_fields):
        if not email:
            raise ValueError("Email is required")
        user = self.model(email=self.normalize_email(email).casefold(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email: str, password: str, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=160)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    def __str__(self) -> str:
        return self.full_name or self.email


class Organization(UUIDTimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SUSPENDED = "SUSPENDED", "Suspended"

    name = models.CharField(max_length=180)
    slug = models.SlugField(max_length=180, unique=True)
    status = models.CharField(max_length=16, choices=Status, default=Status.ACTIVE)
    timezone = models.CharField(max_length=64, default="UTC")
    default_locale = models.CharField(max_length=10, default="en")
    retention_days = models.PositiveIntegerField(default=730)
    job_approval_required = models.BooleanField(default=True)
    blind_feedback = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class OrganizationMembership(UUIDTimeStampedModel):
    class Role(models.TextChoices):
        ORG_ADMIN = "ORG_ADMIN", "Organization Admin"
        HR_ADMIN = "HR_ADMIN", "HR Admin"
        RECRUITER = "RECRUITER", "Recruiter"
        HIRING_MANAGER = "HIRING_MANAGER", "Hiring Manager"
        INTERVIEWER = "INTERVIEWER", "Interviewer"
        AUDITOR = "AUDITOR", "Auditor"

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=24, choices=Role)
    department_scope = models.JSONField(default=list, blank=True)
    location_scope = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="unique_organization_user")
        ]

    def __str__(self) -> str:
        return f"{self.user} · {self.organization} · {self.get_role_display()}"


class Invitation(UUIDTimeStampedModel):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=24, choices=OrganizationMembership.Role)
    token_hash = models.CharField(max_length=64, unique=True)
    invited_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="sent_invitations")
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "email"])]

    @classmethod
    def issue(cls, *, organization, email, role, invited_by) -> tuple[Invitation, str]:
        token = secrets.token_urlsafe(32)
        invitation = cls.objects.create(
            organization=organization,
            email=email.casefold(),
            role=role,
            token_hash=hashlib.sha256(token.encode()).hexdigest(),
            invited_by=invited_by,
            expires_at=timezone.now() + timedelta(hours=48),
        )
        return invitation, token

    @classmethod
    def find_valid(cls, token: str) -> Invitation | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return cls.objects.filter(
            token_hash=token_hash, accepted_at__isnull=True, expires_at__gt=timezone.now()
        ).select_related("organization").first()

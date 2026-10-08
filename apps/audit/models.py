from django.db import models

from apps.common.models import OrganizationOwnedModel


class AuditLog(OrganizationOwnedModel):
    actor = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_events"
    )
    action = models.CharField(max_length=100)
    resource_type = models.CharField(max_length=100)
    resource_id = models.CharField(max_length=64)
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["organization", "created_at"])]


def record_audit(
    *, organization, actor, action, resource, before=None, after=None, request=None
) -> AuditLog:
    return AuditLog.objects.create(
        organization=organization,
        actor=actor if getattr(actor, "is_authenticated", False) else None,
        action=action,
        resource_type=resource._meta.label_lower,
        resource_id=str(resource.pk),
        before=before or {},
        after=after or {},
        ip_address=request.META.get("REMOTE_ADDR") if request else None,
        user_agent=request.headers.get("User-Agent", "")[:255] if request else "",
        request_id=getattr(request, "request_id", "") if request else "",
    )

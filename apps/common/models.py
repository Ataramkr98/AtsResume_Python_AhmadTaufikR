import uuid

from django.db import models


class UUIDTimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class OrganizationOwnedModel(UUIDTimeStampedModel):
    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE)

    class Meta:
        abstract = True

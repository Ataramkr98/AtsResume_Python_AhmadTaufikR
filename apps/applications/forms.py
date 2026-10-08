from django import forms

from .models import RejectionReason


class MoveStageForm(forms.Form):
    target_stage_id = forms.UUIDField()
    expected_version = forms.IntegerField(min_value=1)
    idempotency_key = forms.CharField(max_length=100)
    reason = forms.CharField(max_length=255, required=False)


class RejectApplicationForm(forms.Form):
    reason = forms.ModelChoiceField(queryset=RejectionReason.objects.none())
    note = forms.CharField(max_length=255, required=False)

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["reason"].queryset = RejectionReason.objects.filter(
                organization=organization, active=True
            )

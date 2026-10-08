from django import forms

from apps.accounts.models import User
from apps.jobs.models import ScorecardTemplate


class InterviewForm(forms.Form):
    title = forms.CharField(max_length=160)
    interview_type = forms.ChoiceField(
        choices=[("VIDEO", "Video"), ("PHONE", "Phone"), ("ONSITE", "On-site")]
    )
    start_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    end_at = forms.DateTimeField(widget=forms.DateTimeInput(attrs={"type": "datetime-local"}))
    timezone = forms.CharField(max_length=64, initial="UTC")
    location = forms.CharField(max_length=255, required=False)
    meeting_url = forms.URLField(required=False, assume_scheme="https")
    scorecard_template = forms.ModelChoiceField(queryset=ScorecardTemplate.objects.none())
    participants = forms.ModelMultipleChoiceField(queryset=User.objects.none())

    def __init__(self, *args, application=None, **kwargs):
        super().__init__(*args, **kwargs)
        if application:
            self.fields["scorecard_template"].queryset = application.job.scorecard_templates.filter(active=True)
            member_ids = application.organization.memberships.filter(active=True).values_list("user_id", flat=True)
            self.fields["participants"].queryset = User.objects.filter(id__in=member_ids)

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("start_at") and cleaned.get("end_at") and cleaned["end_at"] <= cleaned["start_at"]:
            self.add_error("end_at", "End time must be after start time.")
        return cleaned

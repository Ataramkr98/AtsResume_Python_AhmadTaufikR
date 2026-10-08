from django import forms

from apps.accounts.models import User
from apps.applications.models import Application
from apps.jobs.models import Job

from .models import Task


class TaskForm(forms.Form):
    title = forms.CharField(max_length=180)
    description = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}), required=False)
    assignee = forms.ModelChoiceField(queryset=User.objects.none())
    priority = forms.ChoiceField(choices=Task.Priority.choices, initial=Task.Priority.NORMAL)
    due_at = forms.DateTimeField(
        required=False, widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    job = forms.ModelChoiceField(queryset=Job.objects.none(), required=False)
    application = forms.ModelChoiceField(queryset=Application.objects.none(), required=False)

    def __init__(self, *args, organization=None, jobs=None, applications=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            member_ids = organization.memberships.filter(active=True).values_list(
                "user_id", flat=True
            )
            self.fields["assignee"].queryset = User.objects.filter(id__in=member_ids).order_by(
                "full_name"
            )
        if jobs is not None:
            self.fields["job"].queryset = jobs
        if applications is not None:
            self.fields["application"].queryset = applications.select_related(
                "candidate", "job"
            )[:200]


class TaskStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Task.Status.choices)

import bleach
from django import forms

from .models import Job


class JobTransitionForm(forms.Form):
    """Validates the raw POST input for a job status transition.

    The view used to call ``int(request.POST.get("expected_version", 0))`` inline, so a
    hand-crafted or truncated request carrying a non-numeric version crashed the worker with an
    uncaught ``ValueError`` before any business rule ran. Every other optimistic-concurrency
    entry point in the codebase validates this pair through a form, so this one does too.
    """

    target_status = forms.ChoiceField(choices=Job.Status.choices)
    expected_version = forms.IntegerField(min_value=1)


class JobForm(forms.ModelForm):
    class Meta:
        model = Job
        fields = [
            "title",
            "department",
            "location",
            "hiring_manager",
            "recruiter",
            "pipeline_template",
            "employment_type",
            "headcount",
            "description_html",
            "requirements_html",
            "min_experience_months",
            "education_level",
        ]
        widgets = {"description_html": forms.Textarea, "requirements_html": forms.Textarea}

    def __init__(self, *args, organization=None, **kwargs):
        super().__init__(*args, **kwargs)
        if organization:
            self.fields["department"].queryset = organization.department_set.filter(active=True)
            self.fields["location"].queryset = organization.location_set.filter(active=True)
            self.fields["pipeline_template"].queryset = organization.pipelinetemplate_set.filter(active=True)
            users = organization.memberships.filter(active=True).values_list("user_id", flat=True)
            self.fields["hiring_manager"].queryset = self.fields["hiring_manager"].queryset.filter(id__in=users)
            self.fields["recruiter"].queryset = self.fields["recruiter"].queryset.filter(id__in=users)

    def clean_description_html(self):
        return bleach.clean(
            self.cleaned_data["description_html"],
            tags={"p", "br", "strong", "em", "ul", "ol", "li", "h2", "h3"},
            strip=True,
        )

    def clean_requirements_html(self):
        return bleach.clean(
            self.cleaned_data["requirements_html"],
            tags={"p", "br", "strong", "em", "ul", "ol", "li", "h2", "h3"},
            strip=True,
        )

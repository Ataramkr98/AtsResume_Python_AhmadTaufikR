from django import forms

from .models import Candidate

EDUCATION_CHOICES = [
    ("", "Not specified"),
    ("high_school", "High school"),
    ("associate", "Associate degree"),
    ("bachelor", "Bachelor's degree"),
    ("master", "Master's degree"),
    ("doctorate", "Doctorate"),
]

SOURCE_CHOICES = [
    ("MANUAL", "Manual upload"),
    ("CAREERS_PAGE", "Careers page"),
    ("REFERRAL", "Referral"),
    ("AGENCY", "Agency"),
    ("EMAIL", "Email ingestion"),
    ("API", "API"),
]


class PublicApplicationForm(forms.Form):
    first_name = forms.CharField(max_length=100)
    last_name = forms.CharField(max_length=100, required=False)
    email = forms.EmailField()
    phone = forms.CharField(max_length=40, required=False)
    resume = forms.FileField()
    consent = forms.BooleanField(label="I consent to the processing of my application data.")


class CandidateForm(forms.ModelForm):
    """Manual candidate entry for referrals, agencies and sourced profiles."""

    email = forms.EmailField(label="Primary email")
    phone = forms.CharField(max_length=40, required=False)
    education_level = forms.ChoiceField(choices=EDUCATION_CHOICES, required=False)
    source_first = forms.ChoiceField(choices=SOURCE_CHOICES, initial="MANUAL")

    class Meta:
        model = Candidate
        fields = [
            "first_name",
            "last_name",
            "current_title",
            "current_company",
            "city",
            "country_code",
            "total_experience_months",
            "education_level",
            "source_first",
            "summary",
        ]
        widgets = {"summary": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["country_code"].widget.attrs.update({"maxlength": 2, "placeholder": "ID"})
        if self.instance and self.instance.pk:
            self.fields["email"].initial = self.instance.primary_email
            self.fields["phone"].initial = self.instance.primary_phone

    def clean_email(self):
        return self.cleaned_data["email"].strip().casefold()

    def clean_country_code(self):
        return (self.cleaned_data.get("country_code") or "").strip().upper()

    def clean_source_first(self):
        return (self.cleaned_data.get("source_first") or "MANUAL").strip().upper()

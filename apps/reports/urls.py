from django.urls import path

from . import views

urlpatterns = [
    path("reports/", views.reports_dashboard, name="reports"),
    path("reports/export/applications.csv", views.export_applications_csv, name="reports-export"),
]
